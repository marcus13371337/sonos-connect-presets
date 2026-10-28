import asyncio
import hashlib
import json
import logging
import ssl
import sys
import time
import urllib.parse
import urllib.request

import websockets

LIBRESPOT_PORT = int(sys.argv[1])
COORDINATOR_IP = sys.argv[2]
MEMBER_IPS = sys.argv[3:]
LIBRESPOT_API = f"http://127.0.0.1:{LIBRESPOT_PORT}"
MAX_VOLUME = 20
SONOS_API_KEY = "123e4567-e89b-12d3-a456-426655440000"
SONOS_TLS = ssl._create_unverified_context()

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


def spclient_host() -> str:
    return http_json("https://apresolve.spotify.com/?type=spclient")["spclient"][0].split(":")[0]


def transfer_playback(to_device_id: str):
    own_device_id = http_json(f"{LIBRESPOT_API}/status")["device_id"]
    token = http_json(f"{LIBRESPOT_API}/token", method="POST")["token"]
    url = f"https://{spclient_host()}/connect-state/v1/connect/transfer/from/{own_device_id}/to/{to_device_id}"
    http_json(url, method="POST", body={"transfer_options": {"restore_paused": "restore"}},
              headers={"Authorization": f"Bearer {token}"})


def sonos_api(ip: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://{ip}:1443/api/v1{path}",
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Sonos-Api-Key": SONOS_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5, context=SONOS_TLS) as resp:
        return json.loads(resp.read())


def sonos_connect_device_id(player_id: str) -> str:
    return hashlib.sha1(player_id.encode()).hexdigest()


def regroup(coordinator_ip: str, member_ips: list[str]):
    info = sonos_api(coordinator_ip, "/players/local/info")
    household = f"/households/{info['householdId']}"
    state = sonos_api(coordinator_ip, f"{household}/groups")
    hosts = {p["id"]: urllib.parse.urlsplit(p["websocketUrl"]).hostname for p in state["players"]}
    wanted = [info["playerId"]] + [player_id for player_id, host in hosts.items() if host in member_ips]
    for ip in set(member_ips) - set(hosts.values()):
        log.warning("%s is offline, skipping", ip)

    volume = MAX_VOLUME
    playing = [g for g in state["groups"] if g["playbackState"] == "PLAYBACK_STATE_PLAYING"]
    if playing:
        group_host = hosts[playing[0]["coordinatorId"]]
        volume = min(MAX_VOLUME, sonos_api(group_host, f"/groups/{playing[0]['id']}/groupVolume")["volume"])

    current = next(g for g in state["groups"] if info["playerId"] in g["playerIds"])
    if current["coordinatorId"] == info["playerId"]:
        group = sonos_api(coordinator_ip, f"/groups/{current['id']}/groups/setGroupMembers", {"playerIds": wanted})
    else:
        group = sonos_api(coordinator_ip, f"{household}/groups/createGroup", {"playerIds": wanted})
    log.info("group is now %s at volume %d", group["group"]["name"], volume)

    for player_id in wanted:
        try:
            sonos_api(hosts[player_id], f"/players/{player_id}/playerVolume", {"volume": volume})
        except OSError as e:
            log.warning("could not set volume on %s: %s", hosts[player_id], e)


class Handoff:
    def __init__(self):
        self.target_id: str | None = None
        self.handed_off = False

    def handle(self, event: str, data: dict):
        if event == "active":
            self.handed_off = False
        elif event == "playing" and not self.handed_off:
            self.handed_off = True
            t0 = time.monotonic()
            try:
                regroup(COORDINATOR_IP, MEMBER_IPS)
            except OSError as e:
                log.warning("regrouping failed, transferring anyway: %s", e)
            t_grouped = time.monotonic()
            if self.target_id is None:
                player_id = sonos_api(COORDINATOR_IP, "/players/local/info")["playerId"]
                self.target_id = sonos_connect_device_id(player_id)
            transfer_playback(self.target_id)
            http_json(f"{LIBRESPOT_API}/player/stop", method="POST")
            log.info("regrouped in %.2fs, transferred %s in %.2fs", t_grouped - t0, data.get("uri"),
                     time.monotonic() - t_grouped)


async def main():
    handoff = Handoff()
    log.info("handing off to %s (+%s)", COORDINATOR_IP, ", ".join(MEMBER_IPS) or "no members")
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
