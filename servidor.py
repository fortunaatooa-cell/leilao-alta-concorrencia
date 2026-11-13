#!/usr/bin/env python3
"""Sobe a API do leilão com dois leilões de exemplo.

    python servidor.py              # http://127.0.0.1:8000
    python servidor.py --porta 9000
"""
import argparse
import time

from leilao.api import Aplicacao, criar_servidor
from leilao.dominio import EstadoLeilao


def main() -> None:
    p = argparse.ArgumentParser(description="API de lances em tempo real")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--porta", type=int, default=8000)
    a = p.parse_args()

    app = Aplicacao()
    agora = time.time()
    app.repo.criar_leilao(EstadoLeilao("relogio-raro", 100_000, agora + 3600))
    app.repo.criar_leilao(EstadoLeilao("ingresso-final", 50_000, agora + 600))

    servidor = criar_servidor(app, a.host, a.porta)
    print(f"API no ar em http://{a.host}:{a.porta}  (Ctrl+C para parar)")
    print("Leilões de exemplo: relogio-raro, ingresso-final")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        servidor.shutdown()


if __name__ == "__main__":
    main()
