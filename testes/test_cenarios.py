"""22/11 (operador): o cenário COMPLETO (preparar -> barreira -> disparos -> reconciliação) rodado 21 vezes no site falso,
alternando os modos de confirmação e os 7 casos pedidos. Invariante em todas: nunca duas apostas na mesma casa
(sem aposta duplicada) e nada aceito quando o resultado é 'falhou'. Gera o resumo dos tempos (RESUMO_CENARIOS=arquivo)."""
import json
import os

import pytest

import programa as P
import servidor
from conftest import SINAL, TIMES

RESUMO = []

# (nome, flags do site falso, {modo: (status esperado, aceitas 1aWin, aceitas PS)})
CENARIOS = [
    ("linha que desaparece", {"linha_sai_uma": 150},
     {"sequencial_processando": ("falhou", 0, 0), "rapida": ("falhou", 0, 0), "sequencial_aceita": ("falhou", 0, 0)}),
    ("odd que muda", {"odd_muda": 150},
     {"sequencial_processando": ("falhou", 0, 0), "rapida": ("falhou", 0, 0), "sequencial_aceita": ("falhou", 0, 0)}),
    # sequencial_aceita: a 1aWin recusou (comprovado) -> a PS nem dispara -> nada apostado
    ("1ª casa processa e recusa", {"processa_e_recusa": "uma"},
     {"sequencial_processando": ("confirmado", 1, 1), "rapida": ("confirmado", 1, 1), "sequencial_aceita": ("falhou", 0, 0)}),
    ("1ª casa aceita atrasada", {"aceita_atrasada": 2500},
     {"sequencial_processando": ("confirmado", 1, 1), "rapida": ("confirmado", 1, 1), "sequencial_aceita": ("confirmado", 1, 1)}),
    # a 1aWin recusa SEMPRE: a 1ª recusa é comprovada (texto novo) -> tenta UMA vez a perna que faltou; a 2ª recusa
    # repete o MESMO texto que já estava na tela -> não dá para provar que é nova -> INCERTO (congela, não repete mais)
    ("apenas a PS aceita", {"processa_e_recusa": "sempre"},
     {"sequencial_processando": ("incerto", 0, 1), "rapida": ("incerto", 0, 1), "sequencial_aceita": ("falhou", 0, 0)}),
    ("ambas aceitam", {},
     {"sequencial_processando": ("confirmado", 1, 1), "rapida": ("confirmado", 1, 1), "sequencial_aceita": ("confirmado", 1, 1)}),
    ("cupom some sem confirmação", {"limpa_sem_apostar": True},
     {"sequencial_processando": ("falhou", 0, 0), "rapida": ("incerto", 0, 1), "sequencial_aceita": ("falhou", 0, 0)}),
]
RODADAS = ["sequencial_processando", "rapida", "sequencial_processando", "sequencial_aceita", "sequencial_aceita"]
CASOS = [(r, m, c) for r, m in enumerate(RODADAS, 1) for c in CENARIOS]


def _aceitas(c):
    return c.executar(lambda: c.page.evaluate("() => +sessionStorage.aceitas || 0"))


def _recarregar_tudo(casas):
    for c in casas.values():
        c.executar(lambda c=c: c.page.evaluate("() => { try { sessionStorage.removeItem('aceitas'); } catch (e) {} }"))
        c.executar(lambda c=c: c.page.reload(wait_until="domcontentloaded"))
    for c in casas.values():
        c.executar(c.limpar_cupom)


