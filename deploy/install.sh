#!/usr/bin/env bash
set -euo pipefail

# Install user-level systemd services for fund_rate_arb
# No sudo required — uses systemctl --user
HOME_DIR="$HOME"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$UNIT_DIR"
mkdir -p "$HOME_DIR/fund_rate_arb/logs"

echo "Installing fund_rate_arb systemd user units (home=$HOME_DIR)"

for f in "$DEPLOY_DIR"/*.service; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    echo "  Installing $name"
    sed "s|<home>|$HOME_DIR|g" "$f" > "$UNIT_DIR/$name"
done

systemctl --user daemon-reload

echo "  Enabling fund-rate-scan.service"
systemctl --user enable fund-rate-scan.service
systemctl --user start fund-rate-scan.service

# Enable lingering so user services survive logout
echo "  Enabling lingering (services persist after logout)"
loginctl enable-linger "$(whoami)" 2>/dev/null || echo "  Warning: loginctl enable-linger failed — services may stop on logout. Ask admin to run: sudo loginctl enable-linger $(whoami)"

echo ""
echo "All services installed. Verify with:"
echo "  systemctl --user status fund-rate-scan"
echo "  journalctl --user -u fund-rate-scan -f"
