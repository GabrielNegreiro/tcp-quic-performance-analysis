FROM alpine:3.23 AS curl-builder

ARG CURL_VERSION=8.23.0

RUN apk add --no-cache \
        build-base \
        brotli-dev \
        curl \
        gnutls-dev \
        libidn2-dev \
        libpsl-dev \
        nghttp2-dev \
        nghttp3-dev \
        ngtcp2-dev \
        pkgconf \
        tar \
        xz \
        zlib-dev \
        zstd-dev

WORKDIR /tmp/build

RUN curl --fail --show-error --location \
        "https://curl.se/download/curl-${CURL_VERSION}.tar.xz" \
        --output curl.tar.xz \
    && tar -xf curl.tar.xz --strip-components=1 \
    && ./configure \
        --prefix=/opt/curl-h3 \
        --disable-static \
        --enable-alt-svc \
        --enable-ipv6 \
        --enable-unix-sockets \
        --with-gnutls \
        --with-nghttp2 \
        --with-nghttp3 \
        --with-ngtcp2 \
    && make -j"$(getconf _NPROCESSORS_ONLN)" \
    && make install \
    && /opt/curl-h3/bin/curl --version \
    && /opt/curl-h3/bin/curl --version | grep -q 'HTTP2' \
    && /opt/curl-h3/bin/curl --version | grep -q 'HTTP3'

FROM alpine:3.23

RUN apk add --no-cache \
        bind-tools \
        brotli-libs \
        ca-certificates \
        gnutls \
        iperf3 \
        iproute2 \
        iputils \
        libidn2 \
        libpsl \
        musl-utils \
        netcat-openbsd \
        nghttp2-libs \
        nghttp3 \
        ngtcp2 \
        ngtcp2-gnutls \
        openssl \
        zlib \
        zstd-libs

COPY --from=curl-builder /opt/curl-h3 /opt/curl-h3
COPY validate-container.sh /usr/local/bin/validate-container

ENV PATH="/opt/curl-h3/bin:${PATH}"
ENV LD_LIBRARY_PATH="/opt/curl-h3/lib"

RUN chmod 0755 /usr/local/bin/validate-container \
    && curl --version \
    && curl --version | grep -q 'HTTP2' \
    && curl --version | grep -q 'HTTP3' \
    && iperf3 --version \
    && tc -V

CMD ["tail", "-f", "/dev/null"]
