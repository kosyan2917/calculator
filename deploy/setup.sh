#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DOMAIN=${1:-}

cd "$ROOT_DIR"

if ! command -v git >/dev/null 2>&1; then
    echo "Git is required." >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "Install Docker Engine with the Compose v2 plugin before running setup." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is unavailable for this user. Check the daemon and docker group membership." >&2
    exit 1
fi

if [ ! -f .env ]; then
    if [ -z "$DOMAIN" ]; then
        if [ -t 0 ]; then
            printf "Public domain (for example artifacts.example.com): "
            read -r DOMAIN
        else
            echo "Usage: ./deploy/setup.sh <domain>" >&2
            exit 1
        fi
    fi
    case "$DOMAIN" in
        ""|*/*|*:*|*" "*)
            echo "Pass a bare domain without scheme, port, path, or spaces." >&2
            exit 1
            ;;
    esac

    cat > .env <<EOF
DOMAIN=$DOMAIN
WEB_CONCURRENCY=2
ARTCALC_TIME_LIMIT=0.5
ARTCALC_SOLUTIONS_PER_CONTAINER=1
ARTCALC_CP_WORKERS=1
ARTCALC_NONLINEAR_ITERATIONS=3
ARTCALC_UPGRADE_TIME_LIMIT=0.25
ARTCALC_UPGRADE_NONLINEAR_ITERATIONS=2
EOF
    chmod 600 .env
    echo "Created $ROOT_DIR/.env"
else
    echo "Keeping existing $ROOT_DIR/.env"
fi

git config core.hooksPath "$ROOT_DIR/.githooks"
git config pull.ff only
chmod +x "$ROOT_DIR/.githooks/post-merge" "$ROOT_DIR/deploy/deploy.sh" "$ROOT_DIR/deploy/setup.sh"

echo "Configured post-merge deployment hook. Future git pull commands will deploy automatically."
exec "$ROOT_DIR/deploy/deploy.sh"
