"""Testes de navegador (Playwright, headless) contra as páginas falsas fiéis das duas casas."""
import time

import pytest

import programa as P
import servidor
from conftest import SINAL, TIMES

ATENAS = ["Atenas Cordoba", "San Lorenzo de Almagro"]


def ir_jogo(casas, times=TIMES, liga=""):
    cps, c1 = casas[P.CASA_PS], casas[P.CASA_1W]
    assert cps.executar(cps.cacar, times)["ok"]
    assert c1.executar(c1.cacar, times, liga)["ok"]
    return c1, cps


def ler(casas, times=TIMES):
    c1, cps = casas[P.CASA_1W], casas[P.CASA_PS]
    return c1.executar(c1.ler_escada, times), cps.executar(cps.ler_escada, times)


def recarregar(c):
    c.executar(lambda: c.page.reload(wait_until="domcontentloaded"))


@pytest.fixture
def cfgc(casas):
    """cfg compartilhado pelas casas (restaura depois)."""
    cfg = casas[P.CASA_1W].cfg
    salvo = dict(cfg)
    yield cfg
    cfg.clear()
    cfg.update(salvo)


def limpar_cupons(casas):
    for c in casas.values():
        c.executar(c.limpar_cupom)


# ------------------------------------------------------------------ caças
def test_caca_ps_pela_lista_e_lista_nao_e_achado(casas):
    cps = casas[P.CASA_PS]
    cps.executar(cps.ir, cps.url_lista())
    tx = cps.executar(cps.texto)
    assert "Phoenix Suns" in tx and P.ps_e_o_jogo(tx, TIMES) is None  # lista contém os times, mas não é achado
    r = cps.executar(cps.cacar, TIMES)
    assert r["ok"] and "/matchup/" in r["url"]
    assert P.url_memorizada(P.CASA_PS, TIMES) == r["url"]


def test_caca_ps_ja_na_pagina_nao_navega(casas):
    cps = casas[P.CASA_PS]
    cps.executar(cps.cacar, TIMES)
    url = cps.executar(lambda: cps.page.url)
    t0 = time.time()
    assert cps.executar(cps.cacar, TIMES)["ok"]
    assert time.time() - t0 < 3 and cps.executar(lambda: cps.page.url) == url


def test_caca_ps_liga_certa_vem_da_url(casas):
    servidor.ESTADO["liga_errada"] = True
    cps = casas[P.CASA_PS]
    cps.executar(cps.ir, cps.url_lista())
    r = cps.executar(cps.cacar, TIMES)
    assert r["ok"] and r["liga"] == "NBA"


def test_caca_1awin_por_liga_com_nomes_tolerantes(casas):
    c1 = casas[P.CASA_1W]
    c1.executar(c1.ir, c1.url_lista())
    r = c1.executar(c1.cacar, ATENAS, "Argentina - Liga Nacional")
    assert r["ok"] and "/betting/match/" in r["url"]
    mem = P._mem_ligas()
    assert any(v["confirmada"] for v in mem.values())
    # segunda vez: endereço decorado
    c1.executar(c1.ir, c1.url_lista())
    t0 = time.time()
    assert c1.executar(c1.cacar, ATENAS, "")["ok"] and time.time() - t0 < 8


def test_caca_1awin_feed_vazio(casas):
    servidor.ESTADO["feed_vazio"] = True
    c1 = casas[P.CASA_1W]
    c1.executar(c1.ir, c1.url_lista())
    r = c1.executar(c1.cacar, ["Time Inexistente", "Outro Time"], "")
    assert not r["ok"] and "feed vazio" in r["motivo"]


# ------------------------------------------------------------------ leitura
def test_leitura_1awin(casas):
    ir_jogo(casas)
    l1, _ = ler(casas)
    assert l1["ok"] and l1["quarto"] == 2
    pers = {o["periodo"] for o in l1["ofertas"]}
    assert "q1" not in pers and "q4" not in pers and "q2" in pers  # q1 encerrado, q4 proibido
    linhas_jogo = {o["linha"] for o in l1["ofertas"] if o["tipo"] == "total" and o["periodo"] == "jogo"}
    assert linhas_jogo == {204.5, 209.5, 214.5, 219.5}  # total de equipe (105.5) e Dicas de IA (211.5) não nascem
    assert all(o["_mid"] for o in l1["ofertas"])


def test_leitura_1awin_primeiro_quarto_vivo(casas):
    servidor.ESTADO["q"] = 1
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    l1, _ = ler(casas)
    assert "q1" in {o["periodo"] for o in l1["ofertas"]}


