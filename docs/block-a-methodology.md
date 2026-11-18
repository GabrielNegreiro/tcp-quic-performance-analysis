# Metodologia do Bloco A — TCP CUBIC vs. TCP BBR

## Matriz experimental

| Experimento | Algoritmo | Perfil | RTT alvo | Perda | Repetições |
| --- | --- | --- | ---: | ---: | ---: |
| E01 | CUBIC | `lan` | 1 ms | 0% | 5 |
| E02 | CUBIC | `wan-degraded` | 50 ms | 2% | 5 |
| E03 | BBR | `wan-degraded` | 50 ms | 2% | 5 |

Cada amostra é uma conexão TCP independente, de fluxo único, no sentido `client-bench` → `server-tcp`. O comando é `iperf3 -c 172.20.0.10 -t 15 -C <algoritmo> -J`, sem `-R`, `-P`, `-O`, `--fq-rate`, limite de banda ou UDP.

## Ordem e preparação

E01 é executado cinco vezes. Em um lote completo, E02 e E03 são alternados por repetição (`E02/1`, `E03/1`, …, `E02/5`, `E03/5`) para reduzir deriva temporal, sem alterar a preparação. Antes de cada tentativa, a automação:

1. confirma ausência de outro iperf3 no cliente;
2. remove somente qdiscs gerenciadas pelo projeto;
3. reaplica `lan` ou `wan-degraded` pelos scripts do Tópico 3;
4. captura e valida as qdiscs do cliente e servidor TCP;
5. aguarda exatamente dois segundos;
6. inicia uma nova conexão iperf3 de 15 segundos;
7. captura `ss -tinp` após dois segundos;
8. valida e salva o JSON;
9. limpa novamente as qdiscs gerenciadas.

Existe um lock exclusivo em `results/.block_a.lock`. O arquivo é removido no encerramento normal ou tratado pela rotina de interrupção. Um lock preexistente bloqueia novas medições para evitar concorrência.

## Perfil e hierarquia de filas

A automação reutiliza exclusivamente `scripts/network/apply_profile.sh`, `inspect_profile.sh` e `reset_profile.sh`. O handle gerenciado é `1a2b:` e a qdisc raiz permanece `netem`:

- LAN: `delay 500us` no cliente e servidor TCP;
- WAN degradada: `delay 25ms loss 2%` no cliente e `delay 25ms` no servidor TCP.

Não são adicionados `sch_fq`, `fq_codel`, TBF ou limites artificiais. E02 e E03 usam a mesma hierarquia e diferem intencionalmente apenas no algoritmo TCP.

## Confirmação do algoritmo

Durante cada amostra, `ss -tinp dst 172.20.0.10:5201` é capturado no cliente. Como o iperf3 usa conexões distintas de controle e dados, o parser escolhe o socket com maior `bytes_sent`, exige ao menos 1 MB já transmitido e confirma o primeiro identificador TCP da linha detalhada (`cubic` ou `bbr`). A evidência integral fica no arquivo `run_NN.ss.txt` associado à amostra.

Uma amostra sem confirmação do algoritmo é inválida. O envio de `-C` sozinho não é aceito como prova.

## Integridade e tentativas

Uma execução válida exige código zero, stderr vazio, JSON válido, TCP, um fluxo, direção normal, zero omissão, nenhuma taxa artificial, duração solicitada de 15 segundos, duração efetiva entre 14,5 e 16,5 segundos, bytes e vazões positivos, retransmissões presentes, algoritmo confirmado e qdisc correta.

São permitidas até três tentativas por repetição. Tentativas inválidas são preservadas em `attempts/` com erro e metadados; não entram nas cinco amostras finais. Arquivos existentes nunca são sobrescritos porque cada execução recebe um `batch_id` exclusivo.

## Dados e processamento

Cada diretório de lote contém:

- `run_NN.json`: saída bruta do iperf3;
- `run_NN.metadata.json`: metadados e métricas validadas;
- `run_NN.ss.txt`: prova do algoritmo no socket de dados;
- `run_NN.qdisc.txt`: estado do perfil antes da medição;
- `run_NN.profile-apply.txt`: saída da aplicação do perfil;
- `run_NN.stderr.txt`: stderr, vazio em uma execução válida.

O CSV consolidado registra lote, experimento, repetição, algoritmos solicitado/confirmado, perfil, RTT, perda, duração, bytes, vazão do transmissor/receptor em Mbps decimais, retransmissões, status, timestamp e caminhos das evidências. O arquivo global preserva lotes anteriores e rejeita chaves duplicadas; cada lote também possui seu próprio CSV.

A estatística preliminar usa a vazão do transmissor. A média é aritmética e o desvio-padrão é amostral: `s = sqrt(sum((x - média)^2) / (n - 1))`. Isso não constitui teste de significância.

## Lote oficial executado

O lote `block_a_20261118T195827Z_1ec19bbc`, executado em 18/11/2026, foi concluído com 15 amostras válidas:

| Experimento | Algoritmo | Cenário | Válidas | Média (Mbps) | Desvio-padrão amostral (Mbps) | Retransmissões totais |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| E01 | CUBIC | LAN | 5 | 24.685,563 | 67,537 | 0 |
| E02 | CUBIC | WAN degradada | 5 | 4,124 | 0,639 | 400 |
| E03 | BBR | WAN degradada | 5 | 291,017 | 8,920 | 37.832 |

A média de E02 foi 99,9833% inferior à de E01; a média de E03 foi 6.956,6922% superior à de E02. Esses percentuais descrevem somente o lote observado e não demonstram significância estatística.

Uma primeira tentativa de E02/3 foi interrompida e preservada como inválida porque a captura `ss` em dois segundos ainda não apresentava volume suficiente para distinguir com segurança o socket de dados. A limpeza terminou com código zero e a repetição válida foi obtida na tentativa seguinte. A tentativa inválida não integra o CSV nem os cálculos.

As durações efetivas ficaram entre 15,001204 e 15,001821 segundos. A auditoria independente reabriu os 15 JSONs, as 15 capturas `ss` e as 15 evidências de qdisc, confirmando cinco chaves únicas por experimento.

Resultados:

- manifesto: `results/raw/block_a/batches/block_a_20261118T195827Z_1ec19bbc.manifest.json`;
- CSV do lote: `results/processed/block_a/block_a_20261118T195827Z_1ec19bbc/block_a_measurements.csv`;
- resumo: `results/processed/block_a/block_a_20261118T195827Z_1ec19bbc/summary.json`;
- CSV consolidado: `results/processed/block_a_measurements.csv`.

## Execução

Somente pré-validação:

```bash
sh scripts/experiments/run_block_a.sh --preflight
```

Lote completo oficial:

```bash
sh scripts/experiments/run_block_a.sh
```

Experimento individual:

```bash
sh scripts/experiments/run_block_a.sh --experiment E01
```

Testes do parser:

```bash
python -m unittest discover -s tests -v
```

## Limitações

Docker Desktop/WSL2 compartilha um kernel virtualizado e a bridge não possui gargalo de banda explicitamente controlado. A perda `netem` é estocástica e pode variar entre conexões. Não se deve interpretar diferenças de médias como significância estatística sem a análise posterior. Os resultados são válidos para esta configuração, kernel, topologia e hierarquia de qdisc documentados.

