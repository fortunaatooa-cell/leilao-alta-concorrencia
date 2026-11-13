import unittest

from leilao.dados import construir_sot, entregar_como_kinesis, executar_pipeline, pseudonimizar
from leilao.simulacao import ConfigSimulacao, executar


class TestPipelineDados(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo, _, _, _ = executar(ConfigSimulacao(lances=3_000, threads=32))

    def test_sot_remove_duplicados_e_reordena(self):
        sor = entregar_como_kinesis(self.repo.outbox, prob_duplicado=0.3)
        self.assertGreater(len(sor), len(self.repo.outbox))
        sot, m = construir_sot(sor)
        self.assertEqual(len(sot), len(self.repo.outbox))
        self.assertEqual(m["duplicados_removidos"], len(sor) - len(sot))

    def test_registro_corrompido_vai_para_quarentena(self):
        sor = entregar_como_kinesis(self.repo.outbox) + [{"offset_ingestao": -1, "payload": "{lixo"}]
        _, m = construir_sot(sor)
        self.assertEqual(m["quarentena_esquema"], 1)

    def test_todas_regras_de_qualidade_passam(self):
        r = executar_pipeline(self.repo.outbox, self.repo.todos_leiloes())
        self.assertTrue(all(c["resultado"] == "PASSOU" for c in r["qualidade"]), r["qualidade"])

    def test_pseudonimizacao_estavel_e_sem_pii(self):
        self.assertEqual(pseudonimizar("usuario-1"), pseudonimizar("usuario-1"))
        self.assertNotIn("usuario", pseudonimizar("usuario-1"))


if __name__ == "__main__":
    unittest.main()
