import os
import sys
import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, os.path.join(AQUI, "fakes"))

import programa as P  # noqa: E402
import servidor  # noqa: E402

TIMES = ["Phoenix Suns", "Charlotte Hornets"]
SINAL = {"id": "phoenix suns x charlotte hornets|total", "jogo": "Phoenix Suns x Charlotte Hornets",
         "times": TIMES, "liga": "", "mercado": "Total"}


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    """Nada de teste toca a pasta dados/ real do operador."""
    monkeypatch.setattr(P, "DADOS", str(tmp_path))
    monkeypatch.setattr(P, "PRINTS", str(tmp_path / "prints"))
    monkeypatch.setattr(P, "ARQ_URLS", str(tmp_path / "urls.json"))
    monkeypatch.setattr(P, "ARQ_LIGAS", str(tmp_path / "ligas.json"))
    monkeypatch.setattr(P, "ARQ_LOG", str(tmp_path / "log.txt"))
    monkeypatch.setattr(P, "ARQ_HIST", str(tmp_path / "hist.json"))
    P._MEM_URLS = None
    P._MEM_LIGAS = None
    servidor.ESTADO.clear()
    yield


@pytest.fixture
def cfg(tmp_path):
    return P.carregar_config(str(tmp_path / "nao-existe.json"), avisar=False)


@pytest.fixture(scope="session")
def site():
    srv, base = servidor.iniciar()
    yield base
    srv.shutdown()


@pytest.fixture(scope="session")
def casas(site, tmp_path_factory):
    d = tmp_path_factory.mktemp("perfis")
    cfg = P.carregar_config(str(d / "x.json"), avisar=False)
    cps = P.Casa(P.CASA_PS, cfg, base_url=site, perfil=str(d / "ps"), headless=True).iniciar()
    c1 = P.Casa(P.CASA_1W, cfg, base_url=site, perfil=str(d / "1w"), headless=True).iniciar()
    yield {P.CASA_1W: c1, P.CASA_PS: cps}
    c1.parar()
    cps.parar()


def oferta(casa, tipo, periodo, lado, linha, odd, time=None, time_idx=None):
    return {"tipo": tipo, "periodo": periodo, "lado": lado, "linha": linha, "odd": odd,
            "time": time, "time_idx": time_idx, "casa": casa, "_mid": "1", "_src": "%s %s" % (linha, odd)}
