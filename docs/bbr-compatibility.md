# Compatibilidade do TCP BBR no Docker Desktop/WSL2

## Conclusão

O TCP BBR está disponível no kernel usado pelo projeto e funciona em conexões reais do iperf3. A ausência inicial de `bbr` em `net.ipv4.tcp_available_congestion_control` não significava que o algoritmo não havia sido compilado: neste kernel, `CONFIG_TCP_CONG_BBR=m`, portanto ele só passa a constar na lista depois que o módulo é registrado.

A solução adotada é solicitar BBR no socket do teste com `iperf3 -C bbr`. Essa operação fez o kernel resolver `tcp_bbr` sob demanda. Não foram executados `modprobe`, alteração de `sysctl`, troca de kernel, edição de `.wslconfig`, mudança de backend ou atualização do Docker/WSL.

O algoritmo global continuou sendo CUBIC. BBR é selecionado apenas nos sockets dos testes que usam `-C bbr`.

## Evidências observadas em 18/11/2026

| Item | Resultado observado |
| --- | --- |
| Windows | Windows 11 Home, build 26200 |
| WSL | 2.7.3.0, distribuições Ubuntu e `docker-desktop` em WSL2 |
| Distribuição Ubuntu | Ubuntu 26.04 LTS |
| Docker Desktop | 4.74.0.227015, contexto `desktop-linux`, backend WSL2 |
| Docker Engine/CLI | 29.4.3 |
| Docker Compose | 5.1.3 |
| Kernel no Ubuntu WSL | `6.6.114.1-microsoft-standard-WSL2` |
| Kernel nos três contêineres | `6.6.114.1-microsoft-standard-WSL2` |
| Distribuição do cliente e servidor TCP | Alpine Linux 3.23 |
| Arquitetura | `x86_64` |
| iperf3 | 3.19.1 |
| Configuração BBR | `CONFIG_TCP_CONG_BBR=m` |
| Configuração CUBIC | `CONFIG_TCP_CONG_CUBIC=y` |
| Disciplinas disponíveis | `CONFIG_NET_SCH_FQ=m`, `CONFIG_NET_SCH_FQ_CODEL=m` |
| Suporte a módulos | `CONFIG_MODULES=y` |
| Algoritmo global padrão | `CONFIG_DEFAULT_TCP_CONG="cubic"`; `net.ipv4.tcp_congestion_control=cubic` |
| Arquivo do módulo | `/lib/modules/6.6.114.1-microsoft-standard-WSL2/kernel/net/ipv4/tcp_bbr.ko` |
| Compatibilidade do módulo | `modinfo` apresentou o mesmo `vermagic` do kernel ativo |
| Estado depois do primeiro `-C bbr` | `tcp_bbr` presente em `/proc/modules` e `/sys/module/tcp_bbr` |
| Algoritmos depois da resolução | `reno cubic bbr` |

O Ubuntu WSL, o ambiente `docker-desktop` e os contêineres apresentaram a mesma versão de kernel. Isso é coerente com a documentação do Docker: as distribuições WSL2 executam lado a lado sobre um único kernel compartilhado. Logo, instalar outro Docker Engine dentro da mesma VM WSL2 não corrigiria uma ausência real de funcionalidade do kernel.

## Causa confirmada

A causa foi uma interpretação incompleta de uma lista dinâmica. Antes do primeiro uso, o kernel anunciava apenas os algoritmos de congestionamento já registrados, `reno cubic`. A configuração exata exposta por `/proc/config.gz` mostrou que BBR não estava ausente: estava compilado como módulo (`=m`). O arquivo `tcp_bbr.ko`, a ferramenta `modprobe` e módulos correspondentes à versão ativa também estavam presentes.

