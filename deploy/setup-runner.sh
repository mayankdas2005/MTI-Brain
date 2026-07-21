#!/bin/bash
set -euo pipefail

# GitHub Actions self-hosted runner setup for mti-brain dev VM.
# Run once to install the runner as a systemd service.
#
# Usage: ./setup-runner.sh <GITHUB_REPO_URL> <RUNNER_TOKEN>
# Example: ./setup-runner.sh https://github.com/your-org/mti-brain AXXXXXXXXXXXX

REPO_URL="${1:?Usage: $0 <GITHUB_REPO_URL> <RUNNER_TOKEN>}"
TOKEN="${2:?Usage: $0 <GITHUB_REPO_URL> <RUNNER_TOKEN>}"

RUNNER_DIR="/opt/actions-runner"
RUNNER_VERSION="2.335.1"
RUNNER_ARCH="linux-x64"

echo "==> Installing GitHub Actions runner to ${RUNNER_DIR}"

sudo mkdir -p "$RUNNER_DIR"
sudo chown "$(whoami)" "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ ! -f "./config.sh" ]; then
  echo "==> Downloading runner v${RUNNER_VERSION}..."
  curl -sL "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" | tar xz
fi

echo "==> Configuring runner..."
./config.sh \
  --url "$REPO_URL" \
  --token "$TOKEN" \
  --name "mti-brain-dev" \
  --labels "self-hosted,linux,mti-brain-dev" \
  --work "_work" \
  --unattended \
  --replace

echo "==> Installing as systemd service..."
sudo ./svc.sh install
sudo ./svc.sh start

echo "==> Runner installed and running"
echo "    Check status: sudo ./svc.sh status"
echo "    View logs:    journalctl -u actions.runner.* -f"
