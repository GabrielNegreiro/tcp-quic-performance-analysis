#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/../network/common.sh"
NETWORK_DIR="$PROJECT_ROOT/scripts/network"
DIAGNOSTICS_DIR="$PROJECT_ROOT/results/diagnostics"

failures=0
cleanup_needed=0

pass() {
    printf '[OK] %s\n' "$1"
}

fail() {
    printf '[FALHA] %s\n' "$1" >&2
    failures=$((failures + 1))
}

cleanup() {
    if [ "$cleanup_needed" -eq 1 ]; then
        printf '%s\n' '[LIMPEZA] Removendo regras gerenciadas após validação/interrupção...'
        sh "$NETWORK_DIR/reset_profile.sh" --all || \
            printf '%s\n' '[LIMPEZA] ERRO: confira as qdiscs manualmente.' >&2
        cleanup_needed=0
    fi
}

trap cleanup 0
trap 'exit 130' HUP INT TERM

parse_ping() {
    ping_text=$1
    summary=$(printf '%s\n' "$ping_text" | grep 'packets transmitted' | tail -n 1)
    rtt_line=$(printf '%s\n' "$ping_text" | grep -E '^(rtt|round-trip) ' | tail -n 1)

    PING_SENT=$(printf '%s\n' "$summary" | awk '{print $1}')
    PING_RECEIVED=$(printf '%s\n' "$summary" | awk '{print $4}')
    PING_LOSS=$(printf '%s\n' "$summary" | sed -n 's/.* \([0-9.][0-9.]*\)% packet loss.*/\1/p')
    rtt_values=$(printf '%s\n' "$rtt_line" | sed -n 's/.* = \([^ ]*\) ms.*/\1/p')
    old_ifs=$IFS
    IFS='/'; set -- $rtt_values; IFS=$old_ifs
    PING_MIN=${1:-unavailable}
    PING_AVG=${2:-unavailable}
    PING_MAX=${3:-unavailable}

    [ -n "$PING_SENT" ] && [ -n "$PING_RECEIVED" ] && [ -n "$PING_LOSS" ] && \
        [ "$PING_AVG" != 'unavailable' ]
}

rtt_in_range() {
    awk -v value="$1" -v minimum="$2" -v maximum="$3" \
        'BEGIN { exit !(value >= minimum && value <= maximum) }'
}

write_diagnostic() {
    profile=$1
    target_service=$2
    configured_rtt=$3
    configured_loss=$4
    validation_status=$5
    client_interface=$6
    server_interface=$7
    client_qdisc=$8
    server_qdisc=$9
    http3_status=${10}
    ping_output=${11}
    client_qdisc_stats=${12}
    server_qdisc_stats=${13}

    timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
    output_dir="$DIAGNOSTICS_DIR/$profile"
    output_file="$output_dir/${timestamp}.txt"
    mkdir -p "$output_dir" || die "não foi possível criar $output_dir."

    {
        printf 'diagnostic_type=network_profile_validation\n'
        printf 'official_experiment=false\n'
        printf 'timestamp_utc=%s\n' "$timestamp"
        printf 'profile=%s\n' "$profile"
        printf 'target_service=%s\n' "$target_service"
        printf 'client_service=client-bench\n'
        printf 'client_interface=%s\n' "$client_interface"
        printf 'server_interface=%s\n' "$server_interface"
        printf 'configured_rtt_ms=%s\n' "$configured_rtt"
        printf 'configured_loss_percent=%s\n' "$configured_loss"
        printf 'packets_sent=%s\n' "$PING_SENT"
        printf 'packets_received=%s\n' "$PING_RECEIVED"
        printf 'observed_loss_percent=%s\n' "$PING_LOSS"
        printf 'observed_rtt_min_ms=%s\n' "$PING_MIN"
        printf 'observed_rtt_avg_ms=%s\n' "$PING_AVG"
        printf 'observed_rtt_max_ms=%s\n' "$PING_MAX"
        printf 'http3_diagnostic=%s\n' "$http3_status"
        printf 'status=%s\n' "$validation_status"
        printf 'client_qdisc=%s\n' "$client_qdisc"
        printf 'server_qdisc=%s\n' "$server_qdisc"
        printf '%s\n' '--- ping_output ---'
        printf '%s\n' "$ping_output"
        printf '%s\n' '--- client_tc_stats ---'
        printf '%s\n' "$client_qdisc_stats"
        printf '%s\n' '--- server_tc_stats ---'
        printf '%s\n' "$server_qdisc_stats"
    } > "$output_file"

    printf '[DIAGNÓSTICO] %s\n' "$output_file"
}

