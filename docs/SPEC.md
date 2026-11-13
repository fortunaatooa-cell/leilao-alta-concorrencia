# SPEC — Leilão online de alta concorrência

Especificação (*spec-driven development*) usada para construir este projeto
com apoio de IA. Ela define **o que** o sistema garante e **como validar**; a
implementação foi feita etapa por etapa, cada uma aceita só com os critérios
de aceite passando.

## Contexto
Desafio técnico para vaga de Engenharia de Dados. Leilões online com centenas de milhares
de usuários dando lances simultâneos nos últimos segundos. Entrevista em contexto bancário:
governança, LGPD, auditoria e reconciliação importam.

## Objetivo
1. Decidir cada lance em tempo real, com ordem de chegada determinística, sem aceitar lance
   inválido ou duplicado por race condition.
2. Transformar cada decisão em dado confiável nas camadas SOR → SOT → SPEC.

## Requisitos funcionais
- **RF1** Lance = {lance_id (UUID do cliente), leilao_id, usuario_id, valor_cents}.
- **RF2** Aceitar só se: valor > 0, leilão aberto, valor ≥ preço atual + incremento mínimo
  (1º lance pode ser igual ao preço inicial), usuário não é o vencedor atual.
- **RF3** Anti-sniping: lance aceito nos últimos 30 s estende o fim em 30 s.
- **RF4** Idempotência: reenvio do mesmo lance_id devolve a decisão original, sem nova decisão.
- **RF5** Ordem oficial = ordem de commit no servidor (sequência por leilão), nunca o relógio do cliente.
- **RF6** Toda decisão (aceita ou rejeitada, com motivo) gera um evento na mesma transação (outbox).

## Decisões técnicas obrigatórias
- Controle de concorrência otimista: escrita condicional "grava se versão = versão lida
  E lance_id não existe". Quem perde a corrida relê e é reavaliado.
- Regras de negócio puras (sem I/O) separadas de repositório e serviço.
- Repositório em memória com a mesma semântica do DynamoDB TransactWriteItems.

## Pipeline de dados
- **SOR:** eventos como chegaram; simular entrega at-least-once (≈5% duplicados) e fora de ordem.
- **SOT:** contrato de esquema (inválido → quarentena), dedup por lance_id, reordenação por
  (leilao_id, sequencia), usuario_id pseudonimizado (SHA-256 com sal).
- **SPEC:** por leilão — recebidos, aceitos, taxa de aceite, participantes únicos, preço final,
  vencedor (pseudônimo) — e motivos de rejeição.
- **Data Quality:** unicidade, completude, sequência sem buracos, aceitos estritamente
  crescentes, reconciliação SPEC × estado operacional, ausência de PII.

## Restrições
- Python 3.10+, apenas biblioteca padrão. Código e docs em português.
- Estrutura: `leilao/{dominio,repositorio,servico,simulacao,dados}.py`, `simular.py`, `tests/`.
- Latência de banco simulada para provocar corridas reais entre threads.

## Critérios de aceite
- **CA1** 50 threads, mesmo valor, largada simultânea (Barrier) → exatamente 1 aceito.
- **CA2** Mesmo lance_id enviado por 30 threads → 1 decisão e 1 evento.
- **CA3** Simulação com 20k lances / 64 threads, 70% num leilão "quente", 8% de retries e
  1 leilão já encerrado → todas as invariantes e regras de DQ passam.
- **CA4** Leilão encerrado não aceita nenhum lance.
- **CA5** `python -m unittest` verde; `python simular.py` imprime transacional, camadas e DQ.

## Entregáveis
1. Código + testes.
2. Diagrama AWS em .drawio com ícones aws4 em 3 faixas: (1) API Gateway → Lambda → DynamoDB
   condicional; (2) DynamoDB Streams → Kinesis → Firehose → S3 SOR; (3) Glue ETL + DQ →
   S3 SOT/SPEC (Iceberg) → Athena → QuickSight, com Lake Formation e CloudWatch.
3. `docs/ARQUITETURA_AWS.md`: decisões, modelo de dados, hot partition, falhas, LGPD,
   POC → AWS. Citar SOR≈Bronze, SOT≈Silver, SPEC≈Gold; DynamoDB = base operacional.
4. `docs/GUIA_ENTREVISTA.md`: roteiro de 10 min, analogia, demo, perguntas prováveis, uso de IA.
5. Página HTML única com simulador no navegador (toggle "sem proteção" mostrando DQ falhando),
   diagrama e explicação.

## Fora de escopo
Infra real na AWS, IaC, autenticação real, benchmark de capacidade.
