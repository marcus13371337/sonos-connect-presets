#!/bin/bash
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
CONFIG=${1:-$REPO/presets.conf}

mkdir -p /opt/spotify-bridge
[ -d /opt/spotify-bridge/venv ] || python3 -m venv /opt/spotify-bridge/venv
/opt/spotify-bridge/venv/bin/pip install -q -r "$REPO/bridge/requirements.txt"
install -m 644 "$REPO/bridge/controller.py" /opt/spotify-bridge/controller.py

PRESETS=""
NEXT_PORT=3681

preset() {
  local id=$1 name=$2 is_group=$3 coordinator=$4 port=$NEXT_PORT; shift 4
  PRESETS="$PRESETS $id"
  NEXT_PORT=$((NEXT_PORT + 1))
  mkdir -p "/etc/go-librespot/$id"
  cat > "/etc/go-librespot/$id/config.yml" <<EOF
device_name: $name
device_type: speaker
device_is_group: $is_group
audio_backend: pipe
audio_output_pipe: /run/go-librespot-$id/audio.fifo
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
  port: $port
log_level: debug
EOF
  printf 'PORT=%s\nCOORDINATOR=%s\nMEMBERS="%s"\n' "$port" "$coordinator" "$*" > "/etc/go-librespot/$id/bridge.conf"
}

source "$CONFIG"

cat > /etc/systemd/system/go-librespot@.service <<'EOF'
[Unit]
Description=go-librespot preset %i
After=network-online.target

[Service]
Environment=HOME=/var/lib/go-librespot-%i
StateDirectory=go-librespot-%i
RuntimeDirectory=go-librespot-%i
ExecStartPre=/usr/bin/mkfifo /run/go-librespot-%i/audio.fifo
ExecStart=/bin/sh -c '(while true; do cat /run/go-librespot-%i/audio.fifo > /dev/null; done) & exec /opt/go-librespot/go-librespot-silent --config_dir /etc/go-librespot/%i'
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/spotify-bridge@.service <<'EOF'
[Unit]
Description=Hand off preset %i to Sonos
After=go-librespot@%i.service
Requires=go-librespot@%i.service

[Service]
EnvironmentFile=/etc/go-librespot/%i/bridge.conf
ExecStart=/opt/spotify-bridge/venv/bin/python -u /opt/spotify-bridge/controller.py $PORT $COORDINATOR $MEMBERS
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
for id in $PRESETS; do
  systemctl enable "go-librespot@$id" "spotify-bridge@$id"
  systemctl restart "go-librespot@$id" "spotify-bridge@$id"
done
sleep 4
for id in $PRESETS; do echo "$id $(systemctl is-active go-librespot@$id) $(systemctl is-active spotify-bridge@$id)"; done
journalctl -u 'spotify-bridge@*' -n 4 --no-pager | cut -c1-160
