#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Uso: sh scripts/network/apply_profile.sh <perfil> <servidor>

Combinações válidas:
  lan           server-tcp
  wan-degraded  server-tcp
  wan-no-loss   server-http3
EOF
}

[ "$#" -eq 2 ] || { usage >&2; exit 2; }
profile=$1
target_service=$2

case "$profile:$target_service" in
    lan:server-tcp)
        one_way_delay='500us'
        client_loss='0%'
        target_ip=$TCP_SERVER_IP
        ;;
    wan-degraded:server-tcp)
        one_way_delay='25ms'
        client_loss='2%'
        target_ip=$TCP_SERVER_IP
        ;;
    wan-no-loss:server-http3)
        one_way_delay='25ms'
        client_loss='0%'
        target_ip=$HTTP3_SERVER_IP
        ;;
    *) usage >&2; die "combinação de perfil e servidor não permitida: $profile $target_service" ;;
esac

cd "$PROJECT_ROOT" || die 'não foi possível acessar a raiz do projeto.'
require_docker

assert_service_owned_and_running client-bench
assert_service_owned_and_running "$target_service"

client_interface=$(discover_interface client-bench "$target_ip")
server_interface=$(discover_interface "$target_service" "$CLIENT_IP")

log "Perfil: $profile"
log "Cliente: client-bench/$client_interface -> $target_ip"
log "Servidor: $target_service/$server_interface -> $CLIENT_IP"
log "Atraso: $one_way_delay em cada direção"
log "Perda: $client_loss somente na saída do cliente"

# Verifica todos os serviços do projeto antes de limpar qualquer regra. Uma
# qdisc desconhecida bloqueia a troca, evitando apagar configuração externa.
for service in $ALL_SERVICES; do
    assert_service_owned_and_running "$service"
    peer_ip=$(peer_ip_for_service "$service")
    interface=$(discover_interface "$service" "$peer_ip")
    assert_qdisc_safe_to_manage "$service" "$interface"
done

# Remove apenas regras com o handle reservado pelo projeto para impedir que um
# perfil anterior contamine o próximo bloco experimental.
for service in $ALL_SERVICES; do
    peer_ip=$(peer_ip_for_service "$service")
    interface=$(discover_interface "$service" "$peer_ip")
    reset_managed_service "$service" "$interface" || \
        die "não foi possível preparar $service/$interface."
done

applied_client=0
rollback() {
    if [ "$applied_client" -eq 1 ]; then
        warn 'Aplicação parcial detectada; removendo a regra já aplicada ao cliente.'
        reset_managed_service client-bench "$client_interface"
    fi
}

log "Aplicando $profile em client-bench/$client_interface..."
if [ "$client_loss" = '2%' ]; then
    compose exec -T client-bench tc qdisc replace dev "$client_interface" root \
        handle "$MANAGED_HANDLE" netem delay "$one_way_delay" loss random "$client_loss" || \
        die 'falha ao aplicar netem no cliente.'
else
    compose exec -T client-bench tc qdisc replace dev "$client_interface" root \
        handle "$MANAGED_HANDLE" netem delay "$one_way_delay" || \
        die 'falha ao aplicar netem no cliente.'
fi
applied_client=1
write_profile_marker client-bench "$client_interface" "$profile" || { rollback; die 'falha ao gravar o marcador do cliente.'; }

log "Aplicando $profile em $target_service/$server_interface..."
compose exec -T "$target_service" tc qdisc replace dev "$server_interface" root \
    handle "$MANAGED_HANDLE" netem delay "$one_way_delay" || {
        rollback
        die 'falha ao aplicar netem no servidor.'
    }
write_profile_marker "$target_service" "$server_interface" "$profile" || {
    reset_managed_service "$target_service" "$server_interface"
    rollback
    die 'falha ao gravar o marcador do servidor.'
}

client_qdisc=$(root_qdisc client-bench "$client_interface")
server_qdisc=$(root_qdisc "$target_service" "$server_interface")
printf '%s\n' "$client_qdisc" | grep -q "^qdisc netem $MANAGED_HANDLE" || { rollback; die 'qdisc do cliente não foi confirmada.'; }
printf '%s\n' "$server_qdisc" | grep -q "^qdisc netem $MANAGED_HANDLE" || {
    reset_managed_service "$target_service" "$server_interface"
    rollback
    die 'qdisc do servidor não foi confirmada.'
}

if [ "$client_loss" = '2%' ]; then
    printf '%s\n' "$client_qdisc" | grep -q 'loss 2%' || {
        reset_managed_service "$target_service" "$server_interface"
        rollback
        die 'a perda de 2% não foi confirmada na saída do cliente.'
    }
fi

applied_client=0
ok "Perfil $profile aplicado sem acúmulo de qdiscs."
printf '%s\n' '-- cliente --' "$client_qdisc" '-- servidor --' "$server_qdisc"
