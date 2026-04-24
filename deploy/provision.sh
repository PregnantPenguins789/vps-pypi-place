#!/bin/bash
# provision.sh
# Creates everything needed on Oracle Cloud from your Linux Mint laptop.
# Run this ONCE. It creates: VCN, subnet, security rules, ARM instance.
# Requires OCI CLI installed and configured first (see below).
#
# USAGE:
#   bash deploy/provision.sh

set -euo pipefail

# ─────────────────────────────────────────────
# EDIT THESE
# ─────────────────────────────────────────────

INSTANCE_NAME="pypi-place-node-test"
SSH_KEY_FILE="$HOME/.ssh/id_ed25519.pub"
# Optional: OCID of the compartment to create resources in.
# Leave blank to use the root (tenancy) compartment.
COMPARTMENT_ID="${COMPARTMENT_ID:-}"

# Leave these unless you know what you're doing
SHAPE="VM.Standard.A1.Flex"
OCPUS=2
RAM_GB=12
IMAGE_OS="Canonical Ubuntu"
IMAGE_OS_VERSION="22.04"
REGION="us-ashburn-1"
VCN_CIDR="10.0.0.0/16"
SUBNET_CIDR="10.0.1.0/24"
VCN_NAME="pypi-place-vcn"
SUBNET_NAME="pypi-place-subnet"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

info() { echo "[INFO]  $*"; }
ok()   { echo "[OK]    $*"; }
warn() { echo "[WARN]  $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

require() { command -v "$1" &>/dev/null || die "$1 not found. Run: bash deploy/install_oci_cli.sh"; }

# ─────────────────────────────────────────────
# PREFLIGHT
# ─────────────────────────────────────────────

require oci
require jq

[ -f "$SSH_KEY_FILE" ] || die "SSH public key not found at $SSH_KEY_FILE. Run: ssh-keygen -t ed25519 -C oracle-pypi-place"

info "Checking OCI identity..."
oci iam user get --user-id "$(oci iam user list --query 'data[0].id' --raw-output 2>/dev/null)" \
    --query 'data.name' --raw-output 2>/dev/null || \
    die "OCI CLI not configured. Run: oci setup config"

TENANCY_ID=$(oci iam tenancy get --tenancy-id \
    "$(oci iam user list --query 'data[0].id' --raw-output | \
    oci iam user get --user-id /dev/stdin --query 'data."compartment-id"' --raw-output 2>/dev/null)" \
    --query 'data.id' --raw-output 2>/dev/null || \
    oci iam compartment list --compartment-id-in-subtree true \
    --access-level ACCESSIBLE --query 'data[0]."compartment-id"' --raw-output)

COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"

ok "Using compartment: $COMPARTMENT_ID"

# ─────────────────────────────────────────────
# STEP 1 — VCN
# ─────────────────────────────────────────────

info "Creating VCN..."
VCN_ID=$(oci network vcn create \
    --compartment-id "$COMPARTMENT_ID" \
    --display-name "$VCN_NAME" \
    --cidr-block "$VCN_CIDR" \
    --wait-for-state AVAILABLE \
    --query 'data.id' --raw-output)
ok "VCN: $VCN_ID"

# ─────────────────────────────────────────────
# STEP 2 — Internet Gateway
# ─────────────────────────────────────────────

info "Creating internet gateway..."
IGW_ID=$(oci network internet-gateway create \
    --compartment-id "$COMPARTMENT_ID" \
    --vcn-id "$VCN_ID" \
    --display-name "pypi-place-igw" \
    --is-enabled true \
    --wait-for-state AVAILABLE \
    --query 'data.id' --raw-output)
ok "Internet gateway: $IGW_ID"

# ─────────────────────────────────────────────
# STEP 3 — Route table
# ─────────────────────────────────────────────

info "Updating default route table..."
RT_ID=$(oci network route-table list \
    --compartment-id "$COMPARTMENT_ID" \
    --vcn-id "$VCN_ID" \
    --query 'data[0].id' --raw-output)

oci network route-table update \
    --rt-id "$RT_ID" \
    --route-rules "[{\"cidrBlock\":\"0.0.0.0/0\",\"networkEntityId\":\"$IGW_ID\"}]" \
    --force --wait-for-state AVAILABLE > /dev/null
ok "Route table updated."

# ─────────────────────────────────────────────
# STEP 4 — Security list (open SSH + HTTP/S)
# ─────────────────────────────────────────────

info "Configuring security rules..."
SL_ID=$(oci network security-list list \
    --compartment-id "$COMPARTMENT_ID" \
    --vcn-id "$VCN_ID" \
    --query 'data[0].id' --raw-output)

oci network security-list update \
    --security-list-id "$SL_ID" \
    --ingress-security-rules '[
        {"protocol":"6","source":"0.0.0.0/0","tcpOptions":{"destinationPortRange":{"min":22,"max":22}},"isStateless":false,"description":"SSH"},
        {"protocol":"6","source":"0.0.0.0/0","tcpOptions":{"destinationPortRange":{"min":80,"max":80}},"isStateless":false,"description":"HTTP"},
        {"protocol":"6","source":"0.0.0.0/0","tcpOptions":{"destinationPortRange":{"min":443,"max":443}},"isStateless":false,"description":"HTTPS"}
    ]' \
    --force --wait-for-state AVAILABLE > /dev/null
