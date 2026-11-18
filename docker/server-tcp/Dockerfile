FROM alpine:3.23

RUN apk add --no-cache \
        iperf3 \
        iproute2 \
    && iperf3 --version \
    && tc -V

EXPOSE 5201/tcp

CMD ["iperf3", "-s", "-p", "5201"]
