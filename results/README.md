# Organização dos resultados experimentais

Este diretório contém resultados reais e verificáveis. O Bloco A oficial foi executado no lote `block_a_20261118T195827Z_1ec19bbc`; o Bloco B oficial foi executado no lote `block_b_20261118T235312Z_e440c163`. Fixtures sintéticas de parser existem somente em `tests/fixtures/`, nunca em `results/raw/`.

## Armazenamento

- `raw/block_a/e01_tcp_cubic_lan/`: E01 — TCP CUBIC em LAN.
- `raw/block_a/e02_tcp_cubic_wan_degraded/`: E02 — TCP CUBIC em WAN degradada.
- `raw/block_a/e03_tcp_bbr_wan_degraded/`: E03 — TCP BBR em WAN degradada.
- `raw/block_b/e04_http2/`: E04 — HTTP/2.
- `raw/block_b/e05_http3_cold/`: E05 — HTTP/3 Cold.
- `raw/block_b/e06_http3_warm/`: E06 — HTTP/3 Warm/Resumption.
- `raw/block_a/batches/`: manifestos de ambiente, ordem e configuração dos lotes.
- `raw/block_b/batches/`: manifestos de ambiente, ordem, versões e método de prova do Bloco B.
- `processed/block_a/<batch_id>/`: CSV e resumo derivados de um lote específico.
- `processed/block_a_measurements.csv`: consolidação deduplicada, identificada por lote.
- `processed/block_b/<batch_id>/`: CSV, estatísticas e comparações derivadas do lote HTTP.
- `processed/block_b_measurements.csv`: consolidação deduplicada de E04–E06.
- `integrated/<integration_id>/`: manifesto, CSV unificado, resumo e auditoria independente de E01–E06.
- `analysis/analysis_<integration_id>/`: manifesto da análise, proveniência, parâmetros estatísticos e hashes das saídas.
- `figures/`: gráficos produzidos a partir dos dados processados.
- `diagnostics/`: evidências de validação da infraestrutura e dos perfis de rede; não são amostras oficiais E01–E06.

Cada experimento possui um subdiretório por `batch_id`. No Bloco A, `run_01.json` a `run_05.json` são saídas brutas do iperf3, acompanhadas por metadados, captura `ss`, qdisc, aplicação do perfil e stderr. No Bloco B, cada execução preserva JSON `%{json}` do curl, trace, headers, metadados, qdisc e aplicação do perfil; E06 também preserva a requisição preparatória. Tickets TLS permanecem somente no `/tmp` do cliente e não são copiados ao repositório. Tentativas inválidas ficam em `attempts/` e não entram no CSV final; o lote oficial não teve tentativas inválidas.

## Campos previstos — Bloco A

| Campo | Descrição |
| --- | --- |
| `experiment_id` | Identificador E01, E02 ou E03 |
| `repetition` | Número inteiro da repetição |
| `algorithm_requested` / `algorithm_confirmed` | Algoritmo solicitado e confirmado no socket de dados |
| `network_profile` | Perfil `lan` ou `wan-degraded` aplicado |
| `throughput_sender_mbps` | Vazão do transmissor em Mbps decimais |
| `throughput_receiver_mbps` | Vazão do receptor em Mbps decimais |
| `retransmissions` | Quantidade de retransmissões observada |
| `duration_seconds` | Duração efetiva da medição em segundos |
| `status` | Estado final da execução/coleta |
| `raw_json_path` | Referência ao JSON bruto correspondente |

## Campos — Bloco B

| Campo | Descrição |
| --- | --- |
| `experiment_id` | Identificador E04, E05 ou E06 |
| `repetition` | Número inteiro da repetição |
| `protocol_requested` / `protocol_confirmed` | Protocolo solicitado e comprovado, sem fallback |
| `connection_mode` | `fresh_tcp_tls`, `fresh_quic_cold` ou `fresh_quic_0rtt` |
| `session_resumed` | Estado explícito da retomada TLS |
| `early_data_status` / `tls_earlydata_bytes` | Aceitação e bytes de early data |
| `network_profile` | Perfil `wan-no-loss` comprovado nas duas qdiscs |
| `ttfb_ms` | Time to First Byte em milissegundos |
| `time_connect_ms` / `time_appconnect_ms` / `time_total_ms` | Tempos auxiliares do curl |
| `response_code` / `size_download_bytes` | Validação de HTTP 200 e objeto de 1024 bytes |
| `status` | Estado final da execução/coleta |

Unidades permanecem explícitas nos nomes dos campos. Falhas são preservadas e nunca substituídas por zero. Consulte `docs/block-b-methodology.md` para os critérios de prova de nova conexão, resumption e 0-RTT.

## Integração N3

A integração oficial do Tópico 6 é `integrated/n3_verify_existing_20261119T005601Z_e6f56660/`. Ela referencia explicitamente os lotes A e B oficiais, contém 30 amostras válidas e não modifica os arquivos de origem.

- `manifest.json`: proveniência, modo offline, commit, versão da automação e 203 hashes SHA-256.
- `measurements.csv`: 30 linhas; vazão em Mbps para E01–E03 e TTFB em ms para E04–E06.
- `summary.json`: estatísticas recalculadas e comparadas com os resumos dos blocos.
- `audit.json`: 203 arquivos analisados, nenhuma ausência, divergência ou rejeição e conclusão explícita de validade.

Cada nova verificação cria outro `integration_id`; diretórios anteriores nunca são sobrescritos. Consulte `docs/n3-automation.md` para seleção explícita de lotes e critérios completos.

## Análise estatística N3

A análise oficial do Tópico 7 deriva exclusivamente da integração `n3_verify_existing_20261119T005601Z_e6f56660`. A tabela consolidada está em `processed/n3_statistics.csv` e `.json`, com versão LaTeX; `processed/n3_comparisons.json` preserva diferenças, razões e percentuais sem arredondamento prematuro. Os dois gráficos estão em `figures/` nos formatos PNG 300 DPI e PDF vetorial. O manifesto correspondente registra os oito hashes de saída, `ddof=1`, as escalas e a quantidade de elementos gráficos usados nas barras de erro.

Execute `sh scripts/analysis/generate_n3_analysis.sh --integration <id>` para analisar explicitamente outra integração válida. Nomes alternativos recebem o ID como sufixo, e a automação recusa sobrescrita. Consulte `docs/n3-results-analysis.md` para a interpretação e as limitações.

## Diagnósticos de rede

Os arquivos em `diagnostics/lan`, `diagnostics/wan-degraded` e `diagnostics/wan-no-loss` são gerados por `scripts/validation/validate-network.sh`. Eles registram configuração de qdisc, RTT e perda observados em verificações reais. Seu campo `official_experiment=false` impede que sejam confundidos com os dados brutos de E01–E06.

Os arquivos em `diagnostics/bbr` são gerados por `scripts/validation/diagnose-bbr.sh` e `scripts/validation/validate-bbr.sh`. Eles registram ambiente, JSONs curtos do iperf3 e capturas `ss` usadas para provar BBR/CUBIC no socket transmissor. Também são apenas diagnósticos, nunca amostras oficiais.

