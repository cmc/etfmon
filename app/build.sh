#!/bin/bash

set -e  # Exit immediately on errors

APP_DIR=$(pwd)  # Current app directory

# --- Stop and Remove Old Container if Exists ---
if [ "$(docker ps -aq -f name=etf-monitor)" ]; then
    echo "🛑 Stopping and removing existing etf-monitor container..."
    sudo docker stop etf-monitor || true
    sudo docker rm etf-monitor || true
fi

# --- Build New Docker Image ---
echo "🔨 Building new etf-monitor image..."
sudo docker build -t etf-monitor .

# --- Run New Container ---
echo "🚀 Starting new etf-monitor container with bind mount and log rotation..."
sudo docker run -d \
  --restart unless-stopped \
  --name etf-monitor \
  --log-opt max-size=10m \
  --log-opt max-file=5 \
  -v "${APP_DIR}:/app" \
  etf-monitor

# --- Tail Logs ---
echo "📋 Following container logs..."
#sudo docker logs -f etf-monitor
tail -100f output.log

