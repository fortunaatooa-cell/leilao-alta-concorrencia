# Entenda o projeto em 10 minutos

Este texto explica a solução sem jargão, do problema até a AWS. Cada termo
técnico aparece só depois de a ideia estar clara.

---

## 1. O problema, em uma cena

Um relógio raro está em leilão. Faltam 5 segundos para acabar.
**Cem mil pessoas** olham o preço na tela, **R$ 1.000**, e clicam ao mesmo
tempo em "dar lance de R$ 1.001".

O sistema precisa garantir quatro coisas:

| # | Garantia | Em português simples |
|---|---|---|
| 1 | **Validade** | só ganha quem realmente cobriu o último preço |
| 2 | **Sem duplicidade** | clicar duas vezes não vira dois lances |
| 3 | **Ordem justa** | "quem chegou primeiro" é decidido pelo servidor, não pelo relógio do celular |
| 4 | **Tempo real** | todo mundo vê o novo preço na hora |

---

## 2. Onde está a armadilha: dois vencedores

O jeito ingênuo de programar um lance é:

```text
1. leia o preço atual
2. se o meu valor for maior, grave o meu valor
```

Parece certo. Agora veja duas pessoas fazendo isso **ao mesmo tempo**:

```text
            Ana                         Beto
t1   lê o preço: R$ 1.000        lê o preço: R$ 1.000
t2   1.001 > 1.000? sim          1.001 > 1.000? sim
t3   grava R$ 1.001 ✅           grava R$ 1.001 ✅
                  ⚠️  dois vencedores com o mesmo lance
```

Os dois leram o preço **antes** de o outro gravar. Cada um decidiu com uma
informação que já estava velha. Isso tem nome: **race condition** (condição
de corrida). Com cem mil pessoas, acontece o tempo todo.

---

## 3. A solução: "só salva se ninguém mexeu"

Pense num **documento compartilhado com número de revisão**.

- Você abre a **revisão 7** e edita.
- Na hora de salvar, o sistema confere: *"o documento ainda está na revisão 7?"*
  - **Sim** → salva, e ele vira revisão 8.
  - **Não** (alguém salvou antes) → seu salvamento é recusado. Você abre a
    versão nova e decide de novo.

O leilão faz exatamente isso. Cada leilão tem um número de **versão**:

```text
            Ana                              Beto
t1   lê: R$ 1.000 (versão 0)          lê: R$ 1.000 (versão 0)
t2   "salve se ainda for versão 0"    "salve se ainda for versão 0"
t3   ✅ salvou → agora versão 1       ❌ recusado: já é versão 1
t4                                    relê: R$ 1.001 (versão 1)
t5                                    1.001 não cobre 1.001 + R$ 1
                                      → "lance abaixo do mínimo"
```

Um vencedor só. O ponto mais importante é que **conferir e salvar acontecem
juntos, numa operação só, dentro do banco de dados**. Não sobra nenhum
instante entre "olhei" e "gravei" para outra pessoa se meter.

> **Nome técnico:** controle de concorrência otimista, ou escrita
> condicional. "Otimista" porque não tranca nada antes: tenta salvar e, se
> alguém passou na frente, tenta de novo.

---

## 4. Clicou duas vezes? A senha do restaurante

A internet falha. O celular manda o lance, a resposta não volta, e o
aplicativo tenta de novo. Sem cuidado, o mesmo lance seria contado duas vezes.

A solução é igual à **senha de pedido de um restaurante**:

- Cada lance sai do celular com um **código único** (a `Idempotency-Key`).
- Se o mesmo código chegar de novo, o sistema não faz outro "prato": só
  **entrega a mesma resposta** da primeira vez.

E esse código é gravado **na mesma operação** do item 3. Ou seja, numa única
gravação o banco confere duas coisas: *"esse código é novo?"* e *"o leilão
ainda está na versão que eu li?"*.

> **Nome técnico:** idempotência. Repetir a operação dá o mesmo resultado
> que fazê-la uma vez.

---

## 5. Quem chegou primeiro?

O relógio do celular de cada um pode estar adiantado, atrasado ou até ser
alterado de propósito. Por isso ele **não decide nada**.

A ordem oficial é a **ordem em que o banco aceitou as gravações**: revisão 1,
2, 3... Como só uma gravação vence por vez (item 3), nunca existe empate:
alguém sempre chegou antes.

---

## 6. Todo mundo vê o novo preço

Quando um lance é aceito, quem está assistindo precisa saber na hora.
Mas **avisar as pessoas não pode atrasar o lance**.

Então a ordem é:

1. **Primeiro** o lance é decidido e salvo, e quem deu o lance recebe a resposta.
2. **Depois**, separadamente, o sistema avisa todo mundo do novo preço.

Se o aviso falhar, o lance continua valendo. Quem estiver em dúvida pode
consultar o preço a qualquer momento.

> **Na AWS:** o banco (DynamoDB) gera um evento a cada gravação (*Streams*),
> uma função (*Lambda*) pega esse evento e o envia para todos os celulares
> conectados (*WebSocket*).

---

## 7. O caminho de um lance, do clique à tela

