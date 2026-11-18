# Organização dos resultados experimentais

Este diretório está preparado para receber apenas resultados reais das futuras execuções E01–E06. Nenhum dado fictício ou experimental foi gerado durante o Tópico 1.

## Armazenamento

- `raw/block_a/e01_tcp_cubic_lan/`: E01 — TCP CUBIC em LAN.
- `raw/block_a/e02_tcp_cubic_wan_degraded/`: E02 — TCP CUBIC em WAN degradada.
- `raw/block_a/e03_tcp_bbr_wan_degraded/`: E03 — TCP BBR em WAN degradada.
- `raw/block_b/e04_http2/`: E04 — HTTP/2.
- `raw/block_b/e05_http3_cold/`: E05 — HTTP/3 Cold.
- `raw/block_b/e06_http3_warm/`: E06 — HTTP/3 Warm/Resumption.
- `processed/`: tabelas limpas ou agregadas, sempre deriváveis dos dados brutos.
- `figures/`: gráficos produzidos a partir dos dados processados.
- `diagnostics/`: evidências de validação da infraestrutura e dos perfis de rede; não são amostras oficiais E01–E06.

Os nomes, formatos e metadados dos arquivos serão definidos junto à automação, sem misturar saídas dos diferentes experimentos.

## Campos previstos — Bloco A

| Campo | Descrição |
| --- | --- |
| `experiment_id` | Identificador E01, E02 ou E03 |
| `repetition` | Número inteiro da repetição |
| `tcp_algorithm` | Algoritmo TCP, como CUBIC ou BBR |
| `network_scenario` | Cenário de rede aplicado |
| `throughput_mbps` | Vazão medida em Mbps |
| `retransmissions` | Quantidade de retransmissões observada |
| `duration_seconds` | Duração efetiva da medição em segundos |
| `execution_status` | Estado final da execução/coleta |

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

