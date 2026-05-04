#!/bin/bash
set -e

APP_DIR=/opt/mti-brain
LOG=/var/log/mti-brain-deploy.log

echo "[$(date)] BeforeInstall" | tee -a "$LOG"

# Preserve .env (not in git) across deployments
if [ -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env" /tmp/mti-brain.env.backup
fi

# Clean target so CodeDeploy can lay down fresh files
sudo rm -rf "$APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown ubuntu:ubuntu "$APP_DIR"

exit 0
