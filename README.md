# Análise Experimental de TCP, HTTP/3 e QUIC em Redes Degradadas

Projeto da Terceira Avaliação (N3) de Redes de Computadores II da UFPI. O objetivo é preparar e, nas etapas posteriores, executar uma análise reproduzível de:

- controle de congestionamento TCP CUBIC e TCP BBR; e
- handshake e retomada de sessão com HTTP/2, HTTP/3 e QUIC 0-RTT.

O Tópico 1 estruturou o repositório, o Tópico 2 implementou a infraestrutura Docker e o Tópico 3 adicionou os perfis reproduzíveis com `tc netem`. Os Tópicos 4 e 5 executaram os Blocos A e B, o Tópico 6 integrou e auditou as 30 medições e o Tópico 7 gerou a análise estatística e as figuras científicas. Os diagnósticos curtos permanecem separados dos resultados oficiais.

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

O curl do cliente é compilado com GnuTLS, nghttp2, ngtcp2 e nghttp3. Para o Bloco B, o build também habilita e valida `SSLS-EXPORT`, necessário para persistir tickets TLS entre processos curl e testar retomada/0-RTT. A imagem oficial padrão do curl não foi usada como cliente final porque sua configuração comum não comprova suporte a HTTP/3. O Caddy usa a imagem oficial fixada em `caddy:2.11.9-alpine`, cujo binário inclui HTTP/3.

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

O lote oficial `block_a_20261118T195827Z_1ec19bbc` contém cinco execuções válidas de 15 segundos por experimento. E02 e E03 foram alternados por repetição, sempre com reaplicação do mesmo perfil `wan-degraded`.

| Experimento | Amostras | Vazão média (Mbps) | Desvio-padrão amostral (Mbps) | Retransmissões |
| --- | ---: | ---: | ---: | ---: |
| E01 | 5 | 24.685,563 | 67,537 | 0 |
| E02 | 5 | 4,124 | 0,639 | 400 |
| E03 | 5 | 291,017 | 8,920 | 37.832 |

E02 ficou 99,9833% abaixo de E01. E03 ficou 6.956,6922% acima de E02 nas condições observadas. Essas diferenças são descritivas, não testes de significância, e devem ser interpretadas considerando a bridge Docker/WSL2, a ausência de gargalo de banda explícito e a perda estocástica.

Para pré-validar ou executar um novo lote sem sobrescrever o existente:

```bash
sh scripts/experiments/run_block_a.sh --preflight
sh scripts/experiments/run_block_a.sh
```

A metodologia completa está em [`docs/block-a-methodology.md`](docs/block-a-methodology.md), o CSV consolidado em [`results/processed/block_a_measurements.csv`](results/processed/block_a_measurements.csv) e os JSONs em `results/raw/block_a/`.

### Bloco B — Handshake e retomada de sessão

| ID | Protocolo/conexão | Objetivo |
| --- | --- | --- |
| E04 | HTTP/2 | Medir a conexão de referência |
| E05 | HTTP/3 Cold | Medir uma conexão sem sessão reutilizável |
| E06 | HTTP/3 Resumption + 0-RTT | Medir nova conexão QUIC com sessão retomada e early data aceita |

O lote oficial `block_b_20261118T235312Z_e440c163` contém cinco amostras válidas por experimento, alternadas por rodada, sob `wan-no-loss`. Todas as amostras foram aprovadas na primeira tentativa.

| Experimento | Amostras | TTFB médio (ms) | Desvio-padrão amostral (ms) | Modalidade confirmada |
| --- | ---: | ---: | ---: | --- |
| E04 | 5 | 158,9658 | 0,3345 | nova conexão HTTP/2 + TLS 1.3 |
| E05 | 5 | 109,4652 | 1,3737 | nova conexão HTTP/3 Cold |
| E06 | 5 | 56,6664 | 0,5518 | nova conexão HTTP/3, resumption e 0-RTT aceito |

As cinco E06 usaram processos e portas locais distintos entre preparação e medição, tiveram a retomada confirmada no trace do curl, registraram 84 bytes de early data e receberam `X-Lab-QUIC-Early-Data: accepted` do matcher TLS do Caddy. E05 ficou 49,5006 ms (31,1392%) abaixo de E04; E06 ficou 52,7988 ms (48,2334%) abaixo de E05. São comparações descritivas deste ambiente, não testes de significância.

Para pré-validar ou executar um novo lote sem sobrescrever o existente:

```bash
sh scripts/experiments/run_block_b.sh --preflight
sh scripts/experiments/run_block_b.sh
sh scripts/experiments/run_block_b.sh --experiment E06
```

A metodologia e as evidências estão em [`docs/block-b-methodology.md`](docs/block-b-methodology.md), e o CSV consolidado está em [`results/processed/block_b_measurements.csv`](results/processed/block_b_measurements.csv).

### Automação integrada N3 — E01–E06

O ponto de entrada exigido para os seis experimentos é `run_n3.sh`. Sem argumentos, ele somente audita e consolida os lotes oficiais existentes; não consulta Docker e não repete benchmarks:

```bash
sh run_n3.sh
sh run_n3.sh --verify-existing
```

Novas medições exigem autorização explícita e reutilizam integralmente os runners dos Blocos A e B:

```bash
sh run_n3.sh --execute
```

O modo `--execute` não foi usado no Tópico 6. A validação offline oficial gerou `results/integrated/n3_verify_existing_20261119T005601Z_e6f56660/`, com 30 amostras, seis experimentos, 203 arquivos históricos analisados e hasheados, nenhuma ausência ou inconsistência e auditoria aprovada.

### Análise estatística e figuras — Tópico 7

A análise oficial é reproduzida offline por:

```bash
sh scripts/analysis/generate_n3_analysis.sh
```

O módulo valida novamente os 203 hashes, exige cinco amostras válidas por experimento, recalcula média, desvio-padrão amostral (`ddof=1`), mínimo, máximo, mediana e coeficiente de variação e recusa sobrescrever artefatos existentes. Os resultados estão em [`results/processed/n3_statistics.csv`](results/processed/n3_statistics.csv), as figuras de 300 DPI e PDFs vetoriais em [`results/figures/`](results/figures/) e a discussão em [`docs/n3-results-analysis.md`](docs/n3-results-analysis.md). Nenhum benchmark é executado por essa automação.

Lotes alternativos nunca são escolhidos implicitamente:

```bash
sh run_n3.sh --verify-existing \
  --block-a-batch block_a_<id> \
  --block-b-batch block_b_<id>
```

A arquitetura, a coordenação de locks, a auditoria e a recuperação segura estão documentadas em [`docs/n3-automation.md`](docs/n3-automation.md).

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
│   ├── experiments/         # Orquestrador oficial do Bloco A
│   └── validation/          # Validações da infraestrutura e dos perfis netem
├── src/
│   ├── automation/          # Execução, parser, integridade e CSV do Bloco A
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
4. O Bloco A foi concluído mantendo `netem` como qdisc raiz e sem adicionar `sch_fq`; novos lotes devem preservar essa decisão para serem comparáveis.
5. O Tópico 6 integrou E01–E06 em uma interface única, auditou offline as 30 amostras e criou uma linha de base SHA-256 dos artefatos históricos sem repetir benchmarks. O próximo passo é o Tópico 7, dedicado às análises e visualizações finais.

