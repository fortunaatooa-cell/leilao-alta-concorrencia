"""Adaptador DynamoDB — o mesmo contrato do RepositorioEmMemoria, em produção.

Não é usado nos testes (precisa de `boto3` e de uma tabela real ou do DynamoDB
Local). Existe para mostrar que trocar a infraestrutura não muda o serviço:
o lock + checagem de versão em memória vira um `TransactWriteItems` condicional.

Tabela única `Leilao` (single-table design):
    PK = "LEILAO#<id>"  -> estado do leilão (preco, versao, encerra_em, vencedor, seq)
    PK = "LANCE#<id>"   -> decisão do lance (idempotência)
DynamoDB Streams ligado (NEW_IMAGE) = outbox para notificações e auditoria.
"""
from __future__ import annotations

from dataclasses import replace
import json

from .dominio import Decisao, EstadoLeilao, Lance, Motivo, RegrasLeilao, Status
from .repositorio import Resultado, ResultadoGravacao


class RepositorioDynamoDB:
    def __init__(self, tabela: str = "Leilao", cliente=None):
        if cliente is None:
            import boto3  # import tardio: o projeto roda sem boto3
            cliente = boto3.client("dynamodb")
        self._ddb = cliente
        self._tabela = tabela

    # ---------------------------------------------------------------- leitura
    def obter_leilao(self, leilao_id: str) -> EstadoLeilao | None:
        r = self._ddb.get_item(TableName=self._tabela, Key={"pk": {"S": f"LEILAO#{leilao_id}"}},
                               ConsistentRead=True)   # leitura forte: evita decidir com preço velho
        i = r.get("Item")
        if not i:
            return None
        return EstadoLeilao(
            leilao_id=leilao_id,
            preco_atual_cents=int(i["preco"]["N"]),
            encerra_em=float(i["encerra_em"]["N"]),
            regras=RegrasLeilao(incremento_minimo_cents=int(i["incremento"]["N"])),
            lance_vencedor_id=i.get("lance_vencedor", {}).get("S"),
            usuario_vencedor_id=i.get("usuario_vencedor", {}).get("S"),
            versao=int(i["versao"]["N"]),
        )

    def buscar_decisao(self, lance_id: str) -> Decisao | None:
        r = self._ddb.get_item(TableName=self._tabela, Key={"pk": {"S": f"LANCE#{lance_id}"}},
                               ConsistentRead=True)
        i = r.get("Item")
        return _decisao_de_json(i["decisao"]["S"]) if i else None

    # ---------------------------------------------------------------- escrita
    def criar_leilao(self, e: EstadoLeilao) -> bool:
        try:
            self._ddb.put_item(
                TableName=self._tabela,
                Item={"pk": {"S": f"LEILAO#{e.leilao_id}"}, "preco": {"N": str(e.preco_atual_cents)},
                      "encerra_em": {"N": str(e.encerra_em)}, "versao": {"N": "0"}, "seq": {"N": "0"},
                      "incremento": {"N": str(e.regras.incremento_minimo_cents)}},
                ConditionExpression="attribute_not_exists(pk)")
            return True
        except self._ddb.exceptions.ConditionalCheckFailedException:
            return False

    def gravar_decisao(self, decisao: Decisao, versao_esperada: int,
                       novo_estado: EstadoLeilao | None) -> ResultadoGravacao:
        leilao_pk = {"pk": {"S": f"LEILAO#{decisao.lance.leilao_id}"}}
        # A sequência é incrementada no próprio item do leilão, dentro da transação.
        atualizacao = {
            "TableName": self._tabela, "Key": leilao_pk,
            "ConditionExpression": "versao = :v",
            "UpdateExpression": "SET seq = seq + :um",
            "ExpressionAttributeValues": {":v": {"N": str(versao_esperada)}, ":um": {"N": "1"}},
        }
        if decisao.status is Status.ACEITO:
            atualizacao["UpdateExpression"] += (", preco = :p, versao = :nv, encerra_em = :fim,"
                                                " lance_vencedor = :l, usuario_vencedor = :u")
            atualizacao["ConditionExpression"] += " AND :agora < encerra_em"
            atualizacao["ExpressionAttributeValues"].update({
                ":p": {"N": str(novo_estado.preco_atual_cents)}, ":nv": {"N": str(novo_estado.versao)},
                ":fim": {"N": str(novo_estado.encerra_em)}, ":l": {"S": decisao.lance.lance_id},
                ":u": {"S": decisao.lance.usuario_id}, ":agora": {"N": str(decisao.decidido_em)}})
        try:
            self._ddb.transact_write_items(TransactItems=[
                {"Put": {"TableName": self._tabela,
                         "Item": {"pk": {"S": f"LANCE#{decisao.lance.lance_id}"},
                                  "decisao": {"S": _decisao_para_json(decisao)}},
                         "ConditionExpression": "attribute_not_exists(pk)"}},
                {"Update": atualizacao},
            ])
        except self._ddb.exceptions.TransactionCanceledException as erro:
            motivos = [c.get("Code") for c in erro.response.get("CancellationReasons", [])]
            if motivos and motivos[0] == "ConditionalCheckFailed":
                return ResultadoGravacao(Resultado.LANCE_JA_EXISTE, self.buscar_decisao(decisao.lance.lance_id))
            return ResultadoGravacao(Resultado.CONFLITO_DE_VERSAO)
        # A sequência exata pode ser lida do Stream; aqui usamos a versão como referência.
        return ResultadoGravacao(Resultado.GRAVADO, replace(decisao, sequencia_no_leilao=decisao.versao_leilao))


def _decisao_para_json(d: Decisao) -> str:
    return json.dumps({"lance": d.lance.__dict__, "status": d.status.value, "motivo": d.motivo.value,
                       "antes": d.preco_antes_cents, "depois": d.preco_depois_cents, "versao": d.versao_leilao,
                       "fim": d.encerra_em, "em": d.decidido_em, "seq": d.sequencia_no_leilao})


def _decisao_de_json(s: str) -> Decisao:
    j = json.loads(s)
    return Decisao(Lance(**j["lance"]), Status(j["status"]), Motivo(j["motivo"]), j["antes"], j["depois"],
                   j["versao"], j["fim"], j["em"], j["seq"])
