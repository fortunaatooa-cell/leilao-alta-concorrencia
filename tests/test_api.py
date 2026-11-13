"""Testes ponta a ponta: servidor HTTP real numa porta livre, chamado via urllib."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest
import urllib.error
import urllib.request
import uuid

from leilao.api import Aplicacao, LimitadorDeTaxa, criar_servidor

AGORA = 1_000.0


def http(base, metodo, caminho, corpo=None, headers=None):
    dados = None if corpo is None else json.dumps(corpo).encode()
    req = urllib.request.Request(base + caminho, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


class BaseAPI(unittest.TestCase):
    def setUp(self):
        self.relogio = [AGORA]
        self.app = Aplicacao(relogio=lambda: self.relogio[0],
                             limitador=LimitadorDeTaxa(por_segundo=1000, rajada=1000))
        self.srv = criar_servidor(self.app, porta=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        st, _, _ = http(self.base, "POST", "/leiloes",
                        {"leilao_id": "L1", "preco_inicial_cents": 10_000, "duracao_s": 3600})
        self.assertEqual(st, 201)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def lance(self, valor, usuario="ana", chave=None, leilao="L1"):
        return http(self.base, "POST", f"/leiloes/{leilao}/lances", {"valor_cents": valor},
                    {"Idempotency-Key": chave or str(uuid.uuid4()), "X-Usuario-Id": usuario})


class TestContrato(BaseAPI):
    def test_lance_aceito_retorna_201_e_atualiza_o_get(self):
        st, corpo, _ = self.lance(10_000)
        self.assertEqual((st, corpo["status"], corpo["sequencia"]), (201, "aceito", 1))
        st, leilao, _ = http(self.base, "GET", "/leiloes/L1")
        self.assertEqual((leilao["preco_atual_cents"], leilao["proximo_lance_minimo_cents"]), (10_000, 10_100))

    def test_reenvio_com_mesma_chave_devolve_a_mesma_resposta(self):
        chave = str(uuid.uuid4())
        st1, c1, _ = self.lance(12_000, chave=chave)
        st2, c2, h2 = self.lance(12_000, chave=chave)
        self.assertEqual((st1, c1), (st2, c2))
        self.assertEqual(h2.get("Idempotent-Replayed"), "true")
        self.assertEqual(http(self.base, "GET", "/leiloes/L1")[1]["versao"], 1)

    def test_mesma_chave_com_outro_valor_e_rejeitada(self):
        chave = str(uuid.uuid4())
        self.lance(12_000, chave=chave)
        st, corpo, _ = self.lance(99_000, chave=chave)
        self.assertEqual((st, corpo["erro"]), (422, "idempotency_key_reutilizada"))

    def test_lance_baixo_retorna_409(self):
        self.lance(10_000, "ana")
        st, corpo, _ = self.lance(10_050, "beto")
        self.assertEqual((st, corpo["motivo"]), (409, "abaixo_do_minimo"))

    def test_leilao_encerrado_retorna_422(self):
        self.relogio[0] = AGORA + 4000
        st, corpo, _ = self.lance(50_000)
        self.assertEqual((st, corpo["motivo"]), (422, "leilao_encerrado"))

    def test_validacoes_de_entrada(self):
        sem_chave = http(self.base, "POST", "/leiloes/L1/lances", {"valor_cents": 1}, {"X-Usuario-Id": "ana"})
        self.assertEqual(sem_chave[0], 400)
        sem_usuario = http(self.base, "POST", "/leiloes/L1/lances", {"valor_cents": 1}, {"Idempotency-Key": str(uuid.uuid4())})
        self.assertEqual(sem_usuario[0], 401)
        self.assertEqual(self.lance("100")[0], 400)
        self.assertEqual(self.lance(20_000, leilao="NAO_EXISTE")[0], 404)
        self.assertEqual(http(self.base, "POST", "/leiloes", {"leilao_id": "L1", "preco_inicial_cents": 1})[0], 409)

    def test_limite_de_taxa_retorna_429(self):
        self.app.limitador = LimitadorDeTaxa(por_segundo=0.001, rajada=2)
        codigos = [self.lance(20_000 + i * 100, usuario="bot")[0] for i in range(4)]
        self.assertEqual(codigos[-1], 429)


class TestConcorrenciaHTTP(BaseAPI):
    def test_50_requisicoes_simultaneas_mesmo_valor_um_vencedor(self):
        barreira = threading.Barrier(50)

        def disparar(i):
            barreira.wait()
            return self.lance(10_000, usuario=f"u{i}")[0]

        with ThreadPoolExecutor(max_workers=50) as pool:
            codigos = list(pool.map(disparar, range(50)))
        self.assertEqual(codigos.count(201), 1)
        self.assertEqual(codigos.count(409), 49)
        self.assertEqual(http(self.base, "GET", "/leiloes/L1")[1]["versao"], 1)


class TestTempoReal(BaseAPI):
    def test_ao_vivo_recebe_novo_preco(self):
        resp = urllib.request.urlopen(self.base + "/leiloes/L1/ao-vivo", timeout=10)
        self.assertEqual(resp.readline().strip(), b"event: estado")
        resp.readline(); resp.readline()              # data + linha em branco
        self.lance(15_000)
        self.assertEqual(resp.readline().strip(), b"event: novo_preco")
        evento = json.loads(resp.readline().decode()[len("data: "):])
        self.assertEqual(evento["preco_atual_cents"], 15_000)
        resp.close()


if __name__ == "__main__":
    unittest.main()
