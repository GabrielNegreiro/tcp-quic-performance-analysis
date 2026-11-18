# Análise Experimental de TCP, HTTP/3 e QUIC em Redes Degradadas

Projeto da Terceira Avaliação (N3) de Redes de Computadores II da UFPI. O objetivo é preparar e, nas etapas posteriores, executar uma análise reproduzível de:

- controle de congestionamento TCP CUBIC e TCP BBR; e
- handshake e retomada de sessão com HTTP/2, HTTP/3 e QUIC 0-RTT.

O Tópico 1 estruturou o repositório e o Tópico 2 implementou a infraestrutura Docker. Os testes de infraestrutura são diagnósticos curtos e não correspondem às medições oficiais E01–E06. Não são executadas análises estatísticas nem medições oficiais nesta etapa.

O Tópico 3 adiciona perfis reproduzíveis de rede com `tc netem`. As regras são aplicadas apenas quando um script de rede é executado e são removidas ao final da validação completa.

## Tecnologias previstas

- Windows 11, WSL2 Ubuntu e Docker Desktop;
- Docker e Docker Compose;
- Linux Traffic Control (`tc netem`);
- `iperf3` para medições TCP;
- `curl` com suporte aos protocolos necessários;
- Python 3 com pandas, NumPy e Matplotlib para processamento, estatística descritiva e gráficos.

## Arquitetura Docker

Os três serviços compartilham a bridge Docker `net_bench` (`172.20.0.0/16`) e possuem `NET_ADMIN`, necessário para a emulação implementada no Tópico 3. A concessão dessa capability não aplica nenhuma regra automaticamente.

| Serviço | Contêiner | IPv4 | Função principal |
| --- | --- | --- | --- |
| `server-tcp` | `server_tcp_cc` | `172.20.0.10` | Alpine 3.23, iperf3 na porta TCP 5201 e `tc` |
| `server-http3` | `server_http3_quic` | `172.20.0.30` | Caddy 2.11.9, TLS 1.3, HTTP/2 em TCP/443 e HTTP/3 em UDP/443 |
| `client-bench` | `client_bench` | `172.20.0.50` | curl 8.23.0 com HTTP/2+HTTP/3, iperf3, `tc`, DNS, ping, netcat e OpenSSL |

O curl do cliente é compilado com GnuTLS, nghttp2, ngtcp2 e nghttp3. A imagem oficial padrão do curl não foi usada como cliente final porque sua configuração comum não comprova suporte a HTTP/3. O Caddy usa a imagem oficial fixada em `caddy:2.11.9-alpine`, cujo binário inclui HTTP/3.

O arquivo `site/health.txt` possui exatamente 1024 bytes. No primeiro start, `server-http3` gera um certificado autoassinado e uma chave privada em `certs/`; ambos ficam fora do controle de versão. O certificado contém SAN para `server-http3`, `localhost`, `172.20.0.30` e `127.0.0.1`.

```text
client_bench (172.20.0.50)
  ├── TCP/5201 ─────────────> server_tcp_cc (172.20.0.10)
  ├── TLS 1.3 + HTTP/2 TCP/443 ─┐
  └── TLS 1.3 + HTTP/3 UDP/443 ─┴> server_http3_quic (172.20.0.30)
```

As portas 5201/TCP, 443/TCP e 443/UDP também são publicadas no host. Se alguma delas já estiver ocupada, encerre o processo conflitante antes de iniciar o Compose; não altere silenciosamente as portas usadas pelo laboratório.

## Construção e operação

Copiar `.env.example` para `.env` é opcional porque o Compose já possui os mesmos valores como defaults. Use `.env` apenas para configuração local e nunca o versione.

No PowerShell, a partir da raiz do projeto:

```powershell
docker info
docker network ls
docker compose config
docker compose up -d --build
docker compose ps
```

No Ubuntu/WSL2 ou Git Bash, os mesmos comandos podem ser usados. A primeira construção baixa as imagens e compila o curl com suporte a HTTP/3, portanto demora mais que as construções seguintes.

