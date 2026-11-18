[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$failures = 0

function Pass([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Fail([string]$Message) {
    Write-Host "[FALHA] $Message" -ForegroundColor Red
    $script:failures++
}

Write-Host '=== Docker e Compose ==='
docker info
if ($LASTEXITCODE -eq 0) { Pass 'daemon Docker acessível' } else { Fail 'daemon Docker inacessível' }

docker compose config --quiet
if ($LASTEXITCODE -eq 0) { Pass 'configuração do Docker Compose válida' } else { Fail 'configuração do Docker Compose inválida' }

$expectedServices = @('server-tcp', 'server-http3', 'client-bench')
$runningServices = @(docker compose ps --services --filter 'status=running')
if ($LASTEXITCODE -ne 0) {
    Fail 'não foi possível consultar o estado dos serviços'
} else {
    foreach ($service in $expectedServices) {
        if ($runningServices -contains $service) { Pass "$service está em execução" } else { Fail "$service não está em execução" }
    }
}

Write-Host '=== Rede e endereços ==='
$subnet = (docker network inspect net_bench --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -eq 0 -and $subnet -eq '172.20.0.0/16') { Pass 'net_bench usa 172.20.0.0/16' } else { Fail "sub-rede inesperada ou inacessível: $subnet" }

$expectedIps = @{
    'server_tcp_cc' = '172.20.0.10'
    'server_http3_quic' = '172.20.0.30'
    'client_bench' = '172.20.0.50'
}

foreach ($container in $expectedIps.Keys) {
    $actualIp = (docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $container 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $actualIp -eq $expectedIps[$container]) {
        Pass "$container recebeu $actualIp"
    } else {
        Fail "$container deveria usar $($expectedIps[$container]), mas retornou '$actualIp'"
    }
}

Write-Host '=== Traffic Control ==='
foreach ($service in $expectedServices) {
    docker compose exec -T $service tc -V
    if ($LASTEXITCODE -eq 0) { Pass "tc disponível em $service" } else { Fail "tc indisponível em $service" }
}

Write-Host '=== Validações dentro do cliente ==='
docker compose exec -T client-bench /usr/local/bin/validate-container
if ($LASTEXITCODE -eq 0) { Pass 'validações internas passaram' } else { Fail 'uma ou mais validações internas falharam' }

if ($failures -gt 0) {
    Write-Host "[RESUMO] $failures grupo(s) de validação falharam." -ForegroundColor Red
    exit 1
}

Write-Host '[RESUMO] Infraestrutura validada com sucesso.' -ForegroundColor Green
exit 0
