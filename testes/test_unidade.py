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
    assert cfg["custo_max"] == 5.0   # 20/11: todos os custos +1
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
    assert any("PARÂMETROS EFETIVOS" in l and "custo_max=5.0" in l for l in P._LOG_MEM)


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
    op._atual[0].join(3)  # o monitor (vazio, neste teste) terminou
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
    """Tabela do operador (20/11: todos +1): 1 pt até 3%; 2 pts até 4,8%; 3–6 abaixo de 5%; 7–10 até 8%."""
    f = P.custo_max_para
    assert f(1.0, cfg) == 3.0 and f(1.5, cfg) == 3.0
    assert f(2.0, cfg) == 4.8 and f(2.5, cfg) == 4.8
    assert f(3.0, cfg) == 4.99 and f(6.5, cfg) == 4.99
    assert f(7.0, cfg) == 8.0 and f(10.5, cfg) == 8.0
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 1.80)
    b = oferta("ps3838", "total", "jogo", "acima", 213.5, 2.05)   # janela 1, custo ~4,2%
    assert P.achar_par([a], [b], SINAL, cfg)[0] is None            # 1 ponto só aceita até 3%


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
    """Exemplo 16/11 (só a lógica): 222,5/224 -> +223,5/225,5 -> +224,5/226,5. Janela = totais com LUCRO na
    carteira inteira, entre a linha mais baixa e a mais alta. Linha inteira (224) devolve: no 224 ainda há lucro."""
    cfg = dict(P.DEFAULTS, perda_max_por_jogo_brl=1000)
    cart = [_p("acima", 222.5), _p("abaixo", 224.0)]
    assert P.cobertura_2g(cart) == {223, 224}
    av = P.avaliar_nova_entrada(cart, [_p("acima", 223.5), _p("abaixo", 225.5)], cfg)
    assert av["ok"] and av["novos_totais"] == [225] and not av["perdidos"]
    cart += [_p("acima", 223.5), _p("abaixo", 225.5)]
    av = P.avaliar_nova_entrada(cart, [_p("acima", 224.5), _p("abaixo", 226.5)], cfg)
    assert av["ok"] and av["novos_totais"] == [226]
    cart += [_p("acima", 224.5), _p("abaixo", 226.5)]
    assert P._txt_totais(P.cobertura_2g(cart)) == "223–226"


def test_exemplo_de_6_apostas_do_operador_conta_certa():
    """16/11: Over 221/Under 222/Over 225/Under 226/Over 228/Under 230, R$ 30 cada a 1,85 (R$ 180). 3 ganhando
    = 166,50 − 180 = −13,50 (NÃO é green). Só 229 tem 4 ganhando (+42). Linhas inteiras devolvem."""
    L = [("acima", 221), ("abaixo", 222), ("acima", 225), ("abaixo", 226), ("acima", 228), ("abaixo", 230)]
    pp = [_p(a, b, 1.85, 30.0) for a, b in L]
    t = {x["total"]: x["liquido"] for x in P.tabela_carteira(pp)}
    assert t[220] == -13.5 and t[223] == -13.5 and t[227] == -13.5 and t[231] == -13.5
    assert t[221] == 16.5 and t[229] == 42.0 and t[230] == 16.5
    assert P._txt_totais(P.cobertura_2g(pp)) == "221–222, 225–226, 228–230"


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
    pos["pernas"].append(pr("1awin", "abaixo", 229.5, 1.85, melhor["stake"], "cobertura"))
    ofs = {"1awin": [oferta("1awin", "total", "jogo", "abaixo", 236.5, 1.89)], "ps3838": []}
    melhor, avs = P.avaliar_cobertura(pos, ofs, None, cfg)
    # na mão o operador fez, mas 230–236 continuaram no PREJUÍZO (−R$ 22): não é janela de green
    assert melhor is None and "nenhum total novo com lucro" in avs[0]["motivo"]



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
    um Under 191.5 (só 2 pts acima do Over) acrescenta 191 e pode entrar; o seguinte só se der lucro de verdade."""
    cfg = dict(cfg, pos_perda_max_total_brl=60.0, perda_max_por_jogo_brl=60.0, pos_stake_1w_brl=10.0)
    E = cfg["perna_ps_eur"] * cfg["cambio_brl_eur"]
    pos = {"id": 1, "periodo": "jogo", "pernas": [
        dict(_p("acima", 189.5, 2.33, 22.74), casa="1awin", origem="entrada"),
        dict(_p("abaixo", 190.5, 1.709, E), casa="ps3838", origem="entrada")]}
    assert P.cobertura_2g(pos["pernas"]) == {190}
    melhor, avs = P.avaliar_cobertura(pos, {"1awin": [oferta("1awin", "total", "jogo", "abaixo", 191.5, 1.85)]}, None, cfg)
    assert melhor and melhor["depois"]["novos_totais"] == [191], avs   # só 2 pts acima do Over original: entra
    pos["pernas"].append(dict(melhor["oferta"], casa="1awin", stake=melhor["stake"], origem="cobertura"))
    assert P.cobertura_2g(pos["pernas"]) == {190, 191}
    # o próximo Under sozinho (192.5) com R$ 10 não põe o 192 no lucro (o custo acumula): fica fora, com o motivo
    melhor, avs = P.avaliar_cobertura(pos, {"1awin": [oferta("1awin", "total", "jogo", "abaixo", 192.5, 1.85)]}, None, cfg)
    assert melhor is None and "lucro" in avs[0]["motivo"]


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


def test_sigla_no_meio_do_nome(cfg):
    """16/11: sinal 'KK SC Derby Podgorica x BC Vienna'; 1aWin 'Studentski Centar — Vienna Basket';
    PS 'KK Studentski centar Podgorica -vs- BC Vienna'. 'SC' = iniciais de Studentski Centar."""
    t = ["KK SC Derby Podgorica", "BC Vienna"]
    assert P.linha_confronto("Studentski Centar — Vienna Basket\n", t) == ["Studentski Centar", "Vienna Basket"]
    assert P.ps_e_o_jogo("ABA - Liga Adriática / KK Studentski centar Podgorica -vs- BC Vienna\n", t)
    # sigla sozinha é pista fraca: só na 2ª passada e com o OUTRO time batendo
    assert not P._nome_bate("KK SC Derby Podgorica", "Studentski Centar")
    assert P.linha_confronto("Sloboda Celik — Igokea\n", t) is None
    assert not P._nome_bate("KK Krka", "Kauno Kalnas", True)  # 'KK' é código de clube, não sigla


def test_parceiro_duplo_duplo_primeiro_senao_sequencia(cfg):
    """Parceiro 17/11: 'peguei Over 200 Under 203; se apitar Over 200 e Under 205, eu pego de novo — se bater
    201/202/203 é duplo duplo. Se não brechar duplo duplo, vou em sequência: 200/202, 202/204, 204/206.' Nada repetido."""
    c0 = P._calcular({"odd": 1.95}, {"odd": 2.05}, cfg)
    cart = [dict(_p("acima", 200.0, 1.95, c0["stake_1w"]), casa="1awin"), dict(_p("abaixo", 203.0, 2.05, c0["stake_ps"]), casa="ps3838")]

    def preferir(a, b, c):
        novas = [dict(a, casa="1awin", stake=c["stake_1w"]), dict(b, casa="ps3838", stake=c["stake_ps"])]
        ks = P.faixa_carteira(cart + novas)
        g, g0 = P.greens_por_total(cart + novas, ks), P.greens_por_total(cart, ks)
        return sum(1 for k in ks if g[k] >= 4 and g0[k] < 4)

    excluir = lambda a, b, jan: not P.avaliar_nova_entrada(
        cart, [dict(a, casa="1awin", stake=P._calcular(a, b, cfg)["stake_1w"]),
               dict(b, casa="ps3838", stake=P._calcular(a, b, cfg)["stake_ps"])], cfg)["ok"]
    w1 = [oferta("1awin", "total", "jogo", "acima", 200.0, 1.95), oferta("1awin", "total", "jogo", "acima", 203.0, 1.95)]
    ps = [oferta("ps3838", "total", "jogo", "abaixo", 205.0, 2.05), oferta("ps3838", "total", "jogo", "abaixo", 209.0, 2.05),
          oferta("ps3838", "total", "jogo", "abaixo", 203.0, 2.05)]
    # sem preferência: maior janela (Over 200 × Under 209); com a do parceiro: o duplo duplo Over 200 × Under 205
    bil, _ = P.achar_par(w1, ps, SINAL, cfg, excluir=excluir, preferir=preferir)
    assert bil and (bil["pernas"][0]["linha"], bil["pernas"][1]["linha"]) in ((200.0, 205.0), (200.0, 209.0))
    tab = {t["total"]: t["greens"] for t in P.tabela_carteira(cart, bil["pernas"])}
    assert tab[201] == 4 and tab[202] == 4  # duplo duplo
    # o mesmo par de novo nunca entra
    bil2, _ = P.achar_par([w1[0]], [ps[2]], SINAL, cfg, excluir=excluir, preferir=preferir)
    assert bil2 is None
    # sem chance de duplo duplo: sequência (Over 203 × Under 205 encosta e acrescenta 204)
    bil3, _ = P.achar_par([w1[1]], [ps[0]], SINAL, cfg, excluir=excluir, preferir=preferir)
    assert bil3 and P.avaliar_nova_entrada(cart, bil3["pernas"], cfg)["novos_totais"] == [204, 205]


