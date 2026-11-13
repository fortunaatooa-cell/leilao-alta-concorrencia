import unittest

from leilao.dominio import EstadoLeilao, Lance, Motivo, RegrasLeilao, Status, avaliar_lance

AGORA = 1_000.0


def leilao(**kw) -> EstadoLeilao:
    base = dict(leilao_id="L1", preco_atual_cents=10_000, encerra_em=AGORA + 3600, regras=RegrasLeilao())
    base.update(kw)
    return EstadoLeilao(**base)


def lance(valor, usuario="u1", lance_id="b1") -> Lance:
    return Lance(lance_id, "L1", usuario, valor)


class TestRegras(unittest.TestCase):
    def test_primeiro_lance_pode_ser_igual_ao_preco_inicial(self):
        status, motivo, novo = avaliar_lance(leilao(), lance(10_000), AGORA)
        self.assertEqual((status, motivo), (Status.ACEITO, Motivo.LANCE_VENCEDOR))
        self.assertEqual((novo.versao, novo.lance_vencedor_id), (1, "b1"))

    def test_lance_seguinte_exige_incremento_minimo(self):
        _, _, e = avaliar_lance(leilao(), lance(10_000), AGORA)
        status, motivo, _ = avaliar_lance(e, lance(10_099, "u2", "b2"), AGORA)
        self.assertEqual(motivo, Motivo.ABAIXO_DO_MINIMO)
        status, _, _ = avaliar_lance(e, lance(10_100, "u2", "b2"), AGORA)
        self.assertEqual(status, Status.ACEITO)

    def test_valor_invalido(self):
        for v in (0, -1):
            self.assertEqual(avaliar_lance(leilao(), lance(v), AGORA)[1], Motivo.VALOR_INVALIDO)

    def test_leilao_encerrado(self):
        e = leilao(encerra_em=AGORA)
        self.assertEqual(avaliar_lance(e, lance(99_999), AGORA)[1], Motivo.LEILAO_ENCERRADO)

    def test_vencedor_nao_cobre_o_proprio_lance(self):
        _, _, e = avaliar_lance(leilao(), lance(10_000), AGORA)
        self.assertEqual(avaliar_lance(e, lance(20_000, lance_id="b2"), AGORA)[1], Motivo.JA_E_O_VENCEDOR)

    def test_anti_sniping_estende_o_fim(self):
        e = leilao(encerra_em=AGORA + 5)          # faltam 5 s
        _, _, novo = avaliar_lance(e, lance(10_000), AGORA)
        self.assertEqual(novo.encerra_em, AGORA + 30)

    def test_sem_anti_sniping_fora_da_janela(self):
        e = leilao(encerra_em=AGORA + 600)
        self.assertEqual(avaliar_lance(e, lance(10_000), AGORA)[2].encerra_em, AGORA + 600)


if __name__ == "__main__":
    unittest.main()
