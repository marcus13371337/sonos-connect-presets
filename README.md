# sonos-connect-presets

Virtual Spotify Connect speakers that group Sonos speakers and hand playback over to them.

Pick "🔊 Alla" in Spotify and the bridge groups the Sonos speakers, sets every speaker to the same volume (the volume already playing, capped at 20 %), transfers playback to the group coordinator and steps aside. Spotify then shows the Sonos group as the active device.

## How it works

- `librespot/silent-playback.patch` patches [go-librespot](https://github.com/devgianlu/go-librespot) v0.10.5:
  - `silent_playback`: plays real-time paced silence for each track instead of fetching audio. No audio keys, no decoding.
  - `device_is_group`: shows the device with Spotify's group icon.
- `bridge/controller.py` runs one per preset. It listens on go-librespot's event websocket. On the first `playing` event it:
  1. Regroups the speakers through the Sonos local API (`https://<ip>:1443/api/v1`).
  2. Transfers playback through Spotify's connect-state API to the coordinator. The coordinator's Spotify Connect ID is `sha1(RINCON id)`.
  3. Stops the virtual device.
- A Sonos speaker can only receive a transfer once it has been registered with the Spotify account. To register it, select it once in the Spotify app, or play Spotify on it from the Sonos app.

## Install

Runs in a Debian 12 LXC with Go at `/opt/go` and the go-librespot build dependencies installed (`libasound2-dev libvorbis-dev libogg-dev libflac-dev`, plus avahi for zeroconf).

```sh
cp presets.example.conf presets.conf
deploy/build_librespot.sh
deploy/install_presets.sh
```

Copy `presets.example.conf` to `presets.conf` and list your presets first. Each line is `preset <id> "<name>" <true|false: group icon> <coordinator ip> [member ips...]`. API ports are assigned from 3681 in file order, so keep the order stable.
