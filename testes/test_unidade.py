import json
import os
import threading
import time
import urllib.request

import pytest

import programa as P
from conftest import SINAL, TIMES, oferta


# ------------------------------------------------------------------ config
def test_config_default_custo_4(cfg):
    assert cfg["custo_max"] == 4.0
    assert cfg["janela_min"] == 1.0
    assert cfg["perna_ps_eur"] == 5.0
    assert cfg["cambio_brl_eur"] == 6.20
    assert cfg["modo_teste"] is True


def test_config_nao_atropela_ordem_do_operador(tmp_path):
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"custo_max": 3.5, "janela_min": 2}))
    c = P.carregar_config(str(arq), avisar=False)
    assert c["custo_max"] == 3.5 and c["janela_min"] == 2.0


def test_config_restricao_temporaria_default_e_religar(tmp_path, cfg):
    assert cfg["so_totais"] is True
    assert cfg["totais_periodos"] == ["jogo", "q1", "q2", "q3"]
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"so_totais": False, "totais_periodos": None}))
    c = P.carregar_config(str(arq), avisar=False)
    assert c["so_totais"] is False
    assert c["totais_periodos"] == P.PERIODOS_TODOS


def test_config_q4_nunca_entra(tmp_path):
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"totais_periodos": ["jogo", "q4"]}))
    assert P.carregar_config(str(arq), avisar=False)["totais_periodos"] == ["jogo"]


def test_config_modo_real_so_com_false(tmp_path):
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"modo_teste": "false"}))
    assert P.carregar_config(str(arq), avisar=False)["modo_teste"] is True
    arq.write_text(json.dumps({"modo_teste": False}))
    assert P.carregar_config(str(arq), avisar=False)["modo_teste"] is False


def test_config_imprime_parametros_efetivos(tmp_path):
    P.carregar_config(str(tmp_path / "x.json"))
    assert any("PARÂMETROS EFETIVOS" in l and "custo_max=4.0" in l for l in P._LOG_MEM)


# ------------------------------------------------------------------ texto e números
@pytest.mark.parametrize("txt,val", [("5,00", 5.0), ("1.613", 1.613), ("1.234,56", 1234.56), ("−3.5", -3.5),
                                     ("+9.5", 9.5), ("R$ 31,00", 31.0), ("abc", None)])
def test_num(txt, val):
    assert P._num(txt) == val


@pytest.mark.parametrize("a,b", [("Atenas Cordoba", "Atlético de Atenas"), ("San Lorenzo de Almagro", "São Lourenço"),
                                 ("Phoenix", "Phoenix Suns"), ("Charlotte Hornets", "Charlotte"),
                                 ("Al Najma", "Al-Najma"), ("Botafogo", "Botafogo RJ")])
def test_nome_bate(a, b):
    assert P._nome_bate(a, b)


@pytest.mark.parametrize("a,b", [("Chicago Bulls", "Atlanta Hawks"), ("Flamengo", "Flamengo W"),
                                 ("Franca", "Pato"), ("Phoenix Suns", "Phoenix Mercury W")])
def test_nome_nao_bate(a, b):
    assert not P._nome_bate(a, b)


def test_limpar_file_mention():
    t = 'odd <file-mention id="x">arquivo.txt</file-mention>1,85 <b>ok</b>'
    out = P._limpar(t)
    assert "file-mention" not in out and "arquivo" not in out and "1,85" in out and "<b>" not in out


def test_ler_valor_eur_e_reais():
    assert P.ler_valor_reais("apostar 5,00 EUR", 6.2) == 31.0
    assert P.ler_valor_reais("apostar €5", 6.2) == 31.0
    assert P.ler_valor_reais("apostar R$ 16,13", 6.2) == 16.13


# ------------------------------------------------------------------ sinais
CONVERSA = """
🛰️ SINAL DE MIDDLE (BetBurger)
Jogo: Phoenix Suns x Charlotte Hornets
Liga: EUA - NBA
Mercado do sinal: Total <file-mention>x</file-mention>
algum texto do analista

🛰️ SINAL DE MIDDLE (BetBurger)
Jogo: Atenas Cordoba vs San Lorenzo de Almagro
Mercado do sinal: Handicap
"""


def test_extrair_sinais():
    s = P.extrair_sinais(CONVERSA)
    assert len(s) == 2
    assert s[0]["times"] == ["Phoenix Suns", "Charlotte Hornets"] and s[0]["liga"] == "EUA - NBA"
    assert s[0]["mercado"] == "Total"
    assert s[1]["times"] == ["Atenas Cordoba", "San Lorenzo de Almagro"] and s[1]["liga"] == ""


def test_resultado_nao_vira_sinal_e_le_devolvida():
    t = CONVERSA + "\nRESULTADO\nJogo: Phoenix Suns x Charlotte Hornets\nDesfecho: DEVOLVIDA\n"
    assert len(P.extrair_sinais(t)) == 2
    r = P.extrair_resultados(t)
    assert r and r[0]["desfecho"] == "DEVOLVIDA"


# ------------------------------------------------------------------ períodos / relógio
@pytest.mark.parametrize("txt,per", [("1º Quarto", "q1"), ("2nd Quarter", "q2"), ("3° quarto - Total", "q3"),
                                     ("1º Tempo", "t1"), ("2nd Half", "t2"), ("Jogo", "jogo"), ("Total", None),
                                     ("Q3", "q3"), ("4º Quarto", "q4"), ("1ª metade", "t1")])
def test_periodo_de_texto(txt, per):
    assert P.periodo_de_texto(txt) == per


def test_relogio_e_periodos_encerrados():
    assert P.quarto_atual("Phoenix — Charlotte\n2º Quarto 5'") == 2
    assert P.quarto_atual("3rd quarter 7'") is None or P.quarto_atual("3 Quarter 7'") == 3
    assert P.periodos_encerrados(2) == {"q1"}
    assert P.periodos_encerrados(3) == {"q1", "q2", "t1"}
    assert P.periodos_encerrados(4) == {"q1", "q2", "t1", "q3"}
    assert P.periodos_encerrados(None) == set()


# ------------------------------------------------------------------ matemática do par
def test_par_phoenix_marco_00h43(cfg):
    """A primeira rodada completa: 1aWin abaixo 214.5 @3.1 x PS acima 204.5 @1.613."""
    cfg["custo_max"] = 100  # só a matemática
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 3.1)
    b = oferta("ps3838", "total", "jogo", "acima", 204.5, 1.613)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert mot is None
    assert bil["janela"] == 10.0
    assert bil["pernas"][0]["stake"] == 16.13 and bil["pernas"][1]["stake"] == 31.00


def test_custo_ate_4_vale_e_acima_nao(cfg):
    cfg["custo_por_janela"] = None
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.10)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 1.80)
    bil, _ = P.achar_par([a], [b], SINAL, cfg)
    assert bil and 3.0 < bil["custo"] < 4.0
    b2 = oferta("ps3838", "total", "jogo", "acima", 210.5, 1.70)
    raio = {}
    bil, mot = P.achar_par([a], [b2], SINAL, cfg, raio)
    assert bil is None and "sem par" in mot
    assert raio["valido"] is False and raio["par"]["janela"] == 4.0  # o "quase"


def test_maior_janela_vence_e_desempate_por_custo(cfg):
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.10)
    bs = [oferta("ps3838", "total", "jogo", "acima", 212.5, 1.90), oferta("ps3838", "total", "jogo", "acima", 211.5, 1.80)]
    bil, _ = P.achar_par([a], bs, SINAL, cfg)
    assert bil["janela"] == 3.0
    a2 = [oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.10), oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.12)]
    bil, _ = P.achar_par(a2, [bs[0]], SINAL, cfg)
    assert bil["pernas"][0]["odd"] == 2.12


def test_janela_minima(cfg):
    a = oferta("1awin", "total", "jogo", "abaixo", 212.0, 2.10)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 2.10)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert bil is None


def test_teto_janela_total_prishtina(cfg):
    a = oferta("1awin", "total", "q1", "abaixo", 136.5, 2.0)
    b = oferta("ps3838", "total", "q1", "acima", 115.0, 2.0)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert bil is None and "1 suspeito" in mot


