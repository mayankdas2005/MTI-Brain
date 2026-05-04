#!/bin/bash
set -e

LOG=/var/log/mti-brain-deploy.log
echo "[$(date)] ValidateService" | tee -a "$LOG"

# Wait up to 2 minutes for backend health
for i in {1..24}; do
  if curl -fs http://localhost:8000/health > /dev/null; then
    echo "Backend healthy" | tee -a "$LOG"
    backend_ok=1
    break
  fi
  sleep 5
done

# Wait up to 2 minutes for frontend
for i in {1..24}; do
  if curl -fs http://localhost:3000 > /dev/null; then
    echo "Frontend healthy" | tee -a "$LOG"
    frontend_ok=1
    break
  fi
  sleep 5
done

if [ "$backend_ok" != "1" ] || [ "$frontend_ok" != "1" ]; then
  echo "Health check FAILED" | tee -a "$LOG"
  docker compose -f /opt/mti-brain/docker-compose.yml logs --tail=200 | tee -a "$LOG"
  exit 1
fi

echo "Deployment validated" | tee -a "$LOG"
exit 0
