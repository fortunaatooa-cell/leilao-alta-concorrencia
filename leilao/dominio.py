"""Regras de negócio do leilão — código puro, sem threads, I/O ou relógio.

Tudo aqui é determinístico: dado um estado e um lance, a decisão é sempre a
mesma. Isso torna as regras fáceis de testar e de reaproveitar na AWS
(ex.: a mesma função rodando dentro do container da Bid API).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Status(str, Enum):
    ACEITO = "aceito"
    REJEITADO = "rejeitado"


class Motivo(str, Enum):
    LANCE_VENCEDOR = "lance_vencedor"
    VALOR_INVALIDO = "valor_invalido"
    ABAIXO_DO_MINIMO = "abaixo_do_minimo"
    LEILAO_ENCERRADO = "leilao_encerrado"
    LEILAO_NAO_ENCONTRADO = "leilao_nao_encontrado"
    JA_E_O_VENCEDOR = "usuario_ja_e_o_vencedor"
    CONTENCAO_EXCESSIVA = "contencao_excessiva_tente_novamente"


@dataclass(frozen=True)
class RegrasLeilao:
    incremento_minimo_cents: int = 100          # lance seguinte precisa superar em R$ 1,00
    janela_anti_sniping_s: float = 30.0         # lance nos últimos 30 s...
    extensao_anti_sniping_s: float = 30.0       # ...empurra o fim para agora + 30 s


@dataclass(frozen=True)
class EstadoLeilao:
    """Um item por leilão (na AWS: item `LEILAO#<id>` no DynamoDB)."""
    leilao_id: str
    preco_atual_cents: int
    encerra_em: float                 # epoch em segundos (relógio do servidor)
    regras: RegrasLeilao = RegrasLeilao()
    lance_vencedor_id: str | None = None
    usuario_vencedor_id: str | None = None
    versao: int = 0                   # cresce 1 a cada lance aceito (controle otimista)

    def valor_minimo_aceito(self) -> int:
        # Sem lances, o preço inicial já é aceito; depois, exige o incremento.
        if self.lance_vencedor_id is None:
            return self.preco_atual_cents
        return self.preco_atual_cents + self.regras.incremento_minimo_cents


@dataclass(frozen=True)
class Lance:
    lance_id: str          # gerado pelo cliente (UUID) = chave de idempotência
    leilao_id: str
    usuario_id: str
    valor_cents: int


@dataclass(frozen=True)
class Decisao:
    lance: Lance
    status: Status
    motivo: Motivo
    preco_antes_cents: int
    preco_depois_cents: int
    versao_leilao: int
    encerra_em: float
    decidido_em: float
    sequencia_no_leilao: int = 0   # atribuída pelo repositório no commit
    reenvio: bool = False          # True quando é a resposta de um retry


def avaliar_lance(estado: EstadoLeilao, lance: Lance, agora: float) -> tuple[Status, Motivo, EstadoLeilao]:
    """Decide um lance contra um estado. Retorna (status, motivo, novo_estado)."""
    if lance.valor_cents <= 0:
        return Status.REJEITADO, Motivo.VALOR_INVALIDO, estado
    if agora >= estado.encerra_em:
        return Status.REJEITADO, Motivo.LEILAO_ENCERRADO, estado
    if estado.usuario_vencedor_id == lance.usuario_id:
        return Status.REJEITADO, Motivo.JA_E_O_VENCEDOR, estado
    if lance.valor_cents < estado.valor_minimo_aceito():
        return Status.REJEITADO, Motivo.ABAIXO_DO_MINIMO, estado

    encerra_em = estado.encerra_em
    if encerra_em - agora <= estado.regras.janela_anti_sniping_s:
        encerra_em = max(encerra_em, agora + estado.regras.extensao_anti_sniping_s)

    novo = replace(
        estado,
        preco_atual_cents=lance.valor_cents,
        lance_vencedor_id=lance.lance_id,
        usuario_vencedor_id=lance.usuario_id,
        versao=estado.versao + 1,
        encerra_em=encerra_em,
    )
    return Status.ACEITO, Motivo.LANCE_VENCEDOR, novo
