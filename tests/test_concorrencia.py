from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from leilao.dominio import EstadoLeilao, Lance, Motivo, Status
from leilao.repositorio import RepositorioEmMemoria
from leilao.servico import ServicoDeLances
from leilao.simulacao import ConfigSimulacao, executar

AGORA = 1_000.0


def novo_servico():
    repo = RepositorioEmMemoria([EstadoLeilao("L1", 10_000, encerra_em=AGORA + 3600)])
    return repo, ServicoDeLances(repo, relogio=lambda: AGORA)


def em_paralelo(funcao, itens, threads=50):
    barreira = threading.Barrier(min(threads, len(itens)))

    def largar(item):
        barreira.wait()          # todas as threads largam juntas
        return funcao(item)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return list(pool.map(largar, itens))


class TestConcorrencia(unittest.TestCase):
    def test_mesmo_valor_ao_mesmo_tempo_so_um_vence(self):
        repo, s = novo_servico()
        lances = [Lance(f"b{i}", "L1", f"u{i}", 10_000) for i in range(50)]
        respostas = em_paralelo(s.dar_lance, lances)
        aceitos = [r for r in respostas if r.status is Status.ACEITO]
        self.assertEqual(len(aceitos), 1)
        self.assertTrue(all(r.motivo is Motivo.ABAIXO_DO_MINIMO for r in respostas if r not in aceitos))
        self.assertEqual(repo.obter_leilao("L1").lance_vencedor_id, aceitos[0].lance.lance_id)

    def test_mesmo_lance_reenviado_em_paralelo_gera_uma_decisao(self):
        repo, s = novo_servico()
        lance = Lance("b1", "L1", "u1", 15_000)
        respostas = em_paralelo(s.dar_lance, [lance] * 30, threads=30)
        self.assertEqual(len(repo.todas_decisoes()), 1)
        self.assertEqual(len(repo.outbox), 1)
        self.assertTrue(all(r.status is Status.ACEITO for r in respostas))
        self.assertEqual(sum(not r.reenvio for r in respostas), 1)
        self.assertEqual(repo.obter_leilao("L1").versao, 1)

    def test_valores_crescentes_concorrentes_mantem_ordem_valida(self):
        repo, s = novo_servico()
        lances = [Lance(f"b{i}", "L1", f"u{i}", 10_000 + i * 100) for i in range(200)]
        em_paralelo(s.dar_lance, lances)
        aceitos = sorted((d for d in repo.todas_decisoes() if d.status is Status.ACEITO),
                         key=lambda d: d.sequencia_no_leilao)
        valores = [d.lance.valor_cents for d in aceitos]
        self.assertEqual(valores, sorted(set(valores)))
        self.assertEqual(repo.obter_leilao("L1").preco_atual_cents, valores[-1])

    def test_leilao_inexistente(self):
        _, s = novo_servico()
        self.assertEqual(s.dar_lance(Lance("b1", "X", "u1", 1)).motivo, Motivo.LEILAO_NAO_ENCONTRADO)

    def test_simulacao_completa_respeita_invariantes(self):
        _, _, _, resumo = executar(ConfigSimulacao(lances=5_000, threads=32))
        self.assertEqual(resumo["invariantes"], "OK")
        self.assertEqual(resumo["lances_unicos"], 5_000)


if __name__ == "__main__":
    unittest.main()
