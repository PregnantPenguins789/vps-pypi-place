#!/bin/bash
# oracle_setup.sh
# Provision a fresh Oracle Cloud Always Free ARM instance for vps-pypi-place.
# Run as root or with sudo on a clean Ubuntu 22.04 ARM64 instance.
# Idempotent — safe to run more than once.

set -euo pipefail

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

APP_USER="pypi"
APP_DIR="/opt/vps-pypi-place"
DATA_DIR="$APP_DIR/data"
REPORTS_DIR="$APP_DIR/reports"
EPISODES_DIR="$APP_DIR/episodes"
LOG_DIR="$APP_DIR/logs"
SITE_DIR="$APP_DIR/site"
PYTHON_VERSION="3.11"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

info()  { echo "[INFO]  $*"; }
ok()    { echo "[OK]    $*"; }
warn()  { echo "[WARN]  $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "Run this script as root or with sudo."
}

# ─────────────────────────────────────────────
# STEP 1 — system update
# ─────────────────────────────────────────────

step_system_update() {
    info "Updating system packages..."
    apt-get update -qq
    apt-get upgrade -y -qq
    ok "System updated."
}

# ─────────────────────────────────────────────
# STEP 2 — install dependencies
# ─────────────────────────────────────────────

step_install_deps() {
    info "Installing dependencies..."
    apt-get install -y -qq \
        git \
        curl \
        wget \
        python3 \
        python3-pip \
        python3-venv \
        sqlite3 \
        docker.io \
        ffmpeg \
        sox \
        povray \
        imagemagick \
        aview \
        timidity \
        asciinema \
        systemd \
        ufw \
        fail2ban

    # Enable and start Docker
    systemctl enable docker
    systemctl start docker
    ok "Dependencies installed."
}

# ─────────────────────────────────────────────
# STEP 3 — create app user
# ─────────────────────────────────────────────

step_create_user() {
    if id "$APP_USER" &>/dev/null; then
        ok "User $APP_USER already exists."
    else
        info "Creating user $APP_USER..."
        useradd -r -m -s /bin/bash "$APP_USER"
        usermod -aG docker "$APP_USER"
        ok "User $APP_USER created and added to docker group."
    fi
}

# ─────────────────────────────────────────────
# STEP 4 — create directory structure
# ─────────────────────────────────────────────

step_create_dirs() {
    info "Creating directory structure..."
    mkdir -p \
        "$APP_DIR" \
        "$DATA_DIR" \
        "$REPORTS_DIR" \
        "$EPISODES_DIR" \
        "$LOG_DIR" \
        "$SITE_DIR"

    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    ok "Directories created under $APP_DIR"
}

# ─────────────────────────────────────────────
# STEP 5 — clone or update repo
# ─────────────────────────────────────────────

step_clone_repo() {
    info "Setting up repository..."

    if [ -d "$APP_DIR/.git" ]; then
        info "Repo already exists, pulling latest..."
        sudo -u "$APP_USER" git -C "$APP_DIR" pull
        ok "Repo updated."
    else
        echo ""
        echo "  Choose your git setup:"
        echo "  1) Clone from remote URL"
        echo "  2) Initialize fresh local repo"
        echo "  3) Install self-hosted Gitea on this server"
        echo ""
        read -rp "  Enter choice [1/2/3]: " GIT_CHOICE

        case "$GIT_CHOICE" in
            1)
                read -rp "  Enter repo URL: " REPO_URL
                sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
                ok "Repo cloned from $REPO_URL"
                ;;
            2)
                sudo -u "$APP_USER" git -C "$APP_DIR" init
                sudo -u "$APP_USER" git -C "$APP_DIR" config user.name "pypi-place"
                read -rp "  Enter your email for git commits: " GIT_EMAIL
                sudo -u "$APP_USER" git -C "$APP_DIR" config user.email "$GIT_EMAIL"
                ok "Local git repo initialized."
                ;;
            3)
                step_install_gitea
                ;;
            *)
                warn "Invalid choice. Skipping git setup."
                ;;
        esac
    fi
}

# ─────────────────────────────────────────────
# STEP 5a — optional: self-hosted Gitea
# ─────────────────────────────────────────────

