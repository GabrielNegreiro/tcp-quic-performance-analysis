#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/../network/common.sh"

DIAGNOSTICS_DIR="$PROJECT_ROOT/results/diagnostics/bbr"
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
output_file="$DIAGNOSTICS_DIR/${timestamp}-environment.txt"
run_probe=0

case "${1:-}" in
    '') ;;
    --probe) run_probe=1 ;;
    -h|--help)
        printf 'Uso: %s [--probe]\n' "$0"
        printf '%s\n' 'Sem opção, coleta somente leitura. --probe acrescenta um teste BBR de 1 segundo.'
        exit 0
        ;;
    *) die "opção inválida: $1" ;;
esac

cd "$PROJECT_ROOT" || die 'não foi possível acessar a raiz do projeto.'
require_docker
mkdir -p "$DIAGNOSTICS_DIR" || die "não foi possível criar $DIAGNOSTICS_DIR."

client_kernel=$(compose exec -T client-bench uname -r 2>&1 | tr -d '\r')
client_arch=$(compose exec -T client-bench uname -m 2>&1 | tr -d '\r')
docker_summary=$(docker info --format 'os={{.OperatingSystem}};kernel={{.KernelVersion}};arch={{.Architecture}};cpus={{.NCPU}}' 2>&1 | tr -d '\r')
docker_desktop_version='unavailable'
if command -v powershell.exe >/dev/null 2>&1; then
    docker_desktop_version=$(powershell.exe -NoProfile -Command \
        '(Get-Item -LiteralPath (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")).VersionInfo.ProductVersion' \
        2>/dev/null | tr -d '\r\000')
    [ -n "$docker_desktop_version" ] || docker_desktop_version='unavailable'
fi
available_cc=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_available_congestion_control 2>&1 | tr -d '\r')
default_cc=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_congestion_control 2>&1 | tr -d '\r')
module_state=$(compose exec -T client-bench sh -c \
    'if grep -q "^tcp_bbr " /proc/modules; then grep "^tcp_bbr " /proc/modules; else printf "%s\n" not_loaded; fi' \
    2>&1 | tr -d '\r')

probe_status='not_requested'
if [ "$run_probe" -eq 1 ]; then
    probe_file="$DIAGNOSTICS_DIR/${timestamp}-probe-bbr.json"
    probe_error="$DIAGNOSTICS_DIR/${timestamp}-probe-bbr.stderr.txt"
    if compose exec -T client-bench iperf3 -c "$TCP_SERVER_IP" -t 1 -C bbr -J \
        > "$probe_file" 2> "$probe_error"; then
        probe_status='passed'
    else
        probe_status='failed'
    fi
    available_cc=$(compose exec -T client-bench sysctl -n net.ipv4.tcp_available_congestion_control 2>&1 | tr -d '\r')
    module_state=$(compose exec -T client-bench sh -c \
        'if grep -q "^tcp_bbr " /proc/modules; then grep "^tcp_bbr " /proc/modules; else printf "%s\n" not_loaded; fi' \
        2>&1 | tr -d '\r')
fi