@pytest.mark.parametrize("rodada,modo,cenario", CASOS, ids=["%d-%s-%s" % (r, m, c[0].replace(" ", "_")) for r, m, c in CASOS])
def test_cenario_completo(casas, rodada, modo, cenario):
    nome, flags, esperado = cenario
    cfg = casas[P.CASA_1W].cfg
    salvo = dict(cfg)
    try:
        cfg.update(modo_teste=False, confirmacao=modo, retentar_selecao_sumida=False, reentrar_mesmo_jogo=False)
        servidor.ESTADO.clear()
        servidor.ESTADO.update(flags)
        cps, c1 = casas[P.CASA_PS], casas[P.CASA_1W]
        assert cps.executar(cps.cacar, TIMES)["ok"] and c1.executar(c1.cacar, TIMES, "")["ok"]
        _recarregar_tudo(casas)
        l1, lp = c1.executar(c1.ler_escada, TIMES), cps.executar(cps.ler_escada, TIMES)
        bil, mot = P.achar_par(l1["ofertas"], lp["ofertas"], SINAL, cfg)
        assert bil, mot
        est = P.Estado(cfg)
        est.registrar_bilhete(bil, "sinal")
        st = P.Operador(cfg, est, casas).executar_bilhete(bil)
        a1, a2 = _aceitas(c1), _aceitas(cps)
        RESUMO.append({"rodada": rodada, "modo": modo, "cenario": nome, "status": st,
                       "estado": bil.get("estado_execucao"), "aceitas": [a1, a2],
                       "preparacao": bil.get("tempos_preparacao"), "disparos": bil.get("disparos")})
        st_esp, a1_esp, a2_esp = esperado[modo]
        assert a1 <= 1 and a2 <= 1, "aposta DUPLICADA (%s, %s)" % (a1, a2)
        assert (st, a1, a2) == (st_esp, a1_esp, a2_esp), (bil.get("detalhe"), "\n".join(P._LOG_MEM[-30:]))
    finally:
        cfg.clear()
        cfg.update(salvo)
        servidor.ESTADO.clear()
        _recarregar_tudo(casas)


# ------------------------------------------------------------------ sequencial_aceita: a 1aWin JÁ aceitou, e aí a PS…
_JS_ODD_OVERS = """(v) => { document.querySelectorAll('a.odds').forEach(a => { const p = a.id.split('|');
    if (p[1] === '0' && p[2] === '3' && p[3] === '3') a.querySelector('span').textContent = v; });
    const o = document.querySelector('.BetItemComponent .odds-info .odds'); if (o) o.textContent = ' ' + v; }"""


def _ps_some(cps, bil):
    aid = bil["pernas"][1]["_aid"]
    cps.page.evaluate("""(aid) => { const a = document.getElementById(aid); if (a) a.remove();
        const x = document.querySelector('.BetItemComponent .remove-icon'); if (x) x.click(); }""", aid)


# (nome, flags, mudança na PS logo depois da 1aWin aceita, cfg extra, (status, aceitas 1aWin, aceitas PS), linha final da PS)
CASOS_ACEITA = [
    ("PS: linha some -> cobertura alternativa", {}, _ps_some, {}, ("confirmado", 1, 1), 212.5),
    ("PS: odd cai abaixo de 1,78 -> perna órfã", {}, lambda cps, bil: cps.page.evaluate(_JS_ODD_OVERS, "1.700"), {},
     ("exposto", 1, 0), None),
    ("PS: custo passa da tabela -> perna órfã", {}, lambda cps, bil: cps.page.evaluate(_JS_ODD_OVERS, "1.790"),
     {"custo_por_janela": [[1, 11, 3.5]]}, ("exposto", 1, 0), None),
    ("PS recusa explicitamente -> recupera", {"rejeita_ps": 1}, None, {}, ("confirmado", 1, 1), 211.5),
    ("PS processando/INCERTA -> congela", {"ps_mudo": True}, None, {}, ("incerto", 1, 0), None),
    ("1aWin aceita atrasada, PS válida", {"aceita_atrasada": 2500}, None, {}, ("confirmado", 1, 1), 211.5),
    ("ambas aceitam", {}, None, {}, ("confirmado", 1, 1), 211.5),
]
CASOS_A = [(r, c) for r in (1, 2) for c in CASOS_ACEITA]


