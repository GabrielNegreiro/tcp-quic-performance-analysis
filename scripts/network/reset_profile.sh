#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/common.sh"

usage() {
    printf '%s\n' 'Uso: sh scripts/network/reset_profile.sh [--all|client-bench|server-tcp|server-http3]'
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
failures=0

for service in $services; do
    assert_service_owned_and_running "$service"
    peer_ip=$(peer_ip_for_service "$service")
    interface=$(discover_interface "$service" "$peer_ip")
    reset_managed_service "$service" "$interface"
    status=$?
    if [ "$status" -ne 0 ]; then
        failures=$((failures + 1))
    fi
done

[ "$failures" -eq 0 ] || die "$failures interface(s) não puderam ser limpas com segurança."
ok 'Limpeza das regras gerenciadas concluída.'
