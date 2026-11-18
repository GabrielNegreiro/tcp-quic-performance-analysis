# Análise Experimental de TCP, HTTP/3 e QUIC em Redes Degradadas

Projeto da Terceira Avaliação (N3) de Redes de Computadores II da UFPI. O objetivo é preparar e, nas etapas posteriores, executar uma análise reproduzível de:

- controle de congestionamento TCP CUBIC e TCP BBR; e
- handshake e retomada de sessão com HTTP/2, HTTP/3 e QUIC 0-RTT.

Este repositório contém, neste momento, **somente a estrutura inicial do Tópico 1**. Nenhum contêiner, servidor, experimento, regra `tc netem` ou dado experimental foi criado ou executado.

## Tecnologias previstas

- Windows 11, WSL2 Ubuntu e Docker Desktop;
- Docker e Docker Compose;
- Linux Traffic Control (`tc netem`);
- `iperf3` para medições TCP;
- `curl` com suporte aos protocolos necessários;
- Python 3 com pandas, NumPy e Matplotlib para processamento, estatística descritiva e gráficos.

## Blocos experimentais

### Bloco A — Controle de congestionamento

| ID | Algoritmo | Cenário |
| --- | --- | --- |
| E01 | TCP CUBIC | LAN: RTT alvo de 1 ms e perda alvo de 0% |
| E02 | TCP CUBIC | WAN degradada: RTT alvo de 50 ms e perda alvo de 2% |
| E03 | TCP BBR | WAN degradada: RTT alvo de 50 ms e perda alvo de 2% |

As execuções TCP terão duração-alvo de 15 segundos e ao menos cinco repetições por experimento.

### Bloco B — Handshake e retomada de sessão

| ID | Protocolo/conexão | Objetivo |
| --- | --- | --- |
| E04 | HTTP/2 | Medir a conexão de referência |
| E05 | HTTP/3 Cold | Medir uma conexão sem sessão reutilizável |
| E06 | HTTP/3 Warm/Resumption | Medir retomada de sessão e, posteriormente, avaliar QUIC 0-RTT |

Os parâmetros acima são valores-alvo para etapas futuras; ainda não representam configurações de rede aplicadas.

## Estrutura do projeto

```text
.
├── docker-compose.yml       # Esboço comentado dos serviços futuros
├── requirements.txt         # Dependências futuras de análise
├── .env.example             # Valores-alvo e endereçamento planejado
├── docker/                  # Contextos Docker futuros
│   ├── client/
│   ├── server-tcp/
│   └── server-http3/
├── certs/                   # Certificados locais (segredos são ignorados)
├── site/                    # Conteúdo servido nos testes HTTP futuros
├── scripts/
│   ├── network/             # Configuração e limpeza de rede futuras
│   ├── experiments/         # Orquestração futura de E01–E06
│   └── validation/          # Validações futuras do ambiente
├── src/
│   ├── automation/          # Código futuro de automação
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

Para as próximas etapas, espera-se:

- Windows 11 com WSL2 e uma distribuição Ubuntu funcional;
- Docker Desktop integrado ao WSL2;
- Docker Engine e Docker Compose v2 ou compatível;
- Python 3.10 ou superior;
- permissões administrativas controladas para os recursos de rede que serão usados posteriormente;
- disponibilidade futura de `iperf3`, `curl` e `tc` dentro dos contêineres/WSL2.

O suporte efetivo a BBR, HTTP/3 e QUIC deverá ser validado no Tópico 2 antes de qualquer experimento.

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

Esses comandos apenas consultam versões. Não use `docker compose up` nem aplique `tc netem` durante o Tópico 1.

### Estado observado na estruturação inicial

- terminal: Windows PowerShell 5.1 (não Git Bash e não uma sessão Ubuntu/WSL2);
- Python: 3.12.10 disponível pelo comando `python`;
- Python Launcher (`py`): presente, mas sem uma instalação registrada;
- Docker CLI: 29.4.3 disponível;
- Docker Compose: 5.1.3 disponível;
- WSL2: `wsl.exe` está presente, mas a enumeração das distribuições retornou `E_ACCESSDENIED`; por isso, a instalação/configuração do WSL2 não pôde ser confirmada;
- Docker: a consulta de versão funcionou, mas houve aviso de acesso negado a `C:\Users\negre\.docker\config.json`.

Essa inspeção não iniciou o daemon, contêineres ou serviços e não alterou configurações do host.

## Próximos tópicos

1. No Tópico 2, validar WSL2/Docker Desktop e construir a infraestrutura Docker dos serviços `server-tcp`, `server-http3` e `client-bench`.
2. Em etapas posteriores, preparar certificados e conteúdo HTTP, implementar perfis de rede com aplicação e limpeza seguras, automatizar E01–E06 e validar a coleta.
3. Somente após a infraestrutura ser validada, executar as repetições, processar dados reais, gerar figuras e produzir o relatório.