{
    printf 'diagnostic_type=bbr_environment\n'
    printf 'official_experiment=false\n'
    printf 'timestamp_utc=%s\n' "$timestamp"
    printf 'docker_backend=WSL2 (observed from Docker Desktop and shared Microsoft WSL2 kernel)\n'
    printf 'docker_desktop_version=%s\n' "$docker_desktop_version"
    printf 'docker_summary=%s\n' "$docker_summary"
    printf 'client_kernel=%s\n' "$client_kernel"
    printf 'client_architecture=%s\n' "$client_arch"
    printf 'available_congestion_control=%s\n' "$available_cc"
    printf 'default_congestion_control=%s\n' "$default_cc"
    printf 'tcp_bbr_module=%s\n' "$module_state"
    printf 'probe_requested=%s\n' "$run_probe"
    printf 'probe_status=%s\n' "$probe_status"
    printf '%s\n' 'recommended_solution=per-socket selection with iperf3 -C bbr; no global sysctl or explicit modprobe'
    printf '%s\n' '--- client_uname ---'
    compose exec -T client-bench uname -a 2>&1
    printf '%s\n' '--- client_os_release ---'
    compose exec -T client-bench cat /etc/os-release 2>&1
    printf '%s\n' '--- server_uname ---'
    compose exec -T server-tcp uname -a 2>&1
    printf '%s\n' '--- docker_version ---'
    docker version 2>&1
    printf '%s\n' '--- docker_info_subset ---'
    docker info --format '{{json .}}' 2>&1
    printf '%s\n' '--- tcp_sysctls ---'
    compose exec -T client-bench sh -c \
        'sysctl net.ipv4.tcp_available_congestion_control; sysctl net.ipv4.tcp_allowed_congestion_control; sysctl net.ipv4.tcp_congestion_control' 2>&1
    printf '%s\n' '--- kernel_config ---'
    compose exec -T client-bench sh -c \
        'if [ -r /proc/config.gz ]; then gzip -dc /proc/config.gz | grep -E "^(CONFIG_TCP_CONG_BBR|CONFIG_TCP_CONG_CUBIC|CONFIG_NET_SCH_FQ|CONFIG_NET_SCH_FQ_CODEL|CONFIG_DEFAULT_TCP_CONG|CONFIG_MODULES)="; else printf "%s\n" /proc/config.gz_unavailable; fi' 2>&1
    printf '%s\n' '--- module_evidence ---'
    compose exec -T client-bench sh -c \
        'grep "^tcp_bbr " /proc/modules || true; if [ -d /sys/module/tcp_bbr ]; then printf "%s\n" /sys/module/tcp_bbr_present; else printf "%s\n" /sys/module/tcp_bbr_absent; fi' 2>&1
    printf '%s\n' '--- qdisc_state ---'
    for service in $ALL_SERVICES; do
        peer_ip=$(peer_ip_for_service "$service")
        interface=$(discover_interface "$service" "$peer_ip")
        printf 'service=%s interface=%s\n' "$service" "$interface"
        compose exec -T "$service" tc qdisc show dev "$interface" 2>&1
    done
    printf '%s\n' '--- wsl_version_if_available ---'
    if command -v wsl.exe >/dev/null 2>&1; then
        # wsl.exe pode emitir rótulos na página de código do Windows. Mantemos
        # rótulos ASCII e números para que o diagnóstico continue UTF-8 válido.
        wsl.exe --version 2>&1 | tr -cd '\11\12\15\40-\176' | tr -d '\r'
    else
        printf '%s\n' 'wsl.exe_unavailable_from_this_shell'
    fi
    printf '%s\n' '--- ubuntu_wsl_if_available ---'
    if command -v wsl.exe >/dev/null 2>&1; then
        if [ "$USING_MSYS" -eq 1 ]; then
            MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- sh -lc \
                'uname -a; cat /etc/os-release; command -v modprobe || true; modinfo tcp_bbr 2>&1 || true; find "/lib/modules/$(uname -r)" -type f \( -name "tcp_bbr.ko*" -o -name "sch_fq.ko*" \) -print 2>/dev/null; if [ -r /proc/config.gz ]; then gzip -dc /proc/config.gz | grep -E "^(CONFIG_TCP_CONG_BBR|CONFIG_TCP_CONG_CUBIC|CONFIG_NET_SCH_FQ|CONFIG_NET_SCH_FQ_CODEL|CONFIG_DEFAULT_TCP_CONG|CONFIG_MODULES)="; fi' \
                2>&1 | tr -d '\r\000'
        else
            wsl.exe -d Ubuntu -- sh -lc \
                'uname -a; cat /etc/os-release; command -v modprobe || true; modinfo tcp_bbr 2>&1 || true; find "/lib/modules/$(uname -r)" -type f \( -name "tcp_bbr.ko*" -o -name "sch_fq.ko*" \) -print 2>/dev/null; if [ -r /proc/config.gz ]; then gzip -dc /proc/config.gz | grep -E "^(CONFIG_TCP_CONG_BBR|CONFIG_TCP_CONG_CUBIC|CONFIG_NET_SCH_FQ|CONFIG_NET_SCH_FQ_CODEL|CONFIG_DEFAULT_TCP_CONG|CONFIG_MODULES)="; fi' \
                2>&1 | tr -d '\r\000'
        fi
    else
        printf '%s\n' 'ubuntu_wsl_unavailable_from_this_shell'
    fi
} > "$output_file"

printf '[DIAGNÓSTICO] %s\n' "$output_file"
printf 'Kernel: %s (%s)\n' "$client_kernel" "$client_arch"
printf 'Algoritmos: %s; padrão: %s; probe: %s\n' "$available_cc" "$default_cc" "$probe_status"

