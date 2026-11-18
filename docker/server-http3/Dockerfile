FROM caddy:2.11.9-alpine

RUN apk add --no-cache \
        iproute2 \
        openssl \
    && caddy version \
    && tc -V

COPY start-server.sh /usr/local/bin/start-server
RUN chmod 0755 /usr/local/bin/start-server

EXPOSE 443/tcp 443/udp

ENTRYPOINT ["/usr/local/bin/start-server"]
