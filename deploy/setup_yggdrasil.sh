#!/bin/bash
# setup_yggdrasil.sh
# Install Yggdrasil + yggcrawl on an Oracle Ubuntu 22.04 node.
# Idempotent — safe to re-run.
#
# Usage: bash setup_yggdrasil.sh
set -euo pipefail

info() { echo "[INFO]  $*"; }
ok()   { echo "[OK]    $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

YGGCRAWL_REPO="https://github.com/PregnantPenguins789/yggcrawl.git"
YGGCRAWL_DIR="/opt/yggcrawl"
YGGCRAWL_NODE_HOME="/opt/yggcrawl-node"
OUTBOX_DIR="/opt/vps-pypi-place/data/outbox"
SERVICE_USER="ubuntu"

# ─────────────────────────────────────────────
# 1. YGGDRASIL
# ─────────────────────────────────────────────

if command -v yggdrasil &>/dev/null; then
    ok "Yggdrasil already installed."
else
    info "Installing Yggdrasil..."
    sudo apt-get install -y curl gpg

    curl -fsSL https://neilalexander.s3.dualstack.eu-west-2.amazonaws.com/deb/key.gpg \
        | sudo gpg --dearmor -o /usr/share/keyrings/yggdrasil-keyring.gpg

    echo "deb [signed-by=/usr/share/keyrings/yggdrasil-keyring.gpg] \
https://neilalexander.s3.dualstack.eu-west-2.amazonaws.com/deb/ debian edgy main" \
        | sudo tee /etc/apt/sources.list.d/yggdrasil.list

    sudo apt-get update -qq
    sudo apt-get install -y yggdrasil
    ok "Yggdrasil installed."
fi

# ─────────────────────────────────────────────
# 2. YGGDRASIL CONFIG + PEERS
# ─────────────────────────────────────────────

if [ ! -f /etc/yggdrasil/yggdrasil.conf ]; then
    info "Generating Yggdrasil config..."
    sudo mkdir -p /etc/yggdrasil
    sudo yggdrasil -genconf | sudo tee /etc/yggdrasil/yggdrasil.conf >/dev/null
    ok "Config generated."
fi

# Inject public peers if not already set
if ! sudo grep -q '"tcp://' /etc/yggdrasil/yggdrasil.conf 2>/dev/null; then
    info "Injecting public peers..."
    sudo python3 - <<'PYEOF'
import re, pathlib

conf_path = pathlib.Path("/etc/yggdrasil/yggdrasil.conf")
conf = conf_path.read_text()

peers = [
    "tcp://54.37.137.221:11234",    # OVH France
    "tcp://163.172.31.125:5678",    # Scaleway Paris
    "tcp://185.175.56.144:3000",    # Netherlands
    "tcp://108.175.10.173:30002",   # US East
    "tcp://23.137.249.65:444",      # US
]
peers_block = "  Peers: [\n" + "\n".join(f'    "{p}",' for p in peers) + "\n  ]"
conf = re.sub(r'  Peers:\s*\[\s*\]', peers_block, conf)
conf_path.write_text(conf)
print("Peers written.")
PYEOF
    ok "Public peers configured."
else
    ok "Peers already configured."
fi

# ─────────────────────────────────────────────
# 3. START YGGDRASIL
# ─────────────────────────────────────────────

info "Enabling Yggdrasil service..."
sudo systemctl enable yggdrasil
sudo systemctl restart yggdrasil

# Wait up to 20s for the Yggdrasil interface to appear
for i in $(seq 1 20); do
    if ip addr show | grep -qP '2[0-9a-f]{2}:'; then
        break
    fi
    sleep 1
done
ok "Yggdrasil running."

# ─────────────────────────────────────────────
# 4. CLONE YGGCRAWL
# ─────────────────────────────────────────────

if [ -d "$YGGCRAWL_DIR/.git" ]; then
    info "Updating yggcrawl..."
    git -C "$YGGCRAWL_DIR" pull origin master
    ok "yggcrawl up to date."
else
    info "Cloning yggcrawl..."
    sudo git clone "$YGGCRAWL_REPO" "$YGGCRAWL_DIR"
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$YGGCRAWL_DIR"
    ok "yggcrawl cloned to $YGGCRAWL_DIR."
fi

# ─────────────────────────────────────────────
# 5. NODE HOME
# ─────────────────────────────────────────────

info "Initialising yggcrawl node home at $YGGCRAWL_NODE_HOME..."
sudo mkdir -p "$YGGCRAWL_NODE_HOME/data/archive"
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$YGGCRAWL_NODE_HOME"

# seeds.txt — empty (we're a producer node, not a web crawler)
[ -f "$YGGCRAWL_NODE_HOME/seeds.txt" ] || touch "$YGGCRAWL_NODE_HOME/seeds.txt"

# peers.txt — empty initially; add other nodes later
[ -f "$YGGCRAWL_NODE_HOME/peers.txt" ] || touch "$YGGCRAWL_NODE_HOME/peers.txt"

# config.json
[ -f "$YGGCRAWL_NODE_HOME/config.json" ] || cat > "$YGGCRAWL_NODE_HOME/config.json" <<JSON
{
  "node_id": "oracle-pypi-place",
  "max_urls_per_run": 0,
  "outbox_dir": "$OUTBOX_DIR"
}
JSON

ok "Node home ready."

# ─────────────────────────────────────────────
# 6. SYSTEMD — yggcrawl-serve
# ─────────────────────────────────────────────

info "Installing yggcrawl-serve.service..."
sudo tee /etc/systemd/system/yggcrawl-serve.service > /dev/null <<SERVICE
[Unit]
Description=YggCrawl HTTP server — serves snapshots over Yggdrasil IPv6
After=network-online.target yggdrasil.service
Wants=network-online.target yggdrasil.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$YGGCRAWL_DIR
Environment=PYTHONUNBUFFERED=1
Environment=YGGCRAWL_HOME=$YGGCRAWL_NODE_HOME
ExecStart=/usr/bin/python3 $YGGCRAWL_DIR/cli.py --home $YGGCRAWL_NODE_HOME serve --port 8080
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable yggcrawl-serve
sudo systemctl restart yggcrawl-serve
ok "yggcrawl-serve running."

# ─────────────────────────────────────────────
# 7. WIRE YGGCRAWL RUN INTO WATCHDOG
# ─────────────────────────────────────────────

info "Updating pypi-watchdog.service to trigger yggcrawl run..."
sudo tee /etc/systemd/system/pypi-watchdog.service > /dev/null <<SERVICE
[Unit]
Description=The PyPI Place watchdog (RSS poller + test runner)
Wants=network-online.target docker.service
After=network-online.target docker.service

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=/opt/vps-pypi-place
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/opt/vps-pypi-place
ExecStart=/opt/vps-pypi-place/venv/bin/python -m watchdog.run
ExecStartPost=/opt/vps-pypi-place/venv/bin/python /opt/vps-pypi-place/site/build.py
ExecStartPost=/usr/bin/python3 $YGGCRAWL_DIR/cli.py --home $YGGCRAWL_NODE_HOME run
SERVICE

sudo systemctl daemon-reload
ok "pypi-watchdog.service updated."

# ─────────────────────────────────────────────
# 8. REPORT
# ─────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Yggdrasil node address"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

YGG_ADDR=$(sudo yggdrasilctl getSelf 2>/dev/null \
    | grep -oP '2[0-9a-f]{2}:[0-9a-f:]+' | head -1 || true)

if [ -n "$YGG_ADDR" ]; then
    ok "Node address : $YGG_ADDR"
    echo ""
    echo "  Snapshots served at:"
    echo "  http://[$YGG_ADDR]:8080/current.json"
    echo "  http://[$YGG_ADDR]:8080/current.json.sha256"
    echo ""
    echo "  To verify from any other Yggdrasil node:"
    echo "  curl http://[$YGG_ADDR]:8080/current.json.sha256"
else
    echo "  [WARN] Address not visible yet — may still be peering."
    echo "  Run:  sudo yggdrasilctl getSelf"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
