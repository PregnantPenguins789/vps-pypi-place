#!/bin/bash
# setup_node.sh
# Run on a fresh Oracle Ubuntu 22.04 instance after provisioning.
# Usage: bash setup_node.sh
set -euo pipefail

info() { echo "[INFO]  $*"; }
ok()   { echo "[OK]    $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ─────────────────────────────────────────────
# SWAP (critical on 1GB instances)
# ─────────────────────────────────────────────

info "Setting up 2GB swapfile..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    ok "Swap enabled."
else
    ok "Swap already exists, skipping."
fi

# ─────────────────────────────────────────────
# SYSTEM PACKAGES
# ─────────────────────────────────────────────

info "Updating package lists..."
sudo apt-get update -qq

info "Installing system dependencies..."
sudo apt-get install -y -qq \
    git \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    docker.io \
    docker-compose \
    sqlite3 \
    nginx \
    curl \
    jq

ok "System packages installed."

# ─────────────────────────────────────────────
# DOCKER
# ─────────────────────────────────────────────

info "Enabling Docker..."
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ubuntu
ok "Docker running. (Re-login required for group to take effect.)"

# ─────────────────────────────────────────────
# PROJECT DIRECTORY
# ─────────────────────────────────────────────

info "Setting up project directory..."
sudo mkdir -p /opt/vps-pypi-place/{data,reports,episodes,logs,site,data/sessions,data/outbox}
sudo chown -R ubuntu:ubuntu /opt/vps-pypi-place
ok "Directories created."

# ─────────────────────────────────────────────
# CLONE REPO
# ─────────────────────────────────────────────

if [ ! -d /opt/vps-pypi-place/repo/.git ]; then
    info "Cloning repository..."
    git clone https://github.com/PregnantPenguins789/vps-pypi-place.git /opt/vps-pypi-place/repo
    ok "Repo cloned to /opt/vps-pypi-place/repo"
else
    info "Repo already present, pulling latest..."
    git -C /opt/vps-pypi-place/repo pull
    ok "Repo updated."
fi

# ─────────────────────────────────────────────
# PYTHON ENVIRONMENT
# ─────────────────────────────────────────────

info "Creating Python virtualenv..."
python3 -m venv /opt/vps-pypi-place/venv
/opt/vps-pypi-place/venv/bin/pip install -q --upgrade pip
/opt/vps-pypi-place/venv/bin/pip install -q -r /opt/vps-pypi-place/repo/requirements.txt
ok "Python environment ready."

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

info "Initialising database..."
sqlite3 /opt/vps-pypi-place/data/pypi_place.db \
    < /opt/vps-pypi-place/repo/db/schema.sql
ok "Database initialised at /opt/vps-pypi-place/data/pypi_place.db"

# ─────────────────────────────────────────────
# IPTABLES — open HTTP
# ─────────────────────────────────────────────

info "Opening firewall for HTTP..."
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
# Persist across reboots
sudo apt-get install -y -qq iptables-persistent
sudo netfilter-persistent save
ok "Firewall rules saved."

# ─────────────────────────────────────────────
# NGINX — serve the dashboard
# ─────────────────────────────────────────────

info "Configuring nginx..."
sudo tee /etc/nginx/sites-available/pypi-place > /dev/null <<'NGINX'
server {
    listen 80 default_server;
    root /opt/vps-pypi-place/site;
    index index.html;
    location / {
        try_files $uri $uri/ =404;
        add_header Cache-Control "no-cache";
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/pypi-place /etc/nginx/sites-enabled/pypi-place
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
ok "Nginx configured."

# ─────────────────────────────────────────────
# SYSTEMD UNITS
# ─────────────────────────────────────────────

info "Installing systemd units..."
sudo cp /opt/vps-pypi-place/repo/deploy/systemd/pypi-watchdog.service /etc/systemd/system/
sudo cp /opt/vps-pypi-place/repo/deploy/systemd/pypi-watchdog.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pypi-watchdog.timer
ok "Systemd units installed."

# ─────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────

echo ""
ok "============================================"
ok "  Node setup complete."
ok "  Repo:    /opt/vps-pypi-place/repo"
ok "  Data:    /opt/vps-pypi-place/data"
ok "  DB:      /opt/vps-pypi-place/data/pypi_place.db"
ok "  Site:    /opt/vps-pypi-place/site  (served by nginx on :80)"
ok ""
ok "  Next: run the watchdog once to verify:"
ok "  cd /opt/vps-pypi-place/repo && \\"
ok "  /opt/vps-pypi-place/venv/bin/python -m watchdog.run --once"
ok "============================================"
echo ""
echo "NOTE: log out and back in for Docker group to take effect."
