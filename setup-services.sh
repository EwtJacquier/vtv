#!/bin/bash
sudo tee /etc/systemd/system/vtv-server.service > /dev/null << 'EOF'
[Unit]
Description=VTV Python Server
After=network.target

[Service]
Type=simple
User=ewerton
WorkingDirectory=/home/ewerton/projects/vtv
ExecStart=/usr/bin/python3 /home/ewerton/projects/vtv/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/vtv-cloudflared.service > /dev/null << 'EOF'
[Unit]
Description=VTV Cloudflared Tunnel
After=network-online.target vtv-server.service
Wants=network-online.target
Requires=vtv-server.service

[Service]
Type=simple
User=ewerton
ExecStart=/usr/local/bin/cloudflared tunnel run vtv
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vtv-server vtv-cloudflared
sudo systemctl start vtv-server vtv-cloudflared
sudo systemctl status vtv-server vtv-cloudflared
