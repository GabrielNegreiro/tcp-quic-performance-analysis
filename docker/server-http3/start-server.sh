#!/bin/sh
set -eu

certificate=/certs/server-http3.crt
private_key=/certs/server-http3.key

if [ ! -s "$certificate" ] || [ ! -s "$private_key" ]; then
    echo "[TLS] Gerando certificado local para server-http3 e 172.20.0.30..."
    umask 077
    openssl req \
        -x509 \
        -newkey rsa:2048 \
        -sha256 \
        -nodes \
        -days 365 \
        -subj "/CN=server-http3/O=UFPI Redes II Lab" \
        -addext "subjectAltName=DNS:server-http3,DNS:localhost,IP:172.20.0.30,IP:127.0.0.1" \
        -addext "keyUsage=digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth" \
        -keyout "$private_key" \
        -out "$certificate"
    chmod 0600 "$private_key"
    chmod 0644 "$certificate"
fi

openssl x509 -in "$certificate" -noout -checkend 86400 >/dev/null || {
    echo "[TLS] ERRO: o certificado está inválido ou expira em menos de 24 horas." >&2
    exit 1
}

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
