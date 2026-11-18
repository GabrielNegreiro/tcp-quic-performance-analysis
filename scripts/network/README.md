# Perfis de rede com Linux Traffic Control

Estes scripts gerenciam exclusivamente as qdiscs criadas pelo projeto `tcp-quic-performance-analysis`. Eles nunca removem a rede Docker compartilhada, não alteram sysctls e não atuam em contêineres sem os labels do Compose deste projeto.

## Modelo experimental

`tc netem` atua no tráfego de **saída** da interface. RTT (Round-Trip Time) é o tempo completo de ida e volta; portanto, 25 ms aplicados somente em uma ponta não equivalem a um RTT de 50 ms. Este projeto distribui metade do atraso em cada sentido:

| Perfil | Cliente, saída | Servidor, saída | RTT alvo | Uso futuro |
| --- | --- | --- | --- | --- |
| `lan` | `delay 500us` | `delay 500us` | 1 ms | E01, TCP CUBIC em LAN |
| `wan-degraded` | `delay 25ms loss random 2%` | `delay 25ms` | 50 ms | E02/E03, TCP em WAN degradada |
| `wan-no-loss` | `delay 25ms` | `delay 25ms` | 50 ms | E04–E06, HTTP/2 e HTTP/3 |

Na WAN degradada, a perda estocástica de 2% é aplicada somente no egress de `client-bench`. O iperf3 no modo padrão envia os dados do cliente ao servidor, logo a perda incide no sentido dos dados. Aplicar 2% também na volta aumentaria a probabilidade combinada de perda em trocas bidirecionais e alteraria o parâmetro científico pretendido. Os ACKs retornam pelo egress do servidor com atraso, mas sem uma segunda perda artificial.

Ausência da palavra `loss` na qdisc significa perda configurada em 0%. A perda observada por ping é uma estimativa aleatória e não precisa ser exatamente 2% em uma amostra finita.

## Segurança e propriedade

Os scripts:

- localizam cada contêiner pelo serviço do Compose;
- exigem os labels `com.docker.compose.project=tcp-quic-performance-analysis` e o service label esperado;
- descobrem a interface pela rota com `ip -o -4 route get`, sem presumir `eth0`;
- reservam o handle `1a2b:` para as qdiscs do projeto;
- gravam um marcador complementar em `/run/tcp-quic-netem/<interface>.profile`;
- aceitam apenas a qdisc padrão `noqueue` ou a qdisc `netem 1a2b:`;
- interrompem a alteração se encontrarem uma qdisc desconhecida;
- removem perfis anteriores nos três serviços antes de aplicar o próximo, evitando contaminação entre blocos;
- fazem rollback do cliente se a aplicação no servidor falhar.

O marcador em `/run` é apenas auxiliar. O handle reservado permite reconhecer uma aplicação interrompida entre a criação da qdisc e a gravação do marcador. Reiniciar o contêiner elimina tanto a qdisc quanto o conteúdo de `/run`.

## Ambientes de execução

Os comandos `tc` são executados **dentro dos contêineres**. Os scripts Shell são orquestradores executados na raiz do projeto, em um terminal Ubuntu/WSL2 ou Git Bash com acesso ao Docker Desktop.

No PowerShell, é possível encaminhar a execução ao WSL2 sem depender de um caminho absoluto:

```powershell
wsl.exe --cd . sh scripts/network/inspect_profile.sh --all
wsl.exe --cd . sh scripts/network/apply_profile.sh lan server-tcp
```

No Ubuntu/WSL2:

```bash
sh scripts/network/inspect_profile.sh --all
sh scripts/network/apply_profile.sh lan server-tcp
```

## Aplicar perfis

Somente as combinações coerentes com o desenho experimental são aceitas:

```bash
sh scripts/network/apply_profile.sh lan server-tcp
sh scripts/network/apply_profile.sh wan-degraded server-tcp
sh scripts/network/apply_profile.sh wan-no-loss server-http3
```

O perfil permanece ativo após o script terminar, permitindo que uma etapa posterior execute um diagnóstico ou experimento. Reaplicar o mesmo perfil ou trocar de perfil substitui a qdisc anterior; regras não são empilhadas.

## Inspecionar

```bash
sh scripts/network/inspect_profile.sh --all
sh scripts/network/inspect_profile.sh client-bench
sh scripts/network/inspect_profile.sh server-tcp
sh scripts/network/inspect_profile.sh server-http3
```

A saída informa serviço, contêiner, interface, peer, classificação de propriedade, perfil marcado e os contadores de `tc -s qdisc show`. `Sent`, `dropped`, `overlimits`, `backlog` e `requeues` são contadores do kernel desde a aplicação da qdisc.

Para medir RTT manualmente enquanto um perfil está ativo:

```bash
docker compose exec -T client-bench ping -c 20 172.20.0.10
docker compose exec -T client-bench ping -c 20 172.20.0.30
```

O campo `rtt min/avg/max/mdev` deve ser interpretado como valores mínimo, médio, máximo e dispersão, em milissegundos. O resumo do ping informa pacotes enviados, recebidos e perda observada.

## Remover perfis

```bash
sh scripts/network/reset_profile.sh --all
sh scripts/network/reset_profile.sh server-tcp
```

O reset é idempotente: executá-lo quando só existe `noqueue` não causa erro. Uma qdisc de handle desconhecido é preservada e produz falha explícita. Use `--all` antes de mudar de bloco experimental e depois de qualquer execução manual.

## Validação completa

No WSL2/Git Bash:

```bash
sh scripts/validation/validate-network.sh
```

No PowerShell:

```powershell
wsl.exe --cd . sh scripts/validation/validate-network.sh
```

O validador:

1. começa em estado limpo;
2. aplica LAN, mede 20 pings e limpa;
3. aplica WAN degradada, mede 100 pings e limpa;
4. aplica WAN sem perda, mede 20 pings, força HTTP/3-only e limpa;
5. reaplica LAN duas vezes e troca para WAN degradada;
6. confirma ausência de acúmulo;
7. limpa todas as regras;
8. testa novamente TCP, HTTP/2 e HTTP/3;
9. confirma que todos os serviços terminaram com qdisc padrão.

Um trap executa limpeza segura quando o validador termina ou recebe `HUP`, `INT` ou `TERM`. O script de aplicação manual não possui limpeza automática, pois o perfil precisa permanecer disponível para o uso posterior.

## Evidências

Os resultados reais de validação são escritos em:

```text
results/diagnostics/lan/
results/diagnostics/wan-degraded/
results/diagnostics/wan-no-loss/
```

Cada arquivo registra timestamp UTC, perfil, serviços, interfaces, qdiscs, RTT mínimo/médio/máximo, perda configurada e observada, quantidade de pacotes e status. Esses arquivos são diagnósticos de infraestrutura e **não** integram as amostras oficiais E01–E06.

## Recuperação de falhas

- Contêiner parado/inexistente: execute `docker compose up -d` e inspecione com `docker compose ps`.
- Daemon indisponível: inicie o Docker Desktop e confirme com `docker info`.
- Qdisc desconhecida: não execute uma remoção genérica; identifique sua origem. Os scripts a preservarão.
- Aplicação parcial: a automação tenta rollback. Depois, execute `inspect_profile.sh --all` e `reset_profile.sh --all`.
- Interrupção da validação: confirme o resultado da limpeza com `inspect_profile.sh --all`.

Não use `docker network prune`, `docker system prune`, `docker compose down -v` ou comandos genéricos como `tc qdisc del` em contêineres de outros projetos.
