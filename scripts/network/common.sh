#!/bin/sh

# Biblioteca interna dos scripts de rede. Execute apply/reset/inspect em vez
# deste arquivo diretamente.

PROJECT_NAME='tcp-quic-performance-analysis'
MANAGED_HANDLE='1a2b:'
STATE_DIR='/run/tcp-quic-netem'
ALL_SERVICES='client-bench server-tcp server-http3'
CLIENT_IP='172.20.0.50'
TCP_SERVER_IP='172.20.0.10'
HTTP3_SERVER_IP='172.20.0.30'

NETWORK_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$NETWORK_SCRIPT_DIR/../.." && pwd)

# Git Bash/MSYS converte argumentos Linux como /run e /certs em caminhos do
# Windows antes de chamar docker.exe. Desativamos essa conversão apenas para o
# Docker e fornecemos o diretório do projeto já convertido para formato Windows.
if command -v cygpath >/dev/null 2>&1; then
    COMPOSE_PROJECT_ROOT=$(cygpath -w "$PROJECT_ROOT")
    USING_MSYS=1
else
    COMPOSE_PROJECT_ROOT=$PROJECT_ROOT
    USING_MSYS=0
fi

log() {
    printf '[INFO] %s\n' "$1"
}

ok() {
    printf '[OK] %s\n' "$1"
}

warn() {
    printf '[AVISO] %s\n' "$1" >&2
}

die() {
    printf '[ERRO] %s\n' "$1" >&2
    exit 1
}

compose() {
    if [ "$USING_MSYS" -eq 1 ]; then
        MSYS_NO_PATHCONV=1 docker compose --project-directory "$COMPOSE_PROJECT_ROOT" "$@"
    else
        docker compose --project-directory "$COMPOSE_PROJECT_ROOT" "$@"
    fi
}

require_docker() {
    command -v docker >/dev/null 2>&1 || die 'docker não está disponível no PATH.'
    docker info >/dev/null 2>&1 || die 'o daemon Docker está indisponível.'
    compose config --quiet || die 'a configuração do Docker Compose é inválida.'
}

validate_service_name() {
    case "$1" in
        client-bench|server-tcp|server-http3) return 0 ;;
        *) die "serviço inválido: $1" ;;
    esac
}

container_id_for_service() {
    validate_service_name "$1"
    compose ps -q "$1"
}

assert_service_owned_and_running() {
    service=$1
    container_id=$(container_id_for_service "$service")
    [ -n "$container_id" ] || die "o serviço $service não possui contêiner criado."

    owner=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container_id") || \
        die "não foi possível inspecionar $service."
    label_service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id") || \
        die "não foi possível confirmar o label de $service."
    running=$(docker inspect --format '{{.State.Running}}' "$container_id") || \
        die "não foi possível consultar o estado de $service."

    [ "$owner" = "$PROJECT_NAME" ] || \
        die "$service pertence ao projeto '$owner', não a '$PROJECT_NAME'."
    [ "$label_service" = "$service" ] || \
        die "o contêiner consultado possui service label '$label_service', esperado '$service'."
    [ "$running" = 'true' ] || die "o serviço $service não está em execução."

    compose exec -T "$service" tc -V >/dev/null 2>&1 || \
        die "tc não está disponível ou NET_ADMIN é insuficiente em $service."
}

peer_ip_for_service() {
    case "$1" in
        client-bench) printf '%s\n' "$TCP_SERVER_IP" ;;
        server-tcp|server-http3) printf '%s\n' "$CLIENT_IP" ;;
        *) die "não há peer conhecido para $1" ;;
    esac
}

discover_interface() {
    service=$1
    peer_ip=$2
    route=$(compose exec -T "$service" ip -o -4 route get "$peer_ip") || \
        die "não foi possível obter a rota de $service para $peer_ip."
    interface=$(printf '%s\n' "$route" | sed -n 's/.* dev \([^ ][^ ]*\).*/\1/p' | head -n 1)
    [ -n "$interface" ] || die "não foi possível identificar a interface de $service para $peer_ip."
    case "$interface" in
        *[!A-Za-z0-9_.-]*) die "nome de interface inseguro ou inesperado: $interface" ;;
    esac
    printf '%s\n' "$interface"
}

root_qdisc() {
    service=$1
    interface=$2
    compose exec -T "$service" tc qdisc show dev "$interface" | sed -n '/ root /p'
}

qdisc_class() {
    line=$1
    case "$line" in
        '') printf '%s\n' 'default' ;;
        'qdisc noqueue 0:'*) printf '%s\n' 'default' ;;
        "qdisc netem $MANAGED_HANDLE "*) printf '%s\n' 'managed' ;;
        *) printf '%s\n' 'unknown' ;;
    esac
}

write_profile_marker() {
    service=$1
    interface=$2
    profile=$3
    compose exec -T "$service" sh -c \
        'mkdir -p "$1" && printf "%s\n" "$2" > "$1/$3.profile"' \
        sh "$STATE_DIR" "$profile" "$interface"
}

read_profile_marker() {
    service=$1
    interface=$2
    compose exec -T "$service" sh -c \
        'if [ -f "$1/$2.profile" ]; then cat "$1/$2.profile"; else printf "%s\n" none; fi' \
        sh "$STATE_DIR" "$interface"
}

remove_profile_marker() {
    service=$1
    interface=$2
    compose exec -T "$service" sh -c \
        'if [ -e "$1/$2.profile" ]; then rm -f "$1/$2.profile"; fi' \
        sh "$STATE_DIR" "$interface"
}

assert_qdisc_safe_to_manage() {
    service=$1
    interface=$2
    line=$(root_qdisc "$service" "$interface") || die "falha ao consultar qdisc de $service/$interface."
    class=$(qdisc_class "$line")
    [ "$class" != 'unknown' ] || \
        die "qdisc desconhecida em $service/$interface; preservada sem alteração: $line"
}

reset_managed_service() {
    service=$1
    interface=$2
    line=$(root_qdisc "$service" "$interface") || return 1
    class=$(qdisc_class "$line")

    case "$class" in
        managed)
            log "Removendo qdisc gerenciada de $service/$interface..."
            compose exec -T "$service" tc qdisc del dev "$interface" root handle "$MANAGED_HANDLE" || return 1
            remove_profile_marker "$service" "$interface" || return 1
            ;;
        default)
            remove_profile_marker "$service" "$interface" || return 1
            ;;
        unknown)
            warn "qdisc desconhecida em $service/$interface; nenhuma alteração realizada: $line"
            return 2
            ;;
    esac

    final_line=$(root_qdisc "$service" "$interface") || return 1
    final_class=$(qdisc_class "$final_line")
    [ "$final_class" = 'default' ] || {
        warn "estado inesperado após limpeza de $service/$interface: $final_line"
        return 1
    }
    ok "$service/$interface sem perfil experimental gerenciado."
}

inspect_service() {
    service=$1
    peer_ip=$(peer_ip_for_service "$service")
    assert_service_owned_and_running "$service"
    interface=$(discover_interface "$service" "$peer_ip")
    container_id=$(container_id_for_service "$service")
    container_name=$(docker inspect --format '{{.Name}}' "$container_id" | sed 's#^/##')
    marker=$(read_profile_marker "$service" "$interface")
    line=$(root_qdisc "$service" "$interface")
    class=$(qdisc_class "$line")

    printf 'service=%s\ncontainer=%s\ninterface=%s\npeer_ip=%s\nownership=%s\nactive_profile=%s\n' \
        "$service" "$container_name" "$interface" "$peer_ip" "$class" "$marker"
    compose exec -T "$service" tc -s qdisc show dev "$interface"
}
