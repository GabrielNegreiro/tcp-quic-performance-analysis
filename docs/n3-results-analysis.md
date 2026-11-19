# Análise estatística dos resultados N3 — E01 a E06

## 1. Introdução aos resultados

Esta análise consolida os 30 resultados oficiais da terceira avaliação de Redes de Computadores II da UFPI. A fonte exclusiva é a integração `n3_verify_existing_20261119T005601Z_e6f56660`, formada pelos lotes `block_a_20261118T195827Z_1ec19bbc` e `block_b_20261118T235312Z_e440c163`. O manifesto, a auditoria e os 203 hashes SHA-256 de origem foram verificados antes dos cálculos. Não foram executados Docker, iperf3, curl ou novos benchmarks.

O Bloco A compara TCP CUBIC e TCP BBR por vazão do transmissor. O Bloco B compara HTTP/2 sobre TCP/TLS, HTTP/3 em conexão nova e HTTP/3 com retomada e 0-RTT aceito por TTFB. Os resultados são descrições deste ambiente experimental; não constituem, isoladamente, teste de significância nem demonstração de superioridade universal de um algoritmo ou protocolo.

Artefatos principais:

- [tabela CSV](../results/processed/n3_statistics.csv), [JSON](../results/processed/n3_statistics.json) e [LaTeX](../results/processed/n3_statistics.tex);
- [comparações quantitativas](../results/processed/n3_comparisons.json);
- [Gráfico 1 em PNG](../results/figures/graph_01_tcp_throughput.png) e [PDF vetorial](../results/figures/graph_01_tcp_throughput.pdf);
- [Gráfico 2 em PNG](../results/figures/graph_02_http_ttfb.png) e [PDF vetorial](../results/figures/graph_02_http_ttfb.pdf);
- [manifesto da análise](../results/analysis/analysis_n3_verify_existing_20261119T005601Z_e6f56660/manifest.json).

## 2. Metodologia estatística

Foram aceitas exatamente cinco amostras válidas e distintas por experimento. A validação rejeita identificador inesperado, duplicata, valor ausente, não finito ou não positivo, unidade incorreta e repetição fora de 1–5. Mbps e milissegundos permanecem separados.

Para cada conjunto \(x_1,\ldots,x_n\), com \(n=5\), foram recalculados média aritmética, mínimo, máximo, mediana e coeficiente de variação. O desvio-padrão é amostral:

```text
média = soma(xi) / n
s = sqrt(soma((xi - média)²) / (n - 1))
CV = 100 × s / média
```

Os cálculos usam precisão completa e `ddof=1`. O arredondamento existe somente na apresentação. As barras de erro de ambos os gráficos são o desvio-padrão amostral, não erro-padrão nem intervalo de confiança.

## 3. Resultados do Bloco A

| ID | Algoritmo e cenário | N | Média (Mbps) | Desvio-padrão (Mbps) | Mediana (Mbps) | CV | Retransmissões |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| E01 | CUBIC; LAN; RTT 1 ms; perda 0% | 5 | 24.685,5634 | 67,5367 | 24.687,7929 | 0,2736% | 0 |
| E02 | CUBIC; WAN; RTT 50 ms; perda 2% | 5 | 4,1240 | 0,6387 | 4,2637 | 15,4875% | 400 |
| E03 | BBR; WAN; RTT 50 ms; perda 2% | 5 | 291,0168 | 8,9203 | 291,8997 | 3,0652% | 37.832 |

O [Gráfico 1](../results/figures/graph_01_tcp_throughput.png) usa dois painéis. O primeiro mantém E01–E03 em eixo logarítmico, explicitamente rotulado, para representar a diferença de aproximadamente quatro ordens de grandeza. O segundo mostra E02 e E03 em escala linear e com origem zero para tornar a comparação WAN legível. Todos os pontos aparecem com barras de erro de um desvio-padrão amostral.

### Comparações quantitativas

- E01 → E02: redução absoluta de 24.681,4394 Mbps, ou 99,9833%; a média de E01 é 5.985,85 vezes a de E02; E02 registrou 400 retransmissões a mais.
- E02 → E03: ganho absoluto de 286,8928 Mbps; E03 ficou 6.956,6922% acima de E02 e a razão BBR/CUBIC foi 70,5669.
- A variabilidade relativa foi maior em E02 (CV 15,4875%) que em E03 (CV 3,0652%).

## 4. Discussão do Bloco A

### E01 e E02: CUBIC sob condições diferentes