O diagnóstico curto `iperf3 -c server-tcp -t 1 -C bbr` terminou com código zero. Imediatamente depois, `bbr` apareceu em `tcp_available_congestion_control`, e `tcp_bbr` apareceu em `/proc/modules`. Isso ocorreu sem uma chamada explícita a `modprobe` e sem mudar o algoritmo global. A opção `TCP_CONGESTION` do Linux é por socket, e a opção `-C` do iperf3 seleciona o algoritmo da conexão no Linux.

O módulo permanecerá registrado enquanto a VM WSL2 estiver ativa. Depois de uma parada completa da VM, a lista pode voltar inicialmente a `reno cubic`; a primeira conexão com `iperf3 -C bbr` repete a resolução sob demanda. Por isso, os validadores testam a seleção antes de concluir que BBR está ausente.

## Prova do algoritmo efetivamente usado

Durante uma conexão de três segundos, executada do cliente para `172.20.0.10`, `ss -tin dst 172.20.0.10:5201` mostrou na conexão de dados:

```text
bbr ... bbr:(bw:...,mrtt:...,pacing_gain:1,cwnd_gain:2) ...
```

No diagnóstico equivalente com CUBIC, a conexão de dados mostrou:

```text
cubic ...
```

O iperf3 cria uma conexão TCP de controle e outra de dados. É normal que a conexão de controle apareça como CUBIC; a comprovação relevante é a conexão de dados no cliente, que é o transmissor no teste normal. A documentação do iperf3 confirma essa separação, e a ESnet ressalta que BBR atua no lado transmissor.

Os dois testes de três segundos produziram JSON, transmitiram bytes e encerraram com código zero. Eles são diagnósticos e não devem ser usados como resultados de E01–E03.

## Comparação das alternativas

| Alternativa | Viabilidade confirmada | Complexidade | Risco | Preserva o projeto Docker | Recomendação |
| --- | --- | --- | --- | --- | --- |
| A — usar o módulo existente | Sim. Foi comprovada com `iperf3`, `/proc/modules` e `ss` | Baixa | Baixo; o padrão global não muda | Sim | **Adotada** |
| B — corrigir/atualizar Docker ou WSL | Desnecessária no ambiente atual; as versões atuais já fornecem o módulo | Média | Médio, com parada e possível regressão | Provavelmente | Não atualizar sem outra necessidade |
| C — executar diretamente no Ubuntu WSL2 | Tecnicamente possível para BBR, pois usa o mesmo kernel; não traz benefício e exigiria reproduzir a topologia/netem | Média | Médio | Não integralmente | Manter como alternativa de diagnóstico, não migrar |
| D — trocar o backend do Docker | Não confirmada para BBR e inadequada como correção; Windows Home limita as opções Hyper-V, e Docker VMM permanece uma mudança ampla | Alta | Alto | Incerto | Não usar para resolver este caso |
| E — VM Linux independente | Viável em princípio, desde que kernel, módulos e recursos sejam validados na VM | Alta | Médio | Exige migrar/recriar o ambiente | Fallback somente se o kernel atual deixar de oferecer BBR |
| F — host Linux externo | Condicional; depende de controle real do kernel. Uma VPS pode proibir módulos/sysctls | Alta | Médio | Pode preservar o Compose, mas muda o host científico | Fallback após validação do host |
| G — kernel WSL2 personalizado | Tecnicamente possível, mas desnecessária e não validada com esta instalação do Docker | Muito alta | Muito alto; pode impedir o Docker de iniciar | Incerto | Não recomendada |

Uma VM ou host externo só seria metodologicamente aceitável se E01, E02 e E03 fossem todos executados no mesmo host, com o mesmo kernel, Compose e perfis de rede. Misturar E03 de outra máquina com E01/E02 deste computador introduziria um fator de confusão.

## FQ, pacing e `tc netem`

O kernel também fornece `sch_fq` como módulo. A ESnet informa que BBR se beneficia de FQ/pacing, mas isso não é requisito para provar que o socket usa o BBR real do kernel. Atualmente as interfaces dos contêineres terminam em `noqueue` quando não há cenário e recebem a qdisc raiz `netem` somente durante os perfis experimentais.

