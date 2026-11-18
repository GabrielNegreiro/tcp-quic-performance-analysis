#!/bin/sh
set -u

failures=0

pass() {
    printf '[OK] %s\n' "$1"
}

fail() {
    printf '[FALHA] %s\n' "$1" >&2
    failures=$((failures + 1))
}

printf '%s\n' '=== Ferramentas do cliente ==='
curl --version || fail 'curl não executa'
curl_features="$(curl --version 2>&1)"
printf '%s' "$curl_features" | grep -q 'HTTP2' && pass 'curl anuncia HTTP/2' || fail 'curl não anuncia HTTP/2'
printf '%s' "$curl_features" | grep -q 'HTTP3' && pass 'curl anuncia HTTP/3' || fail 'curl não anuncia HTTP/3'
iperf3 --version || fail 'iperf3 não executa'
tc -V && pass 'tc está disponível' || fail 'tc não está disponível'

printf '%s\n' '=== DNS e conectividade TCP ==='
getent hosts server-tcp || fail 'DNS interno não resolve server-tcp'
getent hosts server-http3 || fail 'DNS interno não resolve server-http3'
nc -z -w 3 server-tcp 5201 && pass 'porta TCP 5201 acessível' || fail 'porta TCP 5201 inacessível'
iperf3 -c server-tcp -t 1 && pass 'diagnóstico iperf3 de 1 segundo concluído' || fail 'diagnóstico iperf3 falhou'

printf '%s\n' '=== TLS e HTTP/2 ==='
if openssl s_client \
    -connect server-http3:443 \
    -servername server-http3 \
    -CAfile /certs/server-http3.crt \
    -verify_return_error </dev/null 2>&1 | tee /tmp/tls-check.txt | grep -q 'Verify return code: 0'; then
    pass 'certificado TLS validado com o certificado local'
else
    cat /tmp/tls-check.txt >&2
    fail 'validação do certificado TLS falhou'
fi

if h2_metrics="$(curl \
    --http2 \
    --tlsv1.3 \
    --cacert /certs/server-http3.crt \
    --silent \
    --show-error \
    --output /tmp/health-h2.txt \
    --write-out '%{http_code} %{http_version} %{size_download}' \
    https://server-http3/health.txt)"; then
    printf 'HTTP/2: %s\n' "$h2_metrics"
    [ "$h2_metrics" = '200 2 1024' ] \
        && pass 'HTTP/2 negociado, HTTP 200 e objeto de 1024 bytes' \
        || fail "resposta HTTP/2 inesperada: $h2_metrics"
else
    fail 'requisição HTTP/2 falhou'
fi

printf '%s\n' '=== HTTP/3 ==='
if h3_metrics="$(curl \
    --http3-only \
    --tlsv1.3 \
    --cacert /certs/server-http3.crt \
    --silent \
    --show-error \
    --output /tmp/health-h3.txt \
    --write-out '%{http_code} %{http_version} %{size_download}' \
    https://server-http3/health.txt)"; then
    printf 'HTTP/3: %s\n' "$h3_metrics"
    [ "$h3_metrics" = '200 3 1024' ] \
        && pass 'HTTP/3-only negociado, HTTP 200 e objeto de 1024 bytes' \
        || fail "resposta HTTP/3 inesperada: $h3_metrics"
else
    fail 'requisição HTTP/3-only falhou'
fi

printf '%s\n' '=== Controle de congestionamento ==='
available_cc="$(sysctl -n net.ipv4.tcp_available_congestion_control 2>&1)" || available_cc='consulta falhou'
printf 'Disponíveis no kernel: %s\n' "$available_cc"
printf ' %s ' "$available_cc" | grep -q ' cubic ' && pass 'TCP CUBIC disponível' || fail 'TCP CUBIC indisponível'
if printf ' %s ' "$available_cc" | grep -q ' bbr '; then
    pass 'TCP BBR disponível'
    iperf3 --help 2>&1 | grep -q -- '-C' \
        && pass 'iperf3 oferece seleção por -C/--congestion' \
        || fail 'iperf3 não oferece seleção de algoritmo por -C'
    iperf3 -c server-tcp -t 1 -C bbr \
        && pass 'seleção BBR confirmada em diagnóstico de 1 segundo' \
        || fail 'kernel anuncia BBR, mas a seleção pelo iperf3 falhou'
else
    fail 'TCP BBR indisponível no kernel compartilhado com os contêineres'
fi

if [ "$failures" -ne 0 ]; then
    printf '[RESUMO] %s verificação(ões) falharam.\n' "$failures" >&2
    exit 1
fi

printf '%s\n' '[RESUMO] Todas as verificações internas passaram.'
