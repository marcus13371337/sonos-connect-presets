#!/bin/bash
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
CONFIG=${1:-$REPO/presets.conf}

mkdir -p /opt/spotify-bridge
[ -d /opt/spotify-bridge/venv ] || python3 -m venv /opt/spotify-bridge/venv
/opt/spotify-bridge/venv/bin/pip install -q -r "$REPO/bridge/requirements.txt"
install -m 644 "$REPO/bridge/controller.py" "$REPO/bridge/sonos.py" /opt/spotify-bridge/

PRESETS=$(PYTHONPATH="$REPO/bridge" python3 "$REPO/deploy/presets.py" "$CONFIG")

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
ExecStart=/opt/spotify-bridge/venv/bin/python -u /opt/spotify-bridge/controller.py /etc/go-librespot/%i/bridge.json
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