Esta tarefa não trocou a hierarquia de qdisc, pois isso alteraria a implementação já validada dos cenários. Antes dos experimentos oficiais, o Tópico 4 deve registrar essa escolha metodológica e, se desejar introduzir FQ em uma qdisc filha, validar novamente RTT, perda e equivalência entre CUBIC e BBR. Não se deve substituir `netem` nem alterar `net.core.default_qdisc` globalmente sem um novo desenho controlado.

## Uso reproduzível

Coleta somente leitura:

```bash
sh scripts/validation/diagnose-bbr.sh
```

Coleta com um probe BBR de um segundo:

```bash
sh scripts/validation/diagnose-bbr.sh --probe
```

Validação completa de BBR/CUBIC por socket e regressão:

```bash
sh scripts/validation/validate-bbr.sh
```

Validação dos três cenários, sempre encerrando sem qdisc experimental:

```bash
sh scripts/validation/validate-network.sh
sh scripts/network/inspect_profile.sh --all
```

As evidências ficam em `results/diagnostics/bbr/`, separadas de `results/raw/`. O script BBR primeiro remove somente as qdiscs gerenciadas pelo projeto e possui limpeza em caso de interrupção.

## Persistência e reversão

Não há configuração persistente a reverter. A única mudança de estado do kernel é o registro do módulo durante a vida da VM compartilhada. O algoritmo padrão continua CUBIC, e novos sockets sem `-C bbr` continuam usando o padrão.

Uma parada completa do WSL descarrega o estado junto com a VM, mas também interrompe o Docker Desktop e as distribuições. Ela não é necessária para este projeto. Não foi configurado `loadKernelModules=tcp_bbr` porque a resolução por socket já é suficiente e tem menor impacto.

Se um kernel futuro remover o módulo, a validação falhará sem falsos positivos. Nesse caso, a sequência segura é: confirmar `/proc/config.gz` e `/lib/modules`; avaliar uma atualização oficial; e só então considerar VM/host Linux. Um kernel customizado seria a última alternativa. A Microsoft documenta `kernel` e `kernelModules` em `.wslconfig`, mas o Docker alerta explicitamente que kernels WSL personalizados não têm suporte oficial e podem causar falhas de inicialização/operação.

## Fontes oficiais consultadas

- [Docker Desktop — backend WSL2](https://docs.docker.com/desktop/features/wsl/): kernel compartilhado e integração WSL.
- [Docker Desktop — kernels WSL personalizados](https://docs.docker.com/desktop/features/wsl/custom-kernels/): alerta de ausência de suporte oficial e riscos.
- [Docker Desktop — instalação no Windows](https://docs.docker.com/desktop/setup/install/windows-install/): backends e requisitos do Windows.
- [Microsoft — configuração avançada do WSL](https://learn.microsoft.com/windows/wsl/wsl-config): `kernel`, `kernelModules`, `loadKernelModules` e reinicialização da VM.
- [Microsoft — kernel oficial do WSL2](https://github.com/microsoft/WSL2-Linux-Kernel): fonte e procedimento oficial de compilação.
- [Linux kernel — parâmetros TCP](https://docs.kernel.org/networking/ip-sysctl.html): algoritmos permitidos/disponíveis e padrão.
- [Linux man-pages — `tcp(7)`](https://www.kernel.org/pub/linux/docs/man-pages/): `TCP_CONGESTION` por socket.
- [iperf3 — opções de invocação](https://software.es.net/iperf/invoking.html): `-C/--congestion`, JSON e conexões de controle/dados.
- [ESnet — teste de BBR com iperf3](https://fasterdata.es.net/host-tuning/linux/recent-tcp-enhancements/bbr-testing-using-iperf3/): BBR no transmissor e considerações sobre FQ.

