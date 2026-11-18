#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/../network/common.sh"

failures=0

pass() { printf '[OK] %s\n' "$1"; }
fail() { printf '[FALHA] %s\n' "$1" >&2; failures=$((failures + 1)); }

printf '%s\n' '=== Docker e Compose ==='
docker info && pass 'daemon Docker acessível' || fail 'daemon Docker inacessível'
compose config --quiet && pass 'configuração Compose válida' || fail 'configuração Compose inválida'

for service in server-tcp server-http3 client-bench; do
    if compose ps --services --filter status=running | grep -qx "$service"; then
        pass "$service está em execução"
    else
        fail "$service não está em execução"
    fi
done

printf '%s\n' '=== Rede e endereços ==='
subnet="$(docker network inspect net_bench --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>&1)"
[ "$subnet" = '172.20.0.0/16' ] && pass 'net_bench usa 172.20.0.0/16' || fail "sub-rede inesperada: $subnet"

for pair in 'server_tcp_cc:172.20.0.10' 'server_http3_quic:172.20.0.30' 'client_bench:172.20.0.50'; do
    container="${pair%%:*}"
    expected_ip="${pair#*:}"
    actual_ip="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container" 2>&1)"
    [ "$actual_ip" = "$expected_ip" ] \
        && pass "$container recebeu $actual_ip" \
        || fail "$container deveria usar $expected_ip, mas retornou $actual_ip"
done

printf '%s\n' '=== Traffic Control ==='
for service in server-tcp server-http3 client-bench; do
    compose exec -T "$service" tc -V \
        && pass "tc disponível em $service" \
        || fail "tc indisponível em $service"
done

printf '%s\n' '=== Validações dentro do cliente ==='
compose exec -T client-bench /usr/local/bin/validate-container \
    && pass 'validações internas passaram' \
    || fail 'uma ou mais validações internas falharam'

if [ "$failures" -ne 0 ]; then
    printf '[RESUMO] %s grupo(s) de validação falharam.\n' "$failures" >&2
    exit 1
fi

printf '%s\n' '[RESUMO] Infraestrutura validada com sucesso.'
