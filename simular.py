#!/usr/bin/env python3
"""Teste de carga local.

    python simular.py                 # direto no serviço: 20k lances, prova as invariantes
    python simular.py --http          # ponta a ponta pela API HTTP, com latência p50/p95/p99
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import random
import threading
import time
import urllib.error
import urllib.request
import uuid

from leilao.api import Aplicacao, LimitadorDeTaxa, criar_servidor
from leilao.dominio import EstadoLeilao
from leilao.simulacao import ConfigSimulacao, executar


def carga_no_servico(a) -> None:
    _, _, _, resumo = executar(ConfigSimulacao(lances=a.lances, threads=a.threads, seed=a.seed))
    print(json.dumps({k: v for k, v in resumo.items() if k != "vencedores"}, indent=2, ensure_ascii=False))


def carga_http(a) -> None:
    app = Aplicacao(limitador=LimitadorDeTaxa(por_segundo=1e9, rajada=10**9))
    app.repo.criar_leilao(EstadoLeilao("relogio-raro", 100_000, time.time() + 3600))
    srv = criar_servidor(app, porta=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    rng = random.Random(a.seed)

    def usuario(i):
        with urllib.request.urlopen(base + "/leiloes/relogio-raro", timeout=10) as r:
            minimo = json.loads(r.read())["proximo_lance_minimo_cents"]
        corpo = json.dumps({"valor_cents": minimo + 100 * rng.randint(0, 3)}).encode()
        req = urllib.request.Request(base + "/leiloes/relogio-raro/lances", data=corpo, method="POST",
                                     headers={"Idempotency-Key": str(uuid.uuid4()), "X-Usuario-Id": f"u{i}",
                                              "Content-Type": "application/json"})
        inicio = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                codigo = r.status
        except urllib.error.HTTPError as e:
            codigo = e.code
        return codigo, (time.perf_counter() - inicio) * 1000

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=a.threads) as pool:
        resultados = list(pool.map(usuario, range(a.lances)))
    duracao = time.perf_counter() - t0
    srv.shutdown()

    lat = sorted(ms for _, ms in resultados)
    pct = lambda p: round(lat[min(len(lat) - 1, int(p / 100 * len(lat)))], 1)
    estado = app.repo.obter_leilao("relogio-raro")
    print(json.dumps({
        "requisicoes": len(resultados),
        "por_status_http": dict(Counter(c for c, _ in resultados)),
        "latencia_ms": {"p50": pct(50), "p95": pct(95), "p99": pct(99)},
        "requisicoes_por_s_local": round(len(resultados) / duracao, 1),
        "lances_aceitos": estado.versao,
        "preco_final_reais": estado.preco_atual_cents / 100,
        "corridas_resolvidas": app.servico.conflitos_de_versao,
    }, indent=2, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description="Teste de carga do leilão")
    p.add_argument("--http", action="store_true", help="dispara pela API HTTP em vez do serviço")
    p.add_argument("--lances", type=int, default=None)
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    a.lances = a.lances or (2_000 if a.http else 20_000)
    a.threads = a.threads or (8 if a.http else 64)
    if min(a.lances, a.threads) < 1:
        p.error("--lances e --threads devem ser maiores que zero")
    (carga_http if a.http else carga_no_servico)(a)


if __name__ == "__main__":
    main()
