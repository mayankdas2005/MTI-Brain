#!/bin/bash
set -e

APP_DIR=/opt/mti-brain
LOG=/var/log/mti-brain-deploy.log

echo "[$(date)] ApplicationStart" | tee -a "$LOG"

cd "$APP_DIR"
docker compose up -d 2>&1 | tee -a "$LOG"

# Prune dangling images from previous builds to reclaim disk
docker image prune -f 2>&1 | tee -a "$LOG" || true

exit 0
