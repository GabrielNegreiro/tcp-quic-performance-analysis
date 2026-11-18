#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/common.sh"

usage() {
    printf '%s\n' 'Uso: sh scripts/network/inspect_profile.sh [--all|client-bench|server-tcp|server-http3]'
}

[ "$#" -le 1 ] || { usage >&2; exit 2; }
selection=${1:---all}

case "$selection" in
    --all) services=$ALL_SERVICES ;;
    client-bench|server-tcp|server-http3) services=$selection ;;
    *) usage >&2; exit 2 ;;
esac

cd "$PROJECT_ROOT" || die 'não foi possível acessar a raiz do projeto.'
require_docker

for service in $services; do
    printf '=== %s ===\n' "$service"
    inspect_service "$service"
done