def test_reentrada_livre_so_logica_do_parceiro(cfg):
    """Ordem 17/11: nas reentradas SÓ duplo duplo / sequência / nunca o mesmo par (sem total novo com lucro,
    sem 'não tirar lucro', sem janela ≥ 1ª, sem tabela de custo, sem R$ 100 / 4 entradas)."""
    assert P.DEFAULTS["reentrada_livre"] is True

    def par(ov, un, o1=1.90, o2=2.0):
        a = {"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": ov, "odd": o1}
        b = {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": un, "odd": o2}
        c = P._calcular(a, b, cfg)
        return [dict(a, casa="1awin", stake=c["stake_1w"]), dict(b, casa="ps3838", stake=c["stake_ps"])]
    cart = par(200, 203)
    assert P.tipo_reentrada(cart, par(200, 203)) is None              # o mesmo par: nunca
    assert P.tipo_reentrada(cart, par(200, 205)) == "duplo duplo"     # repete o Over e estica o Under
    assert P.tipo_reentrada(cart, par(203, 205)) == "sequência"       # encosta por cima
    assert P.tipo_reentrada(cart, par(198, 200)) == "sequência"       # encosta por baixo (linha desceu)
    assert P.tipo_reentrada(cart, par(210, 212)) is None              # longe da janela: não é sequência
    # janela menor que a da 1ª entrada (1 ponto depois de 3) agora pode, se for sequência
    assert P.tipo_reentrada(cart, par(203, 204)) == "sequência"


def test_reentrada_custo_ate_10_preferindo_4(cfg):
    """Operador 17/11: reentrada até 10% se valer a pena (duplo duplo ou janela ≥ 3); preferência ≤ 4%.
    O par de 22% de 15:48 (1aWin a 1.43) nunca mais entra."""
    assert P.DEFAULTS["reentrada_custo_max"] == 16.0 and P.DEFAULTS["reentrada_custo_bom"] == 5.0  # 20/11: +1
    a = {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": 195.5, "odd": 1.43}
    b = {"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": 186.0, "odd": 1.689}
    assert P._calcular(a, b, cfg)["custo"] > 10
    cfg_re = dict(cfg, custo_por_janela=None, custo_max=10.0)
    bil, _ = P.achar_par([a], [b], SINAL, cfg_re)
    assert bil is None  # acima do teto de 10%



def test_odd_minima_1_78(cfg):
    """Operador 17/11: odds sempre no mínimo 1.78/1.79 — nenhuma perna abaixo (par, reentrada ou avulsa)."""
    assert P.DEFAULTS["odd_min"] == 1.78
    c = dict(cfg, odd_min=1.78)
    a = oferta("1awin", "total", "jogo", "abaixo", 214.5, 1.77)
    b = oferta("ps3838", "total", "jogo", "acima", 211.5, 2.30)
    assert P.achar_par([a], [b], SINAL, c)[0] is None
    a2 = dict(a, odd=1.80)
    assert P.achar_par([a2], [b], SINAL, c)[0] is not None
    pos = _pos(250.5, 252.5)
    m, av = P.avaliar_cobertura(pos, {"1awin": _ofs(254.5, odd=1.60)}, None, dict(c, pos_perda_max_total_brl=100))
    assert m is None and av == []


def test_escadinha_pacers_x_rockets(cfg):
    """19/11: repetiu o Acima 231.5 quatro vezes e entrou com Unders DENTRO da janela (233, 232.5).
    Agora: a reentrada tem que esticar a janela para fora e a mesma linha só repete uma vez."""
    def par(ov, un):
        return [dict(_p("acima", ov, 1.85, 33.68), casa="1awin"), dict(_p("abaixo", un, 2.01, 31.0), casa="ps3838")]
    cart = par(231.5, 234.0)
    assert P.tipo_reentrada(cart, par(231.5, 233.0)) is None      # dentro da janela
    assert P.tipo_reentrada(cart, par(231.5, 232.5)) is None      # dentro da janela
    assert P.tipo_reentrada(cart, par(231.5, 235.5)) == "duplo duplo"  # repete o Over UMA vez e estica
    cart += par(231.5, 235.5)
    assert P.tipo_reentrada(cart, par(231.5, 237.5)) is None      # 3ª vez o mesmo Acima 231.5: não
    assert P.tipo_reentrada(cart, par(234.5, 236.5)) in ("sequência", "duplo duplo")  # escadinha pra cima
    assert P.tipo_reentrada(cart, par(229.5, 231.5)) in ("sequência", "duplo duplo")  # escadinha pra baixo


def test_linha_principal_da_escada():
    ofs = []
    for L, oa, ou in [(229.5, 1.55, 2.40), (231.5, 1.80, 2.02), (232.5, 1.91, 1.91), (234.5, 2.20, 1.66)]:
        ofs += [{"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": L, "odd": oa},
                {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": L, "odd": ou}]
    assert P.linha_principal(ofs) == 232.5
    assert P.DEFAULTS["min_movimento_linha"] == 0.5 and P.DEFAULTS["reentrada_espera_min_s"] == 60  # especificação 19/11


# ------------------------------------------------------------------ escadinha (especificação do operador 19/11)
def _parx(ov, o_ov, un, o_un, cfg, par_id=None):
    """Par como o programa monta: 1aWin = Over, PS = Under (€5 × câmbio), stake da 1aWin pelo retorno igual."""
    a = {"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": ov, "odd": o_ov}
    b = {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": un, "odd": o_un}
    c = P._calcular(a, b, cfg)
    return [dict(a, casa="1awin", stake=c["stake_1w"], par=par_id), dict(b, casa="ps3838", stake=c["stake_ps"], par=par_id)]


def _cfg_esc(cfg):
    return dict(cfg, perda_max_por_jogo_brl=100.0)


def test_esc_janela_continua_pacers(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    assert P.janela_continua(cart) == (232, 234)


def test_esc_01_candidato_dentro_da_janela_recusado(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(232.5, 1.85, 233.5, 2.01, cfg), _cfg_esc(cfg))
    assert not av["ok"] and "não acrescenta total lucrativo novo" in av["motivo"]


def test_esc_02_amplia_um_ponto_para_cima(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(234.5, 1.85, 235.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["novos"] == [235] and av["janela_nova"] == (232, 235)


def test_esc_03_amplia_um_ponto_para_baixo(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(230.5, 1.85, 231.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["novos"] == [231] and av["janela_nova"] == (231, 234)


def test_esc_04_ilha_separada_recusada(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(239.5, 1.85, 241.5, 2.01, cfg), _cfg_esc(cfg))
    assert not av["ok"] and "ilha separada" in av["motivo"]


def test_esc_05_06_segunda_vez_pode_terceira_nao(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 235.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and 235 in av["novos"]                       # 2ª vez do Over 231.5: amplia -> pode
    cart += _parx(231.5, 1.85, 235.5, 2.01, cfg, 2)
    # 19/11 (ajuste): a 3ª vez PODE, se ampliar — o limite é max_repeticoes_mesma_perna (4)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 236.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["tipo"] == "âncora", av["motivo"]
    c2 = dict(_cfg_esc(cfg), max_repeticoes_mesma_perna=2)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 236.5, 2.01, cfg), c2)
    assert not av["ok"] and "já usado 2 vezes" in av["motivo"]
    # o Under 231.5 é outra linha (outro lado): não conta como repetição do Over 231.5
    av = P.avaliar_reentrada(cart, _parx(229.5, 1.85, 231.5, 2.01, cfg), _cfg_esc(cfg))
    assert "repetição" not in av["motivo"]


def test_esc_07_par_identico_recusado(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 234.0, 2.01, cfg), _cfg_esc(cfg))
    assert not av["ok"] and "par idêntico" in av["motivo"]


def test_esc_08_total_antigo_vira_prejuizo_recusado(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    novas = [dict(_p("acima", 234.5, 1.85, 100.0), casa="1awin"), dict(_p("abaixo", 236.5, 1.85, 5.0), casa="ps3838")]
    av = P.avaliar_reentrada(cart, novas, _cfg_esc(cfg))
    assert not av["ok"] and "de lucro em prejuízo" in av["motivo"] and av["piorados"]


def test_esc_09_pior_caso_acima_de_100_recusado(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    novas = [dict(_p("acima", 234.5, 1.85, 10.0), casa="1awin"), dict(_p("abaixo", 236.5, 1.80, 300.0), casa="ps3838")]
    av = P.avaliar_reentrada(cart, novas, _cfg_esc(cfg))
    assert not av["ok"] and "pior caso ultrapassa" in av["motivo"] and av["pior_depois"] < -100


def test_esc_10_11_devolucao_so_em_linha_inteira():
    over_int, under_int = _p("acima", 231.0, 1.9, 10.0), _p("abaixo", 231.0, 1.9, 10.0)
    assert P.resultado_perna(over_int, 231) == "D" and P.pagamento_perna(over_int, 231) == 10.0
    assert P.resultado_perna(under_int, 231) == "D" and P.pagamento_perna(under_int, 231) == 10.0
    assert P.resultado_perna(over_int, 232) == "G" and P.resultado_perna(under_int, 230) == "G"
    meio = _p("acima", 231.5, 1.9, 10.0)
    for k in range(225, 240):
        assert P.resultado_perna(meio, k) in ("G", "P")       # ,5 nunca devolve


def test_esc_12_13_intervalo_e_movimento(cfg):
    c = dict(cfg, reentrada_espera_min_s=60, min_movimento_linha=0.5)
    ok, mot = P.portao_reentrada(1000.0, 232.5, 1030.0, 234.0, c)
    assert not ok and "intervalo mínimo" in mot                 # 30 s < 60 s, mesmo com a linha andando
    ok, mot = P.portao_reentrada(1000.0, 232.5, 1070.0, 232.5, c)
    assert not ok and "não se movimentou" in mot                # passou 60 s mas a linha não andou
    ok, _ = P.portao_reentrada(1000.0, 232.5, 1070.0, 233.0, c)
    assert ok                                                   # 70 s e andou 0,5
    assert P.DEFAULTS["min_movimento_linha"] == 0.5


def test_esc_15_pacers_x_rockets(cfg):
    """O caso real: depois de Over 231.5 + Under 234, os Unders 233 e 232.5 NÃO entram; o 235.5 só como 2º uso."""
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    c = _cfg_esc(cfg)
    assert not P.avaliar_reentrada(cart, _parx(231.5, 1.85, 233.0, 2.01, cfg), c)["ok"]
    assert not P.avaliar_reentrada(cart, _parx(231.5, 1.85, 232.5, 2.07, cfg), c)["ok"]
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 235.5, 1.806, cfg), c)
    assert av["ok"] and av["novos"] == [235]


def test_esc_16_carteira_usa_o_que_a_casa_aceitou():
    bil = {"id": 7, "pernas": [
        {"casa": "1awin", "tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": 195.5, "odd": 2.1,
         "odd_aceita": 1.61, "stake": 26.29},
        {"casa": "ps3838", "tipo": "total", "periodo": "jogo", "lado": "acima", "linha": 193.0, "odd": 1.781,
         "odd_viva": 1.819, "stake": 31.0}]}
    pa = P.pernas_aceitas(bil)
    assert pa[0]["odd"] == 1.61 and pa[1]["odd"] == 1.819 and pa[0]["stake"] == 26.29 and pa[0]["par"] == 7



# ------------------------------------------------------------------ âncora × escadinha (ajuste do operador 19/11)
def test_anc_1_mesma_perna_repetida_ampliando_aceita(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 236.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["tipo"] == "âncora" and av["novos"] == [235, 236] and av["ancora"] == ["Over 231.5"]


def test_anc_2_mesma_perna_repetida_sem_ampliar_recusa(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    for un in (233.0, 232.5):
        av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, un, 2.01, cfg), _cfg_esc(cfg))
        assert not av["ok"] and "não acrescenta total lucrativo novo" in av["motivo"]


def test_anc_3_par_identico_recusa(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 234.0, 2.01, cfg), _cfg_esc(cfg))
    assert not av["ok"] and "par idêntico" in av["motivo"]


def test_anc_4_escadinha_para_cima_aceita(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1) + _parx(231.5, 1.85, 235.5, 2.01, cfg, 2)   # janela 232–235
    assert P.janela_continua(cart) == (232, 235)
    av = P.avaliar_reentrada(cart, _parx(234.5, 1.85, 236.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["tipo"] == "escadinha" and av["janela_nova"] == (232, 236)
    cart += _parx(234.5, 1.85, 236.5, 2.01, cfg, 3)
    av = P.avaliar_reentrada(cart, _parx(236.5, 1.85, 238.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["janela_nova"][1] == 238


def test_anc_5_escadinha_para_baixo_aceita(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1) + _parx(231.5, 1.85, 235.5, 2.01, cfg, 2)
    av = P.avaliar_reentrada(cart, _parx(229.5, 1.85, 231.5, 2.01, cfg), _cfg_esc(cfg))
    assert av["ok"] and av["tipo"] == "escadinha" and av["janela_nova"] == (230, 235)


def test_anc_6_ilha_separada_recusa(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(239.5, 1.85, 241.5, 2.01, cfg), _cfg_esc(cfg))
    assert not av["ok"] and "ilha separada" in av["motivo"]


def test_anc_7_amplia_mas_passa_de_100_recusa(cfg):
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    novas = [dict(_p("acima", 231.5, 1.85, 10.0), casa="1awin"), dict(_p("abaixo", 236.5, 1.80, 300.0), casa="ps3838")]
    av = P.avaliar_reentrada(cart, novas, _cfg_esc(cfg))
    assert not av["ok"] and "pior caso ultrapassa" in av["motivo"] and 235 in (av.get("novos") or [235])


def test_anc_8_escolha_entre_ancora_e_escadinha(cfg):
    """Vários candidatos: simula todos e escolhe pela ordem (novos, pior, capital, lucro mínimo, concentração),
    com empate prático decidido a favor da escadinha completa."""
    c = _cfg_esc(cfg)
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)

    def preferir(a, b, cc):
        novas = [dict(a, casa="1awin", stake=cc["stake_1w"]), dict(b, casa="ps3838", stake=cc["stake_ps"])]
        return P.nota_reentrada(P.avaliar_reentrada(cart, novas, c))

    def excluir(a, b, jan):
        cc = P._calcular(a, b, c)
        novas = [dict(a, casa="1awin", stake=cc["stake_1w"]), dict(b, casa="ps3838", stake=cc["stake_ps"])]
        return not P.avaliar_reentrada(cart, novas, c)["ok"]
    # âncora Over 231.5 × Under 235.5 (+235) contra escadinha Over 234.5 × Under 236.5 (+235–236): mais totais novos vence
    w1 = [oferta("1awin", "total", "jogo", "acima", 231.5, 1.85), oferta("1awin", "total", "jogo", "acima", 234.5, 1.85)]
    ps = [oferta("ps3838", "total", "jogo", "abaixo", 235.5, 2.01), oferta("ps3838", "total", "jogo", "abaixo", 236.5, 2.01)]
    bil, _ = P.achar_par(w1, ps, SINAL, c, excluir=excluir, preferir=preferir)
    assert bil and P.avaliar_reentrada(cart, bil["pernas"], c)["novos"] == [235, 236]
    # mesma ampliação (+235) e números parecidos: âncora Over 231.5 × Under 235.5 contra escadinha Over 232.5 × Under 235.5
    w1 = [oferta("1awin", "total", "jogo", "acima", 231.5, 1.85), oferta("1awin", "total", "jogo", "acima", 232.5, 1.85)]
    ps = [oferta("ps3838", "total", "jogo", "abaixo", 235.5, 2.01)]
    bil, _ = P.achar_par(w1, ps, SINAL, c, excluir=excluir, preferir=preferir)
    av = P.avaliar_reentrada(cart, bil["pernas"], c)
    # 21/11: SEM preferência por tipo — empatados em tudo, qualquer um dos dois serve (o rótulo só explica)
    assert av["ok"] and av["novos"] == [235] and av["tipo"] in ("âncora", "escadinha")


def test_mesma_perna_e_lado_linha_mercado_e_ancora_parada(cfg):
    """Operador 19/11: 'mesma perna' = lado + linha + mercado; ampliação pelo LUCRO da carteira; na âncora
    o lado repetido fica PARADO e só a outra perna vai para fora."""
    o = _p("acima", 231.5)
    assert P._mesma_perna(o, dict(o, casa="ps3838", odd=2.2))            # casa/odd não importam
    assert not P._mesma_perna(o, dict(o, lado="abaixo"))                 # outro lado
    assert not P._mesma_perna(o, dict(o, periodo="q3"))                  # outro mercado (período)
    assert not P._mesma_perna(o, dict(o, linha=232.5))                   # outra linha
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)
    av = P.avaliar_reentrada(cart, _parx(231.5, 1.85, 235.5, 2.01, cfg), _cfg_esc(cfg))  # Over parado, Under fora
    assert av["ok"] and av["tipo"] == "âncora" and av["novos"] == [235]
    # linha nominal 'nova' sem lucro novo não amplia: Under 235.5 com stake mínima não põe 235 no lucro
    fraco = [dict(_p("acima", 231.5, 1.85, 1.0), casa="1awin"), dict(_p("abaixo", 235.5, 1.85, 1.0), casa="ps3838")]
    av = P.avaliar_reentrada(cart, fraco, _cfg_esc(cfg))
    assert not av["ok"] and "não acrescenta total lucrativo novo" in av["motivo"]


def test_posicoes_velhas_fecham_sozinhas(cfg):
    """19/11: com a cobertura desligada, posições de ontem ficavam 'monitorando' no painel."""
    import time as _t
    est = P.Estado(cfg)
    g = P.GerentePosicoes(cfg, est, {})
    b = {"id": 1, "jogo": "A x B", "times": ["A", "B"], "tipo": "total", "periodo": "jogo",
         "pernas": [dict(_p("acima", 150.5), casa="1awin"), dict(_p("abaixo", 151.5), casa="ps3838")]}
    velha = g.abrir(b, {}, simulada=False)
    velha["aberta_em"] = _t.time() - 5 * 3600
    nova = g.abrir(dict(b, id=2, jogo="C x D", times=["C", "D"]), {}, simulada=False)
    assert g.encerrar_velhas() == 1 and not velha["aberta"] and nova["aberta"]
    assert "jogo antigo" in velha["status"]


# ------------------------------------------------------------------ segurança operacional (operador 20/11)
def _l(ok=True, ofertas=(), motivo="", limitada=False):
    return {"ok": ok, "ofertas": list(ofertas), "motivo": motivo, "limitada": limitada}


def test_seg_classificacao_da_leitura():
    assert P.classificar_leitura(_l(ofertas=[{"x": 1}])) == P.OFERTAS_ENCONTRADAS
    assert P.classificar_leitura(_l()) == P.VAZIO_CONFIRMADO
    assert P.classificar_leitura(_l(motivo="PS: mercado travado (cadeado em 32 odds)")) == P.SUSPENSO
    for ruim in (_l(ok=False, motivo="erro na leitura: timeout"), _l(ok=False, motivo="PS deslogada — odds escondidas"),
                 _l(ok=False, limitada=True, motivo="casa limitada (Excesso de requisições)"),
                 _l(ok=False, motivo="a página da 1aWin não é a deste jogo"), None):
        assert P.classificar_leitura(ruim) == P.ERRO_DE_LEITURA


def test_seg_20_vazias_validas_liberam(cfg):
    c, e, acao = 0, 0, None
    for _ in range(20):
        c, e, acao = P.contar_vazio({"1awin": P.VAZIO_CONFIRMADO, "ps3838": P.VAZIO_CONFIRMADO}, c, e, cfg)
    assert c == 20 and acao == "libera"


def test_seg_19_vazias_e_uma_oferta_zera(cfg):
    c, e = 19, 0
    c, e, acao = P.contar_vazio({"1awin": P.OFERTAS_ENCONTRADAS, "ps3838": P.VAZIO_CONFIRMADO}, c, e, cfg)
    assert c == 0 and acao is None


def test_seg_erro_e_suspenso_nao_contam_como_vazio(cfg):
    c, e = 5, 0
    c, e, _ = P.contar_vazio({"1awin": P.ERRO_DE_LEITURA, "ps3838": P.VAZIO_CONFIRMADO}, c, e, cfg)
    assert c == 5 and e == 1                                  # erro não soma vazio
    c, e, _ = P.contar_vazio({"1awin": P.SUSPENSO, "ps3838": P.VAZIO_CONFIRMADO}, c, e, cfg)
    assert c == 5                                             # suspenso não soma vazio
    c, e, _ = P.contar_vazio({"1awin": P.VAZIO_CONFIRMADO, "ps3838": P.SUSPENSO}, c, e, cfg)
    assert c == 5
    acao = None
    for _ in range(10):
        c, e, acao = P.contar_vazio({"1awin": P.ERRO_DE_LEITURA, "ps3838": P.ERRO_DE_LEITURA}, c, e, cfg)
    assert acao == "alerta_erro" and c == 5                   # erro repetido: alerta, NÃO libera


def _op(cfg):
    est = P.Estado(cfg)
    op = P.Operador(cfg, est, {})
    iniciados = []
    op._puxar_fila_original = op._puxar_fila
    op.novo_sinal_original = op.novo_sinal
    return est, op, iniciados


def _sinal(nome):
    a, b = nome.split(" x ")
    return dict(SINAL, id=nome, jogo=nome, times=[a, b])


def _bil(est, jogo="Lakers x Jazz"):
    b = {"jogo": jogo, "times": jogo.split(" x "), "tipo": "total", "periodo": "jogo", "janela": 1.0, "custo": 1.0,
         "pernas": [{"casa": "1awin", "selecao": "Abaixo de 234.5 (Jogo)", "linha": 234.5, "lado": "abaixo"},
                    {"casa": "ps3838", "selecao": "Acima de 233.5 (Jogo)", "linha": 233.5, "lado": "acima"}]}
    est.registrar_bilhete(b, "sinal")
    return b


def test_seg_incompleto_e_incerto_seguram_a_fila(cfg, monkeypatch):
    for estado in ("INCOMPLETO", "INCERTO"):
        est, op, _ = _op(cfg)
        comecou = []
        monkeypatch.setattr(threading.Thread, "start", lambda self: comecou.append(self.name))
        op.bloquear(estado, _bil(est), "1aWin: Abaixo de 234.5", "PS3838: Acima de 233.5")
        assert op.novo_sinal(_sinal("Denver x Boston")) is False          # não começa: vai para a fila
        assert [x["jogo"] for _, x in op._fila] == ["Denver x Boston"]
        op._puxar_fila()
        assert [n for n in comecou if n != "gravar"] == [] and op._fila   # fila PARADA (nenhum monitor começou)
        assert est.protecao["bloqueio"]["estado"] == estado
        assert "1aWin" in est.protecao["bloqueio"]["confirmada"]


def test_seg_nenhuma_perna_aceita_pode_liberar(cfg, monkeypatch):
    est, op, _ = _op(cfg)
    comecou = []
    monkeypatch.setattr(op, "novo_sinal", lambda s, origem="sinal": comecou.append((s["jogo"], origem)))
    op.bloquear("INCERTO", _bil(est), "nenhuma comprovada", "1aWin (incerta)")
    op._fila = [(time.time(), _sinal("Denver x Boston"))]
    assert op.resolver("nao_apostado")
    time.sleep(0.3)
    assert op.bloqueio is None and comecou == [("Denver x Boston", "fila")]
    assert est.bilhetes[-1]["status"] == "falhou"


def test_seg_liberacao_manual(cfg, monkeypatch):
    est, op, _ = _op(cfg)
    comecou = []
    monkeypatch.setattr(op, "novo_sinal", lambda s, origem="sinal": comecou.append(s["jogo"]))
    op.bloquear("INCOMPLETO", _bil(est), "1aWin: x", "PS3838: y")
    op._fila = [(time.time(), _sinal("Denver x Boston"))]
    assert op.resolver("liberar")
    time.sleep(0.3)
    assert op.bloqueio is None and comecou == ["Denver x Boston"]
    assert any("liberação manual" in a for a in est.bilhetes[-1].get("avisos", []))


def test_seg_sinal_expira_na_fila(cfg, monkeypatch):
    est, op, _ = _op(cfg)
    comecou = []
    monkeypatch.setattr(op, "novo_sinal", lambda s, origem="sinal": comecou.append(s["jogo"]))
    op._fila = [(time.time() - 25 * 60, _sinal("Velho x Jogo"))]     # validade: 20 min
    op._puxar_fila()
    time.sleep(0.3)
    assert comecou == [] and op._fila == []
    op._fila = [(time.time() - 25 * 60, _sinal("Velho x Jogo")), (time.time() - 60, _sinal("Novo x Jogo"))]
    op._puxar_fila()
    time.sleep(0.3)
    assert comecou == ["Novo x Jogo"]


def test_seg_reinicio_preserva_protecao_fila_contador_e_bloqueio(cfg):
    est, op, _ = _op(cfg)
    op._prot = {"jogo": "Lakers x Jazz", "pares": 2, "max": 4, "ultima": time.time() - 60,
                "sinal": _sinal("Lakers x Jazz"), "bilhetes": [1, 2], "linha_ultima": 232.5}
    op._vazio = [7, 1]
    op._fila = [(time.time(), _sinal("Denver x Boston"))]
    op._salvar_oper()
    op2 = P.Operador(cfg, P.Estado(cfg), {})
    assert op2._retomar["jogo"] == "Lakers x Jazz" and op2._retomar["bilhetes"] == [1, 2]
    assert op2._vazio == [7, 1] and [x["jogo"] for _, x in op2._fila] == ["Denver x Boston"]
    op2.bloquear("INCERTO", _bil(op2.estado), "1aWin: a", "PS3838: b")
    op3 = P.Operador(cfg, P.Estado(cfg), {})
    assert op3.bloqueio and op3.bloqueio["estado"] == "INCERTO"


# ------------------------------------------------------------------ 21/11: sem prioridade por tipo; motivo real
def _cenario_reentrada(cfg):
    c = dict(cfg)
    cart = _parx(231.5, 1.85, 234.0, 2.01, cfg, 1)

    def preferir(a, b, cc):
        novas = [dict(a, casa="1awin", stake=cc["stake_1w"]), dict(b, casa="ps3838", stake=cc["stake_ps"])]
        return P.nota_reentrada(P.avaliar_reentrada(cart, novas, c), cc["custo"], min(a["odd"], b["odd"]))

    def excluir(a, b, jan):
        cc = P._calcular(a, b, c)
        av = P.avaliar_reentrada(cart, [dict(a, casa="1awin", stake=cc["stake_1w"]),
                                        dict(b, casa="ps3838", stake=cc["stake_ps"])], c)
        return None if av["ok"] else av["motivo"]
    return c, cart, preferir, excluir


def test_r21_ancora_valida_entra_mesmo_com_escadinha_disponivel(cfg):
    c, cart, preferir, excluir = _cenario_reentrada(cfg)
    # âncora Over 231.5 × Under 236.5 (+235–236) contra escadinha Over 232.5 × Under 235.5 (+235): vence a âncora
    w1 = [oferta("1awin", "total", "jogo", "acima", 231.5, 1.85), oferta("1awin", "total", "jogo", "acima", 232.5, 1.85)]
    ps = [oferta("ps3838", "total", "jogo", "abaixo", 236.5, 2.01), oferta("ps3838", "total", "jogo", "abaixo", 235.5, 2.01)]
    bil, _ = P.achar_par(w1, ps, SINAL, c, excluir=excluir, preferir=preferir)
    av = P.avaliar_reentrada(cart, bil["pernas"], c)
    assert av["tipo"] == "âncora" and av["novos"] == [235, 236]


def test_r21_escadinha_valida_entra_mesmo_com_ancora_disponivel(cfg):
    c, cart, preferir, excluir = _cenario_reentrada(cfg)
    # escadinha 1aWin Under 236.5 × PS Over 233.5 (+235–236) contra âncora 1aWin Over 231.5 × PS Under 235.5 (+235)
    w1 = [oferta("1awin", "total", "jogo", "acima", 231.5, 1.85), oferta("1awin", "total", "jogo", "abaixo", 236.5, 1.85)]
    ps = [oferta("ps3838", "total", "jogo", "abaixo", 235.5, 2.01), oferta("ps3838", "total", "jogo", "acima", 233.5, 2.01)]
    bil, _ = P.achar_par(w1, ps, SINAL, c, excluir=excluir, preferir=preferir)
    av = P.avaliar_reentrada(cart, bil["pernas"], c)
    assert av["tipo"] == "escadinha" and av["novos"] == [235, 236]


def test_r21_vence_quem_melhora_mais_a_carteira():
    base = {"novos": [235], "janela_nova": (232, 235), "pior_depois": -5.0}
    melhor = {"novos": [235, 236], "janela_nova": (232, 236), "pior_depois": -8.0}
    assert P.nota_reentrada(melhor, 3.6, 1.85) > P.nota_reentrada(base, 2.0, 1.95)            # 1º: mais totais novos
    menos_risco = dict(base, pior_depois=-3.0)
    assert P.nota_reentrada(menos_risco, 3.6, 1.80) > P.nota_reentrada(base, 2.0, 1.95)       # 3º: menor prejuízo
    mais_barato = dict(base)
    assert P.nota_reentrada(mais_barato, 2.0, 1.80) > P.nota_reentrada(base, 3.0, 1.95)       # 4º: menor custo
    assert P.nota_reentrada(base, 2.0, 1.95) > P.nota_reentrada(base, 2.0, 1.80)              # 6º: maior odd mínima
    # nenhum item da nota olha o rótulo âncora/escadinha
    assert P.nota_reentrada(dict(base, tipo="âncora"), 2, 1.9) == P.nota_reentrada(dict(base, tipo="escadinha"), 2, 1.9)


def test_r21_invalido_continua_recusado_com_motivo_real(cfg):
    c, cart, preferir, excluir = _cenario_reentrada(cfg)
    # dentro da janela: recusado e o log mostra ESTE par e o motivo real
    w1 = [oferta("1awin", "total", "jogo", "acima", 231.5, 1.95)]
    ps = [oferta("ps3838", "total", "jogo", "abaixo", 233.0, 2.05)]   # custo baixo: o motivo é a regra da carteira
    bil, mot = P.achar_par(w1, ps, SINAL, c, excluir=excluir, preferir=preferir)
    assert bil is None
    assert "RECUSADO: 1aWin Over 231,5 @1,95 + PS Under 233 @2,05" in mot
    assert "motivo: não acrescenta total lucrativo novo" in mot
    # custo acima do limite da janela: motivo de custo, com os números
    w1 = [oferta("1awin", "total", "jogo", "abaixo", 214.5, 1.80)]
    ps = [oferta("ps3838", "total", "jogo", "acima", 213.5, 2.05)]
    bil, mot = P.achar_par(w1, ps, SINAL, cfg)
    assert bil is None and "custo" in mot and "acima do limite de 3,00% para janela 1,0" in mot
    # odd abaixo do piso: aparece no motivo
    bil, mot = P.achar_par([oferta("1awin", "total", "jogo", "abaixo", 214.5, 1.70)], ps, SINAL, dict(cfg, odd_min=1.78))
    assert bil is None and "abaixo do piso" in mot


def test_r21_nenhuma_regra_financeira_afrouxada():
    d = P.DEFAULTS
    assert d["custo_por_janela"] == [[1, 2, 3.0], [2, 3, 4.8], [3, 7, 4.99], [7, 11, 8.0]]
    assert d["odd_min"] == 1.78 and d["perda_max_por_jogo_brl"] == 100.0 and d["janela_min"] == 1.0
    assert d["max_entradas_por_jogo"] == 4
    # reentrada usa a MESMA tabela por janela (não há mais teto fixo de 16% aplicado)
    import inspect
    src = inspect.getsource(P.Operador._monitorar)
    assert "custo_max=teto" not in src and "custo_por_janela=None" not in src


# ================================================================== 22/11: execução em estados (Bucks x Portland)
# Casas SIMULADAS (sem navegador): cada uma roda as tarefas em threads próprias, como as casas reais.
class _Pagina:
    url = "http://falsa/jogo"


class CasaFalsa:
    def __init__(self, nome, cfg, **r):
        self.nome, self.cfg, self.r = nome, cfg, r
        self.page = _Pagina()
        self.chamadas = []
        self.leituras = 0
        self.t_disparo = None

    @property
    def rotulo(self):
        return P.NOME_CASA[self.nome]

    def enfileirar(self, fn, *args):
        ev, caixa = threading.Event(), {}

        def roda():
            time.sleep(self.r.get("fila_s", 0))   # a casa ainda ocupada com outra tarefa (fila)
            try:
                caixa["r"] = fn(*args)
            except Exception as e:  # pragma: no cover
                caixa["e"] = e
            ev.set()
        threading.Thread(target=roda, daemon=True).start()
        return ev, caixa

    def em_paralelo(self, fn, *args, timeout=180):
        ev, caixa = self.enfileirar(fn, *args)

        def esperar():
            assert ev.wait(timeout)
            if "e" in caixa:
                raise caixa["e"]
            return caixa.get("r")
        return esperar

    def executar(self, fn, *args, timeout=180):
        return self.em_paralelo(fn, *args, timeout=timeout)()

    # --- PREPARAR
    def preparar(self, perna, times, cancel=None, prazo_s=None, marcas=None):
        self.chamadas.append("preparar")
        t0 = time.time()
        if marcas is not None:
            marcas[self.nome] = t0
        prazo = t0 + prazo_s if prazo_s else None
        r = {"ok": False, "motivo": "", "foto": "", "t_ini": t0}
        fim = t0 + self.r.get("prep_s", 0.05)
        while time.time() < fim:
            if cancel is not None and cancel.is_set():
                r.update(motivo="cancelada (a outra casa falhou ou o par mudou)", cancelada=True)
                break
            if prazo is not None and time.time() > prazo:
                r.update(motivo="passou do prazo de preparação", cancelada=True)
                break
            time.sleep(0.01)
        else:
            if self.r.get("prep_falha"):
                r["motivo"] = self.r["prep_falha"]
            else:
                r["ok"] = True
        r["t_fim"] = time.time()
        r["segundos"] = round(r["t_fim"] - t0, 2)
        return r

    def limpar_cupom(self, perna=None):
        self.chamadas.append("limpar")
        return True

    def desfazer(self, perna=None):
        self.chamadas.append("desfazer")
        return {"ok": True, "foto": "", "motivo": ""}

    def foto(self, tag):
        return ""

    def preencher_stake(self, valor, rapido=False):
        return True, ""

    # --- BARREIRA
    def ler_cupom(self, perna):
        self.leituras += 1
        odds = self.r.get("odds_lidas") or [perna["odd"]]
        odd = odds[min(self.leituras - 1, len(odds) - 1)]
        st = round(perna["stake"] / self.cfg["cambio_brl_eur"], 2) if self.nome == P.CASA_PS else round(perna["stake"], 2)
        falha = self.r.get("barreira_falha")
        if falha and self.leituras == 1:
            return {"ok": False, "motivo": falha, "odd": None, "stake": st, "t": time.time()}
        return {"ok": True, "motivo": "", "odd": odd, "stake": self.r.get("stake_lida", st), "t": time.time()}

    def ler_e_armar(self, perna, armar=True):
        r = self.ler_cupom(perna)
        if r["ok"] and armar:
            r["armado"], r["motivo_armar"] = True, ""
        return r

    # --- DISPARAR / RECONCILIAR
    def disparar(self, perna, odd_ref=None, sincronia=None):
        self.chamadas.append("disparar")
        if sincronia is not None:
            sincronia.wait(2)
        self.t_disparo = time.time()
        return {"clicou": True, "talvez": False, "t": self.t_disparo, "motivo": ""}

    def disparar_e_sentir(self, perna, odd_ref, limite_s):
        d = self.disparar(perna, odd_ref)
        d["evidencia"] = self.r.get("evidencia", "processando")
        return d

    def apurar(self, perna=None):
        self.chamadas.append("apurar")
        ok = self.r.get("resultado", True)
        comp = self.r.get("recusa_comprovada", True)
        return {"ok": ok, "foto": "", "comprovada": ok is False and comp,
                "motivo": "" if ok else ("INCERTO — sem prova" if ok is None else
                                         "a casa recusou: rejected" if comp else "a casa limpou o cupom sem aceitar")}

    def ler_escada(self, times):
        self.chamadas.append("ler_escada")
        return {"ok": True, "ofertas": [dict(o) for o in self.r.get("escada", [])]}

    def montar(self, perna, times):
        self.chamadas.append("montar:%s" % perna["linha"])
        return {"ok": True, "motivo": "", "foto": ""}

    def recolocar(self, perna, times):
        self.chamadas.append("recolocar")
        return {"ok": True, "motivo": ""}

    def confirmar(self, perna=None):
        self.chamadas.append("confirmar:%s" % perna["linha"])
        ok = self.r.get("recuperacao", False)
        return {"ok": ok, "foto": "", "comprovada": ok is False, "motivo": "" if ok else "a casa recusou de novo: rejected"}


def _bucks(cfg, o1=1.85, o2=1.99, l1=231.5, l2=233.5):
    """O par do log 19/11 23:08:46: Over 231,5 @1,85 (1aWin) + Under 233,5 @1,99 (PS), janela 2,0, custo 4,13%."""
    a = {"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": l1, "odd": o1, "casa": P.CASA_1W, "_mid": "1"}
    b = {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": l2, "odd": o2, "casa": P.CASA_PS, "_mid": "2"}
    c = P._calcular(a, b, cfg)
    return P._montar_bilhete(dict(SINAL, jogo="Milwaukee Bucks x Portland Trail Blazers"), a, b, l2 - l1, c)


def _op22(cfg, r1=None, r2=None, modo_teste=False):
    cfg = dict(cfg, modo_teste=modo_teste, odd_min=1.78, tempo_max_preparacao_seg=2.0)
    casas = {P.CASA_1W: CasaFalsa(P.CASA_1W, cfg, **(r1 or {})), P.CASA_PS: CasaFalsa(P.CASA_PS, cfg, **(r2 or {}))}
    est = P.Estado(cfg)
    return P.Operador(cfg, est, casas), casas, est, cfg


def _rodar(op, est, bil, **kw):
    est.registrar_bilhete(bil, "sinal")
    return op.executar_bilhete(bil, **kw)


def test_r22_bucks_par_do_log_custa_4_13(cfg):
    bil = _bucks(cfg)
    assert abs(bil["custo"] - 4.13) < 0.01 and bil["janela"] == 2.0


def test_r22_preparacao_realmente_concorrente(cfg):
    """As duas casas começam JUNTAS: uma não espera a outra terminar."""
    op, casas, est, cfg2 = _op22(cfg, {"prep_s": 0.4}, {"prep_s": 0.4})
    bil = _bucks(cfg2)
    t0 = time.time()
    res = op._preparar_duas(bil)
    assert all(r["ok"] for r in res.values())
    assert time.time() - t0 < 0.7                       # sequencial levaria 0,8 s
    assert res[P.CASA_PS]["t_ini"] < res[P.CASA_1W]["t_fim"] and res[P.CASA_1W]["t_ini"] < res[P.CASA_PS]["t_fim"]
    assert any("PREPARAR: 1aWin pronta" in l and "PS pronta" in l for l in P._LOG_MEM[-5:])


def test_r22_uma_casa_falhando_cancela_a_outra(cfg):
    op, casas, est, cfg2 = _op22(cfg, {"prep_s": 0.05, "prep_falha": "a linha 231,5 não está mais na página"}, {"prep_s": 1.5})
    bil = _bucks(cfg2)
    t0 = time.time()
    assert _rodar(op, est, bil) == "falhou"
    assert time.time() - t0 < 0.8                       # a PS NÃO seguiu montando o candidato velho
    assert "cancelada" in bil["detalhe"]["PS3838"]
    for c in casas.values():
        assert "disparar" not in c.chamadas


def test_r22_prazo_de_preparacao_estourado_cancela_e_limpa(cfg):
    op, casas, est, cfg2 = _op22(cfg, {"prep_s": 0.3}, {"prep_s": 5.0})
    op.cfg["tempo_max_preparacao_seg"] = 0.5
    bil = _bucks(cfg2)
    t0 = time.time()
    assert _rodar(op, est, bil) == "falhou"
    assert time.time() - t0 < 1.5
    assert "prazo" in bil["detalhe"]["PS3838"]
    assert "desfazer" in casas[P.CASA_1W].chamadas      # a 1aWin, que ficou pronta, foi limpa
    assert all("disparar" not in c.chamadas for c in casas.values())


def test_r22_custo_vivo_acima_do_limite_aborta_antes_de_clicar(cfg):
    """19/11 20:19: 'par recalculado ... custo 6,56% (antes 1,87%)' e mesmo assim foi confirmar. Agora: aborta na barreira."""
    op, casas, est, cfg2 = _op22(cfg, {}, {"odds_lidas": [1.80, 1.80]})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert all("disparar" not in c.chamadas for c in casas.values())
    assert any(l.startswith("ABORTADO NA BARREIRA: custo vivo") and "> limite 4,80%" in l
               for l in (x.split("] ", 1)[-1] for x in P._LOG_MEM[-30:])), P._LOG_MEM[-10:]
    assert all("desfazer" in c.chamadas for c in casas.values())


def test_r22_odd_viva_abaixo_de_1_78_aborta(cfg):
    op, casas, est, cfg2 = _op22(cfg, {"odds_lidas": [1.77, 1.77]}, {"odds_lidas": [2.30, 2.30]})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert "abaixo do mínimo 1,78" in bil["detalhe"]["1aWin"]
    assert all("disparar" not in c.chamadas for c in casas.values())


def test_r22_stake_alterada_aborta(cfg):
    op, casas, est, cfg2 = _op22(cfg, {"stake_lida": 10.0}, {})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert "stake alterada" in bil["detalhe"]["1aWin"]


def test_r22_linha_muda_na_barreira_reinicia_uma_vez_e_aborta_se_mudar_de_novo(cfg):
    op, casas, est, cfg2 = _op22(cfg, {}, {"odds_lidas": [1.98, 1.97]})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert casas[P.CASA_PS].leituras == 2                # reiniciou UMA vez
    assert "mudaram de novo durante a barreira" in bil["detalhe"]["PS3838"]
    assert all("disparar" not in c.chamadas for c in casas.values())


def test_r22_linha_muda_uma_vez_e_estabiliza_segue_com_odd_viva(cfg):
    op, casas, est, cfg2 = _op22(cfg, {}, {"odds_lidas": [1.98, 1.98]})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "confirmado"
    assert bil["pernas"][1]["odd_viva"] == 1.98 and bil["custo_vivo"] > bil["custo"]


def test_r22_selecao_some_na_barreira_troca_e_refaz_a_barreira(cfg):
    """A 1aWin tira a linha DEPOIS de pronta: troca só essa perna (substituir decide, sem tipo preferido) e a barreira
    é refeita do zero com as linhas novas (não usa a validação do par anterior)."""
    op, casas, est, cfg2 = _op22(cfg, {"barreira_falha": "cupom com 0 seleções (esperado 1)"}, {})
    bil = _bucks(cfg2)
    novo = _bucks(cfg2, o1=1.87, l1=231.0)
    st = _rodar(op, est, bil, substituir=lambda nome_f, b0: novo if nome_f == P.CASA_1W else None)
    assert st == "confirmado", bil.get("detalhe")
    assert bil["pernas"][0]["linha"] == 231.0
    assert casas[P.CASA_1W].leituras >= 2 and casas[P.CASA_PS].leituras >= 2   # barreira inteira de novo
    assert casas[P.CASA_1W].chamadas.count("preparar") == 2


def test_r22_ambas_confirmadas(cfg):
    op, casas, est, cfg2 = _op22(cfg)
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "confirmado"
    assert bil["estado_execucao"] == "AMBAS_CONFIRMADAS"
    assert bil["disparos"]["1aWin"] and bil["disparos"]["PS3838"] and bil["disparos"]["diferenca_ms"] is not None
    assert any(l.split("] ", 1)[-1].startswith("DISPAROS: 1aWin") and "diferença" in l for l in P._LOG_MEM[-30:])


def test_r22_ambas_recusadas_limpa_e_volta(cfg):
    op, casas, est, cfg2 = _op22(cfg, {"resultado": False}, {"resultado": False})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert bil["estado_execucao"] == "AMBAS_RECUSADAS"
    assert all("desfazer" in c.chamadas for c in casas.values())


def _of(lado, linha, odd, casa):
    return {"tipo": "total", "periodo": "jogo", "lado": lado, "linha": linha, "odd": odd, "casa": casa, "_mid": "x"}


def test_r22_so_a_1awin_confirmada_vira_perna_orfa_e_nunca_repete_a_aceita(cfg):
    esc = [_of("abaixo", 233.5, 1.99, P.CASA_PS), _of("abaixo", 234.5, 2.05, P.CASA_PS)]
    op, casas, est, cfg2 = _op22(cfg, {"resultado": True}, {"resultado": False, "recuperacao": False, "escada": esc})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "exposto"
    assert bil["_orfa"] == {"aceita": P.CASA_1W, "falta": P.CASA_PS}
    c1, cps = casas[P.CASA_1W].chamadas, casas[P.CASA_PS].chamadas
    assert c1.count("disparar") == 1 and not any(x.startswith("confirmar") for x in c1)   # a aceita NUNCA repete
    assert [x for x in cps if x.startswith("confirmar")] == ["confirmar:233.5", "confirmar:234.5"]   # máx. 2, recusas comprovadas
    assert bil["detalhe"]["1aWin"] == "confirmada"


def test_r22_so_a_ps_confirmada_vira_perna_orfa(cfg):
    esc = [_of("acima", 231.5, 1.85, P.CASA_1W)]
    op, casas, est, cfg2 = _op22(cfg, {"resultado": False, "recuperacao": False, "escada": esc}, {"resultado": True})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "exposto"
    assert bil["_orfa"] == {"aceita": P.CASA_PS, "falta": P.CASA_1W}
    assert casas[P.CASA_PS].chamadas.count("disparar") == 1
    assert not any(x.startswith("confirmar") for x in casas[P.CASA_PS].chamadas)
    assert bil["detalhe"]["PS3838"] == "confirmada"


def test_r22_perna_orfa_recuperada_dentro_das_regras(cfg):
    esc = [_of("abaixo", 233.5, 1.99, P.CASA_PS)]
    op, casas, est, cfg2 = _op22(cfg, {"resultado": True}, {"resultado": False, "recuperacao": True, "escada": esc})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "confirmado"


def test_r22_orfa_so_repete_com_recusa_comprovada(cfg):
    """Processando / cupom sumiu / sem ID: pode ter entrado. Sem recusa COMPROVADA -> INCERTO, congela e NÃO repete."""
    esc = [_of("abaixo", 233.5, 1.99, P.CASA_PS)]
    op, casas, est, cfg2 = _op22(cfg, {"resultado": True},
                                 {"resultado": False, "recusa_comprovada": False, "recuperacao": True, "escada": esc})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "incerto"
    cps = casas[P.CASA_PS].chamadas
    assert cps.count("disparar") == 1 and not any(x.startswith(("confirmar", "recolocar", "montar")) for x in cps)
    assert "sem recusa comprovada" in bil["detalhe"]["PS3838"]


def test_r22_orfa_protege_com_outra_linha_usando_o_que_foi_aceito(cfg):
    """A mesma linha ficou cara (1,80): protege com OUTRA linha que passa nas regras, calculada com a linha, odd e
    stake realmente aceitas na 1aWin."""
    esc = [_of("abaixo", 233.5, 1.80, P.CASA_PS), _of("abaixo", 234.0, 2.02, P.CASA_PS)]
    op, casas, est, cfg2 = _op22(cfg, {"resultado": True},
                                 {"resultado": False, "recuperacao": True, "escada": esc, "odds_lidas": [1.99, 2.02]})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "confirmado", bil.get("detalhe")
    assert bil["pernas"][1]["linha"] == 234.0 and bil["janela"] == 2.5
    assert "montar:234.0" in casas[P.CASA_PS].chamadas and "confirmar:233.5" not in casas[P.CASA_PS].chamadas
    assert any("OUTRA linha" in a for a in bil.get("avisos", []))


def test_r22_orfa_respeita_perda_maxima_de_100(cfg):
    op, casas, est, cfg2 = _op22(cfg, {}, {"escada": [_of("abaixo", 233.5, 1.99, P.CASA_PS)]})
    bil = _bucks(cfg2)
    acc = dict(bil["pernas"][0])
    # o par (aceita + proteção) perde até R$ 2,66: com teto de perda de R$ 2 a proteção é RECUSADA; com R$ 100, aceita
    op.cfg["perda_max_por_jogo_brl"] = 2.0
    nova, mot = op._candidato_orfa(bil, P.CASA_PS, acc, [], set())
    assert nova is None and "pior caso" in mot
    op.cfg["perda_max_por_jogo_brl"] = 100.0
    nova, mot = op._candidato_orfa(bil, P.CASA_PS, acc, [], set())
    assert nova and nova["linha"] == 233.5


def test_r22_perna_orfa_sem_recuperacao_segura_nao_aposta(cfg):
    """A perna que faltou só volta se o par ainda passar nas regras (aqui a odd viva da PS caiu: custo estoura)."""
    op, casas, est, cfg2 = _op22(cfg, {"resultado": True}, {"resultado": False, "recuperacao": True,
                                                            "odds_lidas": [1.99, 1.80],
                                                            "escada": [_of("abaixo", 233.5, 1.99, P.CASA_PS)]})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "exposto"
    assert not any(x.startswith("confirmar") for x in casas[P.CASA_PS].chamadas)
    assert "custo vivo" in bil["detalhe"]["PS3838"]


def test_r22_cronometro_conta_so_a_preparacao(cfg):
    """A casa demorou 0,8 s para COMEÇAR (fila/caça/carregamento): não conta. Prazo 0,5 s, preparação 0,3 s -> pronta."""
    op, casas, est, cfg2 = _op22(cfg, {"prep_s": 0.3, "fila_s": 0.8}, {"prep_s": 0.3})
    op.cfg["tempo_max_preparacao_seg"] = 0.5
    bil = _bucks(cfg2)
    res = op._preparar_duas(bil)
    assert all(r["ok"] for r in res.values()), res
    assert P.DEFAULTS["tempo_max_preparacao_seg"] == 3.0


def test_r22_modos_de_confirmacao_tem_nome_que_mostra_o_risco(cfg):
    assert P.DEFAULTS["confirmacao"] == "sequencial_processando"
    # sequencial_aceita: 'processando' NÃO basta — a PS espera a 1aWin ACEITAR
    op, casas, est, cfg2 = _op22(cfg, {"evidencia": "processando", "resultado": False}, {})
    op.cfg["confirmacao"] = "sequencial_aceita"
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert "disparar" not in casas[P.CASA_PS].chamadas
    # sequencial_processando: 'processando' basta (risco: PS órfã se a 1aWin não aceitar) -> aqui vira INCERTO? não:
    op, casas, est, cfg2 = _op22(cfg, {"evidencia": "processando", "resultado": False, "recusa_comprovada": False}, {})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "incerto"        # PS aceita, 1aWin sumiu sem prova: congela, não repete
    assert "disparar" in casas[P.CASA_PS].chamadas


@pytest.mark.parametrize("modo", ["sequencial_processando", "sequencial_aceita", "rapida", "conservadora"])
@pytest.mark.parametrize("tabela", ["padrao", "operador_estreita", "sem_tabela"])
@pytest.mark.parametrize("quem_cai", [P.CASA_1W, P.CASA_PS])
@pytest.mark.parametrize("com_carteira", [False, True])
def test_r22_custo_vivo_acima_do_limite_nunca_dispara(cfg, modo, tabela, quem_cai, com_carteira):
    """Item 6 (operador 22/11): em TODA versão de modo/tabela/casa/carteira, custo vivo acima do limite = nenhum clique."""
    r1 = {"odds_lidas": [1.70, 1.70]} if quem_cai == P.CASA_1W else {}
    r2 = {"odds_lidas": [1.82, 1.82]} if quem_cai == P.CASA_PS else {}
    op, casas, est, cfg2 = _op22(cfg, r1, r2)
    op.cfg["confirmacao"] = modo
    op.cfg["odd_min"] = 1.60
    if tabela == "operador_estreita":
        op.cfg["custo_por_janela"] = [[1, 11, 4.5]]
    elif tabela == "sem_tabela":
        op.cfg["custo_por_janela"], op.cfg["custo_max"] = None, 4.5
    bil = _bucks(cfg2)
    cart = None
    if com_carteira:
        cart = [{"casa": P.CASA_1W, "tipo": "total", "periodo": "jogo", "lado": "acima", "linha": 229.5, "odd": 1.9,
                 "stake": 32.0, "par": 1},
                {"casa": P.CASA_PS, "tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": 231.5, "odd": 1.95,
                 "stake": 31.0, "par": 1}]
    assert _rodar(op, est, bil, carteira=cart) == "falhou"
    assert all("disparar" not in c.chamadas for c in casas.values())
    assert any("ABORTADO NA BARREIRA: custo vivo" in l for l in P._LOG_MEM[-40:]), P._LOG_MEM[-8:]


def test_r22_varredura_de_odds_vivas_nunca_dispara_acima_do_limite(cfg):
    import random
    rnd = random.Random(2211)
    for _ in range(60):
        o1, o2 = round(rnd.uniform(1.78, 2.0), 3), round(rnd.uniform(1.78, 2.1), 3)
        op, casas, est, cfg2 = _op22(cfg, {"odds_lidas": [o1, o1]}, {"odds_lidas": [o2, o2]})
        op.cfg["confirmacao"] = rnd.choice(["sequencial_processando", "sequencial_aceita", "rapida"])
        bil = _bucks(cfg2)
        ok, mot, custo, _ = P.validar_par_vivo(bil, o1, o2, op.cfg)
        st = _rodar(op, est, bil)
        disparou = any("disparar" in c.chamadas for c in casas.values())
        if custo is not None and custo > P.custo_max_para(bil["janela"], op.cfg) + 1e-9:
            assert st == "falhou" and not disparou, (o1, o2, custo)
        else:
            assert disparou == ok, (o1, o2, mot)


def test_r22_resultado_incerto_congela(cfg):
    op, casas, est, cfg2 = _op22(cfg, {"resultado": None}, {"resultado": True})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "incerto"
    assert bil["status"] == "INCERTO" and bil["estado_execucao"] == "INCERTO"


def test_r22_conservadora_sem_prova_imediata_a_ps_espera_o_definitivo(cfg):
    """Sem prova imediata da 1aWin: a PS só dispara se a 1aWin for ACEITA; recusada -> a PS nem dispara."""
    op, casas, est, cfg2 = _op22(cfg, {"evidencia": None, "resultado": False}, {})
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "falhou"
    assert "disparar" not in casas[P.CASA_PS].chamadas
    assert "NÃO confirmada" in bil["detalhe"]["PS3838"]


def test_r22_modo_rapido_dispara_junto_e_registra_a_diferenca(cfg):
    op, casas, est, cfg2 = _op22(cfg)
    op.cfg["confirmacao"] = "rapida"
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "confirmado"
    assert bil["disparos"]["modo"] == "rapida" and bil["disparos"]["diferenca_ms"] < 300


def test_r22_modo_teste_nao_dispara(cfg):
    op, casas, est, cfg2 = _op22(cfg, modo_teste=True)
    bil = _bucks(cfg2)
    assert _rodar(op, est, bil) == "testado"
    assert all("disparar" not in c.chamadas for c in casas.values())


def test_r22_assinatura_e_cooldown_sem_repeticao_infinita(cfg):
    t = P.ControleTentativas(dict(cfg, cooldown_par_s=60, max_tentativas_por_minuto=3))
    a = {"tipo": "total", "periodo": "jogo", "lado": "acima", "linha": 231.5}
    b = {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": 233.5}
    ass = P.assinatura_par(TIMES, a, b)
    assert ass == P.assinatura_par(TIMES, dict(a, odd=1.9, casa="x"), dict(b, odd=2.0))   # odd/casa não entram
    assert ass != P.assinatura_par(TIMES, a, dict(b, linha=234.5))                          # outra oportunidade
    t.falhou(ass, agora=1000)
    assert t.cooldown(ass, agora=1010) == 50 and t.cooldown(ass, agora=1061) == 0
    assert t.cooldown(P.assinatura_par(TIMES, a, dict(b, linha=234.5)), agora=1010) == 0
    for k in range(3):
        t.registrar("jogo", agora=1000 + k)
    assert t.excedeu("jogo", agora=1010) and not t.excedeu("jogo", agora=1065)


def test_r22_reconciliacao_classifica(cfg):
    assert P.classificar_reconciliacao(True, True) == "AMBAS_CONFIRMADAS"
    assert P.classificar_reconciliacao(False, False) == "AMBAS_RECUSADAS"
    assert P.classificar_reconciliacao(True, False) == "UMA_CONFIRMADA"
    assert P.classificar_reconciliacao(False, True) == "UMA_CONFIRMADA"
    assert P.classificar_reconciliacao(None, True) == "INCERTO"
    assert P.classificar_reconciliacao(False, None) == "INCERTO"


def test_r22_nenhuma_regra_financeira_afrouxada(cfg):
    d = P.DEFAULTS
    assert d["odd_min"] == 1.78 and d["custo_max"] == 5.0 and d["perda_max_por_jogo_brl"] == 100.0
    assert d["custo_por_janela"] == [[1, 2, 3.0], [2, 3, 4.8], [3, 7, 4.99], [7, 11, 8.0]]
    c = dict(cfg, odd_min=1.78)
    bil = _bucks(c)
    assert P.validar_par_vivo(bil, 1.85, 1.99, c)[0]                        # o par do log passa
    assert not P.validar_par_vivo(bil, 1.77, 1.99, c)[0]                    # odd abaixo de 1,78
    ok, mot, custo, _ = P.validar_par_vivo(bil, 1.85, 1.80, c)              # custo vivo acima da tabela (janela 2: 4,8%)
    assert not ok and "custo vivo" in mot and "limite 4,80%" in mot
    b1 = _bucks(c, l2=232.5)                                                # janela 1: limite 3%
    ok, mot, _, _ = P.validar_par_vivo(b1, 1.85, 1.99, c)
    assert not ok and "limite 3,00%" in mot
    # com carteira: perda máxima e regras de reentrada valem com as odds vivas
    cart = [dict(p, par=1) for p in P.pernas_aceitas(dict(_bucks(c), id=1))]
    ok, mot, _, _ = P.validar_par_vivo(bil, 1.85, 1.99, c, cart)
    assert not ok and "idêntico" in mot
    ok, mot, _, _ = P.validar_par_vivo(_bucks(c, l1=232.5, l2=234.5), 1.85, 1.99, dict(c, perda_max_por_jogo_brl=1.0), cart)
    assert not ok and "pior caso" in mot


def test_r22_ancora_e_escadinha_continuam_sem_prioridade_fixa():
    """A troca durante a preparação usa o mesmo achar_par/nota_reentrada: a nota não tem o TIPO como critério."""
    av_anc = {"novos": [235], "janela_nova": (232, 235), "pior_depois": -10.0, "tipo": "âncora"}
    av_esc = {"novos": [235, 236], "janela_nova": (232, 236), "pior_depois": -10.0, "tipo": "escadinha"}
    assert P.nota_reentrada(av_esc, 4.0, 1.85) > P.nota_reentrada(av_anc, 4.0, 1.85)
    av_anc2 = dict(av_anc, novos=[235, 236], janela_nova=(232, 236))
    assert P.nota_reentrada(av_anc2, 3.0, 1.85) > P.nota_reentrada(av_esc, 4.0, 1.85)   # âncora vence por custo
    assert P.nota_reentrada(dict(av_esc, tipo="âncora"), 4.0, 1.85) == P.nota_reentrada(av_esc, 4.0, 1.85)
