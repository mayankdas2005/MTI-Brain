#!/bin/bash
set -e

APP_DIR=/opt/mti-brain
LOG=/var/log/mti-brain-deploy.log

echo "[$(date)] ApplicationStop" | tee -a "$LOG"

if [ -d "$APP_DIR" ] && [ -f "$APP_DIR/docker-compose.yml" ]; then
  cd "$APP_DIR"
  docker compose down --remove-orphans 2>&1 | tee -a "$LOG" || true
fi

exit 0
