"""Testes de navegador (Playwright, headless) contra as páginas falsas fiéis das duas casas."""
import threading
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
    c.executar(lambda: c.page.evaluate("() => { try { sessionStorage.removeItem('aceitas'); } catch (e) {} }"))
    c.executar(lambda: c.page.reload(wait_until="domcontentloaded"))


@pytest.fixture
def cfgc(casas):
    """cfg compartilhado pelas casas (restaura depois)."""
    cfg = casas[P.CASA_1W].cfg
    salvo = dict(cfg)
    cfg["reentrar_mesmo_jogo"] = False   # testes antigos: 1 entrada encerra; a reentrada tem teste próprio
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
    assert not r["ok"], r  # mercado morto: a perna não monta
    assert c1.executar(c1.itens_cupom) == []
    assert not c1.executar(c1._eval, "() => window.__cancelou || 0")  # NUNCA clicou 'Cancelar'


def test_aviso_evento_excluido_nao_e_selecao_e_nunca_clica_cancelar(casas):
    """Print 15/11 21:1x: o 'Cancelar' do aviso devolvia a seleção — selecionava e excluía sem parar."""
    servidor.ESTADO["cupom_preso"] = True
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    assert c1.executar(c1.itens_cupom) == []          # aviso de desfazer não é seleção
    assert c1.executar(c1.limpar_cupom) is True
    assert c1.executar(c1._esperar_toast_desfazer) is True   # some sozinho
    assert not c1.executar(c1._eval, "() => window.__cancelou || 0")
    servidor.ESTADO.clear()


def test_aviso_evento_excluido_preso_sai_pela_lixeira(casas):
    servidor.ESTADO["cupom_preso"] = True
    servidor.ESTADO["toast_preso"] = True
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    assert c1.executar(c1._esperar_toast_desfazer, 2.0) is True
    assert not c1.executar(c1._eval, "() => window.__cancelou || 0")
    servidor.ESTADO.clear()


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
    # 20/11 (custos +1): com o limite maior, um par de janela 7 passou a valer e é o escolhido (maior janela)
    assert bil["tipo"] == "handicap" and bil["janela"] >= 5.0
    assert bil["custo"] <= P.custo_max_para(bil["janela"], cfgc) + 1e-9
    l1, l2 = bil["pernas"][0]["linha"], bil["pernas"][1]["linha"]
    assert (l1 > 0) != (l2 > 0)  # handicap: sinais opostos
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
    # sem fim por tempo (pedido do operador): depois do 1º par segue monitorando até 4 pares / mercado fechar
    assert "encerrado" in est.monitor or "monitorando Phoenix x Charlotte" in est.monitor, est.monitor
    op.pausar() if hasattr(op, "pausar") else None


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


@pytest.mark.parametrize("ms", [150, 300])  # 22/11: a preparação ficou ~0,6 s; a odd muda antes da barreira
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


def test_odd_que_cai_mas_fica_no_piso_1_78_e_aceita(casas, cfgc):
    """Operador 18/11 (Chemnitz x Bursa): 'Odds have changed from 2.140 to 1.840' reprovava. 'Até 1.78 pode pegar':
    agora aceita, recalcula o par com a odd viva e só barra se o custo passar do teto (15%)."""
    servidor.ESTADO["odd_muda"] = 1
    servidor.ESTADO["odd_nova"] = "1.72"   # no site falso a perna da PS é 1.80: cai 0,08 (> 0,05 da regra antiga)
    cfgc["odd_min"] = 1.70                 # piso do teste (no programa: 1.78)
    c1, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
    # a odd que caiu até o piso é ACEITA no cupom; o par é recalculado e, desde 21/11, tem que caber na TABELA da
    # janela (aqui as odds do site falso deixam o par caro -> recusa com o motivo; nada é confirmado)
    assert bil["pernas"][1].get("odd_viva") == 1.72, "\n".join(P._LOG_MEM[-25:])
    assert st == "falhou" and "ABORTADO NA BARREIRA: custo vivo" in bil["detalhe"]["PS3838"], bil["detalhe"]
    # caindo para 1.675 (abaixo do piso) continua reprovando
    servidor.ESTADO["odd_nova"] = "1.675"
    cfgc["odd_min"] = 1.70
    recarregar(cps)
    limpar_cupons(casas)
    bil2 = achar(casas, cfgc)
    est.registrar_bilhete(bil2, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil2) == "falhou"
    servidor.ESTADO.clear()
    recarregar(cps)
    limpar_cupons(casas)


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
    servidor.ESTADO.pop("indisponivel", None)


def test_1awin_desfaz_pela_lixeira_quando_o_x_nao_funciona(casas):
    """14/11 18:21: 'DESFAZER FALHOU: cupom não esvaziou' e a 1aWin ficou bloqueada (cupom sujo)."""
    servidor.ESTADO["x_morto"] = True
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    assert c1.executar(c1.montar, dict(o1, stake=26.57), TIMES)["ok"]
    assert c1.executar(c1.desfazer, dict(o1, _mid=None))["ok"]
    assert c1.executar(c1.itens_cupom) == []


def test_1awin_cupom_so_e_vazio_com_a_prova_da_casa(casas):
    """14/11 18:40: o painel disse 'cupom vazio' com seleções presas. Sem 'Selecione um resultado'
    e com cara de seleção, o cupom é SUJO — e a montagem não vai por cima."""
    servidor.ESTADO["cartao_estranho"] = True
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    assert c1.executar(c1.itens_cupom)  # não diz que está vazio
    servidor.ESTADO.clear()
    recarregar(c1)
    assert c1.executar(c1.itens_cupom) == []  # 'Selecione um resultado' = vazio de verdade


