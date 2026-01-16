#!/bin/bash
set -e

# Install script for watchd

INSTALL_DIR="/usr/local/bin"
SERVICE_DIR="/etc/systemd/system"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing watchd..."

# Check for root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

# Copy binaries
cp "$SCRIPT_DIR/watchd.py" "$INSTALL_DIR/watchd"
cp "$SCRIPT_DIR/watch" "$INSTALL_DIR/watch"
chmod +x "$INSTALL_DIR/watchd"
chmod +x "$INSTALL_DIR/watch"

# Install systemd service
cp "$SCRIPT_DIR/watchd.service" "$SERVICE_DIR/"
systemctl daemon-reload

echo "Installation complete."
echo ""
echo "Configure your ntfy.sh topic:"
echo "  sudo systemctl edit watchd"
echo "  Add: [Service]"
echo "       Environment=WATCHD_NTFY_URL=https://ntfy.sh/YOUR-TOPIC"
echo ""
echo "Enable and start:"
echo "  sudo systemctl enable watchd"
echo "  sudo systemctl start watchd"
echo ""
echo "Usage:"
echo "  watch python train.py"
echo "  watch --timeout 300 ssh user@host"