def test_custo_suspeito_menor_que_menos_10(cfg):
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 4.0)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 4.0)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert bil is None and "suspeito" in mot


def test_handicap_regra_do_operador(cfg):
    cfg["so_totais"] = False
    a = oferta("1awin", "handicap", "jogo", "time0", 5.5, 2.0, "Phoenix Suns", 0)
    b = oferta("ps3838", "handicap", "jogo", "time1", -3.5, 2.0, "Charlotte Hornets", 1)
    bil, _ = P.achar_par([a], [b], SINAL, cfg)
    assert bil["janela"] == 2.0
    a = oferta("1awin", "handicap", "jogo", "time0", 6.0, 2.0, "Phoenix Suns", 0)
    b = oferta("ps3838", "handicap", "jogo", "time1", -4.0, 2.0, "Charlotte Hornets", 1)
    assert P.achar_par([a], [b], SINAL, cfg)[0]["janela"] == 2.0


def test_handicap_anti_middle_san_jose(cfg):
    cfg["so_totais"] = False
    a = oferta("1awin", "handicap", "jogo", "time0", -20.0, 2.0, "A", 0)
    b = oferta("ps3838", "handicap", "jogo", "time1", 3.5, 2.0, "B", 1)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert bil is None  # -16,5: as duas perdem juntas numa faixa


def test_handicap_mesmo_sinal_kasentina(cfg):
    cfg["so_totais"] = False
    a = oferta("1awin", "handicap", "jogo", "time0", 7.0, 2.0, "A", 0)
    b = oferta("ps3838", "handicap", "jogo", "time1", 7.5, 2.0, "B", 1)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert bil is None and "1 suspeito" in mot


def test_handicap_mesmo_time_nao_pareia(cfg):
    cfg["so_totais"] = False
    a = oferta("1awin", "handicap", "jogo", "time0", 5.5, 2.0, "A", 0)
    b = oferta("ps3838", "handicap", "jogo", "time0", -3.5, 2.0, "A", 0)
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None


def test_so_totais_desliga_handicap_e_declara(cfg):
    a = oferta("1awin", "handicap", "jogo", "time0", 5.5, 2.0, "A", 0)
    b = oferta("ps3838", "handicap", "jogo", "time1", -3.5, 2.0, "B", 1)
    bil, mot = P.achar_par([a], [b], SINAL, cfg)
    assert bil is None and "restrição ativa" in mot


def test_periodo_fora_da_lista_e_q4(cfg):
    a = oferta("1awin", "total", "t1", "abaixo", 112.5, 2.1)
    b = oferta("ps3838", "total", "t1", "acima", 110.5, 2.1)
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None
    cfg["totais_periodos"] = P.PERIODOS_TODOS + ["q4"]
    a = oferta("1awin", "total", "q4", "abaixo", 58.5, 2.1)
    b = oferta("ps3838", "total", "q4", "acima", 52.5, 2.1)
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None


def test_periodos_diferentes_nao_pareiam(cfg):
    a = oferta("1awin", "total", "q1", "abaixo", 56.5, 2.1)
    b = oferta("ps3838", "total", "q2", "acima", 54.5, 2.1)
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None


def test_escada_vazia_motivo(cfg):
    bil, mot = P.achar_par([], [oferta("ps3838", "total", "jogo", "acima", 211.5, 1.8)], SINAL, cfg)
    assert bil is None and "escada vazia em 1aWin" in mot


def test_stakes_total_fixo(cfg):
    cfg["perna_ps_eur"] = 0
    s1, s2 = P._stakes_iguais(600, 2.0, 2.0)
    assert (s1, s2) == (300.0, 300.0)
    cfg["custo_max"] = 100
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.1)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 1.8)
    bil, _ = P.achar_par([a], [b], SINAL, cfg)
    assert abs(bil["total"] - 600) < 0.02


def test_perna_ps_minima_5eur(cfg):
    cfg["perna_ps_eur"] = 0
    cfg["total_operacao"] = 40
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.1)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 2.0)
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None


def test_cenarios(cfg):
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.10)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 1.80)
    bil, _ = P.achar_par([a], [b], SINAL, cfg)
    c = P.cenarios(bil)
    assert len(c) == 3
    assert c[2]["valor"] > 0 and c[0]["valor"] < 0 and c[1]["valor"] < 0


# ------------------------------------------------------------------ leitura (parser)
def item(seg, odd, heads, mid="1", **kw):
    d = {"mid": mid, "odd": odd, "txt": seg, "seg": seg, "linha": None, "col": "", "ci": -1, "hdrs": None, "heads": heads}
    d.update(kw)
    return d


def test_oferta_por_segmento():
    ofs = P.ofertas_de_enumeracao([item("Abaixo de (214.5) 2.10", "2.10", ["Total", "Phoenix — Charlotte"])], "1awin", TIMES)
    assert ofs[0]["lado"] == "abaixo" and ofs[0]["linha"] == 214.5 and ofs[0]["odd"] == 2.10 and ofs[0]["periodo"] == "jogo"


def test_odd_fantasma_holbaek():
    """2.58 é da 168.5 — nunca pode colar na 158.5."""
    ofs = P.ofertas_de_enumeracao([item("Acima de 158.5 Acima de 168.5 2.58", "2.58", ["Total"])], "1awin", TIMES)
    assert len(ofs) == 1 and ofs[0]["linha"] == 168.5


def test_invariante_src():
    it = item("Acima de (204.5) 1.85", "1.85", ["Total"])
    it["seg"] = "Acima de (204.5)"  # odd não coexiste no texto-fonte
    assert P.ofertas_de_enumeracao([it], "1awin", TIMES) == []


@pytest.mark.parametrize("head", ["Vencedor do Ponto 50", "Dicas de IA", "Total individual: Phoenix Suns",
                                  "Corrida até 20 pontos", "Par/Ímpar", "Margem de vitória", "Phoenix Suns - Total"])
def test_classificacao_nao_linha(head):
    assert P.ofertas_de_enumeracao([item("Acima de (204.5) 1.85", "1.85", [head])], "1awin", TIMES) == []


def test_periodo_manda_na_secao_e_q4_proibido():
    ofs = P.ofertas_de_enumeracao([item("Acima de (54.5) 1.85", "1.85", ["Total", "2º Quarto"]),
                                   item("Acima de (58.5) 1.85", "1.85", ["Total", "4º Quarto"], mid="2")], "1awin", TIMES)
    assert [o["periodo"] for o in ofs] == ["q2"]


def test_periodo_encerrado_morre():
    its = [item("Acima de (54.5) 1.85", "1.85", ["Total", "1º Quarto"]),
           item("Acima de (55.5) 1.85", "1.85", ["Total", "2º Quarto"], mid="2")]
    assert [o["periodo"] for o in P.ofertas_de_enumeracao(its, "1awin", TIMES, quarto=2)] == ["q2"]
    assert len(P.ofertas_de_enumeracao(its, "1awin", TIMES, quarto=1)) == 2


def test_faixa_total_jogo():
    assert P.ofertas_de_enumeracao([item("Acima de (95.5) 1.85", "1.85", ["Total"])], "1awin", TIMES) == []


def test_handicap_segmento_time():
    ofs = P.ofertas_de_enumeracao([item("Charlotte Hornets (+9.5) 2.05", "2.05", ["Handicap"])], "1awin", TIMES)
    assert ofs[0]["tipo"] == "handicap" and ofs[0]["linha"] == 9.5 and ofs[0]["time_idx"] == 1


def test_tabela_ps_colunas():
    hdrs = ["HDP", "Phoenix Suns", "Charlotte Hornets", "Pontos", "Acima", "Abaixo"]
    row = ["-4.5", "2.00", "1.85", "211.5", "1.80", "2.02"]
    its = [item("1.80", "1.80", ["Jogo"], mid="1", linha=row, hdrs=hdrs, col="Acima", ci=4),
           item("1.85", "1.85", ["Jogo"], mid="2", linha=row, hdrs=hdrs, col="Charlotte Hornets", ci=2),
           item("2.00", "2.00", ["Jogo"], mid="3", linha=row, hdrs=hdrs, col="Phoenix Suns", ci=1)]
    ofs = P.ofertas_de_enumeracao(its, "ps3838", TIMES)
    assert ofs[0]["tipo"] == "total" and ofs[0]["lado"] == "acima" and ofs[0]["linha"] == 211.5
    assert ofs[1]["tipo"] == "handicap" and ofs[1]["linha"] == 4.5 and ofs[1]["time_idx"] == 1
    assert ofs[2]["linha"] == -4.5 and ofs[2]["time_idx"] == 0


