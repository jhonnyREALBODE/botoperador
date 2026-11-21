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
    monkeypatch.setattr(P, "ARQ_PREFS", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(P, "ARQ_OPER", str(tmp_path / "operacao.json"))
    P._MEM_URLS = None
    P._MEM_LIGAS = None
    servidor.ESTADO.clear()
    yield


@pytest.fixture
def cfg(tmp_path):
    c = P.carregar_config(str(tmp_path / "nao-existe.json"), avisar=False)
    c["totais_periodos"] = ["jogo", "q1", "q2", "q3"]  # os testes cobrem também a lógica dos quartos
    c["odd_fantasma_margem"] = None  # os dados falsos têm odds 'generosas' de propósito; o filtro tem teste próprio
    c["odd_min"] = None  # idem (odds baixas nos dados falsos); a odd mínima tem teste próprio
    c["tempo_max_preparacao_seg"] = 15.0  # navegador de teste é lento; o prazo de 2 s tem teste próprio
    c["handicap_ativo"] = False   # testes antigos são de TOTAIS; o handicap (24/11) tem testes próprios
    # os testes foram escritos com a tabela 3/4,8/4,99/8 e custo_max 5 (a tabela padrão D tem teste próprio)
    c["custo_por_janela"], c["custo_max"] = [[1, 2, 3.0], [2, 3, 4.8], [3, 7, 4.99], [7, 11, 8.0]], 5.0
    return c


@pytest.fixture(scope="session")
def site():
    srv, base = servidor.iniciar()
    yield base
    srv.shutdown()


@pytest.fixture(scope="session")
def casas(site, tmp_path_factory):
    d = tmp_path_factory.mktemp("perfis")
    cfg = P.carregar_config(str(d / "x.json"), avisar=False)
    cfg["totais_periodos"] = ["jogo", "q1", "q2", "q3"]  # os testes cobrem também a lógica dos quartos
    cfg["odd_fantasma_margem"] = None  # dados falsos generosos; o filtro tem teste próprio
    cfg["odd_min"] = None
    cfg["tempo_max_preparacao_seg"] = 15.0  # navegador de teste (headless) é lento; o prazo tem teste próprio
    cfg["handicap_ativo"] = False   # idem (os testes de handicap ligam explicitamente)
    cfg["custo_por_janela"], cfg["custo_max"] = [[1, 2, 3.0], [2, 3, 4.8], [3, 7, 4.99], [7, 11, 8.0]], 5.0
    cps = P.Casa(P.CASA_PS, cfg, base_url=site, perfil=str(d / "ps"), headless=True).iniciar()
    c1 = P.Casa(P.CASA_1W, cfg, base_url=site, perfil=str(d / "1w"), headless=True).iniciar()
    yield {P.CASA_1W: c1, P.CASA_PS: cps}
    c1.parar()
    cps.parar()


def oferta(casa, tipo, periodo, lado, linha, odd, time=None, time_idx=None):
    return {"tipo": tipo, "periodo": periodo, "lado": lado, "linha": linha, "odd": odd,
            "time": time, "time_idx": time_idx, "casa": casa, "_mid": "1", "_src": "%s %s" % (linha, odd)}