ok "Security rules set: SSH, HTTP, HTTPS."

# ─────────────────────────────────────────────
# STEP 5 — Subnet
# ─────────────────────────────────────────────

info "Creating subnet..."
SUBNET_ID=$(oci network subnet create \
    --compartment-id "$COMPARTMENT_ID" \
    --vcn-id "$VCN_ID" \
    --display-name "$SUBNET_NAME" \
    --cidr-block "$SUBNET_CIDR" \
    --route-table-id "$RT_ID" \
    --security-list-ids "[\"$SL_ID\"]" \
    --wait-for-state AVAILABLE \
    --query 'data.id' --raw-output)
ok "Subnet: $SUBNET_ID"

# ─────────────────────────────────────────────
# STEP 6 — Find ARM Ubuntu image
# ─────────────────────────────────────────────

info "Finding Ubuntu 22.04 ARM64 image..."
IMAGE_ID=$(oci compute image list \
    --compartment-id "$COMPARTMENT_ID" \
    --operating-system "$IMAGE_OS" \
    --operating-system-version "$IMAGE_OS_VERSION" \
    --shape "$SHAPE" \
    --sort-by TIMECREATED \
    --sort-order DESC \
    --query 'data[0].id' --raw-output)
ok "Image: $IMAGE_ID"

# ─────────────────────────────────────────────
# STEP 7 — Launch instance
# ─────────────────────────────────────────────

info "Launching ARM instance (this takes 2-3 minutes)..."

mapfile -t AD_LIST < <(oci iam availability-domain list \
    --compartment-id "$COMPARTMENT_ID" \
    --query 'data[].name' --raw-output | grep -oP 'Zmma:US-ASHBURN-AD-\d')

# Try AD-2 and AD-3 before AD-1 (AD-1 is most saturated)
SORTED_ADS=($(printf '%s\n' "${AD_LIST[@]}" | sort -t- -k4 -r))

INSTANCE_ID=""
for AD in "${SORTED_ADS[@]}"; do
    info "Trying availability domain: $AD"
    INSTANCE_ID=$(oci compute instance launch \
        --compartment-id "$COMPARTMENT_ID" \
        --availability-domain "$AD" \
        --display-name "$INSTANCE_NAME" \
        --image-id "$IMAGE_ID" \
        --shape "$SHAPE" \
        --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$RAM_GB}" \
        --subnet-id "$SUBNET_ID" \
        --assign-public-ip true \
        --ssh-authorized-keys-file "$SSH_KEY_FILE" \
        --wait-for-state RUNNING \
        --query 'data.id' --raw-output 2>&1) && break
    warn "AD $AD failed: $INSTANCE_ID"
    INSTANCE_ID=""
done

[ -n "$INSTANCE_ID" ] || die "All availability domains exhausted. Try again later or reduce OCPUS/RAM_GB further."
ok "Instance running: $INSTANCE_ID"

# ─────────────────────────────────────────────
# STEP 8 — Get public IP
# ─────────────────────────────────────────────

PUBLIC_IP=$(oci compute instance list-vnics \
    --instance-id "$INSTANCE_ID" \
    --query 'data[0]."public-ip"' --raw-output)

# ─────────────────────────────────────────────
# DONE
# ─────────────────────────────────────────────

echo ""
ok "============================================"
ok "  Instance ready."
ok "  IP:   $PUBLIC_IP"
ok "  SSH:  ssh ubuntu@$PUBLIC_IP"
ok "============================================"
echo ""
echo "  Next step:"
echo "  ssh ubuntu@$PUBLIC_IP"
echo ""

# Save IP for later scripts
echo "$PUBLIC_IP" > deploy/.instance_ip
ok "IP saved to deploy/.instance_ip"
