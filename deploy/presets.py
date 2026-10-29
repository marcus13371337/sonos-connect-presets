import json
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from sonos import discover_player_ip, household

CONFIG_ROOT = Path("/etc/go-librespot")
BRIDGE_CONFIG = "bridge.json"
FIRST_PORT = 3681
LIBRESPOT_CONFIG = """device_name: {name}
device_type: speaker
device_is_group: {is_group}
audio_backend: pipe
audio_output_pipe: /run/go-librespot-{id}/audio.fifo
silent_playback: true
bitrate: 96
zeroconf_enabled: true
credentials:
  type: zeroconf
  zeroconf:
    persist_credentials: true
server:
  enabled: true
  address: 127.0.0.1
  port: {port}
log_level: debug
"""


@dataclass
class Preset:
    id: str
    name: str
    coordinator: str
    members: list[str] = field(default_factory=list)


@dataclass
class Config:
    room_prefix: str = "🔈 "
    group_prefix: str = "🔊 "
    all_name: str | None = "Alla"
    all_coordinator: str | None = None
    excluded: set[str] = field(default_factory=set)
    groups: list[Preset] = field(default_factory=list)


def slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def parse(path: Path) -> Config:
    config = Config()
    if not path.exists():
        return config
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match shlex.split(line, comments=True):
            case []:
                pass
            case ["prefix", "room", prefix]:
                config.room_prefix = prefix
            case ["prefix", "group", prefix]:
                config.group_prefix = prefix
            case ["all", "off"]:
                config.all_name = None
            case ["all", name]:
                config.all_name = name
            case ["all", name, "coordinator", room]:
                config.all_name, config.all_coordinator = name, room
            case ["exclude", *rooms]:
                config.excluded.update(rooms)
            case ["group", preset_id, name, coordinator, *members]:
                config.groups.append(Preset(preset_id, name, coordinator, members))
            case _:
                sys.exit(f"{path}:{number}: cannot parse: {line}")
    return config


def plan(config: Config, rooms: list[str]) -> list[Preset]:
    rooms = sorted(set(rooms) - config.excluded)
    presets = []
    if config.all_name is not None and len(rooms) > 1:
        coordinator = config.all_coordinator or rooms[0]
        presets.append(Preset(slug(config.all_name), config.all_name, coordinator,
                              [room for room in rooms if room != coordinator]))
    presets += config.groups
    presets += [Preset(slug(room), room, room) for room in rooms]

    ids = [preset.id for preset in presets]
    if duplicates := {i for i in ids if ids.count(i) > 1}:
        sys.exit(f"duplicate preset ids: {', '.join(sorted(duplicates))}")
    for preset in presets:
        for room in {preset.coordinator, *preset.members} - set(rooms):
            print(f"warning: {preset.id} uses {room}, which is not among the found rooms", file=sys.stderr)
    return presets


def write(config: Config, presets: list[Preset]):
    for port, preset in enumerate(presets, FIRST_PORT):
        prefix = config.group_prefix if preset.members else config.room_prefix
        directory = CONFIG_ROOT / preset.id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.yml").write_text(LIBRESPOT_CONFIG.format(
            id=preset.id,
            name=json.dumps(prefix + preset.name, ensure_ascii=False),
            is_group=json.dumps(bool(preset.members)),
            port=port,
        ))
        (directory / BRIDGE_CONFIG).write_text(json.dumps(
            {"port": port, "coordinator": preset.coordinator, "members": preset.members}, ensure_ascii=False))


def remove_stale(presets: list[Preset]):
    wanted = {preset.id for preset in presets}
    for directory in CONFIG_ROOT.iterdir():
        is_preset = (directory / BRIDGE_CONFIG).exists() or (directory / "bridge.conf").exists()
        if is_preset and directory.name not in wanted:
            subprocess.run(["systemctl", "disable", "--now",
                            f"go-librespot@{directory.name}", f"spotify-bridge@{directory.name}"], check=False)
            shutil.rmtree(directory)
            print(f"removed {directory.name}", file=sys.stderr)


def main():
    dry_run = "--dry-run" in sys.argv
    config = parse(Path(next(arg for arg in sys.argv[1:] if arg != "--dry-run")))
    presets = plan(config, list(household(discover_player_ip()).room_ids))
    if dry_run:
        for preset in presets:
            print(f"{preset.id} \"{preset.name}\": {preset.coordinator} + {', '.join(preset.members) or '-'}", file=sys.stderr)
        return
    write(config, presets)
    remove_stale(presets)
    print(" ".join(preset.id for preset in presets))


if __name__ == "__main__":
    main()
