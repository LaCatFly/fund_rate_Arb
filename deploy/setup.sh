#!/usr/bin/env bash
# DEPRECATED — Use deploy/install.sh instead
# This script used pip; project now uses uv with user-level systemd services.
#
# One-time VPS setup:
#   1. Install python3 + git + uv
#   2. Clone repo to ~/fund_rate_arb
#   3. Run: bash deploy/install.sh
#   4. Fill .env with your secrets
echo "DEPRECATED: Use 'bash deploy/install.sh' instead."
exit 1