def test_limpar_nunca_clica_sim_de_mercado(casas):
    """14/11 22:36: a limpeza clicou 'A partida vai para a prorrogação? Sim' @14.6 (o último 'Sim' da
    página) e o cupom nunca esvaziava. Com o X morto, a limpeza usa a lixeira e não adiciona nada."""
    servidor.ESTADO["x_morto"] = True
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    assert c1.executar(c1.montar, dict(o1, stake=26.57), TIMES)["ok"]
    d = c1.executar(c1.desfazer, dict(o1, _mid=None))
    assert d["ok"], d
    assert "prorroga" not in (c1.executar(c1.texto_cupom) or "")
    assert c1.executar(c1.itens_cupom) == []


def test_1awin_desmarca_pelo_mercado_quando_x_e_lixeira_falham(casas):
    """14/11 22:59: 'Totais (inc OT), Acima De 235.5' ficou preso com o X visível."""
    servidor.ESTADO.update({"x_morto": True, "lixeira_morta": True})
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    assert c1.executar(c1.montar, dict(o1, stake=26.57), TIMES)["ok"]
    d = c1.executar(c1.desfazer, dict(o1, _mid=None))
    assert d["ok"], d
    assert c1.executar(c1.itens_cupom) == []


def test_modo_real_casa_que_nao_reage_ao_clique_nao_apostou(casas, cfgc):
    """Clicou em apostar e a 1aWin não fez NADA: sem mensagem, saldo igual, seleção no cupom, sem processamento.
    20/11 16:42/16:47 (#244/#247) isso virava INCERTO e travava tudo; é 'não apostado'. A PS NÃO é disparada."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["confirma_mudo"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "falhou", bil["detalhe"]
    assert "não reagiu ao clique" in bil["detalhe"]["1aWin"] and "NÃO confirmada" in bil["detalhe"]["PS3838"]
    assert "Aposta aceita" not in casas[P.CASA_PS].executar(casas[P.CASA_PS].texto)
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_botao_ps_fazer_1_aposta_e_clique_unico(casas):
    _, cps = ir_jogo(casas)
    recarregar(cps)   # sem resíduo do teste anterior (página com estado do site falso de outro teste)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    assert cps.executar(cps.montar, dict(o, stake=31.0), TIMES)["ok"]
    assert cps.executar(cps._marcar_botao_apostar)["n"] == 1
    limpar_cupons(casas)


def test_1awin_limpa_sem_apostar_nao_e_incerto(casas, cfgc):
    """14/11 23:58: a 1aWin limpou o cupom sem apostar (saldo igual, histórico vazio).
    Agora é 'falhou' (nada apostado) e a PS é desfeita — não fica INCERTO."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["limpa_sem_apostar"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "falhou"
    assert "saldo continua" in bil["detalhe"]["1aWin"]
    assert casas[P.CASA_PS].executar(casas[P.CASA_PS].itens_cupom) == []


def test_1awin_saldo_descontado_noutro_lugar_e_incerto(casas, cfgc):
    """15/11 20:46: apareceu 'BRL 136,15' (= saldo − stake) mas o leitor de saldo seguia no valor velho
    e o programa disse 'nada foi apostado'. Tem que ser INCERTO (para tudo, não aposta a PS)."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["saldo_noutro_lugar"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "incerto", bil["detalhe"]
    assert "saldo descontado" in bil["detalhe"]["1aWin"]
    assert "Aposta aceita" not in casas[P.CASA_PS].executar(casas[P.CASA_PS].texto)
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_1awin_saldo_cai_e_volta_e_falhou(casas, cfgc):
    """15/11 20:46 conferido pelo operador: o saldo caiu durante o processamento, voltou, e a aposta NÃO existia."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["saldo_noutro_lugar"] = True
    servidor.ESTADO["saldo_volta"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "falhou", bil["detalhe"]
    assert casas[P.CASA_PS].executar(casas[P.CASA_PS].itens_cupom) == []
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_1awin_aceita_com_odd_pior_avisa(casas, cfgc):
    """16/11 09:10: a 1aWin aceitou a 1.61 um par calculado a 2.1 — tem que aparecer o aviso no bilhete."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["aceita_pior"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
    assert st == "confirmado", bil.get("detalhe")
    assert any("ACEITOU a 1.61" in a for a in bil.get("avisos", [])), bil.get("avisos")
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_botao_de_apostar_redesenhado_antes_do_clique(casas, cfgc):
    """16/11 22:32: 'o clique em apostar não aconteceu (Locator.click: Timeout 3000ms … waiting for locator)'.
    A 1aWin redesenhou o botão entre a marca e o clique. Agora remarca e clica (uma vez só)."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["redesenha_botao"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "confirmado", bil.get("detalhe")
    assert any("redesenhado antes do clique" in l for l in P._LOG_MEM[-40:])  # o caso aconteceu mesmo
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_1awin_saldo_sozinho_nao_prova(casas, cfgc):
    """15/11 17:02: o saldo caiu e a aposta NÃO existia. Saldo sozinho não confirma: não aposta a PS."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["so_saldo"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "incerto", bil["detalhe"]
    assert "Aposta aceita" not in casas[P.CASA_PS].executar(casas[P.CASA_PS].texto)
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)


def test_1awin_recibo_de_aposta_aceita(casas, cfgc):
    """Tela real da 1aWin (15/11): 'Aposta aceita / Ir para o histórico / Aberto • Simples' fica no cupom.
    O programa reconhece como aceita e, depois, recarrega para o cupom voltar a ficar vazio."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["recibo"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "confirmado", bil["detalhe"]
    c1 = casas[P.CASA_1W]
    assert c1.executar(c1.limpar_cupom) is True
    assert c1.executar(c1.itens_cupom) == []
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)