def test_leitura_ps_expande_e_le_colunas(casas):
    ir_jogo(casas)
    _, lp = ler(casas)
    assert lp["ok"]
    tot = {(o["lado"], o["linha"]): o["odd"] for o in lp["ofertas"] if o["tipo"] == "total" and o["periodo"] == "jogo"}
    assert tot[("acima", 211.5)] == 1.80 and tot[("abaixo", 212.5)] == 1.92
    hcp = {(o["time_idx"], o["linha"]): o["odd"] for o in lp["ofertas"] if o["tipo"] == "handicap"}
    assert hcp[(0, -4.5)] == 2.00 and hcp[(1, 4.5)] == 1.85
    assert "q4" not in {o["periodo"] for o in lp["ofertas"]}


def test_ps_deslogada(casas):
    servidor.ESTADO["deslogado"] = True
    _, cps = ir_jogo(casas)
    recarregar(cps)
    _, lp = ler(casas)
    assert not lp["ok"] and "deslogada" in lp["motivo"]


def test_ps_limitada(casas):
    cps = casas[P.CASA_PS]
    ir_jogo(casas)
    servidor.ESTADO["limite"] = True
    recarregar(cps)
    lp = cps.executar(cps.ler_escada, TIMES)
    assert lp["limitada"]
    servidor.ESTADO.clear()
    assert cps.executar(cps.recarregar) is False and not cps.limitada


# ------------------------------------------------------------------ montagem / desfazer
def achar(casas, cfg):
    l1, lp = ler(casas)
    bil, mot = P.achar_par(l1["ofertas"], lp["ofertas"], SINAL, cfg)
    assert bil, mot
    return bil


def test_rodada_teste_completa(casas, cfgc):
    ir_jogo(casas)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    assert bil["janela"] == 3.0 and bil["pernas"][0]["linha"] == 214.5 and bil["pernas"][1]["linha"] == 211.5
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    est.registrar_bilhete(bil, "sinal")
    assert op.executar_bilhete(bil) == "testado", bil["detalhe"]
    assert "cupom vazio" in bil["detalhe"]["1aWin"] and "cupom vazio" in bil["detalhe"]["PS3838"]
    for c in casas.values():
        assert c.executar(c.itens_cupom) == []
    assert len([f for f in bil["fotos"] if f]) == 4


def test_stake_ps_com_ponto_e_lida_de_volta(casas):
    _, cps = ir_jogo(casas)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    perna = dict(o, stake=31.0)
    r = cps.executar(cps.montar, perna, TIMES)
    assert r["ok"], r
    v = cps.executar(lambda: cps.page.locator(".BetItemComponent input[name=risk]").input_value())
    assert v == "5.00"
    # a vírgula que custou caro: "5,00" vira 500 na casa
    def virgula():
        inp = cps.page.locator(".BetItemComponent input[name=risk]")
        inp.fill("")
        inp.type("5,00")
        inp.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles: true})); e.blur(); }")
        return inp.input_value()
    assert cps.executar(virgula) == "500.00"
    assert cps.executar(cps.desfazer, perna)["ok"]


def test_quarentena_limpa_cupom_sujo(casas):
    servidor.ESTADO["sujo"] = True
    c1, cps = ir_jogo(casas)
    recarregar(c1)
    recarregar(cps)
    assert c1.executar(c1.itens_cupom) and cps.executar(cps.itens_cupom)
    l1, lp = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    o2 = [x for x in lp["ofertas"] if x["linha"] == 211.5 and x["lado"] == "acima"][0]
    r1 = c1.executar(c1.montar, dict(o1, stake=26.57), TIMES)
    r2 = cps.executar(cps.montar, dict(o2, stake=31.0), TIMES)
    assert r1["ok"] and r2["ok"]
    assert len(c1.executar(c1.itens_cupom)) == 1 and len(cps.executar(cps.itens_cupom)) == 1
    limpar_cupons(casas)


def test_evento_excluido_reprova_perna(casas):
    c1, _ = ir_jogo(casas)
    limpar_cupons(casas)
    itens = c1.executar(c1.enumerar)
    alvo = [i for i in itens if "54.5" in i["seg"] and "Abaixo" in i["seg"]][0]
    perna = {"tipo": "total", "periodo": "q1", "lado": "abaixo", "linha": 54.5, "odd": 2.20,
             "_mid": alvo["mid"], "_src": alvo["seg"], "stake": 20.0, "aba": ""}
    r = c1.executar(c1.montar, perna, TIMES)
    assert not r["ok"] and "Evento excluído" in r["motivo"]
    assert c1.executar(c1.itens_cupom) == []  # saiu pelo "Cancelar"


