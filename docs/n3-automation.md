# Automação integrada N3 — E01 a E06

## Objetivo

`run_n3.sh` é o ponto único para auditar, consolidar ou executar os seis experimentos da avaliação. O comportamento padrão é seguro e offline: valida os lotes históricos selecionados, recalcula as estatísticas e cria uma integração nova, sem Docker, `iperf3`, curl ou alterações em `tc`.

Novos benchmarks só são iniciados quando o operador fornece explicitamente `--execute`.

## Arquitetura

```text
run_n3.sh
└── scripts/experiments/run_n3.sh
    └── src/automation/run_n3.py
        ├── src/automation/audit_n3.py
        │   ├── validadores de block_a.py
        │   └── validadores de block_b.py
        ├── scripts/experiments/run_block_a.sh  (--execute)
        ├── scripts/experiments/run_block_b.sh  (--execute)
        └── scripts/network/reset_profile.sh   (--execute)
```

O orquestrador não reimplementa os benchmarks. O Bloco A continua responsável por E01–E03 e o Bloco B por E04–E06, incluindo sua ordem interna, retries, evidências, criação de lotes e limpeza local.

## Modos de operação

### Verificação offline — padrão

Os dois comandos são equivalentes:

```bash
sh run_n3.sh
sh run_n3.sh --verify-existing
```

O modo:

- usa os IDs oficiais por padrão;
- não consulta Docker nem valida a infraestrutura ao vivo;
- não aplica ou remove qdiscs;
- não executa `iperf3` ou curl;
- não modifica os lotes selecionados;
- cria somente um novo diretório em `results/integrated/`.

Isso permite entregar e auditar os resultados em outro computador que tenha Python 3 e os arquivos históricos, mesmo sem os contêineres.

### Execução futura

```bash
sh run_n3.sh --execute
```

O fluxo futuro é:

1. validar a infraestrutura;
2. limpar somente qdiscs gerenciadas;
3. chamar `run_block_a.sh` sem alterar sua lógica;
4. identificar e auditar o novo lote completo A;
5. limpar as qdiscs;
6. chamar `run_block_b.sh` sem alterar sua lógica;
7. identificar e auditar o novo lote completo B;
8. limpar as qdiscs;
9. consolidar os dois lotes novos;
10. liberar todos os locks.

O modo nunca reutiliza IDs antigos e exige que cada runner produza exatamente um lote novo e completo. Nesta etapa ele foi validado por testes e simulações controladas; os 30 benchmarks não foram repetidos.

## Seleção de lotes

Os padrões são:

```text
Bloco A: block_a_20261118T195827Z_1ec19bbc
Bloco B: block_b_20261118T235312Z_e440c163
```

Outro par completo pode ser escolhido explicitamente:

```bash
sh run_n3.sh --verify-existing \
  --block-a-batch block_a_<id> \
  --block-b-batch block_b_<id>
```

O programa não escolhe automaticamente “o mais recente”. Um ID inexistente, incompleto ou com número incorreto de amostras causa falha.

## Exclusão mútua sem deadlock

O executor cria `results/.n3.lock` com um token aleatório. Depois de adquirir esse lock, verifica se `.block_a.lock` ou `.block_b.lock` já existiam. Assim, uma execução integrada não começa sobre um runner individual ativo.

Os runners A e B continuam adquirindo e removendo seus próprios locks. A única integração adicionada é a consulta ao lock N3:

- uma chamada individual é recusada enquanto `.n3.lock` existir;
- o processo filho iniciado pelo orquestrador recebe `N3_ORCHESTRATOR_TOKEN`;
- somente o token que coincide com o conteúdo do lock global autoriza o filho;
- o filho ainda precisa adquirir seus locks normais.

O orquestrador não adquire os locks dos blocos em nome dos filhos; por isso não ocorre o deadlock “pai possui lock, filho aguarda o mesmo lock”. Locks residuais nunca são removidos silenciosamente.

## Auditoria do Bloco A

Para E01–E03 são verificados:

- manifesto completo e associado ao lote solicitado;
- cinco repetições únicas por experimento;
- JSON real do iperf3 e duração compatível com 15 segundos;
- vazão de transmissor/receptor e retransmissões;
- CUBIC em E01/E02 e BBR em E03;
- algoritmo do socket de dados na captura `ss`;
- perfil `lan` ou `wan-degraded` na evidência qdisc;
- stderr vazio, metadados válidos e limpeza bem-sucedida;
- igualdade entre CSV global e CSV específico do lote;
- estatísticas recalculadas iguais ao resumo histórico.

## Auditoria do Bloco B

Para E04–E06 são verificados:

- manifesto completo e cinco repetições únicas;
- protocolo sem fallback, HTTP 200 e 1024 bytes;
- nova conexão e TTFB numérico;
- perfil `wan-no-loss` nas duas qdiscs;
- E04 como `fresh_tcp_tls` e HTTP/2;
- E05 como `fresh_quic_cold`, sem resumption ou early data;
- E06 com JSON de preparação, portas diferentes, ticket não vazio registrado nos metadados, trace de session resumption, bytes de early data e header `accepted` do Caddy;
- igualdade entre CSV global, CSV do lote e resumo histórico.

Uma string isolada não basta para E06: JSON de preparação, JSON medido, trace, headers, metadados e qdisc precisam existir e ser consistentes.

## Métricas e estatística

O CSV integrado possui uma linha por amostra e mantém unidades separadas:

- E01–E03: `throughput_sender`, Mbps;
- E04–E06: `ttfb`, ms.

O resumo recalcula média, desvio-padrão amostral, mínimo e máximo. E01–E03 também incluem retransmissões totais. As comparações descritivas dos blocos são recalculadas e comparadas com os resumos originais, sem tratar Mbps e ms como grandezas comparáveis.

## Organização dos resultados integrados

Cada validação bem-sucedida cria um ID único:

```text
results/integrated/<integration_id>/
├── manifest.json
├── measurements.csv
├── summary.json
└── audit.json
```

O lote validado nesta etapa é:

```text
n3_verify_existing_20261119T005601Z_e6f56660
```

### `manifest.json`

Registra modo, horário, commit, IDs e manifestos dos blocos, versão SHA-256 da automação, 30 amostras, status e hashes SHA-256 dos 203 arquivos históricos analisados.

Os manifestos antigos possuem hashes da versão da automação usada na execução, mas não um catálogo de hashes de cada arquivo bruto. Esses valores são preservados como proveniência; não são comparados indevidamente com o código atual, que evoluiu no Tópico 6. A integração atual passa a formar uma linha de base por arquivo.

### `measurements.csv`

Contém 30 chaves únicas e campos comuns de rastreabilidade, além de algoritmo, retransmissões, modo de conexão, resumption e early data quando aplicáveis.

### `summary.json`

Contém os seis resumos recalculados, lotes de origem, fórmula de desvio-padrão e comparações descritivas internas de cada bloco.

### `audit.json`

Informa verificações, arquivos analisados, hashes computados, ausências, divergências, rejeições e a conclusão explícita sobre as 30 amostras.

## Preservação e hashes

Antes de escrever a integração, todos os arquivos de origem são lidos e hasheados. Imediatamente antes da publicação atômica do diretório, os hashes são recalculados. Qualquer mudança durante a auditoria interrompe a consolidação.

Nenhum arquivo dentro de `results/raw/block_a`, `results/raw/block_b` ou dos CSVs históricos é aberto para escrita. O código recusa diretório de integração já existente.

## Falhas, sinais e limpeza

No modo offline, não há estado Docker ou qdisc para limpar. O lock global é liberado em `finally`.

No modo `--execute`, a limpeza usa exclusivamente `scripts/network/reset_profile.sh --all`, que só remove o handle gerenciado pelo projeto e preserva qdiscs desconhecidas. Ela é solicitada entre blocos, ao final, em exceções e em interrupções. Falha de limpeza produz exit code não zero. Falhas de execução são registradas em `results/integrated/failed/` e não geram manifesto com status `complete`.

Não são usados `docker system prune`, `docker network prune`, `docker compose down -v` nem remoção da rede `net_bench`.

## Testes

```bash
python -m unittest discover -s tests -v
```

A suíte cobre os 28 testes anteriores e 27 testes integrados, totalizando 55. O modo de execução é simulado: ordem A→B, falha de runner, falha de limpeza, interrupção, token, concorrência e proteção contra execução implícita são verificados sem executar benchmarks.

## Reprodução em outro ambiente

Para auditoria offline:

1. copie o repositório com os lotes e CSVs históricos;
2. instale Python 3.10 ou superior;
3. execute `sh run_n3.sh --verify-existing` em Git Bash, WSL ou outro shell POSIX;
4. confira o novo diretório em `results/integrated/`.

Para novos experimentos, além disso são necessários Docker Desktop/Engine, Compose, os três serviços saudáveis, `NET_ADMIN`, BBR disponível por socket, curl HTTP/3 com `SSLS-EXPORT` e os scripts de rede aprovados.

## Limitações

- Os hashes por arquivo criados nesta integração são uma linha de base nova; os lotes antigos não continham referências equivalentes para comparação retrospectiva.
- `--verify-existing` prova os artefatos históricos, não a saúde atual do Docker.
- `--execute` valida o ambiente vivo e pode demorar vários minutos; deve ser usado conscientemente, pois cria 30 novas medições.
- A consolidação é descritiva. Análises inferenciais e gráficos pertencem ao Tópico 7.
