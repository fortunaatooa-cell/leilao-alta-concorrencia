"""Tempo real: avisa quem está assistindo o leilão a cada novo lance aceito.

Em memória, cada assinante tem uma fila. Na AWS o mesmo papel é feito por
DynamoDB Streams -> Lambda -> API Gateway WebSocket, fora do caminho do lance:
se notificar falhar, o lance continua aceito (o cliente pode consultar o GET).
"""
from __future__ import annotations

from collections import defaultdict
import queue
import threading

from .dominio import Decisao, Status


class Notificador:
    def __init__(self, capacidade_por_assinante: int = 100):
        self._assinantes: dict[str, set[queue.Queue]] = defaultdict(set)
        self._lock = threading.Lock()
        self._capacidade = capacidade_por_assinante

    def assinar(self, leilao_id: str) -> queue.Queue:
        fila: queue.Queue = queue.Queue(maxsize=self._capacidade)
        with self._lock:
            self._assinantes[leilao_id].add(fila)
        return fila

    def cancelar(self, leilao_id: str, fila: queue.Queue) -> None:
        with self._lock:
            self._assinantes[leilao_id].discard(fila)

    def publicar(self, decisao: Decisao) -> None:
        if decisao.status is not Status.ACEITO:
            return
        evento = {
            "leilao_id": decisao.lance.leilao_id,
            "preco_atual_cents": decisao.lance.valor_cents,
            "sequencia": decisao.sequencia_no_leilao,
            "encerra_em": decisao.encerra_em,
        }
        with self._lock:
            filas = list(self._assinantes[decisao.lance.leilao_id])
        for fila in filas:
            try:
                fila.put_nowait(evento)
            except queue.Full:
                pass  # cliente lento não pode travar o leilão; ele recebe o próximo