run_scenario() {
    profile=$1
    target_service=$2
    target_ip=$3
    expected_rtt=$4
    configured_loss=$5
    packet_count=$6
    rtt_minimum=$7
    rtt_maximum=$8

    printf '\n=== Cenário %s ===\n' "$profile"
    sh "$NETWORK_DIR/apply_profile.sh" "$profile" "$target_service" || {
        fail "não foi possível aplicar $profile."
        return 1
    }
    cleanup_needed=1

    client_interface=$(discover_interface client-bench "$target_ip")
    server_interface=$(discover_interface "$target_service" "$CLIENT_IP")
    client_qdisc=$(root_qdisc client-bench "$client_interface")
    server_qdisc=$(root_qdisc "$target_service" "$server_interface")
    scenario_status='passed'
    http3_status='not_applicable'

    printf '%s\n' "$client_qdisc" | grep -q "^qdisc netem $MANAGED_HANDLE" || {
        fail "$profile não foi confirmado no cliente."
        scenario_status='failed'
    }
    printf '%s\n' "$server_qdisc" | grep -q "^qdisc netem $MANAGED_HANDLE" || {
        fail "$profile não foi confirmado no servidor."
        scenario_status='failed'
    }

    if [ "$configured_loss" = '2' ]; then
        printf '%s\n' "$client_qdisc" | grep -q 'loss 2%' || {
            fail 'loss 2% não aparece na qdisc de saída do cliente.'
            scenario_status='failed'
        }
        printf '%s\n' "$server_qdisc" | grep -q 'loss ' && {
            fail 'a perda foi aplicada também no servidor; esperado somente cliente -> servidor.'
            scenario_status='failed'
        }
    else
        printf '%s\n' "$client_qdisc" | grep -q 'loss ' && {
            fail "$profile possui perda inesperada no cliente."
            scenario_status='failed'
        }
    fi

    ping_output=$(compose exec -T client-bench ping -c "$packet_count" -i 0.05 -W 2 "$target_ip" 2>&1)
    ping_status=$?
    printf '%s\n' "$ping_output"
    if [ "$ping_status" -ne 0 ] || ! parse_ping "$ping_output"; then
        PING_SENT=${PING_SENT:-unavailable}
        PING_RECEIVED=${PING_RECEIVED:-unavailable}
        PING_LOSS=${PING_LOSS:-unavailable}
        PING_MIN=${PING_MIN:-unavailable}
        PING_AVG=${PING_AVG:-unavailable}
        PING_MAX=${PING_MAX:-unavailable}
        fail "não foi possível medir RTT/perda em $profile."
        scenario_status='failed'
    elif rtt_in_range "$PING_AVG" "$rtt_minimum" "$rtt_maximum"; then
        pass "$profile: RTT médio ${PING_AVG} ms, alvo ${expected_rtt} ms."
    else
        fail "$profile: RTT médio ${PING_AVG} ms fora da faixa ${rtt_minimum}-${rtt_maximum} ms."
        scenario_status='failed'
    fi

    if [ "$configured_loss" = '0' ] && [ "$PING_LOSS" != '0' ]; then
        fail "$profile apresentou perda observada de ${PING_LOSS}%, esperado 0%."
        scenario_status='failed'
    fi

    if [ "$profile" = 'wan-no-loss' ]; then
        http3_metrics=$(compose exec -T client-bench curl \
            --http3-only \
            --cacert /certs/server-http3.crt \
            --silent \
            --show-error \
            --output /dev/null \
            --write-out '%{http_code} %{http_version} %{size_download}' \
            https://server-http3/health.txt 2>&1)
        if [ "$http3_metrics" = '200 3 1024' ]; then
            http3_status='passed:200_3_1024'
            pass 'HTTP/3-only permaneceu operacional sob WAN sem perda.'
        else
            http3_status="failed:$http3_metrics"
            fail "diagnóstico HTTP/3 inesperado: $http3_metrics"
            scenario_status='failed'
        fi
    fi

    client_qdisc_stats=$(compose exec -T client-bench tc -s qdisc show dev "$client_interface")
    server_qdisc_stats=$(compose exec -T "$target_service" tc -s qdisc show dev "$server_interface")

    write_diagnostic "$profile" "$target_service" "$expected_rtt" "$configured_loss" \
        "$scenario_status" "$client_interface" "$server_interface" \
        "$client_qdisc" "$server_qdisc" "$http3_status" "$ping_output" \
        "$client_qdisc_stats" "$server_qdisc_stats"

    sh "$NETWORK_DIR/reset_profile.sh" --all || {
        fail "limpeza após $profile falhou."
        return 1
    }
    cleanup_needed=0

    [ "$scenario_status" = 'passed' ]
}

cd "$PROJECT_ROOT" || die 'não foi possível acessar a raiz do projeto.'
require_docker
mkdir -p "$DIAGNOSTICS_DIR/lan" "$DIAGNOSTICS_DIR/wan-degraded" "$DIAGNOSTICS_DIR/wan-no-loss"

sh "$NETWORK_DIR/reset_profile.sh" --all || die 'o estado inicial não pôde ser limpo com segurança.'

run_scenario lan server-tcp "$TCP_SERVER_IP" 1 0 20 0.7 3.0
run_scenario wan-degraded server-tcp "$TCP_SERVER_IP" 50 2 100 45 60
run_scenario wan-no-loss server-http3 "$HTTP3_SERVER_IP" 50 0 20 45 60

printf '\n=== Idempotência e troca de perfis ===\n'
if sh "$NETWORK_DIR/apply_profile.sh" lan server-tcp && \
   sh "$NETWORK_DIR/apply_profile.sh" lan server-tcp; then
    cleanup_needed=1
    client_interface=$(discover_interface client-bench "$TCP_SERVER_IP")
    server_interface=$(discover_interface server-tcp "$CLIENT_IP")
    client_count=$(compose exec -T client-bench tc qdisc show dev "$client_interface" | grep -c "^qdisc netem $MANAGED_HANDLE")
    server_count=$(compose exec -T server-tcp tc qdisc show dev "$server_interface" | grep -c "^qdisc netem $MANAGED_HANDLE")
    if [ "$client_count" -eq 1 ] && [ "$server_count" -eq 1 ]; then
        pass 'reaplicação do perfil manteve uma única qdisc por interface.'
    else
        fail "acúmulo detectado: cliente=$client_count servidor=$server_count."
    fi
else
    fail 'reaplicação do perfil LAN falhou.'
fi

if sh "$NETWORK_DIR/apply_profile.sh" wan-degraded server-tcp; then
    cleanup_needed=1
    client_interface=$(discover_interface client-bench "$TCP_SERVER_IP")
    switched_qdisc=$(root_qdisc client-bench "$client_interface")
    if printf '%s\n' "$switched_qdisc" | grep -q 'delay 25ms' && \
       printf '%s\n' "$switched_qdisc" | grep -q 'loss 2%'; then
        pass 'troca LAN -> WAN degradada substituiu o estado anterior.'
    else
        fail "estado inesperado após troca de perfil: $switched_qdisc"
    fi
else
    fail 'troca para WAN degradada falhou.'
fi

sh "$NETWORK_DIR/reset_profile.sh" --all || fail 'limpeza do teste de idempotência falhou.'
cleanup_needed=0

printf '\n=== Regressão após limpeza ===\n'
compose exec -T client-bench nc -z -w 3 server-tcp 5201 \
    && pass 'servidor TCP continua acessível.' \
    || fail 'servidor TCP falhou após limpeza.'

h2_metrics=$(compose exec -T client-bench curl --http2 \
    --cacert /certs/server-http3.crt --silent --show-error --output /dev/null \
    --write-out '%{http_code} %{http_version} %{size_download}' \
    https://server-http3/health.txt 2>&1)
[ "$h2_metrics" = '200 2 1024' ] \
    && pass 'HTTP/2 continua operacional.' \
    || fail "regressão HTTP/2 falhou: $h2_metrics"

h3_metrics=$(compose exec -T client-bench curl --http3-only \
    --cacert /certs/server-http3.crt --silent --show-error --output /dev/null \
    --write-out '%{http_code} %{http_version} %{size_download}' \
    https://server-http3/health.txt 2>&1)
[ "$h3_metrics" = '200 3 1024' ] \
    && pass 'HTTP/3-only continua operacional.' \
    || fail "regressão HTTP/3 falhou: $h3_metrics"

for service in $ALL_SERVICES; do
    peer_ip=$(peer_ip_for_service "$service")
    interface=$(discover_interface "$service" "$peer_ip")
    line=$(root_qdisc "$service" "$interface")
    class=$(qdisc_class "$line")
    [ "$class" = 'default' ] \
        && pass "$service/$interface terminou sem qdisc experimental." \
        || fail "$service/$interface terminou em estado '$class': $line"
done

if [ "$failures" -ne 0 ]; then
    printf '[RESUMO] %s verificação(ões) falharam.\n' "$failures" >&2
    exit 1
fi

printf '%s\n' '[RESUMO] Os três cenários, idempotência, limpeza e regressão foram aprovados.'
