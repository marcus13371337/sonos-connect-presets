import json
import logging
import socket
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass

SONOS_API_KEY = "123e4567-e89b-12d3-a456-426655440000"
SONOS_TLS = ssl._create_unverified_context()
SSDP_ADDRESS = ("239.255.255.250", 1900)
ZONE_PLAYER = "urn:schemas-upnp-org:device:ZonePlayer:1"

log = logging.getLogger("sonos")


def sonos_api(ip: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://{ip}:1443/api/v1{path}",
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Sonos-Api-Key": SONOS_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5, context=SONOS_TLS) as resp:
        return json.loads(resp.read())


def discover_player_ip(timeout: float = 3) -> str:
    search = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDRESS[0]}:{SSDP_ADDRESS[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        f"ST: {ZONE_PLAYER}\r\n\r\n"
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.settimeout(timeout)
        sock.sendto(search.encode(), SSDP_ADDRESS)
        while True:
            reply, (ip, _) = sock.recvfrom(4096)
            if ZONE_PLAYER.encode() in reply:
                return ip


@dataclass
class Household:
    path: str
    groups: list[dict]
    hosts: dict[str, str]
    speaker_ids: dict[str, str]


def household(ip: str) -> Household:
    info = sonos_api(ip, "/players/local/info")
    path = f"/households/{info['householdId']}"
    state = sonos_api(ip, f"{path}/groups")
    return Household(
        path=path,
        groups=state["groups"],
        hosts={p["id"]: urllib.parse.urlsplit(p["websocketUrl"]).hostname for p in state["players"]},
        speaker_ids={p["name"]: p["id"] for p in state["players"]},
    )


class Locator:
    def __init__(self):
        self.ip: str | None = None

    def household(self) -> Household:
        if self.ip is not None:
            try:
                return household(self.ip)
            except OSError as e:
                log.info("%s stopped answering, rediscovering: %s", self.ip, e)
        self.ip = discover_player_ip()
        return household(self.ip)