CUBIC é um controle baseado em perdas. Após um evento de congestionamento, reduz multiplicativamente `cwnd`, registra a janela anterior e volta a aumentá-la segundo uma função cúbica. A WAN de E02 combina RTT 50 vezes maior e perda aleatória de 2%, enquanto E01 usa RTT de 1 ms e perda zero. O RTT maior alonga cada ciclo de realimentação; perdas repetidas provocam recuperação e novas reduções de janela. A diferença observada é compatível com esses mecanismos, mas o desenho altera latência e perda simultaneamente e, por isso, não permite decompor causalmente quanto da redução pertence a cada fator.

O Bandwidth-Delay Product (BDP) é o volume necessário em trânsito para ocupar um caminho, aproximadamente largura de banda × RTT. Quanto maior o BDP, maior precisa ser a quantidade de dados não confirmados para sustentar a capacidade disponível. Uma `cwnd` pequena após perdas pode deixar esse caminho subutilizado enquanto o algoritmo a reconstrói.

### E02 e E03: CUBIC e BBR no mesmo perfil WAN

E02 e E03 usam a mesma configuração de RTT e perda e diferem intencionalmente no algoritmo. O BBR estima a largura de banda do gargalo e o RTT mínimo, usando essas estimativas para modelar o ponto de operação, controlar o volume em voo e aplicar pacing. Diferentemente do CUBIC, sua resposta não depende de tratar toda perda como o sinal primário para reduzir a taxa. Isso oferece uma explicação mecanística plausível para a razão de 70,57 observada sob perda estocástica neste laboratório. É uma interpretação consistente com os dados e a teoria, não prova de que BBR será melhor em outras filas, gargalos, versões ou padrões de tráfego.

### Retransmissões

Os totais absolutos não são taxas de perda. Durante os cinco testes, E02 enviou 38.666.240 bytes e E03 enviou 2.728.525.824 bytes. Como complemento descritivo, os totais correspondem a 10.344,94 retransmissões por GB decimal em E02 e 13.865,36 por GB em E03. Mesmo essa normalização não é uma taxa real de perda: o contador do iperf3 representa retransmissões TCP, não bytes únicos perdidos; um mesmo segmento pode ser retransmitido mais de uma vez, e não há aqui um denominador de segmentos originais comparável. Além disso, a vazão muito maior de E03 expôs muito mais dados à perda durante a duração fixa.

## 5. Resultados do Bloco B

| ID | Protocolo e modalidade | N | Média (ms) | Desvio-padrão (ms) | Mediana (ms) | CV | Retransmissões |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| E04 | HTTP/2 sobre TCP/TLS | 5 | 158,9658 | 0,3345 | 159,0560 | 0,2104% | não aplicável |
| E05 | HTTP/3 Cold | 5 | 109,4652 | 1,3737 | 109,1660 | 1,2549% | não aplicável |
| E06 | HTTP/3 Resumption + 0-RTT | 5 | 56,6664 | 0,5518 | 56,7980 | 0,9737% | não aplicável |

O [Gráfico 2](../results/figures/graph_02_http_ttfb.png) usa barras com origem zero, padrões além das cores e desvios-padrão amostrais. A métrica é exclusivamente TTFB em milissegundos.

Comparações recalculadas:

- E04 → E05: redução de 49,5006 ms (31,1392%); razão E04/E05 de 1,4522.
- E04 → E06: redução de 102,2994 ms (64,3531%); razão E04/E06 de 2,8053.
- E05 → E06: redução de 52,7988 ms (48,2334%); razão E05/E06 de 1,9317.

## 6. Discussão do Bloco B

HTTP/2, neste ensaio, estabelece TCP e então TLS 1.3 antes de atender a requisição. QUIC integra a negociação de parâmetros de transporte ao handshake criptográfico TLS e carrega HTTP/3 sobre UDP. Essa integração permite que uma conexão QUIC nova envie dados de aplicação após o handshake de 1-RTT, reduzindo etapas sequenciais em comparação com a composição TCP + TLS observada em E04. A diferença E04/E05 é compatível com a economia de aproximadamente um RTT no caminho configurado, mas também incorpora as implementações e a topologia específicas.

Na retomada, um ticket obtido em conexão TLS anterior permite derivar uma PSK para a nova conexão. Com 0-RTT, o cliente pode incluir early data no primeiro flight, antes de concluir o novo handshake. Isso explica por que E06 pode reduzir a espera em relação a E05. Contudo, uma diferença próxima de 50 ms, sozinha, não prova 0-RTT.

