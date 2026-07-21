#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PUBLIC_HOST=${1:-}

is_ipv4() {
    printf '%s\n' "$1" | awk -F. '
        NF != 4 { exit 1 }
        {
            for (i = 1; i <= 4; i++) {
                if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) {
                    exit 1
                }
            }
        }
    '
}

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
    if [ -z "$PUBLIC_HOST" ]; then
        if [ -t 0 ]; then
            printf "Public domain or IPv4 address: "
            read -r PUBLIC_HOST
        else
            echo "Usage: ./deploy/setup.sh <domain-or-ipv4>" >&2
            exit 1
        fi
    fi
    case "$PUBLIC_HOST" in
        ""|*/*|*:*|*" "*)
            echo "Pass a bare domain or IPv4 address without scheme, port, path, or spaces." >&2
            exit 1
            ;;
    esac

    if is_ipv4 "$PUBLIC_HOST"; then
        SITE_ADDRESS="http://$PUBLIC_HOST"
        PUBLIC_URL=$SITE_ADDRESS
    else
        case "$PUBLIC_HOST" in
            *[!0-9.]*) ;;
            *)
                echo "Invalid IPv4 address: $PUBLIC_HOST" >&2
                exit 1
                ;;
        esac
        SITE_ADDRESS=$PUBLIC_HOST
        PUBLIC_URL="https://$PUBLIC_HOST"
    fi

    cat > .env <<EOF
DOMAIN=$PUBLIC_HOST
SITE_ADDRESS=$SITE_ADDRESS
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
    echo "Public URL: $PUBLIC_URL"
else
    echo "Keeping existing $ROOT_DIR/.env"
fi

git config core.hooksPath "$ROOT_DIR/.githooks"
git config pull.ff only
chmod +x "$ROOT_DIR/.githooks/post-merge" "$ROOT_DIR/deploy/deploy.sh" "$ROOT_DIR/deploy/setup.sh"

echo "Configured post-merge deployment hook. Future git pull commands will deploy automatically."
exec "$ROOT_DIR/deploy/deploy.sh"