def test_cupom_preso_evento_excluido_sai_pelo_cancelar(casas):
    servidor.ESTADO["cupom_preso"] = True
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    assert c1.executar(c1.itens_cupom)
    assert c1.executar(c1.limpar_cupom) is True


def test_carimbo_morto_relê_por_identidade(casas):
    c1, _ = ir_jogo(casas)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    recarregar(c1)  # página redesenhou: carimbos mortos
    r = c1.executar(c1.montar, dict(o1, stake=26.57), TIMES)
    assert r["ok"], r
    c1.executar(c1.desfazer, o1)


def test_leitura_nao_cai_para_busca_antiga(casas):
    c1, _ = ir_jogo(casas)
    limpar_cupons(casas)
    perna = {"tipo": "total", "periodo": "jogo", "lado": "abaixo", "linha": 299.5, "odd": 1.9,
             "_mid": "9999", "_src": "", "stake": 10.0, "aba": ""}
    r = c1.executar(c1.montar, perna, TIMES)
    assert not r["ok"] and ("a leitura não achou a seleção na página" in r["motivo"]
                            or "não está mais na página" in r["motivo"])
    assert c1.executar(c1.itens_cupom) == []


def test_ps_aborta_montagem_em_lista(casas):
    cps = casas[P.CASA_PS]
    ir_jogo(casas)
    _, lp = ler(casas)
    o = lp["ofertas"][0]
    cps.executar(cps.ir, cps.url_lista())
    r = cps.executar(cps.montar, dict(o, stake=31.0), TIMES)
    assert not r["ok"] and "lista" in r["motivo"]


def test_handicap_religado(casas, cfgc):
    cfgc["so_totais"] = False
    ir_jogo(casas)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    assert bil["tipo"] == "handicap" and bil["janela"] == 5.0
    assert bil["pernas"][0]["linha"] == 9.5 and bil["pernas"][1]["linha"] == -4.5
    op = P.Operador(cfgc, P.Estado(cfgc), casas)
    assert op.executar_bilhete(bil) == "testado", bil.get("detalhe")


def test_modo_real_confirma_fragil_primeiro(casas, cfgc):
    cfgc["modo_teste"] = False
    ir_jogo(casas)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "confirmado"
    # recarrega para não deixar a "aposta" na tela das próximas rodadas
    for c in casas.values():
        recarregar(c)


# ------------------------------------------------------------------ monitoramento ponta a ponta
def esperar(cond, s=60):
    fim = time.time() + s
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.3)
    return False


def test_sinal_fresco_ponta_a_ponta(casas, cfgc):
    limpar_cupons(casas)
    for c in casas.values():
        c.executar(c.ir, c.url_lista())
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    v = P.Vigia(cfgc, op, est)
    v.min_estavel_s = 0
    conversa = "🛰️ SINAL DE MIDDLE (BetBurger)\nJogo: Flamengo x Franca\nMercado do sinal: Total\n"
    for _ in range(3):
        v.processar(conversa)  # backlog
    assert op._atual is None
    conversa += "\n🛰️ SINAL DE MIDDLE (BetBurger)\nJogo: Phoenix x Charlotte\nLiga: EUA - NBA\nMercado do sinal: Total 214.5\n"
    v.processar(conversa)
    assert esperar(lambda: est.bilhetes and est.bilhetes[-1]["status"] == "testado", 90), (est.bilhetes, P._LOG_MEM[-15:])
    assert est.calculadora and est.calculadora["valido"]
    assert "encerrado" in est.monitor


def test_monitor_estaciona_com_casa_limitada(casas, cfgc, monkeypatch):
    monkeypatch.setattr(P, "SONDA_LIMITE_S", 1)
    cfgc["monitor_intervalo_s"] = 0.5
    ir_jogo(casas)
    limpar_cupons(casas)
    servidor.ESTADO["limite"] = True
    recarregar(casas[P.CASA_PS])
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.novo_sinal(dict(SINAL, id="lim"))
    assert esperar(lambda: "ESTACIONADO" in est.monitor, 30), est.monitor
    servidor.ESTADO.clear()
    assert esperar(lambda: est.bilhetes and est.bilhetes[-1]["status"] == "testado", 90), (est.monitor, P._LOG_MEM[-15:])


