#!/bin/bash
set -e

# Install script for watchd

INSTALL_DIR="/usr/local/bin"
SERVICE_DIR="/etc/systemd/system"
CONFIG_DIR="/etc"
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

# Install config file if it doesn't exist
if [ ! -f "$CONFIG_DIR/watchd.conf" ]; then
    cp "$SCRIPT_DIR/watchd.conf.example" "$CONFIG_DIR/watchd.conf"
    echo "Created /etc/watchd.conf"
fi

# Install systemd service
cp "$SCRIPT_DIR/watchd.service" "$SERVICE_DIR/"
systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Edit /etc/watchd.conf and set your ntfy.sh topic:"
echo "   sudo nano /etc/watchd.conf"
echo ""
echo "2. Enable and start the service:"
echo "   sudo systemctl enable watchd"
echo "   sudo systemctl start watchd"
echo ""
echo "3. Subscribe to notifications on your phone:"
echo "   Open https://ntfy.sh/YOUR-TOPIC in browser"
echo ""
echo "4. Test it:"
echo "   watch echo 'hello world'"
echo ""
