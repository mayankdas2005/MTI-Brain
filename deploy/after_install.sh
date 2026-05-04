#!/bin/bash
set -e

APP_DIR=/opt/mti-brain
LOG=/var/log/mti-brain-deploy.log

echo "[$(date)] AfterInstall" | tee -a "$LOG"

# Restore .env preserved by before_install
if [ -f /tmp/mti-brain.env.backup ]; then
  cp /tmp/mti-brain.env.backup "$APP_DIR/.env"
  rm -f /tmp/mti-brain.env.backup
fi

# Fall back: pull .env from SSM Parameter Store / Secrets Manager on first deploy.
# Replace REGION and parameter name with your own.
if [ ! -f "$APP_DIR/.env" ]; then
  aws ssm get-parameter \
    --name "/mti-brain/prod/env" \
    --with-decryption \
    --region us-east-1 \
    --query Parameter.Value \
    --output text > "$APP_DIR/.env"
fi

cd "$APP_DIR"
chmod +x deploy/*.sh

# Build images on the EC2 host (no ECR involved)
docker compose build 2>&1 | tee -a "$LOG"

exit 0