# ------------------------------------------------------------------ casas: textos
def test_ps_confronto_e_lista():
    t = "EUA - NBA\nPhoenix Suns -vs- Charlotte Hornets\n2º Quarto"
    assert P.ps_e_o_jogo(t, TIMES)["liga"] == "EUA - NBA"
    assert P.ps_e_o_jogo("Phoenix Suns\nCharlotte Hornets\n1.90", TIMES) is None
    assert P.liga_da_url_ps("https://x/pt/sports/basketball/eua-nba/matchup/123") == "eua nba"


def test_linha_confronto_1awin():
    assert P.linha_confronto("Menu\nAtl. Atenas — São Lourenço\n2º Quarto", ["Atenas Cordoba", "San Lorenzo de Almagro"])


def test_memoria_ps_so_matchup():
    P.memorizar_url("ps3838", TIMES, "https://x/pt/sports/basketball")
    assert P.url_memorizada("ps3838", TIMES) is None
    P.memorizar_url("ps3838", TIMES, "https://x/pt/a/matchup/1")
    assert P.url_memorizada("ps3838", ["Phoenix", "Charlotte"]) == "https://x/pt/a/matchup/1"


def test_liga_parecida():
    assert P.liga_parecida("Brasil - NBB", "Brazil. NBB") > 0.63
    assert P.liga_parecida("Argentina - Liga Nacional", "Brasil. NBB") < 0.5
    assert P.liga_parecida("Espanha - Liga Feminina", "Spain. Liga") == 0.0
    assert P.liga_parecida("Brasil - Liga 2", "Brasil - Liga") == 0.0


def test_ordenar_ligas_memoria_confirmada():
    ligas = [{"nome": "Espanha. ACB", "url": "u1"}, {"nome": "Brasil. NBB", "url": "u2"}]
    assert P.ordenar_ligas(ligas, "Brasil - NBB")[0]["url"] == "u2"
    P.confirmar_liga("Liga X", {"nome": "Espanha. ACB", "url": "u1"})
    assert P.ordenar_ligas(ligas, "Liga X")[0]["url"] == "u1"


# ------------------------------------------------------------------ vigia
class OperadorFalso:
    def __init__(self):
        self.recebidos = []

    def novo_sinal(self, s, origem="sinal"):
        self.recebidos.append((s, origem))


def test_vigia_backlog_nunca_executa_e_fresco_executa(cfg):
    est = P.Estado(cfg)
    op = OperadorFalso()
    v = P.Vigia(cfg, op, est)
    v.min_estavel_s = 0
    v.processar(CONVERSA)
    assert not v.backlog_ok  # uma leitura só não conclui
    v.processar(CONVERSA)
    assert not v.backlog_ok  # duas também não
    v.processar(CONVERSA)
    assert v.backlog_ok and len(est.backlog) == 2 and op.recebidos == []
    novo = CONVERSA + "\n🛰️ SINAL DE MIDDLE (BetBurger)\nJogo: Flamengo x Franca\nMercado do sinal: Total\n"
    v.processar(novo)
    assert len(op.recebidos) == 1 and op.recebidos[0][0]["times"] == ["Flamengo", "Franca"]
    v.processar(novo)
    assert len(op.recebidos) == 1


def test_vigia_resultado_nao_clica(cfg):
    est = P.Estado(cfg)
    op = OperadorFalso()
    v = P.Vigia(cfg, op, est)
    v.min_estavel_s = 0
    for _ in range(3):
        v.processar(CONVERSA)
    v.processar(CONVERSA + "\nRESULTADO\nJogo: Phoenix Suns x Charlotte Hornets\nDesfecho: DEVOLVIDA\n")
    assert op.recebidos == [] and est.resultados[0]["desfecho"] == "DEVOLVIDA"


def test_cooldown_do_sinal_identico(cfg, monkeypatch):
    monkeypatch.setattr(P.Operador, "_monitorar", lambda self, s, ev, o: None)
    op = P.Operador(cfg, P.Estado(cfg), {})
    assert op.novo_sinal(dict(SINAL)) is True
    assert op.novo_sinal(dict(SINAL)) is False
    outro = dict(SINAL, id="outro")
    assert op.novo_sinal(outro) is True


def test_sinal_novo_cancela_o_atual(cfg, monkeypatch):
    cancelados = []

    def mon(self, s, ev, o):
        ev.wait(5)
        cancelados.append(s["id"])
    monkeypatch.setattr(P.Operador, "_monitorar", mon)
    op = P.Operador(cfg, P.Estado(cfg), {})
    op.novo_sinal(dict(SINAL))
    op.novo_sinal(dict(SINAL, id="b", times=["Flamengo", "Franca"]))
    time.sleep(0.3)
    assert cancelados == [SINAL["id"]]


# ------------------------------------------------------------------ estado/painel
def test_resultado_marca_desfecho_do_bilhete(cfg):
    est = P.Estado(cfg)
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.10)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 1.80)
    bil, _ = P.achar_par([a], [b], SINAL, cfg)
    est.registrar_bilhete(bil, "sinal")
    est.registrar_resultado({"jogo": "Phoenix x Charlotte", "desfecho": "DEVOLVIDA", "id": "x"})
    assert bil["desfecho"] == "DEVOLVIDA"
    est.salvar_agora()  # a gravação normal é por fora (thread)
    assert json.load(open(P.ARQ_HIST))["bilhetes"][0]["desfecho"] == "DEVOLVIDA"


def test_painel_estado_e_backlog(cfg):
    est = P.Estado(cfg)
    op = OperadorFalso()
    op.calibrar = lambda: None
    est.backlog.append(dict(SINAL))
    raio = {"jogo": SINAL["jogo"]}
    P.achar_par([oferta("1awin", "total", "jogo", "abaixo", 214.5, 2.10)],
                [oferta("ps3838", "total", "jogo", "acima", 211.5, 1.80)], SINAL, cfg, raio)
    est.set_calc(raio, 1)
    srv = P.criar_painel(est, op, 0)
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/estado" % porta))
        assert d["modo"] == "TESTE" and d["calculadora"]["valido"] is True
        assert len(d["calculadora"]["cenarios"]) == 3
        assert "_mid" not in json.dumps(d)
        html = urllib.request.urlopen("http://127.0.0.1:%d/" % porta).read().decode()
        assert "Calculadora do motor" in html
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/backlog/0" % porta, method="POST"))
        assert op.recebidos and op.recebidos[0][1] == "backlog" and est.backlog == []
    finally:
        srv.shutdown()


