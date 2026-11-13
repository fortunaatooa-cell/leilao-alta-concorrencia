#!/usr/bin/env python3
"""Executa a simulação de lances concorrentes.

    python simular.py
    python simular.py --lances 100000 --threads 128
"""
import argparse
import json
from pathlib import Path

from leilao.dados import executar_pipeline
from leilao.simulacao import ConfigSimulacao, executar


def main() -> None:
    p = argparse.ArgumentParser(description="Simula lances simultâneos em leilões online")
    p.add_argument("--lances", type=int, default=20_000)
    p.add_argument("--usuarios", type=int, default=5_000)
    p.add_argument("--threads", type=int, default=64)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--saida", type=Path, default=Path("saida"))
    a = p.parse_args()
    if min(a.lances, a.usuarios, a.threads) < 1:
        p.error("--lances, --usuarios e --threads devem ser maiores que zero")

    cfg = ConfigSimulacao(lances=a.lances, usuarios=a.usuarios, threads=a.threads, seed=a.seed)
    repo, _, _, resumo = executar(cfg)
    (a.saida).mkdir(parents=True, exist_ok=True)
    (a.saida / "resumo_transacional.json").write_text(json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")
    dados = executar_pipeline(repo.outbox, repo.todos_leiloes(), a.saida)

    print("== 1. Camada transacional (decisão do lance) ==")
    print(json.dumps({k: v for k, v in resumo.items() if k != "vencedores"}, indent=2, ensure_ascii=False))
    print("\n== 2. Plataforma de dados (SOR -> SOT -> SPEC) ==")
    print(json.dumps(dados["camadas"], indent=2, ensure_ascii=False))
    print("\n== 3. Qualidade de dados ==")
    for c in dados["qualidade"]:
        print(f"  [{c['resultado']}] {c['regra']}")
    print(f"\nArquivos gerados em: {a.saida.resolve()}")


if __name__ == "__main__":
    main()
