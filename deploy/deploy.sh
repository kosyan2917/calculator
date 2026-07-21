#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LOCK_DIR="$ROOT_DIR/.deploy.lock"
LOCK_ACQUIRED=0
PREVIOUS_ARTCALC_IMAGE=""
PREVIOUS_SIMULATOR_IMAGE=""
PREVIOUS_GATEWAY_IMAGE=""
UPDATE_STARTED=0
DEPLOY_SUCCEEDED=0

compose() {
    docker compose "$@"
}

cleanup() {
    status=$?
    trap - 0 1 2 15
    set +e

    if [ "$status" -ne 0 ] && [ "$UPDATE_STARTED" -eq 1 ] \
        && [ -n "$PREVIOUS_ARTCALC_IMAGE" ] \
        && [ -n "$PREVIOUS_SIMULATOR_IMAGE" ] \
        && [ -n "$PREVIOUS_GATEWAY_IMAGE" ]; then
        echo "Deployment failed; restoring the previous service images." >&2
        docker image tag "$PREVIOUS_ARTCALC_IMAGE" stalzone-artcalc-api:local
        docker image tag "$PREVIOUS_SIMULATOR_IMAGE" stalzone-simulator:local
        docker image tag "$PREVIOUS_GATEWAY_IMAGE" stalzone-gateway:local
        compose up -d --no-build --force-recreate --remove-orphans --wait \
            --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-180}"
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
PREVIOUS_ARTCALC_IMAGE=$(compose images -q artcalc-api 2>/dev/null | head -n 1 || true)
PREVIOUS_SIMULATOR_IMAGE=$(compose images -q simulator 2>/dev/null | head -n 1 || true)
PREVIOUS_GATEWAY_IMAGE=$(compose images -q gateway 2>/dev/null | head -n 1 || true)

echo "Building gateway, ArtCalc API, and simulator images..."
compose build --pull gateway artcalc-api simulator

echo "Starting the updated stack and waiting for healthchecks..."
UPDATE_STARTED=1
compose up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-180}"

DEPLOY_SUCCEEDED=1
compose ps
