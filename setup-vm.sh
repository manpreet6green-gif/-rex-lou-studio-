#!/bin/bash
# Rex & Lou Studio — Oracle Cloud VM Setup Script
# Run this on your Oracle Cloud Ubuntu VM after SSH-ing in:
#   chmod +x setup-vm.sh && ./setup-vm.sh

set -e

echo "=== Rex & Lou Studio — Cloud Setup ==="
echo ""

# 1. Update system
echo "[1/5] Updating system packages..."
sudo apt update -y && sudo apt upgrade -y

# 2. Install Python3 + pip
echo "[2/5] Installing Python3 and pip..."
sudo apt install -y python3 python3-pip python3-venv

# 3. Install python dependencies
echo "[3/5] Installing Python dependencies..."
pip3 install edge_tts ddgs

# 4. Open firewall port 8080
echo "[4/5] Configuring firewall..."
sudo ufw allow 8080/tcp 2>/dev/null || echo "  (ufw not enabled — skipping)"

# 5. Create systemd service for auto-start
echo "[5/5] Creating systemd service..."
sudo tee /etc/systemd/system/rexlou-studio.service > /dev/null << 'SERVICEEOF'
[Unit]
Description=Rex & Lou Studio Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/rex-lou-studio
ExecStart=/usr/bin/python3 /home/ubuntu/rex-lou-studio/tts_proxy.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICEEOF

sudo systemctl daemon-reload
sudo systemctl enable rexlou-studio

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Now upload your files to /home/ubuntu/rex-lou-studio/ and run:"
echo "  sudo systemctl start rexlou-studio"
echo "  sudo systemctl status rexlou-studio"