Para consultar logs e versões:

```powershell
docker compose logs server-http3
docker compose exec -T client-bench curl --version
docker compose exec -T client-bench iperf3 --version
docker compose exec -T client-bench tc -V
```

Para validar toda a infraestrutura no PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validation/validate-infrastructure.ps1
```

No WSL2/Git Bash:

```bash
sh scripts/validation/validate-infrastructure.sh
```

Os scripts verificam daemon, Compose, estado, sub-rede, IPs, DNS interno, porta 5201, um diagnóstico iperf3 de apenas 1 segundo, curl, HTTP/2 efetivo, HTTP/3-only efetivo, TLS, tamanho do objeto, `tc`, CUBIC e BBR. Como BBR é um módulo neste kernel, a validação tenta selecioná-lo por socket antes de consultar novamente a lista. Qualquer requisito não comprovado resulta em código de saída diferente de zero.

Testes manuais equivalentes, executados de dentro do cliente:

```powershell
docker compose exec -T client-bench curl --http2 --cacert /certs/server-http3.crt -w "`n%{http_code} %{http_version} %{size_download}`n" https://server-http3/health.txt
docker compose exec -T client-bench curl --http3-only --cacert /certs/server-http3.crt -w "`n%{http_code} %{http_version} %{size_download}`n" https://server-http3/health.txt
docker compose exec -T client-bench iperf3 -c server-tcp -t 1
docker compose exec -T client-bench sysctl net.ipv4.tcp_available_congestion_control
```

Para encerrar sem remover certificados ou imagens:

```powershell
docker compose down
```

Não use `docker compose down -v` ou apague certificados sem intenção; isso não é necessário para a operação normal.

## Emulação dos cenários de rede

Os perfis dividem o atraso igualmente entre o egress do cliente e o egress do servidor correspondente. Na WAN degradada, a perda de 2% é aplicada somente no cliente, no sentido dos dados iperf3. Isso produz o RTT-alvo sem aplicar perda duas vezes.

| Perfil | Par de serviços | Configuração por direção | RTT alvo |
| --- | --- | --- | --- |
| `lan` | cliente + servidor TCP | 500 µs + 500 µs, 0% | 1 ms |
| `wan-degraded` | cliente + servidor TCP | 25 ms + 25 ms; loss 2% no cliente | 50 ms |
| `wan-no-loss` | cliente + servidor HTTP/3 | 25 ms + 25 ms, 0% | 50 ms |

Comandos no Ubuntu/WSL2 ou Git Bash:

```bash
sh scripts/network/apply_profile.sh lan server-tcp
sh scripts/network/apply_profile.sh wan-degraded server-tcp
sh scripts/network/apply_profile.sh wan-no-loss server-http3
sh scripts/network/inspect_profile.sh --all
sh scripts/network/reset_profile.sh --all
sh scripts/validation/validate-network.sh
```

No PowerShell, prefixe com `wsl.exe --cd .`, por exemplo:

```powershell
wsl.exe --cd . sh scripts/validation/validate-network.sh
```

Consulte [scripts/network/README.md](scripts/network/README.md) para metodologia, propriedade de qdiscs, interpretação dos diagnósticos e recuperação de falhas.

### Resultado da validação dos perfis

A validação completa foi executada em 18 de novembro de 2026. Os valores abaixo são diagnósticos da infraestrutura, não amostras oficiais:

| Perfil | RTT alvo | RTT médio observado | Perda configurada | Perda observada | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| LAN | 1 ms | 1,207 ms | 0% | 0% em 20 pacotes | Aprovado |
| WAN degradada | 50 ms | 50,336 ms | 2% no egress do cliente | 3% em 100 pacotes na execução final | Aprovado |
| WAN sem perda | 50 ms | 50,292 ms | 0% | 0% em 20 pacotes | Aprovado; HTTP/3-only `200/3/1024` |

Execuções anteriores da WAN degradada, também registradas em `results/diagnostics`, observaram 0% e 2% em amostras de 100 pacotes. A variação de 0% a 3% é compatível com uma qdisc estocástica configurada em 2%; a configuração foi confirmada diretamente por `tc` em todas as execuções.

A reaplicação de LAN manteve uma única qdisc por interface, a troca LAN → WAN degradada substituiu o estado anterior e a limpeza final restaurou `noqueue` em todos os contêineres. Depois da limpeza, TCP/5201, HTTP/2 e HTTP/3-only foram novamente aprovados.

## Blocos experimentais

### Bloco A — Controle de congestionamento

| ID | Algoritmo | Cenário |
| --- | --- | --- |
| E01 | TCP CUBIC | LAN: RTT alvo de 1 ms e perda alvo de 0% |
| E02 | TCP CUBIC | WAN degradada: RTT alvo de 50 ms e perda alvo de 2% |
| E03 | TCP BBR | WAN degradada: RTT alvo de 50 ms e perda alvo de 2% |

As execuções TCP terão duração-alvo de 15 segundos e ao menos cinco repetições por experimento.

### Bloco B — Handshake e retomada de sessão

| ID | Protocolo/conexão | Objetivo |
| --- | --- | --- |
| E04 | HTTP/2 | Medir a conexão de referência |
| E05 | HTTP/3 Cold | Medir uma conexão sem sessão reutilizável |
| E06 | HTTP/3 Warm/Resumption | Medir retomada de sessão e, posteriormente, avaliar QUIC 0-RTT |

Os parâmetros acima são valores-alvo para etapas futuras; ainda não representam configurações de rede aplicadas.

## Estrutura do projeto

```text
.
├── docker-compose.yml       # Três serviços e a rede net_bench
├── requirements.txt         # Dependências futuras de análise
├── .env.example             # Valores-alvo e endereçamento planejado
├── docker/                  # Dockerfiles e configurações dos serviços
│   ├── client/              # curl HTTP/2+HTTP/3, iperf3 e diagnósticos
│   ├── server-tcp/          # iperf3 e tc
│   └── server-http3/        # Caddyfile, Caddy e geração de TLS
├── certs/                   # Certificado/chave gerados localmente e ignorados
├── site/                    # health.txt com exatamente 1024 bytes
├── scripts/
│   ├── network/             # Aplicação, inspeção e limpeza dos perfis netem
│   ├── experiments/         # Orquestração futura de E01–E06
│   └── validation/          # Validações da infraestrutura e dos perfis netem
├── src/
│   ├── automation/          # Código futuro de automação
│   ├── analysis/            # Processamento e visualização futuros
│   └── utils/               # Utilitários compartilhados futuros
├── results/
│   ├── README.md            # Esquema previsto dos dados
│   ├── raw/
│   │   ├── block_a/e01-e03/ # Saídas brutas de TCP por experimento
│   │   └── block_b/e04-e06/ # Saídas brutas de HTTP por experimento
│   ├── processed/           # Dados derivados reproduzíveis
│   └── figures/             # Figuras destinadas à entrega
├── reports/                 # Relatórios acadêmicos
├── docs/                    # Documentação complementar
└── tests/                   # Testes automatizados futuros
```

Diretórios ainda vazios possuem `.gitkeep` apenas para serem preservados pelo Git.

## Pré-requisitos

Para construir e validar a infraestrutura, espera-se:

- Windows 11 com WSL2 e uma distribuição Ubuntu funcional;
- Docker Desktop integrado ao WSL2;
- Docker Engine e Docker Compose v2 ou compatível;
- Python 3.10 ou superior;
- permissões administrativas controladas para os recursos de rede que serão usados posteriormente;
- acesso à internet durante a primeira construção das imagens.

`iperf3`, curl HTTP/3 e `tc` são fornecidos pelas imagens; não precisam existir no Windows. O suporte a BBR depende do kernel Linux compartilhado pelo Docker Desktop e é comprovado por seleção real do socket e captura com `ss` antes do Tópico 4.

## Verificação básica das ferramentas

No PowerShell:

```powershell
python --version
docker --version
docker compose version
wsl.exe --status
wsl.exe --list --verbose
```

No Ubuntu/WSL2, após confirmar que a distribuição está acessível:

```bash
python3 --version
docker --version
docker compose version
iperf3 --version
curl --version
tc -V
```

Esses comandos apenas consultam versões. Aplique `tc netem` somente pelos scripts documentados, para preservar propriedade, idempotência e limpeza.

### Estado observado e validação do Tópico 2

- terminal: Windows PowerShell 5.1 (não Git Bash e não uma sessão Ubuntu/WSL2);
- Python: 3.12.10 disponível pelo comando `python`;
- Python Launcher (`py`): presente, mas sem uma instalação registrada;
- Docker CLI: 29.4.3 disponível;
- Docker Compose: 5.1.3 disponível;
- OpenSSL do host: 3.2.4;
- WSL2: confirmado na versão 2, com as distribuições `docker-desktop` e `Ubuntu` em execução;
- Docker daemon: 29.4.3, Docker Desktop sobre o kernel `6.6.114.1-microsoft-standard-WSL2`;
- Docker Compose: 5.1.3;
- rede: já existia uma `net_bench` vazia, com `172.20.0.0/16`, criada pelo projeto anterior `segundotrabalhoderedes2`; ela foi preservada e reutilizada, motivo pelo qual o Compose emite um aviso de propriedade;
- contêineres: os três serviços estão em execução; ambos os servidores estão `healthy`;
- TCP: DNS interno, porta 5201 e diagnóstico iperf3 de um segundo aprovados;
- HTTP/2: aprovado com resultado `200 2 1024`;
- HTTP/3-only: aprovado com resultado `200 3 1024`, sem fallback;
- TLS: certificado local validado, SAN correto e TLS 1.3 exigido pelo Caddy;
- `tc`: disponível nos três contêineres; `tc qdisc show` confirmou apenas `noqueue`, sem `netem` aplicado;
- congestionamento: antes do primeiro uso, o kernel anunciava `reno cubic`; a configuração revelou `CONFIG_TCP_CONG_BBR=m`, e `iperf3 -C bbr` resolveu o módulo sob demanda. Depois disso, o kernel anunciou `reno cubic bbr`, mantendo CUBIC como padrão global;
- BBR real: uma conexão de dados ativa foi confirmada como `bbr` por `ss -tin`; o diagnóstico equivalente foi confirmado como `cubic`.

O aviso inicial de acesso a `C:\Users\negre\.docker\config.json` ocorreu apenas na sessão restrita; fora dela, o daemon e o contexto `desktop-linux` funcionaram. O diagnóstico e a resolução do BBR estão detalhados em [`docs/bbr-compatibility.md`](docs/bbr-compatibility.md).

Para repetir a comprovação sem executar E01–E06:

```bash
sh scripts/validation/diagnose-bbr.sh
sh scripts/validation/validate-bbr.sh
```

## Limites e próximos tópicos

1. O Tópico 2 só está integralmente validado quando o Compose e o script de validação terminarem com código zero. Em especial, HTTP/3 não pode ser inferido por fallback para HTTP/2.
2. Uma lista inicial sem `bbr` não basta para concluir ausência: execute `validate-bbr.sh`, que solicita BBR somente no socket e confirma o algoritmo ativo. O projeto não muda o algoritmo global nem carrega módulos explicitamente.
3. O Tópico 3 fornece aplicação, inspeção, limpeza idempotente e validação dos perfis LAN/WAN. Ao terminar uma validação, confirme que `inspect_profile.sh --all` mostra somente a qdisc padrão.
4. A retomada de sessão e 0-RTT permanece fora do escopo desta infraestrutura e será tratada somente no Tópico 5.
5. No Tópico 4, documente a hierarquia de qdisc/FQ antes das medições e mantenha E01–E03 no mesmo kernel e topologia. Somente depois dessas validações deverão ser automatizadas e executadas as repetições E01–E06.