def test_painel_so_localhost(cfg):
    srv = P.criar_painel(P.Estado(cfg), None, 0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_palavra_abolida_nao_existe():
    src = open(os.path.join(os.path.dirname(P.__file__), "programa.py"), encoding="utf-8").read().lower()
    assert "vencid" not in src and "venceu" not in src


def test_log_nao_trava_com_trava_global():
    t = threading.Thread(target=lambda: P.log("teste"))
    t.start()
    t.join(2)
    assert not t.is_alive()


def test_navegador_sem_marca_de_automacao(cfg, tmp_path):
    kw = P.opcoes_navegador(cfg, True)
    assert "--enable-automation" in kw["ignore_default_args"]
    assert "--disable-blink-features=AutomationControlled" in kw["args"]
    exe = tmp_path / "brave.exe"
    exe.write_text("x")
    cfg["navegador_exe"] = str(exe)
    assert P.opcoes_navegador(cfg, False)["executable_path"] == str(exe)
    cfg["navegador_exe"] = str(tmp_path / "nao-existe.exe")
    assert "executable_path" not in P.opcoes_navegador(cfg, False)


def test_vigia_pagina_carregando_nao_vira_fresco(cfg):
    """Caso real 14/11: Grok ainda carregando (vazio) concluiu a varredura e os
    sinais antigos entraram como frescos."""
    est = P.Estado(cfg)
    op = OperadorFalso()
    v = P.Vigia(cfg, op, est)
    v.min_estavel_s = 0.5
    for _ in range(3):
        v.processar("")          # página vazia, ainda carregando
    assert not v.backlog_ok      # tempo mínimo não passou
    for _ in range(3):
        v.processar(CONVERSA)    # conversa apareceu
    time.sleep(0.6)
    v.processar(CONVERSA)
    assert v.backlog_ok and op.recebidos == [] and len(est.backlog) == 2


def test_sinal_do_mesmo_jogo_nao_reinicia(cfg, monkeypatch):
    monkeypatch.setattr(P.Operador, "_monitorar", lambda self, s, ev, o: ev.wait(5))
    op = P.Operador(cfg, P.Estado(cfg), {})
    assert op.novo_sinal(dict(SINAL)) is True
    outro_mercado = dict(SINAL, id="chicago|spread", times=["Phoenix", "Charlotte Hornets"])
    assert op.novo_sinal(outro_mercado) is False
    assert op.novo_sinal(dict(SINAL, id="x", times=["Flamengo", "Franca"])) is True


# ------------------------------------------------------------------ casos REAIS (calibração de 14/11)
REAIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reais")
SAS = ["San Antonio Spurs", "Golden State Warriors"]


def test_ps_real_le_pelo_id_do_botao():
    """A PS mostra '38.5 1.980' só no mandante e 'u 1.746' no abaixo: a verdade está no id."""
    itens = json.load(open(os.path.join(REAIS, "ps_matchup_14-11.json"), encoding="utf-8"))["itens"]
    ofs = P.ofertas_de_enumeracao(itens, "ps3838", SAS, quarto=4)
    t = {(o["lado"], o["linha"]): o["odd"] for o in ofs if o["tipo"] == "total"}
    assert t[("acima", 208.5)] == 1.99 and t[("abaixo", 208.5)] == 1.746 and t[("abaixo", 207.5)] == 1.952
    h = {(o["time_idx"], o["linha"]): o["odd"] for o in ofs if o["tipo"] == "handicap"}
    assert h[(0, -38.5)] == 1.98 and h[(1, 38.5)] == 1.847 and h[(0, -38.0)] == 1.877
    assert all(o["periodo"] == "jogo" for o in ofs) and len(ofs) == 12


def test_ps_id_periodos_e_q4():
    base = {"mid": "1", "odd": "1.900", "txt": "", "seg": "52.5 1.900", "linha": None, "col": "", "ci": -1,
            "hdrs": None, "heads": [], "travado": False, "casa_times": SAS}
    per = lambda aid: [o["periodo"] for o in P.ofertas_de_enumeracao([dict(base, aid=aid)], "ps3838", SAS)]
    assert per("1|3|3|3|0|52.5") == ["q1"] and per("1|4|3|3|0|52.5") == ["q2"] and per("1|1|3|3|0|52.5") == ["t1"]
    assert per("1|6|3|3|0|52.5") == []  # 4º quarto proibido


def test_ps_odd_travada_e_deslogado_nao_nascem():
    base = {"mid": "1", "odd": "1.900", "txt": "", "seg": "4.5 1.900", "linha": None, "col": "", "ci": -1,
            "hdrs": None, "heads": [], "aid": "1|0|2|0|0|-4.5", "travado": True, "casa_times": SAS}
    assert P.ofertas_de_enumeracao([base], "ps3838", SAS) == []
    sem_odd = dict(base, travado=False, odd="4.5", seg="4.5")  # deslogado: só a linha na tela
    assert P.ofertas_de_enumeracao([sem_odd], "ps3838", SAS) == []


def test_relogios_reais():
    assert P.quarto_atual("San Antonio Spurs\n4º Quarto 10`\n") == 4      # 1aWin usa crase
    assert P.quarto_atual("99-62\nQ4 10'") == 4                          # PS
    assert P.quarto_atual("2º Tempo 3`") == 3                            # NCAA (tempos): 1º tempo encerrado
    assert "t1" in P.periodos_encerrados(P.quarto_atual("2º Tempo 3`"))


def test_1awin_real_handicap_positivo_sem_sinal():
    it = item("Golden State Warriors 42.5 1.31", "1.31", ["San Antonio Spurs Golden State Warriors", "Handicap (inc OT)"])
    o = P.ofertas_de_enumeracao([it], "1awin", SAS)[0]
    assert o["linha"] == 42.5 and o["time_idx"] == 1
    it = item("San Antonio Spurs -42.5 3.13", "3.13", ["San Antonio Spurs Golden State Warriors", "Handicap (inc OT)"])
    assert P.ofertas_de_enumeracao([it], "1awin", SAS)[0]["linha"] == -42.5


def test_1awin_real_mercados_que_nao_sao_linha():
    h = lambda seg, odd, heads: P.ofertas_de_enumeracao([item(seg, odd, heads)], "1awin", SAS)
    assert h("Abaixo De 208.5 1.85", "1.85", ["Menos Mais", "Totais (inc OT)"])[0]["linha"] == 208.5
    assert h("Abaixo De 120.5 2.96", "2.96", ["Menos Mais", "San Antonio Spurs total (inc OT)"]) == []
    assert h("San Antonio Spurs 172.0 1.8", "1.8", ["Vencedor Do Ponto"]) == []
    assert h("Dicas de IA San Antonio Spurs marcou mais de 120.5 pontos San Antonio Spurs total (inc OT) Acima De 120.5 1.35",
             "1.35", []) == []
    assert h("Abaixo De 110.5 2.32", "2.32", ["Menos Mais", "Totais. 2º tempo"])[0]["periodo"] == "t2"
    assert h("San Antonio Spurs -1.5 1.97", "1.97", ["San Antonio Spurs Golden State Warriors", "Handicap. 4º quarto"]) == []
    assert h("Nenhum 25 5.37", "5.37", ["4º quarto. Corrida até N pontos"]) == []


def test_status_do_jogo_nao_e_nome_de_time():
    for t in ["4º Quarto 1`", "2º Tempo 3`", "Finalizado", "Intervalo", "Q4 10'"]:
        assert P._e_status_jogo(t), t
    for t in ["Utah State", "Oklahoma (f)", "San Antonio Spurs"]:
        assert not P._e_status_jogo(t), t


def test_nomes_caso_oklahoma_feminino_14_11():
    """PS 'achou' LA Clippers x OKC para Oklahoma (f) x Oral Roberts (f): '(f)' é feminino e
    'Eagles' não é 'Angeles'."""
    assert not P._nome_bate("Oral Roberts Golden Eagles (f)", "Los Angeles Clippers")
    assert not P._nome_bate("Oklahoma (f)", "Oklahoma City Thunder")
    assert P._nome_bate("LA Clippers", "Los Angeles Clippers")
    assert P._nome_bate("Saint Mary's Gaels", "Saint Marys CA")
    assert not P.ps_e_o_jogo("NBA /\nLos Angeles Clippers -vs- Oklahoma City Thunder",
                             ["Oklahoma (f)", "Oral Roberts Golden Eagles (f)"])


def test_liga_polonia_14_11_e_cyber_fora():
    """1aWin não achou MOSiR Krosno x Noteć Inowrocław (PS: 'Poland - 1. Liga')."""
    assert P._divisao("Poland - 1. Liga") == P._divisao("Polônia. 1ª Liga") == P._divisao("Polônia. I Liga") == "1"
    assert P.liga_parecida("Poland - 1. Liga", "Polônia. 1ª Liga") > 0.63
    assert P._nome_bate("Notes Inowroclaw", "Noteć Inowrocław")
    for l in ["Cyber Basketball. NBA", "eBasketball. H2H GG League", "NBA 2K25", "Basquete virtual"]:
        assert P._e_liga_virtual(l), l
    for l in ["Polônia. 1ª Liga", "Turquia. TBL", "EUA. NBA", "Brasil. NBB"]:
        assert not P._e_liga_virtual(l), l


def test_nomes_krosno_14_11():
    assert P._nome_bate("MOSiR Miasto Szkla Krosno", "KKK MOSiR Krosno")
    assert P._nome_bate("Notes Inowroclaw", "Notec Inowroclaw")
    assert P.ordenar_ligas([{"nome": "Sérvia. KLS", "url": "a"}, {"nome": "Polônia. 1ª Liga", "url": "b"}],
                           "Poland - 1. Liga")[0]["url"] == "b"


@pytest.mark.parametrize("a,b,esperado", [
    ("Joensuun Kataja Basket", "Fryshuset Basketbollklubb", False),   # 14/11 12:53: jogo errado na PS
    ("KTP Basket Kotka", "Ockelbo Basket", False),
    ("Co Basketbol", "Cedi Osman Basketbol", True),                    # sigla = iniciais
    ("Pizza Bulls Cedi Osman Basketbol Spor Kulubu", "Co Basketbol", True),
    ("KFUM Fryshuset", "Fryshuset Basketbollklubb", True),
    ("Ockelbo BBK", "Ockelbo Basket", True),
    ("MOSiR Miasto Szkla Krosno", "KKK MOSiR Krosno", True),
])
def test_palavra_generica_nao_identifica_time(a, b, esperado):
    assert P._nome_bate(a, b) is esperado


def test_memoria_de_url_nao_confunde_jogo_por_basket():
    P.memorizar_url("ps3838", ["Fryshuset Basketbollklubb", "Ockelbo Basket"], "https://x/pt/a/matchup/1")
    assert P.url_memorizada("ps3838", ["Joensuun Kataja Basket", "KTP Basket Kotka"]) is None
    assert P._e_liga_virtual("Replays. NBA")


@pytest.mark.parametrize("a,b,esperado", [
    ("Iceland Women", "Islândia (f)", True),          # 14/11 14:33: seleção com nome em PT na 1aWin
    ("Switzerland Women", "Suíça (f)", True),
    ("Iceland Women", "Islândia", False),             # masculino x feminino continua diferente
    ("Países Baixos (f)", "Netherlands Women", True),
    ("CP Esgueira", "Esgueira", True),
])
def test_selecoes_pt_en(a, b, esperado):
    assert P._nome_bate(a, b) is esperado


@pytest.mark.parametrize("a,b,esperado", [
    ("Hapoel Tel Aviv", "Maccabi Tel Aviv", False),       # prefixo de clube diferente = outro time
    ("Hapoel Holon", "Hapoel Haifa", False),
    ("Hapoel Tel Aviv", "Hapoel Tel-Aviv", True),
    ("Los Angeles Lakers", "Los Angeles Clippers", False),  # mesma cidade não basta
    ("LA Clippers", "Los Angeles Clippers", True),
    ("BK Levice Patrioti", "PK Patrioti Levice", True),     # BetBurger 14/11
    ("CSM Ploiesti", "CS Municipal Ploiesti", True),
    ("CSM Ploiesti", "CSM Galati", False),
    ("Hungary (w)", "Hungria (f)", True),
    ("Saint Mary's Gaels", "Saint Louis Billikens", False),
    ("Real Madrid", "Real Betis", False),
    ("Movistar Estudiantes Madrid", "Cb Estudiantes", True),
])
def test_nomes_clubes_e_cidades(a, b, esperado):
    assert P._nome_bate(a, b) is esperado


def test_relogio_ps_ncaa_h2():
    assert P.quarto_atual("45-35\nH2 15'") == 3 and "t1" in P.periodos_encerrados(3)
    assert P.quarto_atual("H1 3'") == 1


def test_pistas_nunca_vazias_no_filtro_frouxo():
    assert P.pistas_nome("Co Basketbol", 3) == ["co"]
    assert "geo" in P.pistas_nome("George Mason", 3)


@pytest.mark.parametrize("titulo,equipe", [
    ("Totais (inc OT)", False), ("Totais. 2º quarto", False), ("Totais. 2º tempo", False),
    ("Totais (inc OT) - Linhas asiáticas", False),
    ("2º quarto. Basquete De Barcelos total", True),      # 14/11 15:37: janela 12 fabricada
    ("Aac Efapel total (inc OT)", True), ("San Antonio Spurs total (inc OT)", True),
])
def test_total_de_equipe_pela_forma_do_titulo(titulo, equipe):
    assert P._e_total_de_equipe(titulo) is equipe


def test_par_fabricado_barcelos_14_11(cfg):
    """O sinal chama 'Barcelos Hotel Terco'; a 1aWin, 'Basquete De Barcelos'. O total do time no
    2º quarto (19.5) não pode virar oferta de total do 2º quarto."""
    times = ["Academica Efapel Coimbra", "Barcelos Hotel Terco"]
    it = item("Acima De 19.5 2.05", "2.05", ["Menos Mais", "2º quarto. Basquete De Barcelos total"])
    assert P.ofertas_de_enumeracao([it], "1awin", times, quarto=2) == []
    it = item("Acima De 32.5 1.95", "1.95", ["Menos Mais", "Totais. 2º quarto"])
    assert P.ofertas_de_enumeracao([it], "1awin", times, quarto=2)[0]["periodo"] == "q2"


def test_feminino_sem_marca_na_ps_14_11():
    """PS lista 'Alges e Dafundo x ACD Ferragudo Cidade Lagoa' (sem 'Women') para o sinal
    'Alges Women x ACD Ferragudo Cidade Lagoa Women'."""
    t = ["Alges Women", "ACD Ferragudo Cidade Lagoa Women"]
    assert P.ps_e_o_jogo("Portugal - Liga Feminina\nAlges e Dafundo -vs- ACD Ferragudo Cidade Lagoa", t)
    # a regra estrita continua valendo para o caso da manhã
    assert not P.ps_e_o_jogo("NBA /\nLos Angeles Clippers -vs- Oklahoma City Thunder",
                             ["Oklahoma (f)", "Oral Roberts Golden Eagles (f)"])
    assert not P._nome_bate("Oklahoma (f)", "Oklahoma City Thunder")
    assert P.ordenar_ligas([{"nome": "EUA. NCAA", "url": "a"}, {"nome": "Portugal. Women. Division", "url": "b"}],
                           "", fem=True)[0]["url"] == "b"


def test_tolerancia_005_exata_passa():
    """14/11 16:05: 1.95 -> 2.0 (diferença 0,05 exata) reprovou por arredondamento."""
    assert abs(2.0 - 1.95) > 0.05            # o computador acha que passou de 0,05…
    assert abs(2.0 - 1.95) <= P.TOL_ODD_CUPOM + 1e-9   # …mas é 0,05: dentro da tolerância


def test_apelido_traduzido_leuven_14_11():
    """1aWin: 'Ursos Stella Artois Leuven' para o sinal 'Leuven Bears'."""
    assert P._nome_bate("Leuven Bears", "Ursos Stella Artois Leuven")
    assert P.linha_confronto("Ursos Stella Artois Leuven\n40\n57\nKangoeroes Basket Mechelen",
                             ["Leuven Bears", "Kangoeroes Basket Mechelen"])
    assert not P._nome_bate("Los Angeles Lakers", "Los Angeles Clippers")  # estrito continua


def test_cidade_em_portugues_e_patrocinador_14_11():
    """1aWin: 'Divina Seguros Joventut x Saragoça' para 'Club Joventut Badalona x Zaragoza'."""
    assert P._nome_bate("Zaragoza", "Saragoça")
    assert P.linha_confronto("Divina Seguros Joventut\n43\nSaragoça\n46", ["Club Joventut Badalona", "Zaragoza"])


def test_ironia_maccabi_ramat_gan_15_11():
    """1aWin: 'Ironia Maccabi Ramat Gan' = PS/sinal 'Maccabi Ironi Ramat Gan'."""
    assert P._nome_bate("Maccabi Ironi Ramat Gan", "Ironia Maccabi Ramat Gan")
    assert P.linha_confronto("Ironia Maccabi Ramat Gan\n45\nIroni Ness Ziona\n52",
                             ["Maccabi Ironi Ramat Gan", "Ironi Ness Ziona"])
    assert not P._nome_bate("Hapoel Tel Aviv", "Maccabi Tel Aviv")
    assert not P._nome_bate("Maccabi Ironi Ramat Gan", "Ironi Ness Ziona")


def test_tabela_perda_por_janela_15_11(cfg):
    """Tabela do operador: 1 pt até 2%; 2 pts até 3,8%; 3–6 abaixo de 4%."""
    f = P.custo_max_para
    assert f(1.0, cfg) == 2.0 and f(1.5, cfg) == 2.0
    assert f(2.0, cfg) == 3.8 and f(2.5, cfg) == 3.8
    assert f(3.0, cfg) == 3.99 and f(6.5, cfg) == 3.99
    assert f(7.0, cfg) == 7.0 and f(10.5, cfg) == 7.0
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 1.81)
    b = oferta("ps3838", "total", "jogo", "acima", 213.5, 2.09)   # janela 1, custo ~3%
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None            # 1 ponto só aceita até 2%


