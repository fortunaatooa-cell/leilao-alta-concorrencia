"""Persistência do leilão — em memória, imitando o contrato do DynamoDB.

O ponto central é `gravar_decisao`: uma única operação ATÔMICA que

1. só grava se o `lance_id` ainda não existir         (idempotência)
2. só atualiza o leilão se a versão for a esperada    (controle otimista)
3. grava o evento na outbox na mesma transação        (não perde eventos)

Na AWS isso é um `TransactWriteItems` com `ConditionExpression`s; aqui é um
lock por leilão. O serviço não sabe qual dos dois está por baixo.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import threading
from typing import Protocol

from .dominio import Decisao, EstadoLeilao, Status


class Resultado(str, Enum):
    GRAVADO = "gravado"
    CONFLITO_DE_VERSAO = "conflito_de_versao"   # alguém mudou o leilão antes
    LANCE_JA_EXISTE = "lance_ja_existe"         # retry de um lance já decidido


@dataclass(frozen=True)
class ResultadoGravacao:
    resultado: Resultado
    decisao: Decisao | None = None


class RepositorioLeiloes(Protocol):
    def obter_leilao(self, leilao_id: str) -> EstadoLeilao | None: ...
    def criar_leilao(self, estado: EstadoLeilao) -> bool: ...
    def buscar_decisao(self, lance_id: str) -> Decisao | None: ...
    def gravar_decisao(self, decisao: Decisao, versao_esperada: int,
                       novo_estado: EstadoLeilao | None) -> ResultadoGravacao: ...


class RepositorioEmMemoria:
    def __init__(self, leiloes: list[EstadoLeilao] | None = None):
        leiloes = leiloes or []
        self._leiloes = {e.leilao_id: e for e in leiloes}
        self._locks = {e.leilao_id: threading.Lock() for e in leiloes}
        self._sequencias = {e.leilao_id: 0 for e in leiloes}
        self._decisoes: dict[str, Decisao] = {}       # tabela de idempotência
        self._lock_decisoes = threading.Lock()
        self.outbox: list[Decisao] = []               # "DynamoDB Streams"
        self._lock_cadastro = threading.Lock()

    def criar_leilao(self, estado: EstadoLeilao) -> bool:
        """Cria se não existir (na AWS: PutItem com attribute_not_exists)."""
        with self._lock_cadastro:
            if estado.leilao_id in self._leiloes:
                return False
            self._locks[estado.leilao_id] = threading.Lock()
            self._sequencias[estado.leilao_id] = 0
            self._leiloes[estado.leilao_id] = estado
            return True

    def obter_leilao(self, leilao_id: str) -> EstadoLeilao | None:
        return self._leiloes.get(leilao_id)  # leitura de um objeto imutável: segura

    def buscar_decisao(self, lance_id: str) -> Decisao | None:
        with self._lock_decisoes:
            return self._decisoes.get(lance_id)

    def gravar_decisao(self, decisao: Decisao, versao_esperada: int,
                       novo_estado: EstadoLeilao | None) -> ResultadoGravacao:
        leilao_id = decisao.lance.leilao_id
        with self._locks[leilao_id]:                    # início da "transação"
            with self._lock_decisoes:
                existente = self._decisoes.get(decisao.lance.lance_id)
            if existente is not None:
                return ResultadoGravacao(Resultado.LANCE_JA_EXISTE, existente)
            if self._leiloes[leilao_id].versao != versao_esperada:
                return ResultadoGravacao(Resultado.CONFLITO_DE_VERSAO)

            self._sequencias[leilao_id] += 1
            gravada = replace(decisao, sequencia_no_leilao=self._sequencias[leilao_id])
            if gravada.status is Status.ACEITO:
                assert novo_estado is not None and novo_estado.versao == versao_esperada + 1
                self._leiloes[leilao_id] = novo_estado
            with self._lock_decisoes:
                self._decisoes[gravada.lance.lance_id] = gravada
            self.outbox.append(gravada)
            return ResultadoGravacao(Resultado.GRAVADO, gravada)  # fim da "transação"

    def todas_decisoes(self) -> list[Decisao]:
        with self._lock_decisoes:
            return list(self._decisoes.values())

    def todos_leiloes(self) -> list[EstadoLeilao]:
        return list(self._leiloes.values())
