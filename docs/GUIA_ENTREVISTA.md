# Guia para a apresentação

Roteiro de cerca de 10 minutos para a entrevista de **Engenharia de Software**.

---

## 1. Abertura (30 s)

> "O coração do problema é uma race condition: milhares de pessoas leem o
> mesmo preço e tentam superá-lo ao mesmo tempo. Resolvi isso transformando
> cada lance numa única escrita condicional no banco. Em volta disso
> construí uma API idempotente, notificação em tempo real e testes que
> provam a correção sob concorrência."

## 2. O problema com uma analogia (1 min)

Uma planilha compartilhada em que a célula "preço atual" só pode ser alterada
se ninguém mexeu nela desde que você olhou. Se alguém mexeu, sua alteração é
recusada e você olha de novo.

```text
SEM PROTEÇÃO                            COM ESCRITA CONDICIONAL
Ana lê 100     Beto lê 100              Ana grava (versão 0) -> OK, versão 1
Ana grava 101  Beto grava 101           Beto grava (versão 0) -> FALHA
dois vencedores                         Beto relê 101 -> 409 abaixo_do_minimo
```

## 3. As três perguntas que a API responde (2 min)

| Pergunta | Mecanismo | Resultado |
|---|---|---|
| Esse lance já foi processado? | `Idempotency-Key` gravada na mesma transação | reenvio recebe a mesma resposta |
| O preço que eu vi ainda é o atual? | `versao` do leilão na condição de escrita | quem perdeu é reavaliado |
| Em que ordem as coisas aconteceram? | `seq` incrementada no commit | ordem do servidor, nunca do celular |

## 4. Tour pelo código (3 min)

Mostre nesta ordem; é uma **arquitetura hexagonal**:

1. **`dominio.py` → `avaliar_lance`**: regras puras, sem banco nem HTTP. Fácil de testar.
2. **`servico.py` → `dar_lance`**: ler → decidir → gravar; em conflito de versão, tenta de novo.
3. **`repositorio.py` → `gravar_decisao`**: a porta e o adaptador em memória; idempotência, versão e evento numa operação atômica.
4. **`repositorio_dynamodb.py`**: o mesmo contrato em produção, com `TransactWriteItems`. "Trocar o banco não muda o serviço."
5. **`api.py`**: códigos HTTP corretos (`201`, `409`, `422`, `429`, `503`), validação, rate limit e o SSE `/ao-vivo`.

## 5. Demonstração ao vivo (2 min)

```bash
python -m unittest -v
```

Destaque `test_50_requisicoes_simultaneas_mesmo_valor_um_vencedor`: 50
requisições HTTP reais largam juntas (com uma barreira) e o resultado é
exatamente um `201` e 49 `409`.

```bash
python servidor.py
```

Em outro terminal, abra `curl -N .../ao-vivo` e dê lances com `curl`: o novo
preço aparece em tempo real. Repita o mesmo `Idempotency-Key` para mostrar o
header `Idempotent-Replayed: true`.

```bash
python simular.py
```

Mostre `corridas_detectadas_e_reavaliadas` (dezenas de milhares) e
`invariantes: OK`. Com `--http`, a distribuição de status e a latência p50/p95/p99.

## 6. Arquitetura AWS (2 min): abra o `.drawio`

- **Laranja (síncrono):** CloudFront + WAF → ALB → Fargate → DynamoDB condicional. Só o necessário para decidir.
- **Roxo (assíncrono):** Streams → Lambda → WebSocket para o tempo real; Scheduler → Lambda para encerrar.
- **Verde (operação):** DLQ, CloudWatch e X-Ray.

> "O caminho do lance tem uma única escrita. Tudo o que pode falhar sem
> invalidar o lance fica fora dele."

## 7. Perguntas prováveis

**Por que não um lock (Redis, `synchronized`)?** O lock é mais um sistema
para falhar, e o TTL pode expirar no meio. Com a condição no banco, conferir
e gravar são a mesma operação.

**E com 100 mil lances/s num único leilão?** Uma chave do DynamoDB aceita
cerca de 1.000 escritas/s. O cache do preço recusa a maioria antes do banco;
para escala extrema, um sequenciador por leilão com resposta assíncrona.

**Como escala horizontalmente?** A API é stateless; todo o estado está no
DynamoDB. Basta adicionar containers.

**E se o cliente não receber a resposta?** Reenvia com a mesma chave e
recebe a mesma decisão. Se usar a chave com outro valor, recebe `422`.

**Por que `409` para lance baixo?** É um conflito com o estado atual do
recurso: alguém foi mais rápido. O corpo traz o preço atual para o cliente
oferecer o próximo valor.

**Por que SSE na POC e WebSocket na AWS?** SSE é unidirecional e cabe na
biblioteca padrão; na AWS, o API Gateway WebSocket gerencia milhões de
conexões sem servidor.

**Por que biblioteca padrão e não FastAPI?** Zero dependências: qualquer
pessoa roda com `python servidor.py`. A camada HTTP é fina e isolada, então
trocar por FastAPI muda só o `api.py`.

## 8. Limitações e próximos passos

- Rodar o `RepositorioDynamoDB` contra o DynamoDB Local nos testes.
- Autenticação real (JWT) e WebSocket.
- IaC (CDK/Terraform), CI/CD e teste de carga distribuído.
- Cache do preço atual para o pré-filtro do leilão quente.

## 9. Uso de IA

Usei IA a partir de uma especificação ([`SPEC.md`](SPEC.md)) com critérios de
aceite. As decisões (controle otimista, idempotência, eventos pelo Stream,
códigos HTTP) foram minhas, e cada etapa só foi aceita com os testes passando.
