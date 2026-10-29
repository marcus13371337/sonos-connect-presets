import asyncio
import hashlib
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

from sonos import Household, Locator, sonos_api

PRESET = json.loads(Path(sys.argv[1]).read_text())
LIBRESPOT_PORT = PRESET["port"]
COORDINATOR = PRESET["coordinator"]
MEMBERS = PRESET["members"]
LIBRESPOT_API = f"http://127.0.0.1:{LIBRESPOT_PORT}"
MAX_VOLUME = 20
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
LIBRESPOT_CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"
TRANSFER_ATTEMPTS_AFTER_LOGIN = 10

log = logging.getLogger("controller")


def http_json(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def http_form(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, method="POST", data=urllib.parse.urlencode(form).encode())
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def spclient_host() -> str:
    return http_json("https://apresolve.spotify.com/?type=spclient")["spclient"][0].split(":")[0]


def transfer_playback(to_device_id: str):
    own_device_id = http_json(f"{LIBRESPOT_API}/status")["device_id"]
    token = http_json(f"{LIBRESPOT_API}/token", method="POST")["token"]
    url = f"https://{spclient_host()}/connect-state/v1/connect/transfer/from/{own_device_id}/to/{to_device_id}"
    http_json(url, method="POST", body={"transfer_options": {"restore_paused": "restore"}},
              headers={"Authorization": f"Bearer {token}"})


def log_in_sonos(ip: str):
    zeroconf = f"http://{ip}:1400/spotifyzc"
    info = http_json(f"{zeroconf}?action=getInfo")
    status = http_json(f"{LIBRESPOT_API}/status")
    token = http_json(f"{LIBRESPOT_API}/token", method="POST")["token"]
    code = http_form(SPOTIFY_TOKEN_URL, {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "requested_token_type": "urn:spotify:params:oauth:authorization_code",
        "resource": "urn:spotify:resources:connect",
        "client_id": LIBRESPOT_CLIENT_ID,
        "audience": info["clientID"],
        "scope": "streaming",
    })["access_token"]
    reply = http_form(zeroconf, {
        "action": "addUser",
        "userName": status["username"],
        "blob": code,
        "clientKey": "",
        "tokenType": info["tokenType"],
        "loginId": status["username"],
        "deviceName": status["device_name"],
        "deviceId": status["device_id"],
        "version": info["version"],
    })
    if reply["status"] != 101:
        raise RuntimeError(f"{ip} refused the Spotify login: {reply['statusString']}")


def hand_off_to(target_id: str, coordinator_ip: str):
    try:
        transfer_playback(target_id)
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    log.info("coordinator is on another Spotify account, logging it in")
    log_in_sonos(coordinator_ip)
    for _ in range(TRANSFER_ATTEMPTS_AFTER_LOGIN):
        time.sleep(1)
        try:
            transfer_playback(target_id)
            return
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    raise RuntimeError("coordinator did not show up on the Spotify account after logging in")


def sonos_connect_device_id(player_id: str) -> str:
    return hashlib.sha1(player_id.encode()).hexdigest()


def regroup(home: Household, coordinator: str) -> list[dict]:
    wanted = [coordinator] + [home.speaker_ids[name] for name in MEMBERS if name in home.speaker_ids]
    for name in set(MEMBERS) - home.speaker_ids.keys():
        log.warning("%s is offline, skipping", name)

    volume = MAX_VOLUME
    playing = [g for g in home.groups if g["playbackState"] == "PLAYBACK_STATE_PLAYING"]
    if playing:
        group_host = home.hosts[playing[0]["coordinatorId"]]
        volume = min(MAX_VOLUME, sonos_api(group_host, f"/groups/{playing[0]['id']}/groupVolume")["volume"])

    coordinator_ip = home.hosts[coordinator]
    current = next(g for g in home.groups if coordinator in g["playerIds"])
    if current["coordinatorId"] == coordinator:
        group = sonos_api(coordinator_ip, f"/groups/{current['id']}/groups/setGroupMembers", {"playerIds": wanted})
    else:
        group = sonos_api(coordinator_ip, f"{home.path}/groups/createGroup", {"playerIds": wanted})
    log.info("group is now %s at volume %d", group["group"]["name"], volume)

    for player_id in wanted:
        try:
            sonos_api(home.hosts[player_id], f"/players/{player_id}/playerVolume", {"volume": volume})
        except OSError as e:
            log.warning("could not set volume on %s: %s", home.hosts[player_id], e)
    return home.groups


def restore_groups(locator: Locator, coordinator: str, previous_groups: list[dict]):
    for previous in sorted(previous_groups, key=lambda g: g["coordinatorId"] != coordinator):
        home = locator.household()
        current = next((g for g in home.groups if previous["coordinatorId"] in g["playerIds"]), None)
        if (current and current["coordinatorId"] == previous["coordinatorId"]
                and set(current["playerIds"]) != set(previous["playerIds"])):
            sonos_api(home.hosts[current["coordinatorId"]], f"/groups/{current['id']}/groups/setGroupMembers",
                      {"playerIds": previous["playerIds"]})
    log.info("restored the previous groups")


class Handoff:
    def __init__(self):
        self.locator = Locator()
        self.armed = False

    def handle(self, event: str, data: dict):
        if event == "active":
            self.armed = True
        elif event in ("playing", "paused") and self.armed:
            self.armed = False
            self.hand_off(data)

    def hand_off(self, data: dict):
        t0 = time.monotonic()
        try:
            home = self.locator.household()
            coordinator = home.speaker_ids.get(COORDINATOR)
            if coordinator is None:
                raise RuntimeError(f"{COORDINATOR} is offline")
            previous_groups = None
            try:
                previous_groups = regroup(home, coordinator)
            except OSError as e:
                log.warning("regrouping failed, transferring anyway: %s", e)
            t_grouped = time.monotonic()
            try:
                hand_off_to(sonos_connect_device_id(coordinator), home.hosts[coordinator])
            except Exception:
                if previous_groups is not None:
                    restore_groups(self.locator, coordinator, previous_groups)
                raise
        finally:
            http_json(f"{LIBRESPOT_API}/player/stop", method="POST")
        log.info("regrouped in %.2fs, transferred %s in %.2fs", t_grouped - t0, data.get("uri"),
                 time.monotonic() - t_grouped)


async def main():
    handoff = Handoff()
    log.info("handing off to %s (+%s)", COORDINATOR, ", ".join(MEMBERS) or "no members")
    async for ws in websockets.connect(f"ws://127.0.0.1:{LIBRESPOT_PORT}/events"):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                data = msg.get("data") or {}
                log.info("event %s %s", msg["type"], data.get("uri"))
                try:
                    await asyncio.to_thread(handoff.handle, msg["type"], data)
                except Exception:
                    log.exception("handling %s failed", msg["type"])
        except websockets.ConnectionClosed:
            continue


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(main())
