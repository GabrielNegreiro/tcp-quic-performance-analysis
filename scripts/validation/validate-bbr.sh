#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/../network/common.sh"

NETWORK_DIR="$PROJECT_ROOT/scripts/network"
DIAGNOSTICS_DIR="$PROJECT_ROOT/results/diagnostics/bbr"
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
summary_file="$DIAGNOSTICS_DIR/${timestamp}-validation.txt"
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
        printf '%s\n' '[LIMPEZA] Removendo somente qdiscs gerenciadas pelo projeto...'
        sh "$NETWORK_DIR/reset_profile.sh" --all || \
            printf '%s\n' '[LIMPEZA] ERRO: confira as qdiscs manualmente.' >&2
        cleanup_needed=0
    fi
}

trap cleanup 0
trap 'exit 130' HUP INT TERM

has_algorithm() {
    algorithm=$1
    available=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null | tr -d '\r')
    printf ' %s ' "$available" | grep -q " $algorithm "
}

run_algorithm_test() {
    algorithm=$1
    container_base="/tmp/bbr-validation-${timestamp}-${algorithm}"
    host_json="$DIAGNOSTICS_DIR/${timestamp}-${algorithm}.json"
    host_stderr="$DIAGNOSTICS_DIR/${timestamp}-${algorithm}.stderr.txt"
    host_ss="$DIAGNOSTICS_DIR/${timestamp}-${algorithm}-ss.txt"

    compose exec -T client-bench rm -f \
        "${container_base}.json" "${container_base}.stderr" "${container_base}.exit" >/dev/null 2>&1 || true

    compose exec -d client-bench sh -c \
        'iperf3 -c "$1" -t 3 -C "$2" -J > "$3.json" 2> "$3.stderr"; code=$?; printf "%s\n" "$code" > "$3.exit"' \
        sh "$TCP_SERVER_IP" "$algorithm" "$container_base" || {
            fail "não foi possível iniciar o diagnóstico $algorithm."
            return 1
        }

    sleep 1
    ss_output=$(compose exec -T client-bench ss -tin dst "$TCP_SERVER_IP:5201" 2>&1 | tr -d '\r')
    printf '%s\n' "$ss_output" > "$host_ss"
    if printf '%s\n' "$ss_output" | grep -Eq "^[[:space:]]*$algorithm([[:space:](]|$)"; then
        pass "socket transmissor confirmado com $algorithm por ss -tin."
    else
        fail "ss não confirmou $algorithm no socket transmissor."
    fi

    attempt=0
    while [ "$attempt" -lt 15 ]; do
        if compose exec -T client-bench test -f "${container_base}.exit" >/dev/null 2>&1; then
            break
        fi
        attempt=$((attempt + 1))
        sleep 1
    done

    if ! compose exec -T client-bench test -f "${container_base}.exit" >/dev/null 2>&1; then
        fail "diagnóstico $algorithm não terminou no prazo."
        return 1
    fi

    exit_code=$(compose exec -T client-bench sh -c 'tr -d "\r\n" < "$1"' sh "${container_base}.exit" 2>/dev/null)
    compose exec -T client-bench cat "${container_base}.json" > "$host_json" 2>/dev/null || true
    compose exec -T client-bench cat "${container_base}.stderr" > "$host_stderr" 2>/dev/null || true

    if [ "$exit_code" = '0' ]; then
        pass "iperf3 -C $algorithm terminou com código 0."
    else
        fail "iperf3 -C $algorithm terminou com código '$exit_code'."
    fi

    if [ -s "$host_json" ] && grep -Eq '"bytes"[[:space:]]*:[[:space:]]*[1-9][0-9]*' "$host_json"; then
        pass "JSON $algorithm íntegro e com dados transmitidos."
    else
        fail "JSON $algorithm ausente ou sem bytes transmitidos."
    fi

    if [ ! -s "$host_stderr" ]; then
        pass "seleção $algorithm sem erro no stderr."
    else
        fail "seleção $algorithm registrou erro; consulte $host_stderr."
    fi

    compose exec -T client-bench rm -f \
        "${container_base}.json" "${container_base}.stderr" "${container_base}.exit" >/dev/null 2>&1 || true
}

cd "$PROJECT_ROOT" || die 'não foi possível acessar a raiz do projeto.'
require_docker
mkdir -p "$DIAGNOSTICS_DIR" || die "não foi possível criar $DIAGNOSTICS_DIR."

sh "$NETWORK_DIR/reset_profile.sh" --all || die 'o estado inicial não pôde ser limpo com segurança.'
cleanup_needed=1

printf '%s\n' '=== Disponibilidade e carregamento sob demanda ==='
before_cc=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_available_congestion_control 2>&1 | tr -d '\r')
default_cc_before=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_congestion_control 2>&1 | tr -d '\r')