# ------------------------------------------------------------------ pós-entrada / cobertura
def _pos(over=250.5, under=252.5, periodo="jogo"):
    return {"id": 1, "periodo": periodo, "pernas": [
        {"casa": "ps3838", "tipo": "total", "periodo": periodo, "lado": "acima", "linha": over, "odd": 1.95, "stake": 31.0, "origem": "entrada"},
        {"casa": "1awin", "tipo": "total", "periodo": periodo, "lado": "abaixo", "linha": under, "odd": 2.05, "stake": 29.5, "origem": "entrada"}]}


def _ofs(*linhas, lado="abaixo", odd=1.9, periodo="jogo"):
    return [{"tipo": "total", "periodo": periodo, "lado": lado, "linha": L, "odd": odd} for L in linhas]


def test_pagamento_e_devolucao_na_linha_inteira():
    p = {"lado": "abaixo", "linha": 256.0, "odd": 1.9, "stake": 10.0}
    assert P.pagamento_perna(p, 255) == 19.0 and P.pagamento_perna(p, 256) == 10.0 and P.pagamento_perna(p, 257) == 0


def test_sem_filtro_de_4_pontos_vale_a_janela_da_carteira(cfg):
    """16/11: a regra 'Under ≥ 4 pts acima do Over original' saiu. Vale: acrescenta total novo à janela?
    Over 250.5 + Under 252.5 cobrem 251–252: Under 253.5 (só 3 pts acima do Over) acrescenta 253 -> avalia e serve."""
    cfg = dict(cfg, pos_perda_max_total_brl=60.0, perda_max_por_jogo_brl=60.0)
    for over, under, nova in [(250.5, 252.5, 253.5), (180.5, 182.5, 183.5), (160.5, 162.5, 163.5)]:
        pos = _pos(over, under)
        melhor, av = P.avaliar_cobertura(pos, {"1awin": _ofs(nova)}, (over + 1, 9.0), cfg)
        assert melhor and melhor["depois"]["novos_totais"] == [int(nova - 0.5)], (over, av)
        assert melhor["dist"] == 1.5  # distância mostrada é da BORDA da janela atual, não do Over original