def test_calibracao(casas, cfgc):
    for c in casas.values():
        c.executar(c.ir, c.url_lista())
    op = P.Operador(cfgc, P.Estado(cfgc), casas)
    op._calibrar()
    import glob, json, os
    arqs = glob.glob(os.path.join(P.PRINTS, "calibracao_*.json"))
    assert arqs
    rel = json.load(open(arqs[0]))
    assert rel and rel[0]["casas"]["1aWin"]["ok"], rel


def test_1awin_espelho_rotativo(site, tmp_path):
    """Caso real 14/11: 1wcwlx.life redirecionou para 1wmvjy.life no meio da carga
    e o goto morreu com 'interrupted by another navigation'."""
    cfg = P.carregar_config(str(tmp_path / "x.json"), avisar=False)
    c = P.Casa(P.CASA_1W, cfg, base_url=site + "/mv", perfil=str(tmp_path / "p"), headless=True).iniciar()
    try:
        c.executar(c.ir, site + "/mv" + cfg["caminho_lista_1awin"])
        assert c.base.startswith("http://localhost:")
        assert "EUA. NBA" in c.executar(c.texto)
        r = c.executar(c.cacar, TIMES, "EUA - NBA")
        assert r["ok"] and r["url"].startswith("http://localhost:")
        # endereço decorado num espelho velho é aberto no espelho atual
        assert P.trocar_origem("https://1wcwlx.life/betting/match/x-1", "https://1wmvjy.life") == "https://1wmvjy.life/betting/match/x-1"
    finally:
        c.parar()


def test_leitura_sobrevive_a_pagina_que_troca_de_rota(site, casas):
    """Caso real 14/11: 'Execution context was destroyed' — a página navegou de novo
    logo depois de carregar."""
    c1 = casas[P.CASA_1W]

    def corrida():
        c1.page.goto(site + "/mv" + c1.cfg["caminho_lista_1awin"], wait_until="commit")
        return c1._sondar(c1._mapa_ligas, 8)
    ligas = c1.executar(corrida)
    assert ligas and any("NBA" in l["nome"] for l in ligas)


def test_ps_boletim_confere_pelo_id_exato(casas):
    """Boletim real da PS: .BetItemComponent[data-id] = id do botão clicado."""
    _, cps = ir_jogo(casas)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    assert o["_aid"].endswith("|0|3|3|1|211.5")
    r = cps.executar(cps.montar, dict(o, stake=31.0), TIMES)
    assert r["ok"], r
    assert cps.executar(cps.desfazer, o)["ok"]
    errado = dict(o, stake=31.0, _aid=o["_aid"].replace("|3|3|", "|3|4|"))  # pede o 'abaixo', clica o 'acima'
    r = cps.executar(cps.montar, errado, TIMES)
    assert not r["ok"] and "outra seleção" in r["motivo"]
    assert cps.executar(cps.itens_cupom) == []


def test_ps_desfaz_pelo_x_quando_remover_tudo_nao_responde(casas):
    """Plano B real: se o 'Remover tudo' não esvaziar, clica o X da seleção."""
    servidor.ESTADO["remover_tudo_morto"] = True
    _, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    assert cps.executar(cps.montar, dict(o, stake=31.0), TIMES)["ok"]
    assert cps.executar(cps.desfazer, o)["ok"]
    assert cps.executar(cps.itens_cupom) == []


def test_calibracao_testa_ps_mesmo_sem_jogo_da_1awin(casas, cfgc, monkeypatch):
    """Caso 14/11 11:05: os 3 jogos da 1aWin não existiam na PS -> a PS nunca era provada."""
    for c in casas.values():
        c.executar(c.ir, c.url_lista())
    monkeypatch.setattr(P, "_calib_listar_jogos", lambda c: [(c.url_lista(), ["Time Que Nao Existe", "Outro Inexistente"])])
    monkeypatch.setattr(P, "PRAZO_CACA_S", 5)
    op = P.Operador(cfgc, P.Estado(cfgc), casas)
    op._calibrar()
    import glob, json, os
    rel = json.load(open(sorted(glob.glob(os.path.join(P.PRINTS, "calibracao_*.json")))[-1]))
    ps = rel[-1]["casas"]["PS3838"]
    assert ps["ok"], rel


