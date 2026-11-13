# SPEC — Leilão online de alta concorrência

Especificação (*spec-driven development*) usada para construir este projeto
com apoio de IA. Ela define **o que** o sistema garante e **como validar**; a
implementação foi feita etapa por etapa, cada uma aceita só com os critérios
de aceite passando.

## Contexto
Desafio técnico para vaga de Engenharia de Software. Leilões online com centenas de
milhares de usuários dando lances simultâneos nos últimos segundos.

## Objetivo
Uma API de lances que responde em tempo real, com ordem de chegada determinística e sem
aceitar lance inválido ou duplicado por race condition, mais o desenho AWS para produção.

## Requisitos funcionais
- **RF1** `POST /leiloes/{id}/lances` com `{valor_cents}`, headers `Idempotency-Key` (UUID) e
  `X-Usuario-Id` (stub do JWT). Também `POST /leiloes`, `GET /leiloes/{id}`, `GET /saude`.
- **RF2** Aceitar só se: valor > 0, leilão aberto, valor ≥ preço atual + incremento mínimo
  (1º lance pode ser igual ao preço inicial), usuário não é o vencedor atual.
- **RF3** Anti-sniping: lance aceito nos últimos 30 s estende o fim em 30 s.
- **RF4** Idempotência: mesma chave devolve a resposta original com `Idempotent-Replayed: true`;
  mesma chave com outro conteúdo → `422`.
- **RF5** Ordem oficial = ordem de commit no servidor (sequência por leilão).
- **RF6** Tempo real: `GET /leiloes/{id}/ao-vivo` (SSE) envia cada novo preço; cliente lento
  não bloqueia o leilão.
- **RF7** Status HTTP: `201` aceito, `409` abaixo do mínimo, `422` encerrado/inválido,
  `400` entrada inválida, `401` sem usuário, `404` inexistente, `429` rate limit, `503` disputa excessiva.

## Decisões técnicas obrigatórias
- Controle de concorrência otimista: escrita condicional "grava se versão = versão lida
  E chave não existe". Quem perde a corrida relê e é reavaliado.
- Arquitetura hexagonal: domínio puro; serviço; porta de repositório com adaptador em
  memória e adaptador DynamoDB (`TransactWriteItems`); camada HTTP fina.
- Rate limit por usuário (token bucket). Backlog de conexões dimensionado para picos.

## Restrições
- Python 3.10+, apenas biblioteca padrão (`http.server`, `urllib`, `threading`). Docs em português.
- Estrutura: `leilao/{dominio,servico,repositorio,repositorio_dynamodb,notificacoes,api,simulacao}.py`,
  `servidor.py`, `simular.py`, `tests/`, `Dockerfile`.

## Critérios de aceite
- **CA1** 50 requisições HTTP simultâneas (Barrier), mesmo valor → exatamente um `201` e 49 `409`.
- **CA2** Mesma chave enviada por 30 threads → 1 decisão e 1 evento; versão do leilão = 1.
- **CA3** Simulação no serviço com 20k lances / 64 threads, 70% num leilão "quente", 8% de
  retries e 1 leilão encerrado → todas as invariantes passam.
- **CA4** Leilão encerrado → `422`; nenhum lance aceito.
- **CA5** Assinante do `/ao-vivo` recebe o novo preço após um lance aceito.
- **CA6** `python -m unittest` verde; `python simular.py --http` mostra status e latência p50/p95/p99.

## Entregáveis
1. Código + testes + Dockerfile.
2. Diagrama AWS (.drawio, ícones aws4) em 3 faixas: (1) CloudFront+WAF → ALB → ECS Fargate →
   DynamoDB condicional, com Cognito; (2) Streams → Lambda → API Gateway WebSocket, e
   EventBridge Scheduler → Lambda de encerramento; (3) SQS DLQ, CloudWatch, X-Ray.
3. `docs/ARQUITETURA_AWS.md`: decisões e alternativas, hot partition, falhas, segurança, POC → AWS.
4. `docs/GUIA_ENTREVISTA.md`: roteiro de 10 min, analogia, demo, perguntas prováveis, uso de IA.
5. Página HTML única com simulador no navegador (toggle "sem proteção"), diagrama e explicação.

## Fora de escopo
Infra real na AWS, IaC, autenticação real, benchmark de capacidade na nuvem.