step_install_gitea() {
    info "Installing self-hosted Gitea..."
    GITEA_VERSION="1.21.0"
    GITEA_BIN="/usr/local/bin/gitea"

    if [ ! -f "$GITEA_BIN" ]; then
        wget -q "https://dl.gitea.com/gitea/${GITEA_VERSION}/gitea-${GITEA_VERSION}-linux-arm64" \
            -O "$GITEA_BIN"
        chmod +x "$GITEA_BIN"
    fi

    useradd -r -m -s /bin/bash git 2>/dev/null || true
    mkdir -p /var/lib/gitea /etc/gitea
    chown -R git:git /var/lib/gitea /etc/gitea

    cat > /etc/systemd/system/gitea.service <<'EOF'
[Unit]
Description=Gitea Git Service
After=network.target

[Service]
User=git
WorkingDirectory=/var/lib/gitea
ExecStart=/usr/local/bin/gitea web --config /etc/gitea/app.ini
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable gitea
    systemctl start gitea
    ok "Gitea installed and running on port 3000."
    info "Complete Gitea setup at http://$(hostname -I | awk '{print $1}'):3000"
}

# ─────────────────────────────────────────────
# STEP 6 — initialise database
# ─────────────────────────────────────────────

step_init_db() {
    info "Initializing database..."
    DB_PATH="$DATA_DIR/pypi_place.db"

    if [ -f "$DB_PATH" ]; then
        ok "Database already exists at $DB_PATH"
    else
        sudo -u "$APP_USER" sqlite3 "$DB_PATH" < "$APP_DIR/db/schema.sql"
        ok "Database initialized at $DB_PATH"
    fi
}

# ─────────────────────────────────────────────
# STEP 7 — Python virtual environment
# ─────────────────────────────────────────────

step_python_env() {
    info "Setting up Python virtual environment..."
    VENV="$APP_DIR/venv"

    if [ ! -d "$VENV" ]; then
        sudo -u "$APP_USER" python3 -m venv "$VENV"
    fi

    sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet --upgrade pip
    if [ -f "$APP_DIR/requirements.txt" ]; then
        sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
        ok "Python dependencies installed."
    else
        ok "Virtual environment ready. No requirements.txt yet."
    fi
}

# ─────────────────────────────────────────────
# STEP 8 — firewall
# ─────────────────────────────────────────────

step_firewall() {
    info "Configuring firewall..."
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow ssh
    ufw allow 80/tcp    # site
    ufw allow 443/tcp   # site TLS
    ufw --force enable
    ok "Firewall configured."
}

# ─────────────────────────────────────────────
# STEP 9 — pull Docker images
# ─────────────────────────────────────────────

step_pull_images() {
    info "Pulling Docker base images (this may take a few minutes)..."
    for image in \
        "python:3.11-slim" \
        "python:3.12-slim" \
        "python:3.13-slim" \
        "python:3.11-alpine" \
        "python:3.12-alpine"
    do
        info "  Pulling $image..."
        docker pull "$image"
    done
    ok "Docker images ready."
}

# ─────────────────────────────────────────────
# STEP 10 — install systemd units
# ─────────────────────────────────────────────

step_systemd() {
    info "Installing systemd units..."
    UNIT_SRC="$APP_DIR/deploy/systemd"

    if [ -d "$UNIT_SRC" ] && [ "$(ls -A $UNIT_SRC)" ]; then
        cp "$UNIT_SRC"/*.service /etc/systemd/system/ 2>/dev/null || true
        cp "$UNIT_SRC"/*.timer   /etc/systemd/system/ 2>/dev/null || true
        systemctl daemon-reload
        ok "Systemd units installed."
    else
        warn "No systemd units found yet in $UNIT_SRC — skipping."
    fi
}

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

main() {
    require_root

    echo ""
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║       vps-pypi-place  setup           ║"
    echo "  ║   Oracle Always Free ARM — Ubuntu     ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo ""

    step_system_update
    step_install_deps
    step_create_user
    step_create_dirs
    step_clone_repo
    step_init_db
    step_python_env
    step_firewall
    step_pull_images
    step_systemd

    echo ""
    ok "Setup complete."
    echo ""
    echo "  Next steps:"
    echo "  1. Review config/settings.toml"
    echo "  2. Start the watchdog: systemctl start pypi-watchdog.timer"
    echo "  3. Watch the logs:     journalctl -fu pypi-watchdog"
    echo ""
}

main "$@"