@pytest.mark.parametrize("ms", [150, 700])
def test_1awin_selecao_some_do_cupom_nao_conta_como_montado(casas, ms):
    """Caso 14/11 12:32: a 1aWin tirou a linha 164.5 do cupom; a stake foi digitada no
    'Código da aposta' e a perna contou como montada. Agora falha com o motivo verdadeiro."""
    servidor.ESTADO["linha_sai"] = ms
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    r = c1.executar(c1.montar, dict(o1, stake=34.56), TIMES)
    assert not r["ok"] and ("sumiu" in r["motivo"] or "não recebeu" in r["motivo"]), r
    codigo = c1.executar(lambda: c1.page.locator('input[placeholder="Código da aposta"]').input_value())
    assert codigo == ""


def test_caca_ps_em_lista_enorme_dentro_do_prazo(casas):
    """Caso 14/11 15:04: lista ao vivo da PS enorme -> 'job cacar passou do prazo'."""
    servidor.ESTADO["muitos"] = 1500
    cps = casas[P.CASA_PS]
    cps.executar(cps.ir, cps.url_lista())
    t0 = time.time()
    r = cps.executar(cps.cacar, ["Atenas Cordoba", "San Lorenzo de Almagro"])
    assert r["ok"], r
    assert time.time() - t0 < 25


def test_carimbo_que_virou_outra_linha_nao_e_clicado(casas):
    """Caso 14/11 15:21 (Ratiopharm): o botão carimbado passou a mostrar outra linha com a
    mesma odd; o programa clicou e o cupom veio 'sem a linha 174.5'. Agora relê por identidade."""
    servidor.ESTADO["troca_linha"] = 1500
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    time.sleep(2.0)  # o redesenho acontece entre a leitura e o clique
    r = c1.executar(c1.montar, dict(o1, stake=26.57), TIMES)
    assert r["ok"], r
    assert "214.5" in c1.executar(c1.itens_cupom)[0]
    c1.executar(c1.desfazer, o1)


@pytest.mark.parametrize("ms", [600, 1100])
def test_odd_que_cai_depois_de_montado_reprova_tudo(casas, cfgc, ms):
    """Caso Ratiopharm 14/11 15:21: PS 'Odds have changed from 2.000 to 1.675' DEPOIS de montado.
    No modo real teria confirmado a 1.675. Agora a conferência final reprova e desfaz as duas."""
    servidor.ESTADO["odd_muda"] = ms
    c1, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
    assert st == "falhou", bil["detalhe"]
    assert "odd" in bil["detalhe"]["PS3838"].lower()
    for c in casas.values():
        assert c.executar(c.itens_cupom) == []


def test_modo_real_reconfere_no_instante_de_confirmar(casas, cfgc):
    """A odd cai DEPOIS da conferência final: no modo real o confirmar reconfere e não aposta."""
    c1, cps = ir_jogo(casas)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    servidor.ESTADO["odd_muda"] = 1
    recarregar(cps)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    def montar_sem_conferir():
        cps.clicar_firme(cps.page.locator('[data-mid="%s"]' % o["_mid"]).first)
        cps.page.wait_for_timeout(500)
    cps.executar(montar_sem_conferir)
    r = cps.executar(cps.confirmar, dict(o, stake=31.0))
    assert not r["ok"] and "odd mudou" in r["motivo"]
    assert "Aposta aceita" not in cps.executar(cps.texto)
    limpar_cupons(casas)


def test_aviso_de_odd_velho_nao_reprova(casas):
    """14/11 16:10:31: 'Odds have changed from 1.689 to 1.909' (de OUTRA seleção) reprovou uma perna @2.06."""
    _, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    def com_aviso_velho():
        cps.clicar_firme(cps.page.locator('[data-mid="%s"]' % o["_mid"]).first)
        cps.page.wait_for_timeout(400)
        cps.page.evaluate("""() => { const d = document.createElement('div'); d.textContent = 'Odds have changed from 1.689 to 1.909';
            document.getElementById('sels').prepend(d); }""")
        return cps.conferir_cupom(o)
    ok, mot = cps.executar(com_aviso_velho)
    assert ok, mot
    limpar_cupons(casas)


def test_ps_selecao_indisponivel_fecha_janela_e_falha(casas):
    """14/11 ~18h: janela 'Seleção Indisponível' (mínimo > máximo) bloqueava a PS."""
    servidor.ESTADO["indisponivel"] = True
    _, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    r = cps.executar(cps.montar, dict(o, stake=31.0), TIMES)
    assert not r["ok"] and "indisponível" in r["motivo"]
    assert "Seleção Indisponível" not in cps.executar(cps.texto)  # janela fechada: a página volta a funcionar