A evidência do mecanismo vem da metodologia preservada: preparação e medição usaram processos curl distintos e novas portas UDP; todas as cinco amostras registraram retomada no trace, 84 bytes de early data e o cabeçalho `X-Lab-QUIC-Early-Data: accepted` produzido pelo matcher TLS do Caddy. O GET era idempotente e local, escolha importante diante do risco de replay de dados 0-RTT. Detalhes estão em [Metodologia do Bloco B](block-b-methodology.md).

## 7. Limitações

- Há somente cinco repetições por experimento. Barras de desvio-padrão descrevem dispersão; não são intervalos de confiança nem teste de significância.
- O ambiente usa Docker Desktop/WSL2 e bridge virtual. Os resultados não representam diretamente uma rede física ampla.
- Não existe gargalo explícito de banda. A capacidade efetiva de E01 depende também do caminho virtual e do host.
- A perda de `netem` é estocástica. E01/E02 alteram simultaneamente RTT e perda, impedindo atribuição causal isolada.
- `netem` permaneceu como qdisc raiz, sem `sch_fq`. Filas e pacing podem modificar o desempenho de BBR; a escolha foi preservada para manter equivalência com o desenho validado.
- Os contadores de retransmissão não informam bytes únicos perdidos nem taxa real de perda. A normalização por GB é apenas complementar.
- O Bloco B mede um objeto local de 1.024 bytes, um servidor Caddy e uma versão específica do curl. TTFB não resume transferências longas, concorrência ou redes públicas.
- 0-RTT tem propriedades de segurança diferentes, especialmente risco de replay; o resultado de desempenho não elimina essa restrição.
- A escala logarítmica do painel completo do Gráfico 1 é necessária para legibilidade. O painel linear WAN evita que o leitor precise inferir a diferença E02/E03 apenas pela distância logarítmica.
- As seções 6.5.11, 6.6.2 e 6.6.1 da 6ª edição de Tanenbaum não estão disponíveis no workspace. Portanto, nenhuma frase, página ou interpretação específica foi atribuída ao livro. A fundamentação acima é geral e se apoia nas fontes primárias verificadas abaixo; a conferência direta do livro permanece para o Tópico 8.

## 8. Conclusões preliminares

Nos dados oficiais, a WAN com RTT de 50 ms e perda de 2% reduziu severamente a vazão do CUBIC em relação à LAN. Sob o mesmo perfil WAN, BBR apresentou média 70,57 vezes maior e menor CV, embora com mais retransmissões absolutas e também mais retransmissões por GB segundo a normalização limitada disponível. O resultado apoia a hipótese de que o controle orientado por modelo lidou melhor com a perda aleatória desta configuração, sem autorizar generalização universal.

No Bloco B, HTTP/3 Cold reduziu o TTFB médio em 31,14% frente a HTTP/2, e HTTP/3 com retomada e 0-RTT aceito reduziu-o em 64,35%. A vantagem de E06 sobre E05 foi 48,23%. As evidências de protocolo, e não apenas a latência, comprovam que a modalidade 0-RTT foi realmente usada.

Os gráficos, a tabela e as comparações estão prontos como material de apoio para o relatório SBRC do Tópico 8. A análise continua deliberadamente separada do relatório final.

## 9. Referências

1. IETF. [RFC 9438 — CUBIC for Fast and Long-Distance Networks](https://www.rfc-editor.org/rfc/rfc9438.html), 2023.
2. N. Cardwell et al. [BBR: Congestion-Based Congestion Control](https://research.google/pubs/bbr-congestion-based-congestion-control-2/). *ACM Queue*, 14, 2016.
3. IETF. [RFC 9000 — QUIC: A UDP-Based Multiplexed and Secure Transport](https://www.rfc-editor.org/rfc/rfc9000.html), 2021.
4. IETF. [RFC 9001 — Using TLS to Secure QUIC](https://www.rfc-editor.org/rfc/rfc9001.html), 2021.
5. IETF. [RFC 8446 — The Transport Layer Security (TLS) Protocol Version 1.3](https://www.rfc-editor.org/rfc/rfc8446.html), 2018.
6. IETF. [RFC 9114 — HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html), 2022.
7. Projeto. [Metodologia do Bloco A](block-a-methodology.md) e [Metodologia do Bloco B](block-b-methodology.md), evidências experimentais locais.

