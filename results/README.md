# Organização dos resultados experimentais

Este diretório contém resultados reais e verificáveis. O Bloco A oficial foi executado no lote `block_a_20261118T195827Z_1ec19bbc`; o Bloco B ainda não foi executado. Fixtures sintéticas de parser existem somente em `tests/fixtures/`, nunca em `results/raw/`.

## Armazenamento

- `raw/block_a/e01_tcp_cubic_lan/`: E01 — TCP CUBIC em LAN.
- `raw/block_a/e02_tcp_cubic_wan_degraded/`: E02 — TCP CUBIC em WAN degradada.
- `raw/block_a/e03_tcp_bbr_wan_degraded/`: E03 — TCP BBR em WAN degradada.
- `raw/block_b/e04_http2/`: E04 — HTTP/2.
- `raw/block_b/e05_http3_cold/`: E05 — HTTP/3 Cold.
- `raw/block_b/e06_http3_warm/`: E06 — HTTP/3 Warm/Resumption.
- `raw/block_a/batches/`: manifestos de ambiente, ordem e configuração dos lotes.
- `processed/block_a/<batch_id>/`: CSV e resumo derivados de um lote específico.
- `processed/block_a_measurements.csv`: consolidação deduplicada, identificada por lote.
- `figures/`: gráficos produzidos a partir dos dados processados.
- `diagnostics/`: evidências de validação da infraestrutura e dos perfis de rede; não são amostras oficiais E01–E06.

Cada experimento possui um subdiretório por `batch_id`. Os arquivos `run_01.json` a `run_05.json` são saídas brutas do iperf3; arquivos `.metadata.json`, `.ss.txt`, `.qdisc.txt`, `.profile-apply.txt` e `.stderr.txt` preservam a verificação de cada amostra. Tentativas inválidas ficam em `attempts/` e não entram no CSV final.

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

## Campos previstos — Bloco B

| Campo | Descrição |
| --- | --- |
| `experiment_id` | Identificador E04, E05 ou E06 |
| `repetition` | Número inteiro da repetição |
| `protocol` | Protocolo medido, como HTTP/2 ou HTTP/3 |
| `connection_type` | Tipo de conexão, como referência, cold ou warm/resumption |
| `network_scenario` | Cenário de rede aplicado |
| `ttfb_ms` | Time to First Byte em milissegundos |
| `execution_status` | Estado final da execução/coleta |

Unidades devem permanecer explícitas nos nomes dos campos. Falhas devem ser registradas pelo status nas etapas futuras, sem substituir medições ausentes por valores inventados.

## Diagnósticos de rede

Os arquivos em `diagnostics/lan`, `diagnostics/wan-degraded` e `diagnostics/wan-no-loss` são gerados por `scripts/validation/validate-network.sh`. Eles registram configuração de qdisc, RTT e perda observados em verificações reais. Seu campo `official_experiment=false` impede que sejam confundidos com os dados brutos de E01–E06.

Os arquivos em `diagnostics/bbr` são gerados por `scripts/validation/diagnose-bbr.sh` e `scripts/validation/validate-bbr.sh`. Eles registram ambiente, JSONs curtos do iperf3 e capturas `ss` usadas para provar BBR/CUBIC no socket transmissor. Também são apenas diagnósticos, nunca amostras oficiais.