@pytest.mark.parametrize("rodada,caso", CASOS_A, ids=["%d-%s" % (r, c[0][:30].replace(" ", "_")) for r, c in CASOS_A])
def test_sequencial_aceita_ps_depois_da_1awin(casas, rodada, caso):
    nome, flags, mudanca, extra, esperado, linha_ps = caso
    cfg = casas[P.CASA_1W].cfg
    salvo = dict(cfg)
    cps, c1 = casas[P.CASA_PS], casas[P.CASA_1W]
    try:
        cfg.update(modo_teste=False, confirmacao="sequencial_aceita", retentar_selecao_sumida=False,
                   reentrar_mesmo_jogo=False, odd_min=1.78, **extra)
        servidor.ESTADO.clear()
        servidor.ESTADO.update(flags)
        assert cps.executar(cps.cacar, TIMES)["ok"] and c1.executar(c1.cacar, TIMES, "")["ok"]
        _recarregar_tudo(casas)
        l1, lp = c1.executar(c1.ler_escada, TIMES), cps.executar(cps.ler_escada, TIMES)
        bil, mot = P.achar_par(l1["ofertas"], lp["ofertas"], SINAL, cfg)
        assert bil, mot
        assert bil["pernas"][1]["linha"] == 211.5 and bil["pernas"][1]["lado"] == "acima"
        if mudanca:
            original = c1.apurar

            def apurar_e_mudar(perna=None, foto_ok=True):
                r = original(perna, foto_ok)
                if r["ok"] is True:   # a 1aWin ACEITOU: agora a PS muda, ANTES do disparo dela
                    cps.executar(mudanca, cps, bil)
                return r
            c1.apurar = apurar_e_mudar
        est = P.Estado(cfg)
        est.registrar_bilhete(bil, "sinal")
        st = P.Operador(cfg, est, casas).executar_bilhete(bil)
        a1, a2 = _aceitas(c1), _aceitas(cps)
        RESUMO.append({"rodada": "A%d" % rodada, "modo": "sequencial_aceita", "cenario": nome, "status": st,
                       "estado": bil.get("estado_execucao"), "aceitas": [a1, a2],
                       "preparacao": bil.get("tempos_preparacao"), "disparos": bil.get("disparos")})
        assert a1 <= 1 and a2 <= 1, "aposta DUPLICADA (%s, %s)" % (a1, a2)
        assert (st, a1, a2) == esperado, (bil.get("detalhe"), "\n".join(P._LOG_MEM[-30:]))
        if linha_ps is not None:
            assert bil["pernas"][1]["linha"] == linha_ps
        if st != "confirmado":
            assert bil["detalhe"]["1aWin"] == "confirmada" and bil["detalhe"]["PS3838"] != "confirmada"
    finally:
        c1.__dict__.pop("apurar", None)
        cfg.clear()
        cfg.update(salvo)
        servidor.ESTADO.clear()
        _recarregar_tudo(casas)


def test_zz_resumo_dos_cenarios():
    assert len(RESUMO) == len(CASOS) + len(CASOS_A)
    preps1 = [r["preparacao"]["1aWin"] for r in RESUMO if r["preparacao"] and r["preparacao"].get("1aWin") is not None]
    prepsp = [r["preparacao"]["PS3838"] for r in RESUMO if r["preparacao"] and r["preparacao"].get("PS3838") is not None]
    tot = [r["preparacao"]["total"] for r in RESUMO if r["preparacao"]]
    difs = [r["disparos"]["diferenca_ms"] for r in RESUMO if r["disparos"] and r["disparos"].get("diferenca_ms") is not None]
    resumo = {"execucoes": RESUMO,
              "preparacao_1awin_s": [min(preps1), sum(preps1) / len(preps1), max(preps1)] if preps1 else None,
              "preparacao_ps_s": [min(prepsp), sum(prepsp) / len(prepsp), max(prepsp)] if prepsp else None,
              "preparacao_total_s": [min(tot), sum(tot) / len(tot), max(tot)] if tot else None,
              "diferenca_disparos_ms": difs}
    arq = os.environ.get("RESUMO_CENARIOS")
    if arq:
        with open(arq, "w", encoding="utf-8") as f:
            json.dump(resumo, f, ensure_ascii=False, indent=1)
