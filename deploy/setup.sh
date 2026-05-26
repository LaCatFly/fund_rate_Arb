#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/fund_rate_arb"

echo "Installing to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp -r ./* "$INSTALL_DIR/"
sudo chown -R $USER:$USER "$INSTALL_DIR"

cd "$INSTALL_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
echo "Edit .env with your API keys and TG credentials"
echo "Then: sudo cp deploy/systemd.service /etc/systemd/system/fund-rate-arb.service"
echo "      sudo systemctl enable --now fund-rate-arb"
