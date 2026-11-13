"""API HTTP do leilão — apenas biblioteca padrão (http.server).

Rotas
    GET  /saude
    POST /leiloes                       cria um leilão
    GET  /leiloes/{id}                  preço atual, vencedor, fim
    POST /leiloes/{id}/lances           dá um lance (header Idempotency-Key obrigatório)
    GET  /leiloes/{id}/ao-vivo          Server-Sent Events com cada novo preço

Autenticação simplificada: o usuário vem do header `X-Usuario-Id`. Em produção
viria do JWT validado (Cognito), nunca do corpo da requisição.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
import socket
import threading
import time
from typing import Callable

from .dominio import Decisao, EstadoLeilao, Lance, Motivo, RegrasLeilao, Status
from .notificacoes import Notificador
from .repositorio import RepositorioEmMemoria, RepositorioLeiloes
from .servico import ServicoDeLances

# Motivo da decisão -> status HTTP
STATUS_HTTP = {
    Motivo.LANCE_VENCEDOR: HTTPStatus.CREATED,
    Motivo.ABAIXO_DO_MINIMO: HTTPStatus.CONFLICT,          # alguém foi mais rápido
    Motivo.JA_E_O_VENCEDOR: HTTPStatus.CONFLICT,
    Motivo.VALOR_INVALIDO: HTTPStatus.UNPROCESSABLE_ENTITY,
    Motivo.LEILAO_ENCERRADO: HTTPStatus.UNPROCESSABLE_ENTITY,
    Motivo.LEILAO_NAO_ENCONTRADO: HTTPStatus.NOT_FOUND,
    Motivo.CONTENCAO_EXCESSIVA: HTTPStatus.SERVICE_UNAVAILABLE,
}


class LimitadorDeTaxa:
    """Token bucket por usuário (na AWS: WAF / API Gateway throttling)."""

    def __init__(self, por_segundo: float, rajada: int, relogio: Callable[[], float] = time.monotonic):
        self._taxa, self._rajada, self._relogio = por_segundo, rajada, relogio
        self._baldes: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def permitir(self, chave: str) -> bool:
        with self._lock:
            agora = self._relogio()
            fichas, ultimo = self._baldes.get(chave, (self._rajada, agora))
            fichas = min(self._rajada, fichas + (agora - ultimo) * self._taxa)
            if fichas < 1:
                self._baldes[chave] = (fichas, agora)
                return False
            self._baldes[chave] = (fichas - 1, agora)
            return True


@dataclass
class Aplicacao:
    """Composição das dependências (o que um container de DI faria)."""
    repo: RepositorioLeiloes = field(default_factory=RepositorioEmMemoria)
    notificador: Notificador = field(default_factory=Notificador)
    limitador: LimitadorDeTaxa = field(default_factory=lambda: LimitadorDeTaxa(por_segundo=20, rajada=40))
    relogio: Callable[[], float] = time.time
    servico: ServicoDeLances = field(init=False)

    def __post_init__(self):
        self.servico = ServicoDeLances(self.repo, relogio=self.relogio, ao_gravar=self.notificador.publicar)


def estado_json(e: EstadoLeilao, agora: float) -> dict:
    return {
        "leilao_id": e.leilao_id,
        "preco_atual_cents": e.preco_atual_cents,
        "proximo_lance_minimo_cents": e.valor_minimo_aceito(),
        "lance_vencedor_id": e.lance_vencedor_id,
        "usuario_vencedor_id": e.usuario_vencedor_id,
        "versao": e.versao,
        "encerra_em": e.encerra_em,
        "aberto": agora < e.encerra_em,
    }


def decisao_json(d: Decisao) -> dict:
    return {
        "lance_id": d.lance.lance_id,
        "status": d.status.value,
        "motivo": d.motivo.value,
        "valor_cents": d.lance.valor_cents,
        "preco_atual_cents": d.preco_depois_cents,
        "sequencia": d.sequencia_no_leilao,
        "encerra_em": d.encerra_em,
    }


ROTA_LEILAO = re.compile(r"^/leiloes/([A-Za-z0-9_-]{1,64})$")
ROTA_LANCES = re.compile(r"^/leiloes/([A-Za-z0-9_-]{1,64})/lances$")
ROTA_AO_VIVO = re.compile(r"^/leiloes/([A-Za-z0-9_-]{1,64})/ao-vivo$")


def criar_handler(app: Aplicacao) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "LeilaoAPI/1.0"

        def setup(self):
            super().setup()
            # resposta pequena + Nagle + ACK atrasado = ~40 ms por requisição; desliga Nagle
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # ------------------------------------------------------------ utilitários
        def log_message(self, *args):  # silencia o log padrão (barulhento em testes)
            pass

        def _responder(self, status: int, corpo: dict, extra: dict | None = None) -> None:
            dados = json.dumps(corpo, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(dados)

        def _erro(self, status: int, motivo: str, detalhe: str = "") -> None:
            self._responder(status, {"erro": motivo, "detalhe": detalhe})

        def _ler_json(self) -> dict | None:
            try:
                tamanho = int(self.headers.get("Content-Length", "0"))
                if tamanho > 10_000:
                    return None
                corpo = json.loads(self.rfile.read(tamanho) or b"{}")
                return corpo if isinstance(corpo, dict) else None
            except (ValueError, json.JSONDecodeError):
                return None

        # ------------------------------------------------------------ GET
        def do_GET(self):
            if self.path == "/saude":
                return self._responder(200, {"status": "ok"})
            if m := ROTA_LEILAO.match(self.path):
                e = app.repo.obter_leilao(m[1])
                if e is None:
                    return self._erro(404, "leilao_nao_encontrado")
                return self._responder(200, estado_json(e, app.relogio()))
            if m := ROTA_AO_VIVO.match(self.path):
                return self._ao_vivo(m[1])
            self._erro(404, "rota_nao_encontrada")

        def _ao_vivo(self, leilao_id: str) -> None:
            e = app.repo.obter_leilao(leilao_id)
            if e is None:
                return self._erro(404, "leilao_nao_encontrado")
            fila = app.notificador.assinar(leilao_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self._enviar_evento("estado", estado_json(e, app.relogio()))
                while True:
                    try:
                        self._enviar_evento("novo_preco", fila.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")   # mantém a conexão viva
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                app.notificador.cancelar(leilao_id, fila)

        def _enviar_evento(self, nome: str, dados: dict) -> None:
            self.wfile.write(f"event: {nome}\ndata: {json.dumps(dados)}\n\n".encode())
            self.wfile.flush()

        # ------------------------------------------------------------ POST
        def do_POST(self):
            if self.path == "/leiloes":
                return self._criar_leilao()
            if m := ROTA_LANCES.match(self.path):
                return self._dar_lance(m[1])
            self._erro(404, "rota_nao_encontrada")

        def _criar_leilao(self) -> None:
            corpo = self._ler_json()
            if corpo is None:
                return self._erro(400, "json_invalido")
            leilao_id = corpo.get("leilao_id")
            preco = corpo.get("preco_inicial_cents")
            duracao = corpo.get("duracao_s", 3600)
            incremento = corpo.get("incremento_minimo_cents", 100)
            if not (isinstance(leilao_id, str) and ROTA_LEILAO.match(f"/leiloes/{leilao_id}")):
                return self._erro(400, "leilao_id_invalido")
            if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (preco, duracao, incremento)):
                return self._erro(400, "valores_invalidos", "preço, duração e incremento devem ser inteiros > 0")
            estado = EstadoLeilao(leilao_id, preco, app.relogio() + duracao,
                                  RegrasLeilao(incremento_minimo_cents=incremento))
            if not app.repo.criar_leilao(estado):
                return self._erro(409, "leilao_ja_existe")
            self._responder(201, estado_json(estado, app.relogio()), {"Location": f"/leiloes/{leilao_id}"})

        def _dar_lance(self, leilao_id: str) -> None:
            chave = self.headers.get("Idempotency-Key", "").strip()
            usuario = self.headers.get("X-Usuario-Id", "").strip()
            if not usuario:
                return self._erro(401, "nao_autenticado", "envie o header X-Usuario-Id")
            if not (8 <= len(chave) <= 64):
                return self._erro(400, "idempotency_key_obrigatoria", "use um UUID por lance")
            corpo = self._ler_json()
            valor = None if corpo is None else corpo.get("valor_cents")
            if not isinstance(valor, int) or isinstance(valor, bool):
                return self._erro(400, "valor_cents_obrigatorio", "inteiro em centavos")
            if not app.limitador.permitir(usuario):
                return self._erro(429, "muitas_requisicoes")

            decisao = app.servico.dar_lance(Lance(chave, leilao_id, usuario, valor))
            if decisao.reenvio and (decisao.lance.usuario_id, decisao.lance.leilao_id,
                                    decisao.lance.valor_cents) != (usuario, leilao_id, valor):
                return self._erro(422, "idempotency_key_reutilizada", "mesma chave com outro conteúdo")
            extra = {"Idempotent-Replayed": "true"} if decisao.reenvio else {}
            if decisao.status is Status.ACEITO:
                extra["Location"] = f"/leiloes/{leilao_id}"
            self._responder(STATUS_HTTP[decisao.motivo], decisao_json(decisao), extra)

    return Handler


class ServidorConcorrente(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 1024   # backlog padrão é 5: picos de conexões seriam recusados


def criar_servidor(app: Aplicacao, host: str = "127.0.0.1", porta: int = 8000) -> ThreadingHTTPServer:
    return ServidorConcorrente((host, porta), criar_handler(app))
