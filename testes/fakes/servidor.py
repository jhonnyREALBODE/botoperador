"""Servidor das páginas falsas das duas casas (testes de navegador)."""
import json
import os
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PASTA = os.path.dirname(os.path.abspath(__file__))
ESTADO = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urlparse(self.path).path
        if p.startswith("/mv/"):
            # domínio de referência que redireciona (por JS, no meio da carga) para o espelho atual
            porta = self.server.server_address[1]
            corpo = ("<html><head><script>location.replace('http://localhost:%d%s?cda_id=abc&token=xyz')</script>"
                     "</head><body>redirecionando...</body></html>" % (porta, p[3:])).encode()
            tipo = "text/html; charset=utf-8"
        elif p.startswith("/fakes/"):
            arq = os.path.join(PASTA, os.path.basename(p))
            corpo = open(arq, "rb").read()
            tipo = "application/javascript"
        elif p.startswith("/betting/"):
            corpo = open(os.path.join(PASTA, "1awin.html"), encoding="utf-8").read().replace("__ESTADO__", json.dumps(ESTADO)).encode()
            tipo = "text/html; charset=utf-8"
        elif p.startswith("/pt/"):
            corpo = open(os.path.join(PASTA, "ps.html"), encoding="utf-8").read().replace("__ESTADO__", json.dumps(ESTADO)).encode()
            tipo = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def iniciar():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]
