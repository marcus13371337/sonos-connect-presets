# sonos-connect-presets

Virtual Spotify Connect speakers that group Sonos speakers and hand playback over to them.

Pick "🔊 Alla" in Spotify and the bridge groups the Sonos speakers, sets every speaker to the same volume (the volume already playing, capped at 20 %), transfers playback to the group coordinator and steps aside. Spotify then shows the Sonos group as the active device.

## How it works

- `librespot/silent-playback.patch` patches [go-librespot](https://github.com/devgianlu/go-librespot) v0.10.5:
  - `silent_playback`: plays real-time paced silence for each track instead of fetching audio. No audio keys, no decoding.
  - `device_is_group`: shows the device with Spotify's group icon.
- `bridge/controller.py` runs one per preset. It listens on go-librespot's event websocket. On the first `playing` or `paused` event after the device becomes active it:
  1. Finds a Sonos speaker through SSDP, looks up the preset's rooms by name, and regroups them through the Sonos local API (`https://<ip>:1443/api/v1`).
  2. Transfers playback through Spotify's connect-state API to the coordinator. The coordinator's Spotify Connect ID is `sha1(RINCON id)`.
  3. Stops the virtual device.
- A Sonos speaker is logged in to one Spotify account at a time, and only that account can transfer playback to it. When the transfer fails because the coordinator is on another account, the bridge logs the coordinator in to the preset's account through Sonos's local zeroconf endpoint (`http://<ip>:1400/spotifyzc`) and retries. The login sends an authorization code from Spotify's token exchange, issued for Sonos's client ID.
- If the handoff fails, the bridge restores the previous groups and stops the virtual device.

## Install

Runs in a Debian 12 LXC with Go at `/opt/go` and the go-librespot build dependencies installed (`libasound2-dev libvorbis-dev libogg-dev libflac-dev`, plus avahi for zeroconf).

```sh
deploy/build_librespot.sh
deploy/install_presets.sh
```

The install script finds every Sonos room and creates one preset per room plus "🔊 Alla" with all rooms. Presets that no longer exist are removed. Run it again after adding or renaming a room in Sonos.

`presets.conf` is optional. See `presets.example.conf`:

- `prefix room "<prefix>"`, `prefix group "<prefix>"`: put before the names shown in Spotify. Defaults are `🔈 ` and `🔊 `.
- `all "<name>" [coordinator "<room>"]`: renames Alla or picks its coordinator, which streams for the whole group. Defaults to the first room alphabetically. `all off` removes it.
- `exclude "<room>"...`: leaves rooms out of every automatic preset.
- `group <id> "<name>" "<coordinator room>" "<room>"...`: adds a preset for its own group.

`PYTHONPATH=bridge python3 deploy/presets.py presets.conf --dry-run` shows the presets without installing.

## Disclaimer

Not affiliated with Spotify or Sonos. go-librespot is an unofficial Spotify client, and the bridge calls Spotify's private connect-state API. Both need Spotify Premium and may break or go against Spotify's terms at any time. Use at your own risk.

## License

[GPL-3.0](LICENSE), the same license as go-librespot, which the patch is derived from.
