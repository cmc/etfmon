#!/bin/bash

# Stop and remove existing container if running
if [ "$(docker ps -aq -f name=etf-monitor)" ]; then
    echo "Stopping and removing existing etf-monitor container..."
    docker stop etf-monitor
    docker rm etf-monitor
fi

# Build new image
echo "Building new etf-monitor image..."
docker build -t etf-monitor .

# Run new container
echo "Starting new etf-monitor container..."
sudo docker run -d \
  --restart unless-stopped \
  --name etf-monitor \
  --log-opt max-size=10m \
  --log-opt max-file=5 \
  etf-monitor