def test_ps_janela_confirmar_apostas_e_aviso_velho(casas, cfgc):
    """15/11 15:21 (1ª real): a PS abriu 'Confirmar Apostas — Tem certeza…? OK/CANCELAR' e o programa
    deu a PS como recusada em 2s por causa de um 'Odds have changed' que JÁ estava no boletim.
    Resultado: 1aWin apostada e PS não (perna exposta). Agora clica OK e só lê recusa nova."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["modal_confirmar"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    cps = casas[P.CASA_PS]
    real_montar = cps.preparar

    def montar_com_aviso(perna, times, *a):
        r = real_montar(perna, times, *a)
        cps.page.evaluate("""() => { const d = document.createElement('div');
            d.textContent = 'Odds have changed from 1.780 to 1.800'; document.getElementById('sels').prepend(d); }""")
        return r
    cps.preparar = montar_com_aviso
    try:
        est = P.Estado(cfgc)
        est.registrar_bilhete(bil, "sinal")
        assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "confirmado", bil["detalhe"]
    finally:
        del cps.preparar   # volta ao método da classe (sem deixar resíduo para os outros testes)
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)


def test_odd_que_sobe_e_aceita_e_que_cai_nao(casas):
    """Ordem do operador 15/11: subiu (melhor) = aceita; caiu além de 0,05 = reprova."""
    _, cps = ir_jogo(casas)
    recarregar(cps)  # a página guarda o ESTADO do site falso de quando carregou (odd_muda de testes anteriores)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    def com_odd(nova):
        cps.clicar_firme(cps.page.locator('[data-mid="%s"]' % o["_mid"]).first)
        cps.page.wait_for_timeout(400)
        cps.page.evaluate("(v) => { document.querySelector('.BetItemComponent .odds-info .odds').textContent = ' ' + v; }", nova)
        r = cps.conferir_cupom(o)
        cps.limpar_cupom()
        return r
    r0 = cps.executar(com_odd, "1.950")
    assert r0[0] is True, r0        # subiu 1.80 -> 1.95
    ok, mot = cps.executar(com_odd, "1.700")                 # caiu 1.80 -> 1.70
    assert not ok and "caiu" in mot


def test_ps_rejeita_e_programa_tenta_de_novo(casas, cfgc):
    """15/11 15:56: 1aWin apostada e a PS 'Your bet has been rejected.' — agora reaposta na hora."""
    cfgc["modo_teste"] = False
    servidor.ESTADO["rejeita_ps"] = 1
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    try:
        assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "confirmado", bil["detalhe"]
    finally:
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)


@pytest.mark.parametrize("final,esperado", [("aceita", True), ("rejeita", False)])
def test_ps_aguardando_processamento(casas, final, esperado):
    """15/11 15:57: PS 'Aguarde… sendo processada' — espera o resultado final (aceita ou rejeitada)."""
    servidor.ESTADO["processando"] = final
    _, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    p = dict(o, stake=31.0)
    assert cps.executar(cps.montar, p, TIMES)["ok"]
    r = cps.executar(cps.confirmar, p, timeout=90)
    assert r["ok"] is esperado, r
    servidor.ESTADO.clear()
    recarregar(cps)


@pytest.mark.parametrize("modo", ["sequencial_aceita", "sequencial_processando"])
def test_1awin_processa_e_nao_entra(casas, cfgc, modo):
    """15/11 17:02: spinner, saldo cai, depois volta e o cupom limpa — a 1aWin NÃO entrou.
    sequencial_aceita (padrão 24/11): a PS só sai com a 1aWin ACEITA -> nada apostado.
    sequencial_processando: a PS sai no 'processando' -> PS órfã; 'saldo voltou' é recusa comprovada -> tenta a 1aWin de
    novo (não entra) -> perna órfã (exposto). A PS aceita nunca é repetida."""
    cfgc["modo_teste"] = False
    cfgc["confirmacao"] = modo
    servidor.ESTADO["processa_e_some"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    try:
        st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
        a = [c.executar(lambda c=c: c.page.evaluate("() => +sessionStorage.aceitas || 0")) for c in (casas[P.CASA_1W], casas[P.CASA_PS])]
        if modo == "sequencial_aceita":
            assert st == "falhou" and a == [0, 0], (st, a, bil["detalhe"])
        else:
            assert st == "exposto" and a == [0, 1], (st, a, bil["detalhe"])
    finally:
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)


def test_monitoramento_pos_entrada_cobertura_simulada(casas, cfgc):
    """Middle testado -> posição aberta em aba separada -> acha aposta avulsa que acrescenta totais novos
    à janela da carteira -> cobertura (simulada no modo teste). Sem distância mínima fixa (16/11)."""
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    cfgc["pos_monitorar"] = True
    cfgc["avulsas_permitidas"] = True  # (18/11: no programa as avulsas estão PROIBIDAS; o teste liga para cobrir a mecânica)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.gerente = P.GerentePosicoes(cfgc, est, casas)
    bil = achar(casas, cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert op.executar_bilhete(bil) == "testado"
    pos = est.posicoes[-1]
    assert pos["aberta"] and P.over_original(pos) == 211.5
    op.gerente.rodada(pos)
    cob = [p for p in pos["pernas"] if p["origem"] == "cobertura"]
    assert cob and cob[0]["simulada"], pos.get("candidatos")
    ref_un = P.under_original(pos)
    antes = P.cobertura_2g([p for p in pos["pernas"] if p["origem"] != "cobertura"])
    assert P.cobertura_2g(pos["pernas"]) - antes, cob  # a avulsa acrescentou totais novos à janela
    assert pos["resumo"]["gasto"] > 0 and len(pos["pernas"]) == 3
    for c in casas.values():
        assert c.executar(c.itens_cupom) == []
        c.executar(c.fechar_aba, "pos%d" % pos["id"])


def test_reentrada_continua_no_jogo_e_nao_repete_o_mesmo_par(casas, cfgc):
    """Parceiro 15/11: depois do middle, continua no jogo; o MESMO par não reentra (não aumenta a faixa)."""
    servidor.ESTADO.clear()
    cfgc["reentrar_mesmo_jogo"] = True
    cfgc["monitor_intervalo_s"] = 0.5
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.novo_sinal(dict(SINAL, id="reentra"))
    assert esperar(lambda: est.bilhetes and est.bilhetes[-1]["status"] == "testado", 90), [(b["status"], b.get("detalhe")) for b in est.bilhetes]
    assert esperar(lambda: "procurando reentrada" in est.monitor, 30), est.monitor
    time.sleep(4)
    assert len([b for b in est.bilhetes if b["status"] == "testado"]) == 1  # mesmo par não repete
    op._atual[1].set()  # encerra o monitoramento
    time.sleep(1.5)


def test_par_que_falhou_vai_pra_geladeira_e_nao_repete(casas, cfgc):
    """Print 15/11 20:45–20:50 (Toronto x Knicks): a PS dizia 'indisponível' e o programa repetia o MESMO par."""
    servidor.ESTADO["indisponivel"] = True
    cfgc["monitor_intervalo_s"] = 0.5
    cfgc["pausa_ps_indisponivel_s"] = 3
    cfgc["pausa_ps_periodo_indisponivel_s"] = 3
    for c in casas.values():
        recarregar(c)  # o site falso só lê as chaves de ESTADO ao carregar a página
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.novo_sinal(dict(SINAL, id="geladeira"))
    assert esperar(lambda: len([b for b in est.bilhetes if b["status"] == "falhou"]) >= 2, 150), (
        est.monitor, [(b["status"], b.get("detalhe")) for b in est.bilhetes], P._LOG_MEM[-12:])
    op._atual[1].set()
    time.sleep(1.5)
    falhos = [b for b in est.bilhetes if b["status"] == "falhou"]
    pernas_ps = [b["pernas"][1]["selecao"] for b in falhos]
    assert len(pernas_ps) == len(set(pernas_ps)), pernas_ps  # nenhuma perna PS gelada foi tentada de novo
    servidor.ESTADO.pop("indisponivel", None)
    limpar_cupons(casas)



def test_avulsa_proibida_por_padrao(casas, cfgc):
    """18/11: 'a ideia era fazer LINHAS' — com o padrão, nenhuma aposta de uma perna só, mesmo com a posição aberta."""
    assert P.DEFAULTS["avulsas_permitidas"] is False and P.DEFAULTS["pos_monitorar"] is False
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    cfgc["pos_monitorar"] = True
    cfgc["avulsas_permitidas"] = False
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.gerente = P.GerentePosicoes(cfgc, est, casas)
    bil = achar(casas, cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert op.executar_bilhete(bil) == "testado"
    pos = est.posicoes[-1]
    op.gerente.rodada(pos)
    assert not [p for p in pos["pernas"] if p["origem"] == "cobertura"]
    assert pos.get("tabela")  # a carteira continua aparecendo


def test_perna_solta_procura_o_par_na_ps(casas, cfgc):
    """18/11: 'sempre buscar pares para cobrir'. A 1aWin aceitou e a PS recusou 3x (a 1ª + 2 reapostas):
    o monitoramento NÃO para; na rodada seguinte cobre a perna solta com uma linha oposta da PS."""
    cfgc["modo_teste"] = False
    cfgc["monitor_intervalo_s"] = 0.5
    cfgc["reentrar_mesmo_jogo"] = False
    cfgc["cobrir_perna_solta"] = True   # 20/11: desligado por padrão (posição incompleta congela tudo); aqui cobre a mecânica
    # 22/11: a PS recusa 2x (a 1ª + 1 recuperação, ambas COMPROVADAS por texto novo): 1 tentativa só, porque a 2ª recusa
    # numa linha nova repetiria o MESMO texto na tela -> sem prova nova -> INCERTO (comportamento seguro, outro teste)
    cfgc["orfa_tentativas"] = 1
    cfgc["cobrir_orfa_automatico"] = False   # mecânica antiga da perna solta (a automática tem teste próprio)
    servidor.ESTADO["rejeita_ps"] = 2
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.novo_sinal(dict(SINAL, id="solta"))
    assert esperar(lambda: any("COBERTA" in str((b.get("detalhe") or {}).get("PS3838", "")) for b in est.bilhetes), 150), (
        [(b["status"], b.get("detalhe")) for b in est.bilhetes], P._LOG_MEM[-15:])
    b = [b for b in est.bilhetes if "COBERTA" in str((b.get("detalhe") or {}).get("PS3838", ""))][0]
    assert b["status"] == "confirmado"
    # especificação 19/11 (teste 14): falha numa perna = POSIÇÃO INCOMPLETA, alerta, e o jogo não reentra mais
    assert any("POSIÇÃO INCOMPLETA" in a for a in b.get("avisos", []))
    assert esperar(lambda: "sem novas reentradas" in est.monitor, 10), est.monitor
    assert not op._atual[0].is_alive() or esperar(lambda: not op._atual[0].is_alive(), 10)
    op._atual[1].set()
    time.sleep(1.5)
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)



def test_ancora_na_montagem_troca_so_a_perna_que_caiu(casas, cfgc):
    """20/11 (operador): 'podia ter tentado âncora'. Lakers 00:40 / Brasov 14:27: uma perna montou e a outra caiu
    porque a casa trocou a linha. Agora segura a montada e troca SÓ a que caiu por uma linha que ainda forma par."""
    servidor.ESTADO["linha_sai_uma"] = 150
    # no site falso a outra linha da 1aWin não passa na tabela de custo: o teste afrouxa SÓ o custo (na troca e na
    # barreira) — a mecânica é o que está em teste; no programa as regras são as mesmas da entrada
    cfgc["custo_por_janela"], cfgc["custo_max"] = None, 50.0
    cfgc["retentar_selecao_sumida"] = False   # aqui o teste é da âncora (a 2ª tentativa da mesma seleção tem teste próprio)
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    linha_antiga = bil["pernas"][0]["linha"]
    c1 = casas[P.CASA_1W]

    def substituir(nome_f, b0):
        assert nome_f == P.CASA_1W
        lf = c1.executar(c1.ler_escada, TIMES)
        ok_of = {k: v for k, v in b0["pernas"][1].items() if k not in ("stake", "selecao", "casa")}
        outras = [o for o in lf["ofertas"] if not (o["lado"] == b0["pernas"][0]["lado"] and o["linha"] == linha_antiga)]
        # no site falso a outra linha da 1aWin não passa na tabela de custo: o teste afrouxa só o custo (a mecânica
        # é o que está em teste; no programa as regras são as mesmas da entrada)
        novo, _ = P.achar_par(outras, [ok_of], SINAL, dict(cfgc, custo_por_janela=None, custo_max=50.0))
        return novo

    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    st = P.Operador(cfgc, est, casas).executar_bilhete(bil, substituir=substituir)
    assert st == "testado", "\n".join(P._LOG_MEM[-14:])
    assert any("âncora na montagem" in a for a in bil.get("avisos", []))
    assert bil["pernas"][0]["linha"] != linha_antiga
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_sem_encerrar_por_tempo_e_sinal_novo_assume(casas, cfgc):
    """24/11 (operador): 'não precisa encerrar depois de 15 min; se eu quiser encerrar eu peço outro sinal'.
    Com par confirmado o jogo segue (sem limite de tempo); o sinal NOVO assume na hora."""
    servidor.ESTADO.clear()
    cfgc["reentrar_mesmo_jogo"] = True
    cfgc["monitor_intervalo_s"] = 0.5
    assert P.DEFAULTS["protecao_sem_entrada_min"] == 0
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.novo_sinal(dict(SINAL, id="prot"))
    assert esperar(lambda: est.bilhetes and est.bilhetes[-1]["status"] == "testado", 90)
    assert esperar(lambda: op._prot is not None, 10)
    op._prot["ultima"] = time.time() - 3600       # 1 hora sem nova entrada: NÃO encerra
    time.sleep(3)
    assert op._atual[2]["jogo"] == SINAL["jogo"] and op._atual[0].is_alive()
    s2 = dict(SINAL, id="outro", jogo="Flamengo x Franca", times=["Flamengo", "Franca"])
    assert op.novo_sinal(s2) is True                # o sinal novo ASSUME (é assim que o operador encerra)
    assert esperar(lambda: op._atual and op._atual[2]["jogo"] == s2["jogo"], 10)
    op._atual[1].set()
    time.sleep(1.5)


def test_perna_solta_segura_o_jogo_e_sinal_novo_vai_pra_fila(cfg):
    """Com PERNA SOLTA sendo coberta, o sinal novo NÃO abandona a aposta descoberta: vai para a fila."""
    op = P.Operador(dict(cfg, modo_teste=False), P.Estado(cfg), {})
    ev = threading.Event()
    th = threading.Thread(target=ev.wait, daemon=True)
    th.start()
    op._atual = (th, threading.Event(), dict(SINAL))
    op._prot = {"jogo": SINAL["jogo"], "pares": 0, "max": 4, "ultima": time.time(), "perna_solta": True}
    s2 = dict(SINAL, id="outro", jogo="Flamengo x Franca", times=["Flamengo", "Franca"])
    assert op.novo_sinal(s2) is False and [x["jogo"] for _, x in op._fila] == ["Flamengo x Franca"]
    ev.set()


def test_incompleto_bloqueia_tudo_e_a_fila_nao_anda(casas, cfgc):
    """20/11 (operador): 1aWin confirmada e PS recusada -> INCOMPLETO: BLOQUEADO, alerta com as pernas, a fila NÃO
    assume (não começa Denver x Boston). Só a resolução do operador libera."""
    cfgc["modo_teste"] = False
    cfgc["monitor_intervalo_s"] = 0.5
    cfgc["orfa_tentativas"] = 1   # 22/11: 1 recuperação com recusa comprovada -> INCOMPLETO (ver test_perna_solta…)
    cfgc["cobrir_orfa_automatico"] = False   # aqui o teste é do BLOQUEIO (a cobertura automática tem teste próprio)
    servidor.ESTADO["rejeita_ps"] = 3
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    op.novo_sinal(dict(SINAL, id="incomp"))
    assert esperar(lambda: op.bloqueio is not None, 150), [(b["status"], b.get("detalhe")) for b in est.bilhetes]
    assert op.bloqueio["estado"] == "INCOMPLETO" and "1aWin" in op.bloqueio["confirmada"] and "PS3838" in op.bloqueio["falhou"]
    assert esperar(lambda: not op._atual[0].is_alive(), 20)
    s2 = dict(SINAL, id="denver", jogo="Flamengo x Franca", times=["Flamengo", "Franca"])
    assert op.novo_sinal(s2) is False
    time.sleep(2)
    assert op._atual[2]["jogo"] == SINAL["jogo"] and [x["jogo"] for _, x in op._fila] == ["Flamengo x Franca"]
    assert est.protecao["bloqueio"]["estado"] == "INCOMPLETO"
    servidor.ESTADO.clear()
    op._fila = []
    op.resolver("liberar")
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)



def test_selecao_que_some_num_lance_tenta_de_novo_uma_vez(casas, cfgc):
    """20/11 (Osasco/Pato): 'a seleção sumiu durante a stake' — a 1aWin tirou a linha 1–2 s num lance. Agora espera
    e monta a MESMA seleção mais uma vez (com a mesma conferência)."""
    servidor.ESTADO["linha_sai_uma"] = 150
    cfgc["retentar_selecao_sumida"] = True   # 21/11: desligado por padrão (não insistir); aqui testa o mecanismo
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    r = c1.executar(c1.montar, dict(o1, stake=34.56), TIMES)
    assert r["ok"], r
    assert any("tento a mesma seleção mais uma vez" in l for l in P._LOG_MEM[-10:])
    c1.executar(c1.desfazer, o1)
    servidor.ESTADO.clear()
    recarregar(c1)


def test_clique_com_erro_depois_de_clicar_nao_vira_nao_apostou(casas, cfgc, monkeypatch):
    """19/11 21:06:46 (Tatuí x Assis): o clique em 'Fazer uma aposta' ACONTECEU (a aposta entrou) e o Playwright
    devolveu erro de tempo. O programa achou que não clicou, desfez a PS e a 1aWin ficou SOZINHA.
    Agora: erro depois da ação de clique = decide pelas provas da casa (aqui: 'Aposta aceita')."""
    from playwright.sync_api import Locator
    original = Locator.click

    def click_que_erra(self, *a, **k):
        original(self, *a, **k)
        raise Exception("Locator.click: Timeout 1500ms exceeded.\nCall log:\n  - waiting for locator('[data-alvo-apostar]')"
                        "\n  - performing click action\n  - click action done")
    cfgc["modo_teste"] = False
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    monkeypatch.setattr(Locator, "click", click_que_erra)
    try:
        st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
    finally:
        monkeypatch.setattr(Locator, "click", original)
    assert st == "confirmado", bil.get("detalhe")   # a 1aWin aceitou e a PS foi confirmada: o par fechou
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_r22_preparacao_concorrente_no_navegador(casas, cfgc, monkeypatch):
    """22/11 (Bucks x Portland): as duas casas PREPARAM ao mesmo tempo — uma não espera a outra terminar."""
    marcas = {}
    original = P.Casa.preparar

    def preparar_registrando(self, perna, times, *a):
        t0 = time.time()
        r = original(self, perna, times, *a)
        marcas[self.nome] = (t0, time.time())
        return r
    monkeypatch.setattr(P.Casa, "preparar", preparar_registrando)
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "testado", bil.get("detalhe")
    (a0, a1), (b0, b1) = marcas[P.CASA_1W], marcas[P.CASA_PS]
    assert b0 < a1 and a0 < b1, marcas            # os intervalos se sobrepõem: montagem concorrente
    assert any("PREPARAR: 1aWin pronta" in l for l in P._LOG_MEM[-60:])
    assert any("BARREIRA OK" in l for l in P._LOG_MEM[-60:])
    for c in casas.values():
        assert c.executar(c.itens_cupom) == []


def test_r22_dom_redesenhado_elemento_antigo_invalido(casas, cfgc):
    """22/11: a 1aWin redesenhou o mercado depois da leitura (o nó carimbado não existe mais). A preparação NÃO usa o
    elemento velho: faz UMA busca imediata pela identidade e clica no nó novo."""
    servidor.ESTADO["redesenha_odds"] = 1200
    c1, _ = ir_jogo(casas)
    recarregar(c1)
    limpar_cupons(casas)
    l1, _ = ler(casas)
    o1 = [x for x in l1["ofertas"] if x["linha"] == 214.5 and x["lado"] == "abaixo"][0]
    mid_velho = o1["_mid"]
    time.sleep(1.6)
    assert c1.executar(lambda: c1.page.locator('[data-mid="%s"]' % mid_velho).count()) == 0   # o nó velho sumiu
    perna = dict(o1, stake=34.56)
    r = c1.executar(c1.preparar, perna, TIMES, None, 15.0)
    assert r["ok"], r
    assert any("cliquei a seleção" in l and "(relida)" in l for l in P._LOG_MEM[-15:])
    c1.executar(c1.desfazer, perna)
    servidor.ESTADO.clear()
    recarregar(c1)


def test_r21_linha_some_na_montagem_nao_deixa_aposta_isolada(casas, cfgc):
    """21/11: a 1aWin tira a linha durante a montagem e não há substituta -> a PS NEM é montada, cupons limpos,
    nada confirmado (modo real)."""
    cfgc["modo_teste"] = False
    cfgc["retentar_selecao_sumida"] = False
    servidor.ESTADO["linha_sai_uma"] = 150
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    st = P.Operador(cfgc, est, casas).executar_bilhete(bil, substituir=lambda nome_f, b0: None)
    assert st == "falhou", bil.get("detalhe")
    # 22/11: preparação concorrente — a PS foi cancelada no meio ou, se já estava pronta, desfeita; nunca confirmada
    assert "não montada" in bil["detalhe"]["PS3838"] or "desfez" in bil["detalhe"]["PS3838"], bil["detalhe"]
    for c in casas.values():
        assert c.executar(c.itens_cupom) == []
        assert "Aposta aceita" not in c.executar(c.texto)
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_sinal_novo_interrompe_a_caca_em_andamento(casas):
    """Log 20/11 07:12–07:15: a PS seguiu 50 s caçando um jogo já cancelado. Agora a caça para em poucos segundos."""
    cps = casas[P.CASA_PS]
    w = cps.em_paralelo(cps.cacar, ["Time Inexistente Azul", "Time Inexistente Verde"], timeout=90)
    time.sleep(3)
    t0 = time.time()
    cps.interromper_caca()
    r = w()
    assert r.get("interrompida") and not r["ok"], r
    assert time.time() - t0 < 12
    # a caça seguinte (do sinal NOVO) funciona normalmente
    assert cps.executar(cps.cacar, TIMES)["ok"]


def test_1awin_saldo_cai_e_volta_com_selecao_no_cupom_nao_e_incerto(casas, cfgc):
    """Log 20/11 07:47 (Wyverns x Samuraiz): saldo 465,68 -> 436,48 -> 465,68 e a seleção FICOU no cupom. Era INCERTO e
    travou tudo. Saldo que caiu e voltou = nada apostado (conferido pelo operador 15/11): falhou, a PS nem dispara."""
    cfgc["modo_teste"] = False
    servidor.ESTADO.update(saldo_noutro_lugar=True, saldo_volta=True, mantem_selecao=True)
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    try:
        assert P.Operador(cfgc, est, casas).executar_bilhete(bil) == "falhou", bil["detalhe"]
        assert "voltou" in bil["detalhe"]["1aWin"]
        assert "Aposta aceita" not in casas[P.CASA_PS].executar(casas[P.CASA_PS].texto)
    finally:
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)
        limpar_cupons(casas)


@pytest.mark.parametrize("modo", ["sequencial_aceita", "sequencial_processando"])
def test_ps_odd_mudou_no_clique_e_recusa_comprovada_e_recupera(casas, cfgc, modo):
    """20/11 08:26 (Hapoel Beer Sheva x Ironi Ness Ziona): 1aWin aceita; a PS 'Odds have changed from 1.813 to 1.800' e a
    seleção ficou no boletim (NÃO apostou). Era INCERTO e a 1aWin ficou sozinha. Agora: recusa comprovada -> refaz a conta
    com a odd nova (dentro das regras) -> coloca a PS -> par fechado. Nada duplicado."""
    cfgc["modo_teste"] = False
    cfgc["confirmacao"] = modo
    servidor.ESTADO["odd_muda_no_clique"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    try:
        st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
        assert st == "confirmado", (bil["detalhe"], "\n".join(P._LOG_MEM[-25:]))
        a = [c.executar(lambda c=c: c.page.evaluate("() => +sessionStorage.aceitas || 0")) for c in (casas[P.CASA_1W], casas[P.CASA_PS])]
        assert a == [1, 1], a
        assert bil["pernas"][1].get("odd_aceita") or bil["pernas"][1].get("odd_viva") == 1.79
    finally:
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)
        limpar_cupons(casas)


def test_operador_diz_que_so_a_1awin_entrou_e_o_programa_cobre_e_segue(casas, cfgc):
    """20/11 08:26: a 1aWin entrou e a PS não; o operador só tinha 'nenhuma entrou' e o jogo morreu. Agora 'Só a 1aWin
    entrou': o jogo volta a ser monitorado, a perna é coberta na PS (regras normais) e o par vira a 1ª entrada do jogo
    (escadinha/âncora seguem dali). A 1aWin NUNCA é apostada de novo."""
    cfgc["modo_teste"] = False
    cfgc["monitor_intervalo_s"] = 0.5
    cfgc["reentrar_mesmo_jogo"] = True
    servidor.ESTADO.clear()
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    bil.update(jogo=SINAL["jogo"], times=TIMES)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    est.atualizar(bil, status="falhou", detalhe={"1aWin": "confirmada", "PS3838": "INCERTO — Odds have changed"})
    op = P.Operador(cfgc, est, casas)
    try:
        assert op.retomar_perna(bil["id"], P.CASA_1W)
        assert esperar(lambda: bil["status"] == "confirmado", 120), "\n".join(P._LOG_MEM[-30:])
        assert esperar(lambda: op._prot and op._prot.get("pares") == 1, 10)
        a = [c.executar(lambda c=c: c.page.evaluate("() => +sessionStorage.aceitas || 0")) for c in (casas[P.CASA_1W], casas[P.CASA_PS])]
        assert a[0] == 0 and a[1] == 1, a      # a 1aWin NÃO foi apostada de novo; só a cobertura na PS
        assert any("perna solta COBERTA" in l for l in P._LOG_MEM[-40:])
    finally:
        if op._atual:
            op._atual[1].set()
        time.sleep(1.5)
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)
        limpar_cupons(casas)


def test_sinal_novo_do_mesmo_jogo_carrega_os_pares_ja_confirmados(casas, cfgc):
    """23/11 (bilhetes #245/#250): depois de 'liberação manual' o mesmo jogo voltou por outro sinal e recomeçou do zero
    (entrou ilha e par sem total novo). Agora o monitoramento carrega os pares já confirmados do jogo: o MESMO par não
    entra de novo e a reentrada segue as regras."""
    cfgc["monitor_intervalo_s"] = 0.5
    cfgc["reentrar_mesmo_jogo"] = True
    servidor.ESTADO.clear()
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    ir_jogo(casas)
    bil = achar(casas, cfgc)
    bil.update(jogo=SINAL["jogo"], times=TIMES)
    est.registrar_bilhete(bil, "sinal")
    est.atualizar(bil, status="testado")
    op = P.Operador(cfgc, est, casas)
    try:
        op.novo_sinal(dict(SINAL, id="de-novo"))
        assert esperar(lambda: any("1 par(es) já confirmado(s) neste jogo" in l for l in P._LOG_MEM[-60:]), 30)
        assert esperar(lambda: op._prot and op._prot.get("pares") == 1, 10)
        time.sleep(4)
        # o mesmo par (identico) NUNCA entra de novo
        novos = [b for b in est.bilhetes if b["id"] != bil["id"] and b.get("status") == "testado"]
        assert all(not (b["pernas"][0]["linha"] == bil["pernas"][0]["linha"] and b["pernas"][1]["linha"] == bil["pernas"][1]["linha"])
                   for b in novos), novos
    finally:
        if op._atual:
            op._atual[1].set()
        time.sleep(1.5)
        limpar_cupons(casas)


def test_botao_desabilitado_clique_nao_saiu_nao_e_incerto(casas, cfgc, monkeypatch):
    """20/11 16:33/16:43/16:47: 'Locator.click: Timeout 1500ms exceeded' com o botão da 1aWin desabilitado. O registro
    tem 'attempting click action' (toda tentativa tem), mas NÃO 'performing click action': o clique não saiu. Era INCERTO
    e travava tudo; agora é 'não clicou' -> nada apostado, a PS não dispara."""
    from playwright.sync_api import Locator
    original = Locator.click

    def click_desabilitado(self, *a, **k):
        if "data-alvo-apostar" in str(self):
            raise Exception("Locator.click: Timeout 1500ms exceeded.\nCall log:\n  - waiting for locator('[data-alvo-apostar=\"1\"]').first"
                            "\n  - locator resolved to <button>Fazer uma aposta</button>\n  - attempting click action"
                            "\n  - waiting for element to be visible, enabled and stable\n  - element is not enabled")
        return original(self, *a, **k)
    cfgc["modo_teste"] = False
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    monkeypatch.setattr(Locator, "click", click_desabilitado)
    try:
        st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
    finally:
        monkeypatch.setattr(Locator, "click", original)
    assert st == "falhou", bil.get("detalhe")
    assert "não aconteceu" in bil["detalhe"]["1aWin"]
    for c in casas.values():
        assert "Aposta aceita" not in c.executar(c.texto)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)


def test_so_uma_casa_entrou_cobre_sozinho_sem_bloquear(casas, cfgc):
    """24/11 (operador): 'só foi em 1 casa, já pega e faz a correção automático'. A PS recusa (comprovado) 2x: a entrada e
    a recuperação imediata falham; o programa NÃO bloqueia — segue no jogo e cobre a perna sozinho na rodada seguinte."""
    cfgc["modo_teste"] = False
    cfgc["monitor_intervalo_s"] = 0.5
    cfgc["reentrar_mesmo_jogo"] = True
    cfgc["orfa_tentativas"] = 1
    servidor.ESTADO["rejeita_ps"] = 2
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    est = P.Estado(cfgc)
    op = P.Operador(cfgc, est, casas)
    try:
        op.novo_sinal(dict(SINAL, id="auto-orfa"))
        # (olha o ESTADO do programa, não o log — o log pode ter linhas de testes anteriores)
        assert esperar(lambda: op._prot and op._prot.get("perna_solta"), 120), "\n".join(P._LOG_MEM[-30:])
        assert op.bloqueio is None
        assert esperar(lambda: op._prot and op._prot.get("pares") == 1 and not op._prot.get("perna_solta"), 90), \
            "\n".join(P._LOG_MEM[-30:])
        assert op.bloqueio is None
    finally:
        if op._atual:
            op._atual[1].set()
        time.sleep(1.5)
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)
        limpar_cupons(casas)


def test_ps_marca_aceitar_melhores_probabilidades(casas, cfgc):
    """24/11 (operador): 'na PS a odd só alterando pra cima e demora pra aceitar'. O programa marca 'Aceitar melhores
    probabilidades' antes do disparo (a casa aceita sozinha odd MELHOR; odd que cai continua recusada)."""
    _, cps = ir_jogo(casas)
    recarregar(cps)
    limpar_cupons(casas)
    _, lp = ler(casas)
    o = [x for x in lp["ofertas"] if x["tipo"] == "total" and x["linha"] == 211.5 and x["lado"] == "acima"][0]
    p = dict(o, stake=31.0)
    assert cps.executar(cps.montar, p, TIMES)["ok"]
    assert cps.executar(lambda: cps.page.evaluate("() => document.getElementById('melhores').checked")) is False
    r = cps.executar(cps.ler_e_armar, p, True)
    assert r["ok"], r
    assert cps.executar(lambda: cps.page.evaluate("() => document.getElementById('melhores').checked")) is True
    assert any("Aceitar melhores probabilidades" in l for l in P._LOG_MEM[-10:])
    limpar_cupons(casas)


def test_odds_changed_junto_do_confirmar_nao_duplica_a_aposta(casas, cfgc):
    """21/11 14:18 (APOSTA DUPLICADA): a PS mostrou 'Odds have changed' junto da janela 'Confirmar Apostas'; o OK ENVIOU a
    aposta e o programa leu o aviso como recusa -> 'cobriu' de novo (5 na PS, 4 na 1aWin). Agora: 1 aposta em cada casa."""
    cfgc["modo_teste"] = False
    cfgc["confirmacao"] = "sequencial_aceita"
    servidor.ESTADO.update(modal_confirmar=True, aviso_no_modal=True)
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    est = P.Estado(cfgc)
    est.registrar_bilhete(bil, "sinal")
    try:
        st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
        a = [c.executar(lambda c=c: c.page.evaluate("() => +sessionStorage.aceitas || 0")) for c in (casas[P.CASA_1W], casas[P.CASA_PS])]
        assert st == "confirmado" and a == [1, 1], (st, a, bil["detalhe"], "\n".join(P._LOG_MEM[-20:]))
    finally:
        servidor.ESTADO.clear()
        for c in casas.values():
            recarregar(c)
        limpar_cupons(casas)


def test_hcp_time_trocado_no_cupom_aborta_na_barreira(casas, cfgc):
    """24/11: antes de confirmar, a barreira confere TIME, linha, odd... Se o cupom da 1aWin passar a mostrar o OUTRO time,
    nada é apostado (ABORTADO NA BARREIRA)."""
    cfgc["so_totais"] = False
    cfgc["handicap_ativo"] = True
    ir_jogo(casas)
    for c in casas.values():
        recarregar(c)
    limpar_cupons(casas)
    bil = achar(casas, cfgc)
    assert bil["tipo"] == "handicap"
    c1 = casas[P.CASA_1W]
    t_certo = bil["pernas"][0]["time"]
    t_outro = next(t for t in TIMES if not P._nome_bate(t, t_certo))
    original = c1.preparar

    def preparar_e_trocar_time(perna, times, *a):
        r = original(perna, times, *a)
        c1.page.evaluate("""([de, para]) => { const w = document.createTreeWalker(document.getElementById('slip'), NodeFilter.SHOW_TEXT);
            for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.includes(de)) n.nodeValue = n.nodeValue.split(de).join(para); }""",
                         [t_certo, t_outro])
        return r
    c1.preparar = preparar_e_trocar_time
    try:
        est = P.Estado(cfgc)
        est.registrar_bilhete(bil, "sinal")
        st = P.Operador(cfgc, est, casas).executar_bilhete(bil)
        assert st == "falhou", bil.get("detalhe")
        assert "ABORTADO NA BARREIRA" in bil["detalhe"]["1aWin"] and "time" in bil["detalhe"]["1aWin"], bil["detalhe"]
    finally:
        del c1.preparar
        for c in casas.values():
            recarregar(c)
        limpar_cupons(casas)
