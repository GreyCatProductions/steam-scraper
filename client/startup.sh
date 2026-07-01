#!/bin/bash
set -e

SERVER_IP=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/project/attributes/SERVER_IP)

cd /opt
git clone http://github.com/GreyCatProductions/steam-scraper.git
cd steam-scraper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m client.src.main --server "http://${SERVER_IP}:8000" --batch 50