# Metodologia do Bloco B — HTTP/2, HTTP/3 Cold e HTTP/3 0-RTT

## Objetivo e lote oficial

O Bloco B mede o TTFB de um GET idempotente a `https://server-http3/health.txt`, objeto estático de 1024 bytes, sob o perfil `wan-no-loss` (25 ms por direção, RTT-alvo de 50 ms e perda configurada de 0%). O lote oficial é `block_b_20261118T235312Z_e440c163`, com cinco amostras válidas por experimento e nenhuma tentativa adicional.

| Experimento | Modalidade comprovada | Protocolo |
| --- | --- | --- |
| E04 | nova conexão TCP + TLS 1.3 | HTTP/2 |
| E05 | nova conexão QUIC, sem sessão importada | HTTP/3 Cold |
| E06 | nova conexão QUIC, sessão TLS retomada e GET aceito em 0-RTT | HTTP/3 |

HTTP/2 usa TCP, TLS e o protocolo HTTP/2 negociado por ALPN. HTTP/3 mapeia a semântica HTTP sobre QUIC; o estabelecimento criptográfico TLS 1.3 faz parte do handshake QUIC. As definições normativas estão no [RFC 9113 (HTTP/2)](https://www.rfc-editor.org/rfc/rfc9113.html), [RFC 9000 (QUIC)](https://www.rfc-editor.org/rfc/rfc9000.html), [RFC 9114 (HTTP/3)](https://www.rfc-editor.org/rfc/rfc9114.html) e [RFC 8446 (TLS 1.3)](https://www.rfc-editor.org/rfc/rfc8446.html).

## Distinção entre os estados de conexão

- **Cold:** processo curl novo, conexão nova e nenhuma sessão TLS importada.
- **Connection reuse:** outra requisição usa a mesma conexão ainda aberta. Esta modalidade não integra E06.
- **Session resumption:** uma conexão nova importa o ticket de uma conexão anterior e retoma a sessão TLS.
- **0-RTT:** além da retomada, a aplicação envia o primeiro GET antes da conclusão do handshake e o servidor o aceita como early data.

Uma requisição anterior, uma redução de TTFB ou a existência de um arquivo de sessão, isoladamente, não prova resumption nem 0-RTT.

## Suporte do cliente e do servidor

O curl 8.23.0 foi mantido com GnuTLS 3.8.13, ngtcp2 1.22.1 e nghttp3 1.13.1. A imagem passou a usar `--enable-ssls-export`, e seu build falha se `curl --version` não anunciar `SSLS-EXPORT`, HTTP/2 e HTTP/3 ou se as opções `--ssl-sessions` e `--tls-earlydata` não existirem. A [documentação HTTP/3 do curl](https://curl.se/docs/http3.html) descreve o backend ngtcp2; o [manual do curl](https://curl.se/docs/manpage.html) documenta a persistência experimental de tickets, `--tls-earlydata`, `%{tls_earlydata}` e `time_starttransfer`.

O Caddy 2.11.9 atende h1/h2/h3 com TLS 1.3. Segundo as [opções globais do Caddy](https://caddyserver.com/docs/caddyfile/options), 0-RTT é habilitado por padrão nos listeners QUIC. O Caddyfile usa o matcher documentado [`tls early_data`](https://caddyserver.com/docs/caddyfile/matchers) para adicionar uma prova independente à resposta:

- `X-Lab-QUIC-Early-Data: accepted` quando o GET chegou antes do fim do handshake;
- `X-Lab-QUIC-Early-Data: not-used` nos demais casos.

## Procedimento por amostra

Antes de cada medição, o orquestrador:

1. recusa execução concorrente por locks compatíveis com o Bloco A;
2. confirma que não há outro curl no cliente;
3. remove regras gerenciadas anteriores;
4. reaplica `wan-no-loss` no cliente e no servidor HTTP/3;
5. valida duas qdiscs `netem` com handle `1a2b:`, atraso de 25 ms e sem loss;
6. aguarda dois segundos de estabilização;
7. executa uma única medição;
8. remove as qdiscs mesmo após erro.

E04 e E05 usam processos curl separados e `--no-sessionid`. E04 exige `--http2` e `http_version=2`; E05 exige `--http3-only`, `http_version=3`, `num_connects=1`, `tls_earlydata=0`, ausência de marcador de sessão retomada no trace e header do Caddy diferente de `accepted`.

Para cada E06:

1. um primeiro processo curl usa `--http3-only --ssl-sessions <arquivo-temporário> --tls-earlydata` para obter um ticket habilitado para early data;
2. a preparação precisa retornar HTTP/3, 200, 1024 bytes, `num_connects=1` e `tls_earlydata=0`;
3. um segundo processo curl, totalmente novo, importa o mesmo ticket e mede o GET com `--tls-earlydata`;
4. as portas UDP locais da preparação e da medição precisam ser diferentes;
5. o trace precisa conter `SSL reusing session with ALPN 'h3'` e a capacidade de early data;
6. `%{tls_earlydata}` precisa ser maior que zero;
7. o header independente do Caddy precisa ser `accepted`.

A requisição preparatória nunca entra no CSV oficial. O arquivo de ticket existe somente em `/tmp` no contêiner, tem seu tamanho registrado e é apagado em `finally`; seu conteúdo nunca é copiado para o host ou Git. Os traces preservados não contêm o ticket.

## Coleta e validade

O curl emite `%{json}`. O parser exige GET, exit code zero, uma nova conexão, protocolo exato, HTTP 200 e 1024 bytes. O TTFB é calculado sem arredondamento prematuro:

```text
TTFB_ms = time_starttransfer × 1000
```

Também são preservados `time_namelookup`, `time_connect`, `time_appconnect`, `time_pretransfer`, `time_total`, porta local, número de conexões, resposta, tamanho e bytes de early data. Cada diretório de amostra inclui JSON bruto do curl, trace, headers, metadados, qdisc e saída da aplicação do perfil. E06 inclui ainda os três arquivos correspondentes à preparação, sem o segredo de sessão.

As execuções foram sequenciais e alternadas por rodada: E04/1, E05/1, E06/1, ..., E04/5, E05/5, E06/5. Uma tentativa inválida seria movida para `attempts/` e preservada; nenhuma ocorreu no lote oficial.

## Resultados oficiais

| Experimento | Amostras | Média TTFB (ms) | Desvio-padrão amostral (ms) | Mínimo (ms) | Máximo (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| E04 | 5 | 158,9658 | 0,3345 | 158,389 | 159,250 |
| E05 | 5 | 109,4652 | 1,3737 | 108,277 | 111,544 |
| E06 | 5 | 56,6664 | 0,5518 | 56,050 | 57,395 |

Comparações descritivas das médias:

- E05 − E04: −49,5006 ms (−31,1392%).
- E06 − E04: −102,2994 ms (−64,3531%).
- E06 − E05: −52,7988 ms (−48,2334%).

Todas as cinco E06 abriram nova conexão, retomaram a sessão e registraram 84 bytes de early data aceitos pelo Caddy. Os resultados demonstram o comportamento nesta bridge Docker/WSL2 e neste objeto pequeno; não são teste de significância nem autorizam generalização de que HTTP/3 sempre será mais rápido.

## Reprodução

```bash
sh scripts/experiments/run_block_b.sh --preflight
sh scripts/experiments/run_block_b.sh
sh scripts/experiments/run_block_b.sh --experiment E06
python -m unittest discover -s tests -v
```

Cada execução oficial cria um novo `batch_id` e recusa sobrescrita. O CSV consolidado fica em `results/processed/block_b_measurements.csv`; o resumo e o CSV do lote ficam em `results/processed/block_b/<batch_id>/`. Ao final, confirme `active_profile=none` com `sh scripts/network/inspect_profile.sh --all`.

## Limitações e decisões

- `SSLS-EXPORT` é experimental no curl; por isso o build e a pré-validação verificam a capacidade real, não apenas a presença da opção no manual.
- A evidência de 0-RTT combina duas fontes, curl e matcher do Caddy. Não foi necessário introduzir um cliente especializado nem inferir 0-RTT por latência.
- O GET é idempotente, local e sem credenciais, reduzindo o risco de replay inerente a 0-RTT.
- Não há limitação artificial de banda. As comparações refletem somente este cenário WAN emulado, a infraestrutura local e o objeto de 1024 bytes.
- O Bloco A, o kernel, BBR e o algoritmo TCP global não foram alterados nem reexecutados.
