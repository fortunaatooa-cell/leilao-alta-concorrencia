"""Caso de uso "dar lance" — o que a Bid API (ECS/Fargate) executaria.

Fluxo: ler estado -> decidir (regra pura) -> gravar condicionalmente.
Se outro lance ganhou a corrida entre a leitura e a gravação, a gravação
falha por versão e o lance é REAVALIADO contra o estado novo. Assim nenhum
lance é aceito com base em um preço desatualizado (race condition).
"""
from __future__ import annotations

from dataclasses import replace
import time
from typing import Callable

from .dominio import Decisao, EstadoLeilao, Lance, Motivo, Status, avaliar_lance
from .repositorio import RepositorioLeiloes, Resultado


class ServicoDeLances:
    def __init__(self, repositorio: RepositorioLeiloes,
                 relogio: Callable[[], float] = time.time,
                 max_tentativas: int = 100,
                 latencia_simulada_s: float = 0.0,
                 ao_gravar: Callable[[Decisao], None] | None = None):
        self._repo = repositorio
        self._relogio = relogio
        self._max_tentativas = max_tentativas
        self._latencia = latencia_simulada_s  # imita a ida e volta ao banco (só na simulação)
        self._ao_gravar = ao_gravar   # ex.: notificar o novo preço (na AWS: Streams -> Lambda)
        self.conflitos_de_versao = 0   # métrica: quantas corridas foram detectadas

    def consultar(self, leilao_id: str) -> EstadoLeilao | None:
        return self._repo.obter_leilao(leilao_id)

    def dar_lance(self, lance: Lance) -> Decisao:
        # Atalho barato para retries: já decidido? devolve a MESMA resposta.
        anterior = self._repo.buscar_decisao(lance.lance_id)
        if anterior is not None:
            return replace(anterior, reenvio=True)

        for _ in range(self._max_tentativas):
            estado = self._repo.obter_leilao(lance.leilao_id)
            agora = self._relogio()
            if estado is None:
                return self._decisao_sem_gravar(lance, Motivo.LEILAO_NAO_ENCONTRADO, agora)

            status, motivo, novo = avaliar_lance(estado, lance, agora)
            if self._latencia:
                time.sleep(self._latencia)
            decisao = Decisao(
                lance=lance, status=status, motivo=motivo,
                preco_antes_cents=estado.preco_atual_cents,
                preco_depois_cents=novo.preco_atual_cents,
                versao_leilao=novo.versao, encerra_em=novo.encerra_em,
                decidido_em=agora,
            )
            r = self._repo.gravar_decisao(decisao, estado.versao,
                                          novo if status is Status.ACEITO else None)
            if r.resultado is Resultado.GRAVADO:
                if self._ao_gravar is not None:
                    self._ao_gravar(r.decisao)
                return r.decisao
            if r.resultado is Resultado.LANCE_JA_EXISTE:
                return replace(r.decisao, reenvio=True)
            self.conflitos_de_versao += 1   # perdeu a corrida: reavalia

        # Não gravado: o cliente pode reenviar com o mesmo lance_id com segurança.
        return self._decisao_sem_gravar(lance, Motivo.CONTENCAO_EXCESSIVA, self._relogio())

    @staticmethod
    def _decisao_sem_gravar(lance: Lance, motivo: Motivo, agora: float) -> Decisao:
        return Decisao(lance=lance, status=Status.REJEITADO, motivo=motivo,
                       preco_antes_cents=0, preco_depois_cents=0, versao_leilao=0,
                       encerra_em=0.0, decidido_em=agora)
