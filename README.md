# Leilão online de alta concorrência — da decisão do lance ao dado confiável

Solução para o desafio técnico de Engenharia, com olhar de **Engenharia de
Dados** e de um **contexto bancário** (governança, LGPD, auditoria e
reconciliação).

> Cenário fictício criado para o processo seletivo. Não usa dados nem
> sistemas internos de nenhuma instituição.

O problema tem duas metades, e a solução trata as duas:

1. **Decidir o lance certo, em tempo real**: com centenas de milhares de
   lances simultâneos, nenhum lance inválido ou duplicado pode ser aceito
   por race condition, e a ordem de chegada tem de ser determinística.
2. **Transformar cada decisão em dado confiável**: o stream entrega
   *at-least-once* e fora de ordem. A plataforma de dados deduplica,
   reordena, pseudonimiza, valida a qualidade e **reconcilia** o analítico
   com o sistema transacional.

| Entregável pedido | Onde está |
|---|---|
| Desenho de arquitetura AWS (ícones oficiais) | [`docs/arquitetura_leilao_aws.drawio`](docs/arquitetura_leilao_aws.drawio) |
| Código-fonte funcional | [`leilao/`](leilao), [`simular.py`](simular.py), [`tests/`](tests) |
| Documentação das decisões técnicas | [`docs/ARQUITETURA_AWS.md`](docs/ARQUITETURA_AWS.md) |
| Explicação didática para a apresentação | [`docs/GUIA_ENTREVISTA.md`](docs/GUIA_ENTREVISTA.md) |
| Especificação usada para construir com IA (spec-driven) | [`docs/SPEC.md`](docs/SPEC.md) |

## Como rodar

Requer **Python 3.10+** e só a biblioteca padrão.

```bash
python -m unittest -v
```

```bash
python simular.py
```

```bash
python simular.py --lances 100000 --threads 128
```

O comando imprime três blocos: o **transacional** (lances decididos e
disputas resolvidas), as **camadas de dados** (SOR → SOT, com os
duplicados removidos) e a **qualidade de dados** (regras PASSOU/FALHOU). As
saídas vão para `saida/`:

```
saida/
  sor/eventos_lances_brutos.csv   eventos como chegaram (com duplicados, fora de ordem)
  sot/lances.csv                  1 linha por lance, ordenado, usuário pseudonimizado
  spec/resumo_leiloes.csv          preço final, vencedor, taxa de aceite, participantes
  spec/motivos_rejeicao.csv        por que os lances foram recusados
  qualidade.json                   resultado das regras de Data Quality
  resumo_transacional.json         visão do sistema operacional
```

Para abrir o diagrama, acesse <https://app.diagrams.net/?libs=aws4&splash=0>,
clique em **Arquivo → Abrir do dispositivo** e selecione o `.drawio`.

## Estrutura do código

```
leilao/
  dominio.py      regras do lance (puras): incremento mínimo, encerramento, anti-sniping
  repositorio.py  escrita ATÔMICA e condicional (imita o DynamoDB) + outbox de eventos
  servico.py      "dar lance": ler → decidir → gravar; se perder a corrida, reavaliar
  simulacao.py    carga concorrente e verificação das invariantes transacionais
  dados.py        pipeline SOR → SOT → SPEC, Data Quality, reconciliação e LGPD
simular.py        linha de comando
tests/            testes de regras, concorrência e pipeline de dados (16 testes)
```
