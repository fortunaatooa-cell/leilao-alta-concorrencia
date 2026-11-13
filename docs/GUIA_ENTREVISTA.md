# Guia para a apresentação (explicação didática)

Roteiro de cerca de 10 minutos, pensado para uma entrevista de **dados**.

---

## 1. Como enquadrar (30 s)

> "Vi o problema em duas metades. A primeira é **decidir o lance certo em
> tempo real**, sem race condition. A segunda, que é o coração de dados, é
> **transformar cada decisão em dado confiável**, sabendo que o stream
> duplica e embaralha eventos. No fim eu provo que o dado analítico bate com
> o sistema transacional."

## 2. O problema com uma analogia (1 min)

Um leiloeiro com mil pessoas gritando "R$ 500!" no mesmo segundo precisa:
ouvir um de cada vez, aceitar só quem supera o último valor, não contar duas
vezes quem repetiu o grito e anotar tudo num livro que depois vai ser
auditado.

A race condition em software:

```text
Ana lê preço = 100      Beto lê preço = 100
Ana grava 101           Beto grava 101        <- dois vencedores!
```

## 3. Metade 1 — decidir o lance (2 min)

**Uma frase:** o lance vira uma escrita condicional, "grave só se o leilão
ainda está na versão que eu li e este `lance_id` ainda não existe".

```text
Ana grava (versão 0) -> OK, versão passa a 1
Beto grava (versão 0) -> FALHA -> relê (preço 101) -> 101 não supera 101 + incremento -> rejeitado
```

| Conceito | Em uma linha |
|---|---|
| Controle otimista | a checagem e a escrita são a mesma operação atômica no DynamoDB |
| Idempotência | o `lance_id` é a chave; um retry devolve a mesma resposta |
| Ordem determinística | a ordem é a do commit no servidor, não o relógio do celular |

## 4. Metade 2 — o dado confiável (3 min) ⭐

Esta é a parte que mais conversa com o time de dados. Uso as camadas
**SOR → SOT → SPEC**:

| Camada | Em uma frase |
|---|---|
| **SOR** (System of Record) | "o que a origem mandou, exatamente como veio" |
| **SOT** (Source of Truth) | "a versão oficial e confiável: limpa, única e ordenada" |
| **SPEC** (Specialized) | "a visão pronta para uma área usar" |

(Se alguém falar em medalhão: SOR ≈ Bronze, SOT ≈ Silver, SPEC ≈ Gold.)

1. **CDC como outbox:** o DynamoDB Stream emite um evento para cada gravação.
   Se gravou, o evento existe. Nada se perde entre "decidir" e "publicar".
2. **SOR:** chega tudo como veio. O Kinesis/Firehose garante
   *at-least-once*, então **haverá duplicados e eventos fora de ordem**. A
   simulação injeta isso de propósito (cerca de 5% de duplicados e ordem
   embaralhada).
3. **SOT:**
   - **contrato de esquema**: um registro inválido vai para quarentena e o pipeline não para;
   - **dedup pela chave de negócio** `lance_id` (não pelo offset do stream);
   - **reordenação** por `(leilao_id, sequencia)`, a sequência gravada pelo transacional;
   - **LGPD**: o `usuario_id` vira um pseudônimo (hash com sal). Dá para
     contar participantes e fazer joins, mas não para identificar a pessoa.
4. **SPEC:** preço final, vencedor, taxa de aceite, participantes únicos e
   motivos de rejeição. Respondem perguntas de negócio e de antifraude.
5. **Data Quality + reconciliação:** regras automáticas, e a principal é
   **"o preço final na SPEC é igual ao preço no DynamoDB"**. Em um banco,
   conciliar o analítico com o operacional é o que dá confiança para
   alguém tomar decisão com o dado.
6. **Governança:** catálogo e Lake Formation (quem vê o quê). Encaixa num
   **data mesh**: o domínio Leilões publica um produto de dados com dono,
   contrato e SLA de qualidade.

## 5. Demonstração (2 min)

```bash
python simular.py
```

Leia os três blocos impressos:
- **Transacional:** `corridas_detectadas_e_reavaliadas` mostra dezenas de
  milhares de disputas resolvidas; `invariantes: OK`.
- **Dados:** `linhas_sor` > `linhas_sot`; a diferença são os
  duplicados removidos.
- **Qualidade:** todas as regras com `[PASSOU]`.

Depois abra `saida/sot/lances.csv` (usuário pseudonimizado) e
`saida/spec/resumo_leiloes.csv`.

```bash
python -m unittest -v
```

Destaques: `test_mesmo_valor_ao_mesmo_tempo_so_um_vence` (50 threads, 1
aceito) e `test_sot_remove_duplicados_e_reordena`.

## 6. Diagrama (2 min) — siga as três faixas

1. **Laranja, transacional:** API Gateway → Lambda → DynamoDB (escrita condicional).
2. **Roxo, ingestão:** Stream (CDC) → Kinesis → Firehose → S3 SOR.
3. **Verde, dados:** Glue (ETL + Data Quality) → S3 SOT/SPEC (Iceberg) → Athena → QuickSight, com Lake Formation governando.

> "Escolhi poucos serviços, todos gerenciados. O caminho que responde ao
> usuário tem só o necessário; todo o resto é assíncrono e não pode atrasar
> nem derrubar o lance."

## 7. Perguntas prováveis

**Por que não usar o timestamp para ordenar?** O relógio do cliente é
manipulável e dessincronizado. O servidor define a sequência no commit, e é
ela que a SOT usa para reordenar.

**Por que deduplicar de novo no lake, se o transacional já é idempotente?**
São garantias diferentes: o transacional garante 1 decisão por lance; o
stream pode **entregar** a mesma decisão mais de uma vez. Cada camada protege
a sua fronteira.

**Por que Iceberg?** Permite MERGE (dedup idempotente em reprocessamentos),
*time travel* (auditoria: "como estava a tabela ontem?") e evolução de
esquema.

**Como você sabe que o dado está certo?** Pelas regras de Data Quality e,
principalmente, pela reconciliação com a base operacional (DynamoDB), com alarme no
CloudWatch se divergir.

**E a LGPD?** O lake não guarda o id real. A chave do hash fica no Secrets
Manager e o Lake Formation restringe colunas. Para reidentificar, seria
preciso acesso controlado e auditado ao transacional.

**E se um leilão receber 100 mil lances/s?** Um pré-filtro com o preço em
cache recusa a maioria antes do banco, e só os candidatos plausíveis vão
para a escrita condicional. A alternativa extrema é um sequenciador por
leilão (Kinesis particionado por `leilao_id`), com resposta assíncrona.

**Por que Python com threads, se tem GIL?** A POC prova **correção** sob
concorrência (com latência de banco simulada, as threads realmente disputam
o mesmo leilão). Desempenho se mede com teste de carga na AWS.

## 8. Próximos passos (mostra maturidade)

- Trocar o repositório em memória por DynamoDB real (mesma interface).
- Escrever o job SOT em PySpark/Glue sobre Iceberg e as regras em DQDL.
- IaC (CDK/Terraform), CI e teste de carga.
- SPEC de antifraude: detectar contas com padrão de bot.

## 9. Uso de IA

Usei IA para acelerar o código, os testes e a documentação. As decisões
(escrita condicional, CDC como outbox, dedup e reordenação na SOT,
pseudonimização e reconciliação) foram revisadas por mim e estão cobertas
por testes.
