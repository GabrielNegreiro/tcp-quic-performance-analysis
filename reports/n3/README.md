# Relatório N3 — formato SBC/SBRC

Este diretório contém o artigo técnico-científico da terceira avaliação de Redes de Computadores II. A compilação usa somente os resultados históricos dos Tópicos 4–7; não executa Docker, `iperf3`, curl, `tc` nem novos benchmarks.

## Template

O documento usa o template de conferências da Sociedade Brasileira de Computação, com `sbc-template.sty` atualizado em 2017 e `sbc.bst`. A origem institucional é a página de [templates para artigos e capítulos da SBC](https://www.sbc.org.br/documentos-da-sbc/send/169-templates-para-artigos-e-capitulos-de-livros/878-modelosparapublicaodeartigos). Como essa página estava indisponível durante a preparação, os dois arquivos foram obtidos de uma [cópia arquivada que referencia a origem oficial](https://github.com/hellsdeur/template-sbc). Os arquivos de estilo não foram modificados.

O modelo SBC é o formato de conferências adotado como base pelo SBRC. Ele define papel A4, uma coluna, fonte nominal de 12 pontos e margens de 3,5 cm no topo, 2,5 cm na base e 3 cm nas laterais.

## Arquivos

- `main.tex`: artigo em português, com resumo e abstract;
- `references.bib`: RFCs e publicação original do BBR;
- `sbc-template.sty` e `sbc.bst`: template e estilo bibliográfico;
- `build.ps1`: compilação local reproduzível;
- `relatorio_n3_redes2.pdf`: PDF final;
- `build/`: intermediários ignorados pelo Git.

Os gráficos são referenciados diretamente em `../../results/figures/`, evitando cópias divergentes. A tabela é composta no LaTeX a partir dos valores auditados em `results/processed/n3_statistics.json`.

## Pendências antes da entrega

Substitua no `main.tex`:

- `[NOME COMPLETO DO(A) DISCENTE]`;
- `[E-MAIL ACADÊMICO]`.

O texto integral da 6ª edição de Tanenbaum não estava disponível no workspace. Antes da entrega, confira diretamente as Seções 6.5.11, 6.6.2 e 6.6.1 e acrescente a referência bibliográfica correta, sem inventar página ou edição.

## Compilação

Requer Tectonic 0.17.0 ou uma distribuição LaTeX compatível. Com Tectonic no `PATH`:

```powershell
powershell -ExecutionPolicy Bypass -File reports/n3/build.ps1
```

Com binário portátil:

```powershell
powershell -ExecutionPolicy Bypass -File reports/n3/build.ps1 `
  -TectonicExe "C:\caminho\para\tectonic.exe"
```

O Tectonic resolve LaTeX e BibTeX em uma única invocação. Para uma distribuição tradicional, execute `pdflatex`, `bibtex` e mais duas passagens de `pdflatex` a partir de `reports/n3`.

## Atualização controlada

1. Gere uma nova integração somente em tarefa experimental autorizada.
2. Execute a análise estatística offline do Tópico 7 para essa integração.
3. Confira os valores da tabela e as referências aos gráficos.
4. Compile o artigo; não rode os experimentos para produzir o PDF.

## Validação

Após compilar:

```powershell
pdftotext reports/n3/relatorio_n3_redes2.pdf -
git diff --check
```

Confirme entre quatro e seis páginas, extração de texto, referências resolvidas, presença dos dois gráficos e ausência de textos cortados. Renderize e inspecione visualmente todas as páginas. A pasta `build/` contém o log para diagnóstico e não deve ser adicionada ao Git.
