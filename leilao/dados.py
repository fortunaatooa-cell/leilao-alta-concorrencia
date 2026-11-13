"""Pipeline de dados: eventos de lance -> SOR -> SOT -> SPEC + qualidade.

Simula o que acontece na plataforma de dados (Kinesis -> Firehose -> S3 ->
Glue): a entrega é *at-least-once* e fora de ordem, então a SOT precisa
deduplicar e reordenar. A SPEC é reconciliada com o sistema operacional
(DynamoDB = base operacional que decide o lance) para provar que o dado analítico é confiável.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import random

from .dominio import Decisao, EstadoLeilao

ESQUEMA_SOT = {  # contrato de dados: coluna -> tipo
    "leilao_id": str, "sequencia": int, "lance_id": str, "usuario_pseudo": str,
    "valor_cents": int, "status": str, "motivo": str,
    "preco_antes_cents": int, "preco_depois_cents": int, "versao": int, "decidido_em": float,
}


def pseudonimizar(usuario_id: str, sal: str = "sal-guardado-no-secrets-manager") -> str:
    """LGPD: o lake não guarda o id real; o hash com sal permite joins sem expor a pessoa."""
    return hashlib.sha256(f"{sal}:{usuario_id}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------- SOR
def entregar_como_kinesis(outbox: list[Decisao], seed: int = 7,
                          prob_duplicado: float = 0.05) -> list[dict]:
    """Imita o stream: reentrega alguns eventos e embaralha a ordem."""
    rng = random.Random(seed)
    entregas = []
    for d in outbox:
        ev = asdict(d)
        ev["status"], ev["motivo"] = d.status.value, d.motivo.value
        bruto = json.dumps(ev, ensure_ascii=False)
        entregas.append(bruto)
        if rng.random() < prob_duplicado:
            entregas.append(bruto)
    rng.shuffle(entregas)
    return [{"offset_ingestao": i, "payload": p} for i, p in enumerate(entregas)]


# ---------------------------------------------------------------- SOT
def construir_sot(sor: list[dict]) -> tuple[list[dict], dict]:
    vistos: dict[str, dict] = {}
    rejeitados_esquema = 0
    for linha in sor:
        try:
            ev = json.loads(linha["payload"])
            lance = ev["lance"]
            reg = {
                "leilao_id": lance["leilao_id"], "sequencia": ev["sequencia_no_leilao"],
                "lance_id": lance["lance_id"], "usuario_pseudo": pseudonimizar(lance["usuario_id"]),
                "valor_cents": lance["valor_cents"], "status": ev["status"], "motivo": ev["motivo"],
                "preco_antes_cents": ev["preco_antes_cents"], "preco_depois_cents": ev["preco_depois_cents"],
                "versao": ev["versao_leilao"], "decidido_em": ev["decidido_em"],
            }
            if any(not isinstance(reg[c], t) for c, t in ESQUEMA_SOT.items()):
                raise TypeError
        except (KeyError, TypeError, json.JSONDecodeError):
            rejeitados_esquema += 1      # na AWS: vai para uma pasta de quarentena
            continue
        vistos.setdefault(reg["lance_id"], reg)   # dedup pela chave de negócio
    sot = sorted(vistos.values(), key=lambda r: (r["leilao_id"], r["sequencia"]))
    return sot, {"linhas_sor": len(sor), "duplicados_removidos": len(sor) - len(sot) - rejeitados_esquema,
                    "quarentena_esquema": rejeitados_esquema, "linhas_sot": len(sot)}


# ---------------------------------------------------------------- SPEC
def construir_spec(sot: list[dict]) -> tuple[list[dict], list[dict]]:
    por_leilao: dict[str, list[dict]] = defaultdict(list)
    for r in sot:
        por_leilao[r["leilao_id"]].append(r)
    resumo, motivos = [], []
    for leilao_id, linhas in sorted(por_leilao.items()):
        aceitos = [r for r in linhas if r["status"] == "aceito"]
        ultimo = aceitos[-1] if aceitos else None
        resumo.append({
            "leilao_id": leilao_id,
            "lances_recebidos": len(linhas),
            "lances_aceitos": len(aceitos),
            "taxa_aceite_pct": round(100 * len(aceitos) / len(linhas), 2),
            "participantes_unicos": len({r["usuario_pseudo"] for r in linhas}),
            "preco_final_reais": (ultimo["valor_cents"] / 100) if ultimo else None,
            "vencedor_pseudo": ultimo["usuario_pseudo"] if ultimo else None,
        })
        for motivo, qtd in Counter(r["motivo"] for r in linhas).most_common():
            motivos.append({"leilao_id": leilao_id, "motivo": motivo, "quantidade": qtd})
    return resumo, motivos


# ---------------------------------------------------------------- Qualidade
def checar_qualidade(sot: list[dict], spec: list[dict], estados: list[EstadoLeilao]) -> list[dict]:
    """Regras de data quality (na AWS: Glue Data Quality / DQDL)."""
    checks = []

    def check(nome, ok, detalhe=""):
        checks.append({"regra": nome, "resultado": "PASSOU" if ok else "FALHOU", "detalhe": detalhe})

    ids = [r["lance_id"] for r in sot]
    check("unicidade(lance_id)", len(ids) == len(set(ids)))
    check("completude(colunas obrigatórias)",
          all(r[c] is not None for r in sot for c in ESQUEMA_SOT))
    por_leilao: dict[str, list[dict]] = defaultdict(list)
    for r in sot:
        por_leilao[r["leilao_id"]].append(r)
    seq_ok = all([r["sequencia"] for r in ls] == list(range(1, len(ls) + 1)) for ls in por_leilao.values())
    check("sequencia sem buracos por leilão (ordem determinística)", seq_ok)
    cresc_ok = all(
        all(b["valor_cents"] > a["valor_cents"] for a, b in zip(ac, ac[1:]))
        for ac in ([r for r in ls if r["status"] == "aceito"] for ls in por_leilao.values()))
    check("lances aceitos estritamente crescentes", cresc_ok)
    sor = {e.leilao_id: e for e in estados}
    reconc = all(
        (g["preco_final_reais"] is None and sor[g["leilao_id"]].lance_vencedor_id is None)
        or g["preco_final_reais"] == sor[g["leilao_id"]].preco_atual_cents / 100
        for g in spec)
    check("reconciliação SPEC x sistema operacional (DynamoDB)", reconc,
          "preço final da SPEC = preço do registro operacional")
    check("sem PII no lake", not any("usuario-" in json.dumps(r) for r in sot))
    return checks


# ---------------------------------------------------------------- Escrita
def _csv(caminho: Path, linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as f:
        if linhas:
            w = csv.DictWriter(f, fieldnames=list(linhas[0]))
            w.writeheader()
            w.writerows(linhas)


def executar_pipeline(outbox: list[Decisao], estados: list[EstadoLeilao], pasta: Path | None = None) -> dict:
    sor = entregar_como_kinesis(outbox)
    sot, metricas_sot = construir_sot(sor)
    spec, motivos = construir_spec(sot)
    qualidade = checar_qualidade(sot, spec, estados)
    if pasta is not None:
        pasta.mkdir(parents=True, exist_ok=True)
        _csv(pasta / "sor" / "eventos_lances_brutos.csv", sor)
        _csv(pasta / "sot" / "lances.csv", sot)
        _csv(pasta / "spec" / "resumo_leiloes.csv", spec)
        _csv(pasta / "spec" / "motivos_rejeicao.csv", motivos)
        (pasta / "qualidade.json").write_text(json.dumps(qualidade, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"camadas": metricas_sot, "qualidade": qualidade, "spec": spec}
