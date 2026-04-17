#!/bin/bash
# install_oci_cli.sh
# Install OCI CLI and configure it for your Oracle account.
# Run this on your Linux Mint laptop BEFORE provision.sh.

set -euo pipefail

info() { echo "[INFO]  $*"; }
ok()   { echo "[OK]    $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ─────────────────────────────────────────────
# STEP 1 — Install OCI CLI
# ─────────────────────────────────────────────

info "Installing OCI CLI..."
bash -c "$(curl -fsSL https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)" \
    -- --accept-all-defaults
ok "OCI CLI installed."

# Reload PATH so oci command is available immediately
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"
source "$HOME/.bashrc" 2>/dev/null || true

# ─────────────────────────────────────────────
# STEP 2 — Generate SSH key if needed
# ─────────────────────────────────────────────

if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
    info "Generating SSH key pair..."
    ssh-keygen -t ed25519 -C "oracle-pypi-place" -N "" -f "$HOME/.ssh/id_ed25519"
    ok "SSH key created."
else
    ok "SSH key already exists."
fi

# ─────────────────────────────────────────────
# STEP 3 — Configure OCI CLI
# ─────────────────────────────────────────────

echo ""
echo "  ─────────────────────────────────────────"
echo "  Now configuring OCI CLI."
echo "  You will need:"
echo ""
echo "    1. Your Oracle user OCID"
echo "       Console → top right avatar → My profile → copy OCID"
echo ""
echo "    2. Your Tenancy OCID"
echo "       Console → top right avatar → Tenancy → copy OCID"
echo ""
echo "    3. Region: us-ashburn-1"
echo "  ─────────────────────────────────────────"
echo ""
read -rp "  Press Enter when you have those two OCIDs ready..."

oci setup config

# ─────────────────────────────────────────────
# STEP 4 — Upload API key to Oracle
# ─────────────────────────────────────────────

API_KEY=$(cat "$HOME/.oci/oci_api_key_public.pem" 2>/dev/null || \
          ls "$HOME/.oci/"*.pem 2>/dev/null | grep public | head -1 | xargs cat)

echo ""
echo "  ─────────────────────────────────────────"
echo "  IMPORTANT: Upload this API key to Oracle."
echo ""
echo "  1. Go to Oracle Console"
echo "  2. Top right → My profile"
echo "  3. Scroll down → API keys → Add API key"
echo "  4. Select 'Paste public key'"
echo "  5. Paste this:"
echo ""
echo "$API_KEY"
echo ""
echo "  6. Click Add"
echo "  ─────────────────────────────────────────"
echo ""
read -rp "  Press Enter once you've added the key in the console..."

# ─────────────────────────────────────────────
# STEP 5 — Verify
# ─────────────────────────────────────────────

info "Verifying connection..."
if oci iam region list --output table 2>/dev/null | grep -q "ashburn"; then
    echo ""
    ok "OCI CLI is working."
    echo ""
    echo "  Run next:"
    echo "  bash deploy/provision.sh"
    echo ""
else
    die "OCI CLI verification failed. Check your OCIDs and that the API key was uploaded."
fi
