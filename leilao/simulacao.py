"""Teste de carga local: muitos usuários dando lances ao mesmo tempo.

Cada thread imita um usuário: lê o preço atual, soma um incremento e envia o
lance. Como centenas fazem isso juntas, muitos leem o MESMO preço e só um pode
vencer — exatamente a race condition do enunciado. Parte dos clientes reenvia
o mesmo lance (timeout/retry), e há lances inválidos e em leilão encerrado.

Ao final, `verificar_invariantes` prova que o resultado está correto.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import random
import threading
import time

from .dominio import Decisao, EstadoLeilao, Lance, RegrasLeilao, Status
from .repositorio import RepositorioEmMemoria
from .servico import ServicoDeLances

LEILAO_QUENTE = "leilao-001"        # item raro: concentra 70% do tráfego
LEILAO_ENCERRADO = "leilao-006"     # já terminou: todo lance deve ser recusado


@dataclass(frozen=True)
class ConfigSimulacao:
    lances: int = 20_000
    usuarios: int = 5_000
    threads: int = 64
    leiloes: int = 6
    prob_retry: float = 0.08
    prob_invalido: float = 0.02
    seed: int = 42


def criar_leiloes(qtd: int, agora: float) -> list[EstadoLeilao]:
    leiloes = []
    for i in range(1, qtd + 1):
        leilao_id = f"leilao-{i:03d}"
        encerra = agora - 1 if leilao_id == LEILAO_ENCERRADO else agora + 3600
        leiloes.append(EstadoLeilao(leilao_id, preco_atual_cents=100_00,
                                    encerra_em=encerra, regras=RegrasLeilao()))
    return leiloes


def executar(cfg: ConfigSimulacao) -> tuple[RepositorioEmMemoria, ServicoDeLances, list[Decisao], dict]:
    rng = random.Random(cfg.seed)
    repo = RepositorioEmMemoria(criar_leiloes(cfg.leiloes, time.time()))
    servico = ServicoDeLances(repo, latencia_simulada_s=0.0005)
    ids = [e.leilao_id for e in repo.todos_leiloes()]

    # Plano determinístico (seed); a execução concorrente é que é imprevisível.
    plano = []
    for i in range(cfg.lances):
        leilao = LEILAO_QUENTE if rng.random() < 0.70 else rng.choice(ids[1:])
        plano.append((f"lance-{i:07d}", leilao, f"usuario-{rng.randrange(cfg.usuarios):05d}",
                      rng.randint(1, 5), rng.random() < cfg.prob_invalido,
                      rng.random() < cfg.prob_retry))

    respostas: list[Decisao] = []
    locais = threading.local()

    def rng_local() -> random.Random:
        if not hasattr(locais, "rng"):
            locais.rng = random.Random()
        return locais.rng

    def usuario(passo) -> list[Decisao]:
        lance_id, leilao, usuario_id, multiplo, invalido, faz_retry = passo
        estado = servico.consultar(leilao)                 # "vê" o preço na tela
        valor = -1 if invalido else estado.valor_minimo_aceito() + (multiplo - 1) * estado.regras.incremento_minimo_cents
        time.sleep(rng_local().uniform(0, 0.002))          # "rede" do usuário: preço visto envelhece
        lance = Lance(lance_id, leilao, usuario_id, valor)
        r = [servico.dar_lance(lance)]
        if faz_retry:
            r.append(servico.dar_lance(lance))             # mesmo lance_id reenviado
        return r

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=cfg.threads) as pool:
        for r in pool.map(usuario, plano):
            respostas.extend(r)
    duracao = time.perf_counter() - inicio

    verificar_invariantes(repo, respostas, cfg.lances)
    resumo = montar_resumo(repo, servico, respostas, cfg, duracao)
    return repo, servico, respostas, resumo


def verificar_invariantes(repo: RepositorioEmMemoria, respostas: list[Decisao], lances_unicos: int) -> None:
    """Propriedades que TÊM de valer, independentemente da ordem das threads."""
    decisoes = repo.todas_decisoes()
    # 1. Todo lance único tem exatamente uma decisão gravada (nenhum perdido/duplicado).
    assert len(decisoes) == lances_unicos, (len(decisoes), lances_unicos)
    # 2. Outbox tem exatamente um evento por decisão (nada perdido entre decidir e publicar).
    assert len(repo.outbox) == len(decisoes)
    gravadas = {d.lance.lance_id: d for d in decisoes}
    # 3. Retry recebe exatamente a mesma resposta da primeira vez.
    for r in respostas:
        if r.reenvio:
            o = gravadas[r.lance.lance_id]
            assert (r.status, r.motivo, r.sequencia_no_leilao) == (o.status, o.motivo, o.sequencia_no_leilao)
    por_leilao: dict[str, list[Decisao]] = defaultdict(list)
    for d in decisoes:
        por_leilao[d.lance.leilao_id].append(d)
    for estado in repo.todos_leiloes():
        lista = sorted(por_leilao[estado.leilao_id], key=lambda d: d.sequencia_no_leilao)
        # 4. Ordem determinística: sequência 1..n sem buracos.
        assert [d.sequencia_no_leilao for d in lista] == list(range(1, len(lista) + 1))
        aceitos = [d for d in lista if d.status is Status.ACEITO]
        # 5. Cada aceito supera o anterior pelo incremento mínimo, versões 1..n.
        for a, b in zip(aceitos, aceitos[1:]):
            assert b.lance.valor_cents >= a.lance.valor_cents + estado.regras.incremento_minimo_cents
            assert b.preco_antes_cents == a.lance.valor_cents
        assert [d.versao_leilao for d in aceitos] == list(range(1, len(aceitos) + 1))
        # 6. Estado final = último aceito.
        if aceitos:
            assert estado.lance_vencedor_id == aceitos[-1].lance.lance_id
            assert estado.preco_atual_cents == aceitos[-1].lance.valor_cents
        if estado.leilao_id == LEILAO_ENCERRADO:
            assert not aceitos


def montar_resumo(repo, servico, respostas, cfg, duracao) -> dict:
    decisoes = repo.todas_decisoes()
    return {
        "lances_unicos": len(decisoes),
        "requisicoes_recebidas": len(respostas),
        "reenvios_idempotentes": sum(r.reenvio for r in respostas),
        "por_motivo": dict(Counter(d.motivo.value for d in decisoes).most_common()),
        "corridas_detectadas_e_reavaliadas": servico.conflitos_de_versao,
        "threads": cfg.threads,
        "duracao_s": round(duracao, 3),
        "requisicoes_por_s_local": round(len(respostas) / duracao, 1),
        "invariantes": "OK",
        "vencedores": {e.leilao_id: {"lance": e.lance_vencedor_id, "usuario": e.usuario_vencedor_id,
                                     "valor_reais": e.preco_atual_cents / 100, "lances_aceitos": e.versao}
                       for e in sorted(repo.todos_leiloes(), key=lambda e: e.leilao_id)},
    }
