# Leilão online de alta concorrência

Solução para o desafio técnico de **Engenharia de Software**: centenas de
milhares de pessoas dando lances ao mesmo tempo, com **resposta em tempo
real**, **ordem de chegada determinística** e **nenhum lance inválido ou
duplicado aceito por race condition**.

> Cenário fictício criado para um processo seletivo.

| Entregável pedido | Onde está |
|---|---|
| **Comece aqui:** o conceito explicado de forma simples | [`docs/ENTENDA_O_PROJETO.md`](docs/ENTENDA_O_PROJETO.md) |
| Desenho de arquitetura AWS (ícones oficiais) | [`docs/arquitetura_leilao_aws.drawio`](docs/arquitetura_leilao_aws.drawio) |
| Código-fonte funcional (API + testes) | [`leilao/`](leilao), [`servidor.py`](servidor.py), [`tests/`](tests) |
| Documentação das decisões técnicas | [`docs/ARQUITETURA_AWS.md`](docs/ARQUITETURA_AWS.md) |
| Roteiro para a apresentação | [`docs/GUIA_ENTREVISTA.md`](docs/GUIA_ENTREVISTA.md) |
| Especificação usada para construir com IA (spec-driven) | [`docs/SPEC.md`](docs/SPEC.md) |

## A ideia em uma frase

> Cada lance vira **uma única escrita condicional** no registro do leilão:
> "grave somente se este `Idempotency-Key` ainda não existe **e** o leilão
> ainda está na versão que eu li". Quem perde a corrida relê e é reavaliado.
> A ordem oficial é a ordem desses commits, nunca o relógio do usuário.

## Como rodar

Requer **Python 3.10+**. Sem dependências externas: só a biblioteca padrão.

```bash
python -m unittest -v
```

```bash
python servidor.py
```

Com o servidor no ar (`http://127.0.0.1:8000`), em outro terminal:

```bash
curl http://127.0.0.1:8000/leiloes/relogio-raro
```

```bash
curl -i -X POST http://127.0.0.1:8000/leiloes/relogio-raro/lances -H "Content-Type: application/json" -H "X-Usuario-Id: ana" -H "Idempotency-Key: 7f3c9a2e-1b4d-4e8a-9c1f-2a6b8d0e5f11" -d "{\"valor_cents\": 100000}"
```

```bash
curl -N http://127.0.0.1:8000/leiloes/relogio-raro/ao-vivo
```

Testes de carga:

```bash
python simular.py
```

```bash
python simular.py --http
```

Com Docker:

```bash
docker build -t leilao . && docker run -p 8000:8000 leilao
```

## API

| Método e rota | O que faz | Respostas |
|---|---|---|
| `POST /leiloes` | cria um leilão `{leilao_id, preco_inicial_cents, duracao_s, incremento_minimo_cents}` | `201`, `400`, `409` já existe |
| `GET /leiloes/{id}` | preço atual, próximo lance mínimo, vencedor, fim | `200`, `404` |
| `POST /leiloes/{id}/lances` | dá um lance `{valor_cents}`; headers `Idempotency-Key` e `X-Usuario-Id` | ver abaixo |
| `GET /leiloes/{id}/ao-vivo` | Server-Sent Events: cada novo preço em tempo real | stream |
| `GET /saude` | health check | `200` |

Respostas do lance:

| HTTP | Quando |
|---|---|
| `201 Created` | lance aceito |
| mesma resposta + `Idempotent-Replayed: true` | reenvio com a mesma `Idempotency-Key` |
| `409 Conflict` | abaixo do mínimo (alguém foi mais rápido) ou usuário já é o vencedor |
| `422 Unprocessable Entity` | leilão encerrado, valor inválido ou chave reutilizada com outro conteúdo |
| `400` / `401` / `404` / `429` | entrada inválida, sem usuário, leilão inexistente, limite de taxa |
| `503` | disputa excessiva: reenvie com a mesma chave |

## Estrutura do código (arquitetura hexagonal)

```
leilao/
  dominio.py               regras puras: incremento mínimo, encerramento, anti-sniping
  servico.py               caso de uso "dar lance": ler → decidir → gravar; se perder a corrida, reavaliar
  repositorio.py           porta (interface) + adaptador em memória com escrita atômica condicional
  repositorio_dynamodb.py  adaptador de produção: TransactWriteItems condicional
  notificacoes.py          tempo real: publica cada novo preço para quem assiste
  api.py                   HTTP: rotas, validação, códigos de status, limite de taxa, SSE
  simulacao.py             carga concorrente que prova as invariantes
servidor.py                sobe a API com leilões de exemplo
simular.py                 teste de carga (serviço ou HTTP)
tests/                     regras, concorrência e API HTTP ponta a ponta (21 testes)
```

O domínio não conhece HTTP nem banco. Trocar o repositório em memória pelo
DynamoDB não muda nenhuma linha do serviço nem da API.