def test_linha_que_nao_acrescenta_total_novo_nao_entra(cfg):
    pos = _pos(250.5, 252.5)
    melhor, av = P.avaliar_cobertura(pos, {"1awin": _ofs(251.5)}, (251.5, 9.0), cfg)  # dentro da janela já coberta
    assert melhor is None and av and "nenhum total novo" in av[0]["motivo"]


def test_nunca_mistura_periodo_nem_over_nem_repete(cfg):
    pos = _pos(250.5, 252.5)
    ofs = {"1awin": _ofs(256.5, periodo="q3") + _ofs(256.5, lado="acima") + _ofs(252.5)}
    melhor, av = P.avaliar_cobertura(pos, ofs, (251.5, 9.0), cfg)
    assert melhor is None
    assert all(c["oferta"]["periodo"] == "jogo" for c in av)            # outro período nem é avaliado
    assert not any(c["lado"] == "abaixo" and c["linha"] == 252.5 for c in av)  # a mesma aposta não repete
    pos["pernas"].append(dict(pos["pernas"][1], linha=256.5, origem="cobertura"))
    _, av = P.avaliar_cobertura(pos, {"1awin": _ofs(256.5)}, (251.5, 9.0), cfg)
    assert av == []  # mesma casa/lado/linha já apostada


def test_cobertura_so_quando_a_posicao_inteira_melhora(cfg):
    cfg = dict(cfg, pos_perda_max_total_brl=40.0, perda_max_por_jogo_brl=40.0)
    pos = _pos(250.5, 252.5)
    mod = (251.5, 9.0)
    melhor, av = P.avaliar_cobertura(pos, {"1awin": _ofs(256.5, odd=1.95)}, mod, cfg)
    assert melhor and melhor["depois"]["p_preju"] < melhor["antes"]["p_preju"]
    assert melhor["depois"]["pior"] >= -cfg["pos_perda_max_total_brl"]
    # todas as pernas entram na conta (nada substitui a entrada)
    res = P.resumo_posicao(pos["pernas"] + [dict(melhor["oferta"], casa="1awin", stake=melhor["stake"])], mod)
    assert res["gasto"] == round(31.0 + 29.5 + melhor["stake"], 2)
    # odd ruim (EV caindo): com pos_exigir_ev ligado não entra; desligado (padrão 16/11) é só informação
    melhor2, _ = P.avaliar_cobertura(pos, {"1awin": _ofs(256.5, odd=1.15)}, mod, dict(cfg, pos_exigir_ev=True))
    assert melhor2 is None


def test_limites_de_perda_e_orcamento(cfg):
    pos = _pos(250.5, 252.5)
    cfg = dict(cfg, pos_perda_max_total_brl=3.0)
    melhor, _ = P.avaliar_cobertura(pos, {"1awin": _ofs(256.5, odd=1.95)}, (251.5, 9.0), cfg)
    assert melhor is None  # qualquer stake estoura a perda máxima
    cfg = dict(cfg, pos_perda_max_total_brl=15.0, pos_max_total_coberturas_brl=0.0)
    assert P.avaliar_cobertura(pos, {"1awin": _ofs(256.5, odd=1.95)}, (251.5, 9.0), cfg)[0] is None