if ! has_algorithm bbr; then
    printf '%s\n' '[INFO] BBR ainda não registrado; solicitando-o somente no socket de um diagnóstico de 1 segundo.'
    warmup_json="$DIAGNOSTICS_DIR/${timestamp}-autoload-probe.json"
    warmup_stderr="$DIAGNOSTICS_DIR/${timestamp}-autoload-probe.stderr.txt"
    if compose exec -T client-bench iperf3 -c "$TCP_SERVER_IP" -t 1 -C bbr -J \
        > "$warmup_json" 2> "$warmup_stderr"; then
        pass 'o kernel resolveu tcp_bbr sob demanda pela seleção por socket.'
    else
        fail 'o kernel não conseguiu resolver tcp_bbr pela seleção por socket.'
    fi
fi

after_cc=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_available_congestion_control 2>&1 | tr -d '\r')
default_cc_after=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_congestion_control 2>&1 | tr -d '\r')

has_algorithm bbr && pass 'BBR aparece na lista de algoritmos disponíveis.' || fail 'BBR não aparece na lista de algoritmos disponíveis.'
has_algorithm cubic && pass 'CUBIC permanece disponível.' || fail 'CUBIC não está disponível.'
[ "$default_cc_before" = "$default_cc_after" ] \
    && pass "algoritmo global padrão permaneceu $default_cc_after." \
    || fail "algoritmo global padrão mudou de $default_cc_before para $default_cc_after."

printf '%s\n' '=== Seleção real por socket ==='
run_algorithm_test bbr
run_algorithm_test cubic

printf '%s\n' '=== Regressão da infraestrutura ==='
compose config --quiet && pass 'Docker Compose permanece válido.' || fail 'Docker Compose ficou inválido.'
for service in $ALL_SERVICES; do
    if compose ps --services --filter status=running | grep -qx "$service"; then
        pass "$service permanece em execução."
    else
        fail "$service não está em execução."
    fi
done

compose exec -T client-bench nc -z -w 3 server-tcp 5201 \
    && pass 'servidor TCP permanece acessível.' \
    || fail 'servidor TCP não está acessível.'

h2_metrics=$(compose exec -T client-bench curl --http2 \
    --cacert /certs/server-http3.crt --silent --show-error --output /dev/null \
    --write-out '%{http_code} %{http_version} %{size_download}' \
    https://server-http3/health.txt 2>&1)
[ "$h2_metrics" = '200 2 1024' ] \
    && pass 'HTTP/2, TLS e objeto de 1024 bytes aprovados.' \
    || fail "regressão HTTP/2/TLS falhou: $h2_metrics"

h3_metrics=$(compose exec -T client-bench curl --http3-only \
    --cacert /certs/server-http3.crt --silent --show-error --output /dev/null \
    --write-out '%{http_code} %{http_version} %{size_download}' \
    https://server-http3/health.txt 2>&1)
[ "$h3_metrics" = '200 3 1024' ] \
    && pass 'HTTP/3-only, TLS e objeto de 1024 bytes aprovados.' \
    || fail "regressão HTTP/3/TLS falhou: $h3_metrics"

sh "$NETWORK_DIR/reset_profile.sh" --all || fail 'limpeza final dos perfis gerenciados falhou.'
cleanup_needed=0

for service in $ALL_SERVICES; do
    peer_ip=$(peer_ip_for_service "$service")
    interface=$(discover_interface "$service" "$peer_ip")
    line=$(root_qdisc "$service" "$interface")
    class=$(qdisc_class "$line")
    [ "$class" = 'default' ] \
        && pass "$service/$interface terminou sem qdisc experimental." \
        || fail "$service/$interface terminou em estado '$class': $line"
done

module_line=$(compose exec -T client-bench sh -c 'grep "^tcp_bbr " /proc/modules || true' 2>&1 | tr -d '\r')
{
    printf 'diagnostic_type=bbr_validation\n'
    printf 'official_experiment=false\n'
    printf 'timestamp_utc=%s\n' "$timestamp"
    printf 'kernel=%s\n' "$(compose exec -T client-bench uname -r 2>&1 | tr -d '\r')"
    printf 'available_before=%s\n' "$before_cc"
    printf 'available_after=%s\n' "$after_cc"
    printf 'default_before=%s\n' "$default_cc_before"
    printf 'default_after=%s\n' "$default_cc_after"
    printf 'module=%s\n' "$module_line"
    printf 'failures=%s\n' "$failures"
    if [ "$failures" -eq 0 ]; then printf 'status=passed\n'; else printf 'status=failed\n'; fi
    printf '%s\n' 'note=The iperf3 JSON is diagnostic only; active algorithms are proven by the matching ss captures.'
} > "$summary_file"

printf '[DIAGNÓSTICO] %s\n' "$summary_file"
if [ "$failures" -ne 0 ]; then
    printf '[RESUMO] %s verificação(ões) falharam.\n' "$failures" >&2
    exit 1
fi

printf '%s\n' '[RESUMO] BBR e CUBIC foram selecionados e confirmados em sockets reais; regressão aprovada.'