```text
 📱 Ana clica "R$ 1.001"
  │
  ▼
 🛡️ Portaria (CloudFront + WAF)
  │   bloqueia robôs e excesso de cliques
  ▼
 ⚙️ API de lances (ECS Fargate)
  │   confere as regras: leilão aberto? valor válido? cobre o mínimo?
  ▼
 🗄️ Banco (DynamoDB)
  │   "salve SÓ SE o código é novo E a versão não mudou"
  │
  ├──► ✅ responde para a Ana: "aceito" (em milissegundos)
  │
  └──► 📣 evento de "novo preço" (em paralelo)
         │
         ▼
       📱📱📱 todos veem R$ 1.001 na tela
```

---

## 8. As respostas da API, como um semáforo

| Resposta | Significado para o usuário |
|---|---|
| 🟢 **201** | "Seu lance foi aceito, você está ganhando." |
| 🟢 **mesma resposta de antes** | "Já recebemos esse lance, está tudo certo." (clique repetido) |
| 🟡 **409** | "Alguém foi mais rápido. O preço agora é X, quer cobrir?" |
| 🔴 **422** | "Esse leilão já acabou" ou "valor inválido". |
| 🔴 **429** | "Calma, você está mandando lances rápido demais." |

---

## 9. Regras extras (pensando além do enunciado)

- **Incremento mínimo:** cada lance precisa superar o anterior em pelo menos
  R$ 1,00. Evita uma enxurrada de lances de um centavo.
- **Anti-sniping (lance de última hora):** um lance aceito nos últimos 30
  segundos estende o leilão em 30 segundos. Assim ninguém ganha só por
  clicar no último milissegundo.
- **Quem já está ganhando não cobre o próprio lance.**
- **Limite de velocidade por usuário:** protege contra robôs.

---

## 10. E se o leilão for MUITO disputado?

Um único leilão popular concentra todo o tráfego num único registro do banco.
Esse registro aguenta cerca de 1.000 gravações por segundo.

O truque: **a maioria dos lances nem precisa chegar ao banco.** Se o preço
atual já é R$ 5.000 e alguém manda R$ 4.000, a API recusa na hora, olhando
uma cópia rápida do preço. Só os lances que têm chance real de ganhar
disputam a gravação.

---

## 11. Como eu provo que funciona

Não basta dizer que está certo. Os testes **provocam** a corrida de propósito:

- **50 pessoas ao mesmo tempo, mesmo valor:** 50 requisições saem juntas
  (seguradas por uma "largada" sincronizada). Resultado obrigatório:
  **exatamente 1 aceito e 49 "alguém foi mais rápido"**.
- **O mesmo lance enviado 30 vezes ao mesmo tempo:** resultado obrigatório:
  **1 lance registrado**.
- **Simulação com 20 mil lances:** ao final, o programa confere que:
  - cada lance tem uma única decisão;
  - os lances aceitos só sobem de valor;
  - o vencedor final é o último lance aceito;
  - o leilão encerrado não aceitou nada.

Na [página interativa](pagina/leilao.html) dá para **desligar a proteção** e
ver essas regras quebrando na hora.

---

## 12. Como o código está organizado

Pense em camadas de uma cebola. O miolo não sabe nada das camadas de fora:

```text
┌───────────────────────────────────────────────┐
│ api.py               a porta de entrada (HTTP) │
│ ┌───────────────────────────────────────────┐ │
│ │ servico.py         o "garçom": lê, decide, │ │
│ │                    grava, tenta de novo    │ │
│ │ ┌───────────────────────────────────────┐ │ │
│ │ │ dominio.py       as regras do leilão  │ │ │
│ │ │                  (puras, sem banco)   │ │ │
│ │ └───────────────────────────────────────┘ │ │
│ └───────────────────────────────────────────┘ │
│ repositorio*.py      onde os dados moram       │
│                      (memória hoje, DynamoDB   │
│                       em produção)             │
└───────────────────────────────────────────────┘
```

A vantagem: trocar a memória pelo DynamoDB **não muda nenhuma regra**. As
regras são testadas sozinhas, rapidamente.

> **Nome técnico:** arquitetura hexagonal (portas e adaptadores).

---

## 13. Resumo para guardar

> **Um lance só é aceito se o banco conseguir gravá-lo de uma vez,
> conferindo na mesma operação que ele é novo e que ninguém mudou o leilão
> desde a leitura. Quem perde a corrida relê e tenta de novo. Todo o resto
> (avisar as pessoas, encerrar o leilão, monitorar) acontece em paralelo,
> sem atrasar nem colocar em risco o lance.**

| Conceito | Analogia | Nome técnico |
|---|---|---|
| Só salva se ninguém mexeu | documento com número de revisão | controle otimista / escrita condicional |
| Clique repetido não duplica | senha do restaurante | idempotência |
| Ordem decidida pelo servidor | ordem de chegada no balcão | sequência de commit |
| Avisar sem atrasar | o garçom serve, depois o painel atualiza | processamento assíncrono (eventos) |
| Regras separadas do banco | miolo da cebola | arquitetura hexagonal |

---

**Para ir além:** [ARQUITETURA_AWS.md](ARQUITETURA_AWS.md) (decisões técnicas),
[GUIA_ENTREVISTA.md](GUIA_ENTREVISTA.md) (roteiro da apresentação) e
[SPEC.md](SPEC.md) (especificação usada para construir).