def test_modelo_da_escada_ps():
    ofs = []
    for L, oa, ou in [(208.5, 1.55, 2.40), (210.5, 1.70, 2.15), (212.5, 1.90, 1.92)]:
        ofs += [{"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": L, "odd": oa},
                {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": L, "odd": ou}]
    m, sg = P.modelo_total(ofs, "jogo")
    assert 211 < m < 214 and 3 < sg < 30


def test_painel_troca_modo_da_cobertura(cfg):
    est = P.Estado(cfg)
    srv = P.criar_painel(est, None, 0)
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/pos_modo/manual" % porta, method="POST"))
        d = json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/estado" % porta))
        assert d["params"]["pos_modo"] == "manual" and d["params"]["pos_monitorar"] is True
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/pos_modo/off" % porta, method="POST"))
        assert json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/estado" % porta))["params"]["pos_monitorar"] is False
        html = urllib.request.urlopen("http://127.0.0.1:%d/" % porta).read().decode()
        assert "posmodo" in html
    finally:
        srv.shutdown()


def test_placares_dupla_e_reentrada(cfg):
    """Parceiro 15/11: Over 150/Under 151 e depois Over 151/Under 152 — cada nova entrada só vale
    se aumentar a faixa de placares em que as duas ganham."""
    ac = {"linha": 150.5}; ab = {"linha": 152.5}
    assert P.placares_dupla(ac, ab) == {151, 152}
    assert P.placares_dupla({"linha": 150.0}, {"linha": 152.0}) == {151}   # linha inteira: empate devolve
    ja = {151, 152}
    a = oferta("1awin", "total", "jogo", "abaixo", 152.5, 2.1)
    b = oferta("ps3838", "total", "jogo", "acima", 150.5, 2.1)
    exc = lambda x, y, j: not (P.placares_dupla(*(sorted([x, y], key=lambda o: o["lado"] != "acima"))) - ja)
    assert P.achar_par([a], [b], SINAL, cfg, excluir=exc)[0] is None            # mesmo par: não reentra
    a2 = oferta("1awin", "total", "jogo", "abaixo", 153.5, 2.1)
    b2 = oferta("ps3838", "total", "jogo", "acima", 151.5, 2.1)
    assert P.achar_par([a2], [b2], SINAL, cfg, excluir=exc)[0] is not None       # aumenta a faixa: 153


def test_geladeira_de_ofertas_que_falharam():
    """Print 15/11 20:45–20:50: Toronto x Knicks tentou o mesmo par 6x seguidas. Quem falhou fica de fora um tempo."""
    assert P._tempo_geladeira("a PS marcou a seleção como indisponível (aposta mínima maior que a máxima)") == 120
    assert P._tempo_geladeira("cupom não recebeu a seleção") == 60
    assert P._tempo_geladeira("montou, mas na conferência final: cupom com 0 seleções (esperado 1)") == 60
    assert P._tempo_geladeira("a casa avisou no cupom que a odd mudou (Odds have changed from 2.010 to 1.757)") == 0
    assert P._tempo_geladeira("montou e desfez (cupom vazio)") == 0
    assert P._tempo_geladeira(None) == 0
    a = {"tipo": "total", "periodo": "q2", "lado": "abaixo", "linha": 54.5}
    perna = dict(a, casa="1aWin", odd=2.38, stake=21.65, selecao="Abaixo de 54.5")
    assert P._chave_oferta(a) == P._chave_oferta(perna)  # oferta da leitura == perna do bilhete


def _p(lado, linha, odd=1.90, stake=30.0):
    return {"tipo": "total", "periodo": "jogo", "lado": lado, "linha": linha, "odd": odd, "stake": stake}


def test_carteira_amplia_a_cobertura_como_no_exemplo_do_operador():
    """Exemplo 16/11 (só a lógica): 222,5/224 -> +223,5/225,5 -> +224,5/226,5 -> Under 228,5 -> Under 236,5."""
    cfg = dict(P.DEFAULTS, perda_max_por_jogo_brl=1000)
    cart = [_p("acima", 222.5), _p("abaixo", 224.0)]
    assert P.cobertura_2g(cart) == {223}
    av = P.avaliar_nova_entrada(cart, [_p("acima", 223.5), _p("abaixo", 225.5)], cfg)
    assert av["ok"] and av["novos_totais"] == [224, 225]
    assert 224 in av["tres_ou_mais"]  # Under 224 devolve em 224, mas três outras ganham
    cart += [_p("acima", 223.5), _p("abaixo", 225.5)]
    av = P.avaliar_nova_entrada(cart, [_p("acima", 224.5), _p("abaixo", 226.5)], cfg)
    assert av["ok"] and av["novos_totais"] == [226]
    cart += [_p("acima", 224.5), _p("abaixo", 226.5)]
    av = P.avaliar_nova_entrada(cart, [_p("abaixo", 228.5)], cfg)  # perna sozinha: os Overs anteriores fazem o 2º green
    assert av["ok"] and av["novos_totais"] == [227, 228]
    cart += [_p("abaixo", 228.5)]
    av = P.avaliar_nova_entrada(cart, [_p("abaixo", 236.5)], cfg)
    assert av["ok"] and av["novos_totais"] == list(range(229, 237))
    assert av["piorou_em"]  # acima de 236 essa aposta só perde: tem que aparecer como piora


def test_carteira_recusa_entrada_que_nao_amplia_e_respeita_perda_por_jogo():
    cfg = dict(P.DEFAULTS, perda_max_por_jogo_brl=15)
    cart = [_p("acima", 222.5), _p("abaixo", 224.0)]
    mesmo = P.avaliar_nova_entrada(cart, [_p("acima", 222.5), _p("abaixo", 224.0)], cfg)
    assert not mesmo["ok"] and "nenhum total novo" in mesmo["motivo"]
    caro = P.avaliar_nova_entrada(cart, [_p("abaixo", 240.5, odd=1.5, stake=200)], cfg)
    assert not caro["ok"] and "limite" in caro["motivo"]


def test_tabela_carteira_resultado_por_total():
    tab = {t["total"]: t for t in P.tabela_carteira([_p("acima", 222.5), _p("abaixo", 224.0)])}
    assert tab[223]["res"] == ["G", "G"] and tab[223]["greens"] == 2
    assert tab[224]["res"] == ["G", "D"] and tab[224]["greens"] == 1
    assert tab[222]["res"] == ["P", "G"]
    assert abs(tab[223]["liquido"] - (30 * 1.9 * 2 - 60)) < 0.01
    assert P._txt_totais([223, 224, 225, 228]) == "223–225, 228"


def test_padrao_so_total_do_jogo_inteiro(tmp_path):
    """Operador 16/11: só totais do jogo inteiro (os quartos ao vivo trocavam a linha antes do clique)."""
    c = P.carregar_config(str(tmp_path / "nada.json"), avisar=False)
    assert c["totais_periodos"] == ["jogo"]
    a = oferta("1awin", "total", "q2", "abaixo", 54.5, 2.10)
    b = oferta("ps3838", "total", "q2", "acima", 53.5, 2.10)
    bil, mot = P.achar_par([a], [b], SINAL, c)
    assert bil is None


def test_posicao_simulada_nunca_vira_aposta_real(cfg):
    """Log 16/11 09:07: posição SIMULADA (modo teste 08:48) retomada no modo REAL ganhou cobertura de verdade."""
    est = P.Estado(cfg)
    g = P.GerentePosicoes(cfg, est, {})
    b = {"id": 1, "jogo": "Chiba x SAN-EN", "times": ["Chiba", "SAN-EN"], "tipo": "total", "periodo": "jogo",
         "pernas": [dict(_p("acima", 189.5), casa="1awin"), dict(_p("abaixo", 190.5), casa="ps3838")]}
    pos = g.abrir(b, {}, simulada=True)
    assert pos["simulada"] and pos["aberta"]
    cfg["modo_teste"] = False
    assert g.executar_cobertura(pos, {"casa": "1awin", "linha": 195.5, "odd": 2.1, "stake": 10.0, "oferta": {}}) is False
    assert not pos["aberta"]
    pos2 = g.abrir(dict(b, id=2), {}, simulada=True)  # (abrir no teste; agora reinicia no real)
    assert pos2["id"] != pos["id"] or not pos["aberta"]
    g2 = P.GerentePosicoes(cfg, est, {})
    g2.iniciar()
    assert all(not p["aberta"] for p in est.posicoes if p.get("simulada"))
    op = P.Operador(cfg, est, {})
    assert op._posicao_aberta(["Chiba", "SAN-EN"], "jogo") is None


def test_odd_fantasma_da_1awin_e_ignorada(cfg):
    cfg = dict(cfg, odd_fantasma_margem=0.15)
    """Log 16/11 09:09: 1aWin Abaixo 195.5 a 2.1 com a PS em ~193 (justo ≈ 1.6): tela velha — aceitou a 1.61."""
    ps = [oferta("ps3838", "total", "jogo", "acima", 193.0, 1.781), oferta("ps3838", "total", "jogo", "abaixo", 193.0, 2.05),
          oferta("ps3838", "total", "jogo", "acima", 195.0, 2.10), oferta("ps3838", "total", "jogo", "abaixo", 195.0, 1.74)]
    w1 = [oferta("1awin", "total", "jogo", "abaixo", 195.5, 2.10)]
    bil, mot = P.achar_par(w1, ps, SINAL, cfg)
    assert bil is None and "fantasma" in mot
    cfg2 = dict(cfg, odd_fantasma_margem=None)  # desligado: voltaria a achar o par
    bil2, _ = P.achar_par(w1, ps, SINAL, cfg2)
    assert bil2 is not None


def test_pos_monitoramento_repete_a_sequencia_manual_do_operador(cfg):
    """15–16/11 Sacramento x OKC, na mão: 3 pares (janela 223–226), depois Under 229.5 SOZINHO (abre 227–229)
    e Under 236.5 SOZINHO (abre 230–236). O jogo terminou entre 227 e 229."""
    cfg = dict(cfg, modo_teste=True, pos_perda_max_total_brl=80.0, perda_max_por_jogo_brl=80.0)  # a lógica da sequência
    E = cfg["perna_ps_eur"] * cfg["cambio_brl_eur"]
    def pr(casa, lado, linha, odd, st, origem):
        return {"casa": casa, "tipo": "total", "periodo": "jogo", "lado": lado, "linha": linha, "odd": odd, "stake": st,
                "origem": origem}
    pos = {"id": 9, "periodo": "jogo", "pernas": [
        pr("1awin", "acima", 222.5, 1.85, 29.40, "entrada"), pr("ps3838", "abaixo", 224.0, 1.813, E, "entrada"),
        pr("1awin", "abaixo", 225.5, 1.85, 30.0, "reentrada"), pr("ps3838", "acima", 223.5, 1.90, E, "reentrada"),
        pr("ps3838", "acima", 224.5, 2.04, E, "reentrada"), pr("1awin", "abaixo", 226.5, 1.85, 35.0, "reentrada")]}
    assert P._txt_totais(P.cobertura_2g(pos["pernas"])) == "223–226"
    ofs = {"1awin": [oferta("1awin", "total", "jogo", "abaixo", 229.5, 1.85), oferta("1awin", "total", "jogo", "acima", 229.5, 1.85),
                     oferta("1awin", "total", "jogo", "abaixo", 224.5, 2.40)],  # 224.5: não acrescenta total novo -> fora
           "ps3838": []}
    melhor, avaliados = P.avaliar_cobertura(pos, ofs, None, cfg)
    assert melhor and melhor["lado"] == "abaixo" and melhor["linha"] == 229.5, avaliados
    assert melhor["depois"]["novos_totais"] == [227, 228, 229]
    pos["pernas"].append(pr("1awin", "abaixo", 229.5, 1.85, 37.0, "cobertura"))
    ofs = {"1awin": [oferta("1awin", "total", "jogo", "abaixo", 236.5, 1.89)], "ps3838": []}
    melhor, _ = P.avaliar_cobertura(pos, ofs, None, cfg)
    assert melhor and melhor["depois"]["novos_totais"] == list(range(230, 237))
    assert melhor["depois"]["piorou_em"]  # acima de 236 essa aposta só perde: o painel avisa
    apertado = dict(cfg, pos_perda_max_total_brl=40.0)
    melhor, avs = P.avaliar_cobertura(pos, ofs, None, apertado)
    assert melhor is None and "limite" in avs[0]["motivo"]



def test_limite_padrao_mantem_o_formato_do_operador(cfg):
    cfg = dict(cfg, perda_max_por_jogo_brl=15.0)
    """Alvo 16/11: pontas ≈ −R$ 10, meio largo ≈ +R$ 45 (TIPO esses valores). Com limite de R$ 15, um Under
    sozinho que deixaria uma ponta em −R$ 40+ NÃO entra; pares que somam janela entram."""
    E = cfg["perna_ps_eur"] * cfg["cambio_brl_eur"]
    cart = [dict(_p("acima", 155.5, 1.85, 33.5), casa="1awin"), dict(_p("abaixo", 156.5, 2.0, E), casa="ps3838")]
    par = [dict(_p("acima", 157.5, 1.85, 33.5), casa="1awin"), dict(_p("abaixo", 159.5, 2.0, E), casa="ps3838")]
    assert P.avaliar_nova_entrada(cart, par, cfg)["ok"]
    sozinho = [dict(_p("abaixo", 166.5, 1.85, 30.0), casa="1awin")]
    av = P.avaliar_nova_entrada(cart + par, sozinho, cfg)
    assert not av["ok"] and "limite" in av["motivo"]



def test_reentrada_sem_distancia_minima_fixa(cfg):
    """Operador 16/11: removida a regra 'Under ≥ 4 pts acima do Over original'. Over 189.5 + Under 190.5 cobrem 190;
    um Under 191.5 (só 2 pts acima do Over) acrescenta 191 e pode entrar; depois 192.5 acrescenta 192, e assim por diante."""
    cfg = dict(cfg, pos_perda_max_total_brl=60.0, perda_max_por_jogo_brl=60.0, pos_stake_1w_brl=10.0)
    E = cfg["perna_ps_eur"] * cfg["cambio_brl_eur"]
    pos = {"id": 1, "periodo": "jogo", "pernas": [
        dict(_p("acima", 189.5, 2.33, 22.74), casa="1awin", origem="entrada"),
        dict(_p("abaixo", 190.5, 1.709, E), casa="ps3838", origem="entrada")]}
    assert P.cobertura_2g(pos["pernas"]) == {190}
    for linha, esperado in ((191.5, [191]), (192.5, [192]), (193.5, [193])):
        melhor, avs = P.avaliar_cobertura(pos, {"1awin": [oferta("1awin", "total", "jogo", "abaixo", linha, 1.85)]}, None, cfg)
        assert melhor and melhor["depois"]["novos_totais"] == esperado, avs
        pos["pernas"].append(dict(melhor["oferta"], casa="1awin", stake=melhor["stake"], origem="cobertura"))
    assert P.cobertura_2g(pos["pernas"]) == {190, 191, 192, 193}


def test_janela_cresce_nas_duas_direcoes(cfg):
    """16/11: Over 189.5 + Under 190.5 = double green em 190. Linha sobe: Under 191.5 acrescenta 191 (usa o Over 189.5).
    Linha desce: Over 188.5 acrescenta 189 (usa o Under 190.5). Linha que não acrescenta nada fica de fora."""
    cfg = dict(cfg, pos_perda_max_total_brl=60.0, perda_max_por_jogo_brl=60.0, pos_stake_1w_brl=10.0)
    E = cfg["perna_ps_eur"] * cfg["cambio_brl_eur"]
    base = [dict(_p("acima", 189.5, 2.33, 22.74), casa="1awin", origem="entrada"),
            dict(_p("abaixo", 190.5, 1.709, E), casa="ps3838", origem="entrada")]
    sobe = {"id": 1, "periodo": "jogo", "pernas": list(base)}
    m, _ = P.avaliar_cobertura(sobe, {"1awin": _ofs(191.5, lado="abaixo", odd=1.85)}, None, cfg)
    assert m and m["lado"] == "abaixo" and m["depois"]["novos_totais"] == [191]
    desce = {"id": 2, "periodo": "jogo", "pernas": list(base)}
    m, _ = P.avaliar_cobertura(desce, {"1awin": _ofs(188.5, lado="acima", odd=1.85)}, None, cfg)
    assert m and m["lado"] == "acima" and m["depois"]["novos_totais"] == [189]
    # as duas aparecem ao mesmo tempo: as duas são avaliadas; a escolhida continua a janela sem buraco
    m, av = P.avaliar_cobertura(sobe, {"1awin": _ofs(191.5, lado="abaixo", odd=1.85) + _ofs(188.5, lado="acima", odd=1.85)}, None, cfg)
    assert len([c for c in av if c["ok"]]) == 2 and m["depois"]["novos_totais"] in ([191], [189])
    # linha que se moveu mas não amplia a janela não é entrada automática
    m, av = P.avaliar_cobertura(sobe, {"1awin": _ofs(190.5, lado="acima", odd=1.85)}, None, cfg)
    assert m is None and "nenhum total novo" in av[0]["motivo"]



def test_limites_do_operador_100_por_jogo_e_ate_6_euros(cfg):
    """16/11: perda máxima R$ 100 por jogo; avulsa até €6 na PS ou o equivalente na 1aWin (R$ 37,20)."""
    c = P.carregar_config("/nao/existe.json", avisar=False)
    assert c["perda_max_por_jogo_brl"] == 100.0 and c["pos_perda_max_total_brl"] == 100.0 and c["perna_ps_eur_max"] == 6.0
    E = c["perna_ps_eur"] * c["cambio_brl_eur"]
    pos = {"id": 1, "periodo": "jogo", "pernas": [
        dict(_p("acima", 150.5, 1.90, 33.45), casa="1awin", origem="entrada"),
        dict(_p("abaixo", 151.5, 2.05, E), casa="ps3838", origem="entrada")]}
    m, _ = P.avaliar_cobertura(pos, {"1awin": _ofs(153.5, odd=1.85)}, None, c)
    assert m and m["stake"] == 37.2  # €6 × 6,20
    m, _ = P.avaliar_cobertura(pos, {"ps3838": _ofs(153.5, odd=1.85)}, None, c)
    assert m and m["stake"] == round(6 * 6.2, 2)
