#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LOCK_DIR="$ROOT_DIR/.deploy.lock"
LOCK_ACQUIRED=0
PREVIOUS_IMAGE=""
UPDATE_STARTED=0
DEPLOY_SUCCEEDED=0

compose() {
    docker compose "$@"
}

cleanup() {
    status=$?
    trap - 0 1 2 15
    set +e

    if [ "$status" -ne 0 ] && [ "$UPDATE_STARTED" -eq 1 ] && [ -n "$PREVIOUS_IMAGE" ]; then
        echo "Deployment failed; restoring the previous application image." >&2
        docker image tag "$PREVIOUS_IMAGE" stalzone-artcalc:local
        compose up -d --no-build artcalc
    fi

    if [ "$LOCK_ACQUIRED" -eq 1 ]; then
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
    if [ "$DEPLOY_SUCCEEDED" -eq 1 ]; then
        echo "Deployment completed successfully."
    fi
    exit "$status"
}

trap cleanup 0 1 2 15

cd "$ROOT_DIR"

if [ ! -f .env ]; then
    echo "Missing $ROOT_DIR/.env. Run ./deploy/setup.sh <domain> first." >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed or is not available in PATH." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required." >&2
    exit 1
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another deployment is already running: $LOCK_DIR" >&2
    exit 1
fi
LOCK_ACQUIRED=1

compose config --quiet
PREVIOUS_IMAGE=$(compose images -q artcalc 2>/dev/null | head -n 1 || true)

echo "Pulling the reverse proxy image..."
compose pull caddy

echo "Building the application image..."
compose build --pull artcalc

echo "Starting the updated stack and waiting for healthchecks..."
UPDATE_STARTED=1
compose up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-180}"

DEPLOY_SUCCEEDED=1
compose ps
