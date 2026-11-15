# -*- coding: utf-8 -*-
"""
BOT OPERADOR DE MIDDLES — basquete, 1aWin (frágil) x PS3838 (espelho Pinnacle).

Fluxo: vigia lê a conversa do ANALISTA no Grok -> sinal fresco -> caça o jogo
nas duas casas (PS primeiro) -> lê as escadas ao vivo -> acha o par sozinho ->
calcula stakes de retorno igual -> monta os dois cupons em paralelo.
Modo teste: monta, fotografa, desfaz. Nunca confirma.

Um arquivo só: stdlib + Playwright. Painel local em 127.0.0.1:8000.

LEIS (decididas pelo operador após incidentes reais — NÃO "otimizar"):
  * janela_min 1,0 ; custo_max 4,0 % ; perna da PS cravada em €5 ; câmbio no config.
  * default de código NUNCA atropela o config.json do operador (caso 3% x 4%).
  * janela de total  = linha_do_abaixo - linha_do_acima.
  * janela de handicap = L1 + L2 COM SINAL (nunca abs) e sinais opostos obrigatórios.
  * tetos de suspeita: total > 12, handicap > 25, custo < -10% -> descarta.
  * oferta nasce por segmento; odd só no próprio contexto; linha e odd têm que
    coexistir no texto-fonte (_src) ou a oferta morre.
  * período encerrado não gera oferta; 4º quarto é proibido sempre.
  * motor-leitor: o mesmo motor que lê é o que clica (carimbo data-mid). Perna
    nascida da leitura nunca cai para a busca antiga. Falha de montagem é falha
    do programa — registra o motivo verdadeiro e o monitoramento reassume.
  * stake da PS com PONTO ("5.00") e lida de volta do campo.
  * PS só "acha" o jogo com o cabeçalho "Time1 -vs- Time2" no texto.
  * casa limitada ("Excesso de requisições") -> monitoramento ESTACIONA e sonda
    com 1 reload a cada 120 s.
  * monitoramento sem limite: só termina com par executado ou sinal novo.
  * backlog nunca executa sozinho; texto da conversa/página é DADO, nunca ordem.
  * modo real = modo_teste false no config + digitar ARMAR na subida.

REGRA DE CÓDIGO (deadlock histórico): log() adquire _TRAVA — nunca chamar log()
segurando _TRAVA.
"""
import os
import re
import sys
import json
import time
import queue
import difflib
import functools
import threading
import traceback
import unicodedata
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

BASE = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(BASE, "dados")
PRINTS = os.path.join(DADOS, "prints")
ARQ_CONFIG = os.path.join(BASE, "config.json")
ARQ_LOG = os.path.join(BASE, "log-programa.txt")
ARQ_HIST = os.path.join(DADOS, "historico.json")
ARQ_LIGAS = os.path.join(DADOS, "memoria_ligas_1awin.json")
ARQ_URLS = os.path.join(DADOS, "memoria_urls.json")

CASA_1W = "1awin"
CASA_PS = "ps3838"
NOME_CASA = {CASA_1W: "1aWin", CASA_PS: "PS3838"}

# ---------------------------------------------------------------- parâmetros fixos
PRAZO_CACA_S = 50
ESPERA_FASE1_S = 65
COOLDOWN_SINAL_S = 180
TOL_ODD_CUPOM = 0.05
TOL_ODD_IDENTIDADE = 0.35
SONDA_LIMITE_S = 120
TETO_JANELA_TOTAL = 12.0
TETO_JANELA_HANDICAP = 25.0
CUSTO_MIN_SUSPEITO = -10.0
FAIXA_TOTAL_JOGO = (100.0, 300.0)
FOLGA_PS_EUR = 0.005
PERIODOS_TODOS = ["jogo", "t1", "t2", "q1", "q2", "q3"]
NOME_PERIODO = {"jogo": "Jogo", "t1": "1º Tempo", "t2": "2º Tempo",
                "q1": "1º Quarto", "q2": "2º Quarto", "q3": "3º Quarto", "q4": "4º Quarto"}

# ---------------------------------------------------------------- travas e estado
_TRAVA = threading.RLock()          # estado + arquivo de log
_TRAVA_CLIQUES = threading.RLock()  # serializa OPERAÇÕES inteiras (não as casas de um bilhete)
_LOG_MEM = []


def agora():
    return datetime.now().strftime("%H:%M:%S")


def log(msg):
    """Escreve no console, no arquivo e na memória do painel.
    NUNCA chamar segurando _TRAVA (deadlock histórico)."""
    linha = "[%s] %s" % (agora(), msg)
    with _TRAVA:
        _LOG_MEM.append(linha)
        del _LOG_MEM[:-400]
        try:
            with open(ARQ_LOG, "a", encoding="utf-8") as f:
                f.write(datetime.now().strftime("%d/%m ") + linha + "\n")
        except Exception:
            pass
    try:
        print(linha, flush=True)
    except Exception:
        try:
            print(linha.encode("ascii", "replace").decode(), flush=True)
        except Exception:
            pass


# ---------------------------------------------------------------- config
DEFAULTS = {
    "janela_min": 1.0,
    "custo_max": 4.0,            # ordem do operador 13/11 14:54 (era 3,0)
    "perna_ps_eur": 5.0,         # 0 = volta ao total fixo
    "total_operacao": 600.0,
    "cambio_brl_eur": 6.20,
    "monitor_intervalo_s": 10,
    "modo_teste": True,
    "auto_aprovar": False,       # legado da era do painel
    "so_totais": True,           # restrição temporária 13/11 23:26 (false/null religa handicap)
    "totais_periodos": ["jogo", "q1", "q2", "q3"],  # idem (null = todos)
    "url_grok": "",
    "url_1awin": "https://1wcwlx.life",
    "url_ps3838": "https://www.ps3838.com",
    "caminho_lista_1awin": "/betting/live/basketball-23",
    "caminho_lista_ps": "/pt/sports/basketball",
    "navegador_exe": "",          # ex.: caminho do brave.exe/chrome.exe ("" = Chromium do Playwright)
    "canal_navegador": "",        # "chrome" / "msedge" (opcional)
    "headless": False,
    "porta": 8000,
    "seletor_cupom_1awin": "",
    "seletor_cupom_ps": "",
}


def carregar_config(caminho=None, avisar=True):
    """Lê o config.json. Default só preenche chave AUSENTE — nunca atropela o
    valor escrito pelo operador. false/null em so_totais/totais_periodos = religar."""
    caminho = caminho or ARQ_CONFIG
    bruto = {}
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8-sig") as f:
            bruto = json.load(f)
    cfg = dict(DEFAULTS)
    for k, v in bruto.items():
        cfg[k] = v
    # normalizações (sem trocar a ordem do operador)
    for k in ("janela_min", "custo_max", "perna_ps_eur", "total_operacao", "cambio_brl_eur"):
        cfg[k] = float(_num(cfg[k]) if isinstance(cfg[k], str) else cfg[k] or 0)
    cfg["monitor_intervalo_s"] = float(cfg.get("monitor_intervalo_s") or 10)
    cfg["modo_teste"] = cfg.get("modo_teste") is not False
    cfg["so_totais"] = bool(cfg.get("so_totais"))
    tp = cfg.get("totais_periodos")
    cfg["totais_periodos"] = list(PERIODOS_TODOS) if not tp else [str(p).lower() for p in tp if str(p).lower() != "q4"]
    if avisar:
        log("PARÂMETROS EFETIVOS: janela_min=%s custo_max=%s%% perna_ps=€%s total_fixo=R$%s câmbio=%s "
            "monitor=%ss modo_teste=%s so_totais=%s periodos=%s" % (
                _fmt(cfg["janela_min"], 1), _fmt(cfg["custo_max"], 1), _fmt(cfg["perna_ps_eur"]),
                _fmt(cfg["total_operacao"]), _fmt(cfg["cambio_brl_eur"]), int(cfg["monitor_intervalo_s"]),
                cfg["modo_teste"], cfg["so_totais"], ",".join(cfg["totais_periodos"])))
    return cfg


# ---------------------------------------------------------------- utilidades de texto/número
def _fmt(x, casas=2):
    try:
        return ("%." + str(casas) + "f") % float(x)
    except Exception:
        return str(x)


def _br(x, casas=2):
    return _fmt(x, casas).replace(".", ",")


def _num(s):
    """'5,00' -> 5.0 ; '1.613' -> 1.613 ; '1.234,56' -> 1234.56 ; '−3.5' -> -3.5"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("−", "-").replace("–", "-").replace(" ", "")
    m = re.search(r"[-+]?\d[\d.,]*", t)
    if not m:
        return None
    t = m.group(0)
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _numeros(txt):
    """Todos os números do texto (com sinal), como floats."""
    out = []
    for m in re.finditer(r"[-+−]?\d+(?:[.,]\d+)?", txt or ""):
        v = _num(m.group(0))
        if v is not None:
            out.append(v)
    return out


def _coexiste(valor, txt, usar_abs=False):
    for v in _numeros(txt):
        if abs((abs(v) if usar_abs else v) - (abs(valor) if usar_abs else valor)) < 0.001:
            return True
    return False


_LETRAS = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss",
                         "æ": "ae", "Æ": "AE", "ı": "i", "œ": "oe", "þ": "th", "ð": "d"})


def _sem_acento(s):
    s = unicodedata.normalize("NFKD", (s or "").translate(_LETRAS))
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm(s):
    s = _sem_acento(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


_ALIAS_TOKENS = {"sao": "san", "santo": "san", "saint": "san", "st": "san", "sankt": "san",
                 "lourenco": "lorenzo", "jose": "jose", "atletico": "atletico", "atl": "atletico",
                 # apelidos que a 1aWin traduz para o português (caso 'Ursos Stella Artois Leuven' = 'Leuven Bears')
                 "ursos": "bears", "urso": "bears", "aguias": "eagles", "aguia": "eagles", "leoes": "lions",
                 "leao": "lions", "lobos": "wolves", "lobo": "wolves", "tigres": "tigers", "tigre": "tigers",
                 "reis": "kings", "rei": "kings", "gigantes": "giants", "guerreiros": "warriors",
                 "dragoes": "dragons", "dragao": "dragons", "tubaroes": "sharks", "panteras": "panthers",
                 "falcoes": "hawks", "falcao": "hawks", "touros": "bulls", "cangurus": "kangaroos",
                 "piratas": "pirates", "cavaleiros": "knights", "raposas": "foxes", "corvos": "ravens",
                 "trovao": "thunder", "relampago": "lightning", "tempestade": "storm", "estrelas": "stars",
                 "gatos": "cats", "bufalos": "buffaloes", "cometas": "comets", "foguetes": "rockets",
                 "titas": "titans", "spartanos": "spartans", "vikings": "vikings", "herois": "heroes",
                 "aguias-reais": "eagles", "gaivotas": "gulls", "golfinhos": "dolphins", "abelhas": "bees",
                 "vespas": "hornets", "magos": "wizards", "selvagens": "savages", "dragoes-azuis": "dragons",
                 # cidades que a 1aWin escreve em português (caso 'Saragoça' = 'Zaragoza')
                 "saragoca": "zaragoza", "corunha": "coruna", "bilbau": "bilbao", "sevilha": "sevilla",
                 "milao": "milano", "milan": "milano", "turim": "torino", "turin": "torino", "florenca": "firenze",
                 "florence": "firenze", "veneza": "venezia", "venice": "venezia", "napoles": "napoli",
                 "naples": "napoli", "genova": "genoa", "munique": "munchen", "munich": "munchen",
                 "colonia": "koln", "cologne": "koln", "varsovia": "warszawa", "warsaw": "warszawa",
                 "cracovia": "krakow", "praga": "praha", "prague": "praha", "viena": "wien", "vienna": "wien",
                 "belgrado": "beograd", "belgrade": "beograd", "atenas": "athens", "athina": "athens",
                 "moscou": "moskva", "moscovo": "moskva", "moscow": "moskva", "estocolmo": "stockholm",
                 "copenhague": "kobenhavn", "copenhaga": "kobenhavn", "copenhagen": "kobenhavn",
                 "bruxelas": "brussels", "bruxelles": "brussels", "antuerpia": "antwerp", "antwerpen": "antwerp",
                 "istambul": "istanbul", "londres": "london", "lisboa": "lisbon", "genebra": "geneve",
                 "geneva": "geneve", "zurique": "zurich", "basileia": "basel", "berlim": "berlin",
                 "hamburgo": "hamburg", "estrasburgo": "strasbourg", "marselha": "marseille", "lyon": "lyon",
                 "tessalonica": "thessaloniki", "salonica": "thessaloniki", "liubliana": "ljubljana",
                 "zagrebe": "zagreb", "sofia": "sofia", "bucareste": "bucuresti", "bucharest": "bucuresti",
                 "kaunas": "kaunas", "vilnius": "vilnius", "riga": "riga", "tallinn": "tallinn",
                 "jerusalem": "jerusalem", "telavive": "tel aviv"}
_MARCA_IDENTIDADE = [
    (re.compile(r"\b(w|f|women|woman|fem|feminino|feminina|femenino|womens|ladies|damen)\b"), "fem"),
    (re.compile(r"\b(u\d{2}|sub\s?\d{2}|youth|junior|juniors|juvenil|base)\b"), "base"),
    (re.compile(r"\b(3x3)\b"), "3x3"),
    (re.compile(r"\b(b|ii|2)\b"), "reserva"),
]


def _marcas(s):
    n = _norm(s)
    return {nome for rx, nome in _MARCA_IDENTIDADE if rx.search(n)}


# palavras que quase todo time tem — NUNCA identificam um time (caso 14/11: "Joensuun Kataja
# Basket" bateu com "Fryshuset Basketbollklubb" só por "basket")
_GENERICOS = {"club", "clube", "klub", "klubb", "spor", "kulubu", "sport", "sports", "team", "basquete",
              "baloncesto", "pallacanestro", "kosarka", "kosarkaski", "koszykowka", "sportclub", "sportverein",
              "athletic", "atletico", "esporte", "esportes", "associacao", "associazione", "societa",
              "bc", "bk", "bbk", "kk", "kkk", "ks", "sk", "sc", "fc", "ac", "cb", "cd", "ud", "bv", "tv", "vfl",
              "women", "feminino", "feminina", "fem", "ladies",
              "saint", "santa", "state", "city", "united", "real", "sporting", "union", "uniao", "deportivo",
              "university", "universidad", "college", "municipal"}
# prefixos que dizem QUAL clube da cidade (Hapoel x Maccabi Tel Aviv): diferentes = time diferente
_PREFIXOS_CLUBE = {"hapoel", "maccabi", "ironi", "bnei", "elitzur", "beitar", "dinamo", "dynamo", "lokomotiv",
                   "spartak", "crvena", "torpedo", "olimpija", "olimpia"}


def _generico(t):
    return t in _GENERICOS or t in _PREFIXOS_CLUBE or t.startswith(("basket", "basquet", "koszyk", "kosark"))


@functools.lru_cache(maxsize=50000)
def _norm_paises(n):
    """Seleções: 'Islândia (f)' = 'Iceland Women' — país em PT vira EN antes de comparar."""
    for pt, en in _PAISES_ORDEM():
        n = re.sub(r"\b" + pt + r"\b", en, n)
    return n


def _tokens(s):
    toks = [_ALIAS_TOKENS.get(t, t) for t in _norm_paises(_norm(s)).split()]
    return [t for t in toks if (len(t) >= 4 or t == "san") and not _generico(t)]


def _iniciais(s):
    return "".join(t[0] for t in _norm(s).split() if len(t) >= 3 and not _generico(t))


def _sigla_bate(a, b):
    """'Co Basketbol' x 'Cedi Osman Basketbol': a sigla curta de um = iniciais seguidas do outro."""
    for w in _norm(a).split():
        if 2 <= len(w) <= 4 and not _generico(w) and len(w) <= len(_iniciais(b)) and w in _iniciais(b) \
                and not [t for t in _tokens(a) if len(t) >= 4]:
            return True
    return False


def pistas_nome(nome, tam=5):
    """Pedaços curtos (5 letras) das palavras próprias do time, em EN e PT (seleções), para o
    filtro rápido dentro da página. Lista vazia = sem filtro (nome só com sigla/genérico)."""
    out = set()
    n = _norm(nome)
    for t in _tokens(nome):
        if len(t) >= 4:
            out.add(t[:tam])
            for k, v in _ALIAS_TOKENS.items():  # 'lorenzo' também procura 'lourenco'
                if v == t and len(k) >= 4:
                    out.add(k[:tam])
    if tam <= 3:  # frouxo: também as siglas/palavras curtas ('Co Basketbol', 'LA')
        for w in n.split():
            if 2 <= len(w) <= 4 and not _generico(w):
                out.add(w[:tam])
    inv = {en: pt for pt, en in _PAISES.items()}
    for en, pt in inv.items():
        if re.search(r"\b" + en + r"\b", _norm_paises(n)):
            out.add(pt.split()[0][:tam])
    for pt in _PAISES:
        if re.search(r"\b" + pt + r"\b", n):
            out.add(pt.split()[0][:tam])
    return sorted(out)


def _token_bate(a, b):
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    # parecido só vale com a mesma inicial ('lorenzo'~'lourenco' sim; 'eagles'~'angeles' NÃO — caso 14/11)
    return a[:1] == b[:1] and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


@functools.lru_cache(maxsize=100000)
def _nome_bate(a, b, leniente=False):
    """Casamento tolerante de nomes de time entre casas. Nome é pista, nunca chave.
    Feminino/base/3x3/reserva fazem parte da identidade."""
    if not a or not b:
        return False
    ma, mb = _marcas(a), _marcas(b)
    if ma != mb:
        # leniente: a casa OMITIU a marca de um lado (PS: 'Alges e Dafundo' para 'Alges Women');
        # marca diferente dos dois lados continua barrada
        if not (leniente and (not ma or not mb) and (ma | mb) <= {"fem"}):
            return False
    pa = {t for t in _norm(a).split() if t in _PREFIXOS_CLUBE}
    pb = {t for t in _norm(b).split() if t in _PREFIXOS_CLUBE}
    if pa and pb and pa != pb:
        return False  # Hapoel Tel Aviv ≠ Maccabi Tel Aviv
    na, nb = _norm_paises(_norm(a)), _norm_paises(_norm(b))
    if na == nb:
        return True
    ta = [t for t in _tokens(a) if t != "san"] or _tokens(a)
    tb = [t for t in _tokens(b) if t != "san"] or _tokens(b)
    if not ta or not tb:
        if _sigla_bate(a, b) or _sigla_bate(b, a):
            return True
    if ta and tb:
        menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        bate = sum(1 for t in menor if any(_token_bate(t, u) for u in maior))
        # os dois com 2+ palavras próprias: uma só em comum (a cidade) não basta
        # (Los Angeles Lakers ≠ Los Angeles Clippers)
        # (leniente = 2ª passada: uma palavra própria em comum basta, desde que os DOIS times batam)
        minimo = 2 if len(ta) >= 2 and len(tb) >= 2 and not leniente else 1
        if bate * 2 >= len(menor) and bate >= minimo:
            return True
        if minimo == 2:
            return False
    return na[:1] == nb[:1] and difflib.SequenceMatcher(None, na, nb).ratio() >= 0.75


def _limpar(txt):
    """Remove tags <file-mention>…</file-mention> e qualquer tag HTML que o Grok
    grude no texto (caso Caxias do Sul x Pato)."""
    t = txt or ""
    t = re.sub(r"<file-mention[^>]*>.*?</file-mention>", " ", t, flags=re.S | re.I)
    t = re.sub(r"</?file-mention[^>]*>", " ", t, flags=re.I)
    t = re.sub(r"<[^>\n]{1,200}>", " ", t)
    t = t.replace(" ", " ").replace("−", "-")
    return t


def ler_valor_reais(txt, cambio):
    """'apostar R$ 31,00' -> 31.0 ; 'apostar 5,00 EUR' -> 5*câmbio (caso Caxias)."""
    t = _limpar(txt)
    m = re.search(r"(\d[\d.,]*)\s*(?:eur|€)", t, re.I) or re.search(r"(?:€|eur)\s*(\d[\d.,]*)", t, re.I)
    if m:
        return round(_num(m.group(1)) * cambio, 2)
    m = re.search(r"R\$\s*(\d[\d.,]*)", t) or re.search(r"(\d[\d.,]*)\s*(?:reais|brl)", t, re.I)
    if m:
        return _num(m.group(1))
    return None


# ---------------------------------------------------------------- períodos e relógio
_RX_QUARTO = re.compile(r"\b([1-4])\s*(?:º|°|ª|o|st|nd|rd|th)?\s*(?:quarto|quarter|qtr|periodo|period)\b")
_RX_Q = re.compile(r"\bq([1-4])\b")
_RX_TEMPO = re.compile(r"\b([12])\s*(?:º|°|ª|o|a|st|nd)?\s*(?:tempo|metade|half)\b")
_RX_JOGO = re.compile(r"\b(jogo|partida|match|game|full ?time|tempo regulamentar|incl(?:uindo|\.)? prorrog\w*|ot incl\w*)\b")


def periodo_de_texto(txt):
    """Período de um cabeçalho de seção: 'jogo','t1','t2','q1'..'q4' ou None."""
    if not txt:
        return None
    t = _sem_acento(txt).lower().replace("º", " ").replace("°", " ")
    t = re.sub(r"(\d)\s*(o|a)\b", r"\1 ", t)
    m = _RX_QUARTO.search(t) or _RX_Q.search(t)
    if m:
        return "q" + m.group(1)
    m = _RX_TEMPO.search(t)
    if m:
        return "t" + m.group(1)
    if re.search(r"\b(primeiro|first)\s+(tempo|half)\b", t):
        return "t1"
    if re.search(r"\b(segundo|second)\s+(tempo|half)\b", t):
        return "t2"
    if _RX_JOGO.search(t):
        return "jogo"
    return None


_RX_RELOGIO = re.compile(r"\b([1-4])\s*[º°ªo]?\s*(?:quarto|quarter|q)\b\s*[-–:]?\s*(\d{1,2})\s*['’`´]", re.I)
_RX_RELOGIO2 = re.compile(r"\bq([1-4])\s*[-–:]?\s*(\d{1,2})\s*['’`´]", re.I)
_RX_RELOGIO_H = re.compile(r"\bh([12])\s*[-–:]?\s*(\d{1,2})\s*['’`´]", re.I)  # PS: 'H2 15'' (NCAA)
_RX_RELOGIO_TEMPO = re.compile(r"\b([12])\s*[º°ªo]?\s*(?:tempo|half|metade)\b\s*[-–:]?\s*(\d{1,2})\s*['’`´]", re.I)
_RX_INTERVALO = re.compile(r"\b(intervalo|half ?time|halftime)\b", re.I)


def quarto_atual(texto_pagina):
    """Lê o relógio 'Nº Quarto M'' do texto da página. None = desconhecido."""
    t = _sem_acento(texto_pagina or "")
    m = _RX_RELOGIO.search(t) or _RX_RELOGIO2.search(t)
    if m:
        return int(m.group(1))
    m = _RX_RELOGIO_TEMPO.search(t) or _RX_RELOGIO_H.search(t)  # jogo em tempos (NCAA): 2º tempo = 1º tempo encerrado
    if m:
        return 1 if m.group(1) == "1" else 3
    if _RX_INTERVALO.search(t):
        return 2.5
    return None


def periodos_encerrados(q):
    """No 2º quarto morre o 1º; no 3º morrem 1º/2º e o 1º tempo; no 4º também o 3º."""
    if q is None:
        return set()
    mortos = set()
    if q >= 2:
        mortos.add("q1")
    if q >= 2.5:
        mortos |= {"q2", "t1"}
    if q >= 4:
        mortos.add("q3")
    return mortos


# ---------------------------------------------------------------- sinais (vigia)
_RX_SINAL = re.compile(r"SINAL\s+DE\s+MIDDLE", re.I)


def _campo(bloco, nome):
    m = re.search(r"^\s*[*_]*" + nome + r"[*_]*\s*:\s*[*_]*\s*(.+?)\s*$", bloco, re.I | re.M)
    return m.group(1).strip(" *_") if m else ""


def _separar_times(jogo):
    partes = re.split(r"\s+(?:x|vs\.?|v|-vs-|—|–)\s+", jogo.strip(), maxsplit=1, flags=re.I)
    if len(partes) == 2 and partes[0].strip() and partes[1].strip():
        return partes[0].strip(), partes[1].strip()
    return None


def extrair_sinais(texto):
    """Lista ordenada de sinais na conversa: dicts {id, jogo, times, liga, mercado}."""
    t = _limpar(texto)
    pos = [m.start() for m in _RX_SINAL.finditer(t)]
    out = []
    for i, p in enumerate(pos):
        fim = pos[i + 1] if i + 1 < len(pos) else len(t)
        bloco = t[p:fim]
        # o bloco termina na próxima mensagem de RESULTADO, se houver
        mr = re.search(r"\bRESULTADO\b", bloco[10:], re.I)
        if mr:
            bloco = bloco[:10 + mr.start()]
        jogo = _campo(bloco, "Jogo")
        if not jogo:
            continue
        times = _separar_times(jogo)
        if not times:
            continue
        liga = _campo(bloco, "Liga")
        mercado = _campo(bloco, r"Mercado(?:\s+do\s+sinal)?")
        ident = _norm(jogo) + "|" + _norm(mercado)
        out.append({"id": ident, "jogo": "%s x %s" % times, "times": list(times),
                    "liga": liga, "mercado": mercado})
    return out


_DESFECHOS = ["DEVOLVIDA", "MEIO", "GANHOU", "PERDEU", "CANCELADA"]


def extrair_resultados(texto):
    """Mensagens de RESULTADO só alimentam o histórico — nunca viram clique."""
    t = _limpar(texto)
    out = []
    for m in re.finditer(r"RESULTADO[^\n]*\n?(.{0,400})", t, re.I | re.S):
        bloco = m.group(0)
        jogo = _campo(bloco, "Jogo")
        desf = ""
        for d in _DESFECHOS:
            if re.search(r"\b" + d + r"\b", bloco, re.I):
                desf = d
                break
        if jogo and desf:
            out.append({"jogo": jogo, "desfecho": desf, "id": _norm(jogo) + "|" + desf})
    return out


# ---------------------------------------------------------------- matemática do par
def _stakes_iguais(total, odd1, odd2):
    """Retorno igual sobre um total fixo."""
    s1 = total * odd2 / (odd1 + odd2)
    return round(s1, 2), round(total - s1, 2)


def _calcular(oferta_1w, oferta_ps, cfg):
    """Stakes, retorno e custo de um par (perna 1aWin, perna PS)."""
    o1, o2 = oferta_1w["odd"], oferta_ps["odd"]
    cambio = cfg["cambio_brl_eur"]
    if cfg["perna_ps_eur"] > 0:
        s_ps = round(cfg["perna_ps_eur"] * cambio, 2)
        s_1w = round(s_ps * o2 / o1, 2)
    else:
        s_1w, s_ps = _stakes_iguais(cfg["total_operacao"], o1, o2)
    total = round(s_1w + s_ps, 2)
    retorno = min(s_1w * o1, s_ps * o2)
    custo = (total - retorno) / total * 100 if total > 0 else 999.0
    return {"stake_1w": s_1w, "stake_ps": s_ps, "total": total,
            "retorno": round(retorno, 2), "custo": round(custo, 3),
            "ps_eur": round(s_ps / cambio, 3)}


def _janela_total(a, b):
    """(acima, abaixo) -> janela = linha_do_abaixo - linha_do_acima."""
    return round(b["linha"] - a["linha"], 3)


def _janela_handicap(a, b):
    """Linhas de times diferentes, cada uma na perspectiva do próprio time.
    Janela = L1 + L2 COM SINAL. Mesmo sinal = leitura suspeita (None)."""
    l1, l2 = a["linha"], b["linha"]
    if (l1 > 0 and l2 > 0) or (l1 < 0 and l2 < 0) or l1 == 0 or l2 == 0:
        return None
    return round(l1 + l2, 3)


def descricao_oferta(o):
    per = NOME_PERIODO.get(o.get("periodo"), o.get("periodo"))
    if o["tipo"] == "total":
        return "%s de %s (%s)" % ("Acima" if o["lado"] == "acima" else "Abaixo", _fmt_linha(o["linha"]), per)
    return "%s %s%s (%s)" % (o.get("time") or "time", "+" if o["linha"] > 0 else "", _fmt_linha(o["linha"]), per)


def _fmt_linha(x):
    return ("%.1f" % x) if abs(x * 2 - round(x * 2)) < 1e-9 else ("%.2f" % x)


def achar_par(esc_1w, esc_ps, sinal, cfg, raiox=None):
    """Pareia as escadas reais das duas casas. Retorna (bilhete, None) ou (None, motivo).
    Varre todos os mercados/períodos permitidos — o sinal é só a pista."""
    permitidos = set(cfg.get("totais_periodos") or PERIODOS_TODOS) - {"q4"}
    so_totais = cfg.get("so_totais")
    candidatos = []
    suspeitos = 0
    melhor_janela = None
    menor_custo_ok = None

    def filtra(esc):
        out = []
        for o in esc or []:
            if o.get("periodo") == "q4":
                continue
            if o["tipo"] == "total" and o.get("periodo") not in permitidos:
                continue
            if o["tipo"] == "handicap" and so_totais:
                continue
            out.append(o)
        return out

    e1, e2 = filtra(esc_1w), filtra(esc_ps)
    for a in e1:
        for b in e2:
            if a["tipo"] != b["tipo"] or a["periodo"] != b["periodo"]:
                continue
            if a["tipo"] == "total":
                if a["lado"] == b["lado"]:
                    continue
                acima, abaixo = (a, b) if a["lado"] == "acima" else (b, a)
                jan = _janela_total(acima, abaixo)
                if jan > TETO_JANELA_TOTAL:
                    suspeitos += 1
                    continue
            else:
                if a.get("time_idx") is None or b.get("time_idx") is None or a["time_idx"] == b["time_idx"]:
                    continue
                jan = _janela_handicap(a, b)
                if jan is None:
                    suspeitos += 1
                    continue
                if jan > TETO_JANELA_HANDICAP:
                    suspeitos += 1
                    continue
            c = _calcular(a, b, cfg)
            if c["custo"] < CUSTO_MIN_SUSPEITO:
                suspeitos += 1
                continue
            melhor_janela = jan if melhor_janela is None else max(melhor_janela, jan)
            if jan >= cfg["janela_min"]:
                menor_custo_ok = c["custo"] if menor_custo_ok is None else min(menor_custo_ok, c["custo"])
            ps_ok = c["ps_eur"] + FOLGA_PS_EUR >= (cfg["perna_ps_eur"] or 5.0)
            valido = jan >= cfg["janela_min"] and c["custo"] <= cfg["custo_max"] and ps_ok
            candidatos.append((valido, jan, c, a, b))

    validos = [x for x in candidatos if x[0]]
    restricao = ""
    if so_totais:
        restricao = " [restrição ativa: só totais %s; handicap desligado]" % "/".join(
            p for p in PERIODOS_TODOS if p in permitidos)
    if validos:
        validos.sort(key=lambda x: (-x[1], x[2]["custo"]))
        _, jan, c, a, b = validos[0]
        bil = _montar_bilhete(sinal, a, b, jan, c)
        if raiox is not None:
            raiox.update({"valido": True, "par": bil, "motivo": "par válido",
                          "melhor_janela": melhor_janela, "menor_custo": menor_custo_ok,
                          "suspeitos": suspeitos})
        return bil, None

    motivo = "sem par: %d combinações; melhor janela %s; menor custo com janela ok %s; %d suspeito(s) descartado(s)%s" % (
        len(candidatos),
        _br(melhor_janela, 1) if melhor_janela is not None else "—",
        (_br(menor_custo_ok, 2) + "%") if menor_custo_ok is not None else "—",
        suspeitos, restricao)
    if not e1 or not e2:
        motivo = "sem par: escada vazia em %s%s" % (
            " e ".join(n for n, e in ((NOME_CASA[CASA_1W], e1), (NOME_CASA[CASA_PS], e2)) if not e), restricao)
    if raiox is not None:
        quase = [x for x in candidatos if x[1] >= cfg["janela_min"]]
        quase.sort(key=lambda x: (x[2]["custo"], -x[1]))
        raiox.update({"valido": False, "motivo": motivo, "melhor_janela": melhor_janela,
                      "menor_custo": menor_custo_ok, "suspeitos": suspeitos,
                      "par": _montar_bilhete(sinal, quase[0][3], quase[0][4], quase[0][1], quase[0][2]) if quase else None})
    return None, motivo


def _limpa_oferta(o, casa, stake):
    p = {k: v for k, v in o.items()}
    p["casa"] = casa
    p["stake"] = stake
    p["selecao"] = descricao_oferta(o)
    return p


def _montar_bilhete(sinal, a, b, jan, c):
    return {
        "jogo": (sinal or {}).get("jogo", ""),
        "times": (sinal or {}).get("times", []),
        "liga": (sinal or {}).get("liga", ""),
        "pernas": [_limpa_oferta(a, CASA_1W, c["stake_1w"]), _limpa_oferta(b, CASA_PS, c["stake_ps"])],
        "janela": jan, "custo": c["custo"], "total": c["total"], "retorno": c["retorno"],
        "ps_eur": c["ps_eur"], "tipo": a["tipo"], "periodo": a["periodo"],
    }


def cenarios(bil):
    """Os 3 cenários em R$: só acima ganha / só abaixo ganha / caiu no meio.
    Para handicap: só perna 1 / só perna 2 / meio."""
    p1, p2 = bil["pernas"]
    r1, r2 = p1["stake"] * p1["odd"], p2["stake"] * p2["odd"]
    tot = bil["total"]
    if bil["tipo"] == "total":
        ac, ab = (p1, p2) if p1["lado"] == "acima" else (p2, p1)
        rac, rab = (r1, r2) if p1["lado"] == "acima" else (r2, r1)
        return [
            {"nome": "só o acima ganha (passou de %s)" % _fmt_linha(ab["linha"]), "valor": round(rac - tot, 2)},
            {"nome": "só o abaixo ganha (ficou abaixo de %s)" % _fmt_linha(ac["linha"]), "valor": round(rab - tot, 2)},
            {"nome": "caiu no meio (as duas ganham)", "valor": round(rac + rab - tot, 2)},
        ]
    return [
        {"nome": "só %s ganha" % p1["selecao"], "valor": round(r1 - tot, 2)},
        {"nome": "só %s ganha" % p2["selecao"], "valor": round(r2 - tot, 2)},
        {"nome": "caiu no meio (as duas ganham)", "valor": round(r1 + r2 - tot, 2)},
    ]


# ---------------------------------------------------------------- MOTOR-LEITOR (JS)
SEL_CUPOM_PADRAO = {
    CASA_1W: '[data-scope="Betslip"], [class*="coupon" i], [class*="betslip" i], [class*="bet-slip" i], [class*="cupom" i]',
    CASA_PS: '[class*="betslip" i], [class*="bet-slip" i], [class*="boletim" i], [id*="betslip" i], [class*="coupon" i]',
}

# Enumera a página: carimba cada elemento clicável que carrega UMA odd com data-mid
# sequencial (limpando os carimbos da varredura anterior). Devolve, por botão:
# texto do botão, segmento (maior contêiner que só tem ESTA odd = _src), linha de
# tabela + cabeçalho da coluna, e a pilha de cabeçalhos de seção (mais perto primeiro).
JS_ENUMERAR = r"""
(selCupom) => {
  document.querySelectorAll('[data-mid]').forEach(e => e.removeAttribute('data-mid'));
  const ODD = /^\s*\d{1,2}[.,]\d{1,3}\s*$/;
  const cup = selCupom ? Array.from(document.querySelectorAll(selCupom)) : [];
  const noCupom = e => cup.some(c => c.contains(e));
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    for (let x = e; x && x !== document.body; x = x.parentElement) {
      const s = getComputedStyle(x);
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    }
    return true;
  };
  const folhas = [];
  for (const e of document.body.querySelectorAll('*')) {
    if (['SCRIPT','STYLE','INPUT','TEXTAREA','OPTION','SELECT'].includes(e.tagName)) continue;
    const t = (e.textContent || '').trim();
    if (!ODD.test(t)) continue;
    let filhoIgual = false;
    for (const c of e.children) { if (ODD.test((c.textContent||'').trim())) { filhoIgual = true; break; } }
    if (filhoIgual) continue;
    const v = parseFloat(t.replace(',', '.'));
    if (!(v >= 1.01 && v <= 30)) continue;
    if (noCupom(e) || !vis(e)) continue;
    if ((e.closest('[aria-disabled="true"],[disabled],.locked,.blocked,.suspended'))) continue;
    folhas.push(e);
  }
  // mesmo botão com dois números "com cara de odd" (ex.: linha 4.5 + odd 1.980): a odd é o ÚLTIMO
  for (let i = folhas.length - 1; i >= 0; i--) {
    const b = folhas[i].closest('a, button, [role=button]');
    if (b && folhas.some((g, k) => k > i && b.contains(g))) folhas.splice(i, 1);
  }
  const conta = el => folhas.reduce((n, f) => n + (el.contains(f) ? 1 : 0), 0);
  const clicavel = e => {
    if (['BUTTON','A','TD'].includes(e.tagName)) return true;
    const r = e.getAttribute('role');
    if (r === 'button' || r === 'option' || e.hasAttribute('onclick') || e.hasAttribute('data-odd')) return true;
    return getComputedStyle(e).cursor === 'pointer' && (!e.parentElement || getComputedStyle(e.parentElement).cursor !== 'pointer');
  };
  const curto = s => (s || '').replace(/\s+/g, ' ').trim();
  const cabecalhos = alvo => {
    const hs = [];
    let filho = alvo;
    // barra de navegação (abas) não é cabeçalho de seção: tem vários botões/abas dentro
    const eBarra = e => e.querySelectorAll('button, [role=tab], a').length >= 3;
    for (let a = alvo.parentElement, n = 0; a && a !== document.body && n < 14; filho = a, a = a.parentElement, n++) {
      const nivel = [];
      for (const irm of a.children) {
        if (irm === filho || irm.contains(alvo)) break;
        const tx = curto(irm.innerText);
        if (!tx || tx.length > 90) continue;
        if (folhas.some(f => irm.contains(f)) || eBarra(irm)) continue;
        nivel.push(tx);
      }
      if (!nivel.length) {
        const h = a.getAttribute('aria-label') || a.getAttribute('data-title') || '';
        if (h && h.length <= 90) nivel.push(curto(h));
      }
      for (const tx of nivel.reverse().slice(0, 2)) hs.push(tx);  // o mais perto primeiro
    }
    return hs;
  };
  const out = [];
  let mid = 0;
  for (const f of folhas) {
    let alvo = f;
    for (let a = f, n = 0; a && a !== document.body && n < 5; a = a.parentElement, n++) {
      if (conta(a) > 1) break;
      if (clicavel(a)) { alvo = a; break; }
    }
    let seg = alvo;
    while (seg.parentElement && seg.parentElement !== document.body && conta(seg.parentElement) === 1
           && !['TR','TBODY','TABLE'].includes(seg.parentElement.tagName)) seg = seg.parentElement;
    let linha = null, col = '', ci = -1, hdrs = null;
    const td = f.closest('td');
    if (td) {
      const tr = td.parentElement;
      linha = Array.from(tr.children).map(c => curto(c.innerText));
      ci = Array.from(tr.children).indexOf(td);
      const tab = td.closest('table');
      let hr = tab && tab.tHead && tab.tHead.rows[0];
      if (!hr && tab) hr = tab.rows[0] !== tr ? tab.rows[0] : null;
      if (hr) { hdrs = Array.from(hr.cells).map(c => curto(c.innerText)); col = hdrs[ci] || ''; }
    }
    // PS: o <a> da odd carrega id "evento|período|mercado|lado|alt|linha"
    const a = f.closest('a[id]') || alvo.querySelector('a[id]');
    const aid = a && /^\d+\|\d+\|\d+\|\d+\|/.test(a.id) ? a.id : '';
    const caixa = a ? a.parentElement : null;
    const travado = !!(caixa && caixa.querySelector(':scope > .style-lock:not(.lock-hidden)'));
    const tr = f.closest('tr');
    const nt = tr && tr.querySelector('[data-home-team]');
    const casaTimes = nt ? [nt.getAttribute('data-home-team'), nt.getAttribute('data-away-team')] : null;
    mid += 1;
    alvo.setAttribute('data-mid', String(mid));
    out.push({mid: String(mid), odd: curto(f.textContent), txt: curto(alvo.innerText),
              seg: curto(seg.innerText), linha, col, ci, hdrs, aid, travado, casa_times: casaTimes,
              heads: cabecalhos(td ? td.closest('table') || alvo : seg)});
  }
  return out;
}
"""

JS_ITENS_CUPOM = r"""
([sc, si]) => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cups = Array.from(document.querySelectorAll(sc));
  const out = [];
  if (si) for (const c of cups) for (const e of c.querySelectorAll(si)) if (vis(e) && !out.some(o => o.contains(e))) out.push(e);
  document.querySelectorAll('[data-item-cupom]').forEach(e => e.removeAttribute('data-item-cupom'));
  if (!out.length) {
    const MERC = /(acima|abaixo|mais|menos|over|under|handicap|total|-vs-|\s[-—]\s)/i;
    const ODD = /(^|\s)(\d{1,2}[.,]\d{2,3})(\s|$)/;
    const cand = [];
    for (const c of cups) for (const e of c.querySelectorAll('*')) {
      if (!vis(e) || e.tagName === 'INPUT') continue;
      const t = (e.innerText || '').trim();
      if (!t || t.length > 400) continue;
      if (/evento exclu[ií]do|event (excluded|removed)/i.test(t)) { cand.push(e); continue; }  // seleção morta também é seleção
      const m = t.match(ODD);
      if (!m || parseFloat(m[2].replace(',', '.')) < 1.01) continue;
      if (!MERC.test(t)) continue;
      if (/selecione um resultado|cota[cç][oõ]es a partir|b[oô]nus|possível vitória|quantia/i.test(t)) continue;
      cand.push(e);
    }
    for (const e of cand) if (!cand.some(o => o !== e && e.contains(o))) {
      // sobe até o cartão: o maior ancestral que ainda contém só esta seleção
      let card = e;
      while (card.parentElement && !cups.includes(card.parentElement) &&
             !card.parentElement.querySelector('input') &&
             cand.filter(o => card.parentElement.contains(o) && !cand.some(x => x !== o && o.contains(x))).length === 1 &&
             (card.parentElement.innerText || '').length < 400) card = card.parentElement;
      if (!out.some(o => o.contains(card) || card.contains(o))) out.push(card);
    }
  }
  out.forEach((e, i) => e.setAttribute('data-item-cupom', String(i + 1)));
  window.__idsCupom = out.map(e => e.getAttribute('data-id') || (e.querySelector('[data-id]') || {getAttribute: () => ''}).getAttribute('data-id') || '');
  return out.map(e => e.innerText);
}
"""

# Expande acordeões/grupos fechados (botão atrás de seção fechada não pode nascer).
JS_EXPANDIR = r"""
(selCupom) => {
  const cup = selCupom ? Array.from(document.querySelectorAll(selCupom)) : [];
  let n = 0;
  const alvos = document.querySelectorAll('[aria-expanded="false"], .collapsed, [data-collapsed="true"]');
  for (const e of alvos) {
    if (cup.some(c => c.contains(e))) continue;
    const t = (e.innerText || '').trim();
    if (t.length > 120) continue;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    try { e.click(); n++; } catch (x) {}
    if (n > 40) break;
  }
  return n;
}
"""

# ---------------------------------------------------------------- classificação de mercados
_RX_NAO_LINHA = re.compile(
    r"(vencedor do ponto|winner of point|ponto\s*\d|dicas de ia|ai tips|margem|margin|corrida|race to|"
    r"primeiro a|first to|par\s*/\s*impar|par ou impar|odd\s*/\s*even|impar|individual|da equipe|de equipe|"
    r"team total|total do time|total da equipe|exato|exact|intervalo de|range|3 vias|3-way|1x2|"
    r"quem marca|resultado|vencedor|winner|moneyline)", re.I)
_RX_FAM_TOTAL = re.compile(r"\b(total|totais|totals|mais\s*/\s*menos|acima\s*/\s*abaixo|over\s*/\s*under|o/u|pontos|points)\b", re.I)
_RX_FAM_HCP = re.compile(r"\b(handicap|hdp|spread|desvantagem)\b", re.I)
_RX_LADO_TOTAL = re.compile(r"\b(acima|mais|over|abaixo|menos|under)\b(?:\s*de)?\s*[\(\[]?\s*(\d{1,3}(?:[.,]\d{1,2})?)?", re.I)
_RX_HCP_LINHA = re.compile(r"[\(\[]\s*([-+]?\s*\d{1,2}(?:[.,]\d{1,2})?)\s*[\)\]]|(?<![\d.,])([-+]\s*\d{1,2}(?:[.,]\d{1,2})?)(?![\d.,])")


def _lado_total(palavra):
    return "acima" if _sem_acento(palavra).lower() in ("acima", "mais", "over") else "abaixo"


def _idx_time(nome, times):
    for i, t in enumerate(times or []):
        if t and _nome_bate(nome, t):
            return i
    return None


def _cab_familia(heads, times):
    """Família e período pelos cabeçalhos de seção (o período mora na seção).
    Retorna (familia|None, periodo, bloqueado)."""
    fam = None
    per = None
    for i, h in enumerate(heads):
        if re.search(r"\d+\s*['’`´]", h) and quarto_atual(h):
            continue  # relógio do jogo não é cabeçalho de seção
        hn = _sem_acento(h)
        if i < 2 or (fam is None and i < 3):
            if _RX_NAO_LINHA.search(hn):
                return None, None, True
            if _e_total_de_equipe(h) or (times and re.search(r"\btotal", hn, re.I)
                                           and any(_nome_bate(h2, t) for t in times for h2 in _pedacos(h))):
                return None, None, True  # total de equipe
            if _RX_FAM_HCP.search(hn):
                fam = "handicap"
            elif _RX_FAM_TOTAL.search(hn):
                fam = "total"
        if per is None:
            p = periodo_de_texto(h)
            if p:
                per = p
        if fam and per:
            break
    return fam, per or "jogo", False


_PALAVRAS_TOTAL_JOGO = {"total", "totais", "totals", "pontos", "points", "jogo", "game", "match", "partida",
                        "inc", "incl", "ot", "prorrogacao", "prorrogacoes", "overtime", "mais", "menos", "over",
                        "under", "acima", "abaixo", "do", "da", "de", "of", "the", "e", "and", "o", "a",
                        "quarto", "quarter", "tempo", "half", "metade", "periodo", "period", "1", "2", "3", "4",
                        "1o", "2o", "3o", "4o", "1a", "2a", "1st", "2nd", "3rd", "4th", "q1", "q2", "q3", "q4",
                        "h1", "h2", "linha", "linhas", "asiatico", "asiaticos", "asiatica", "asiaticas", "asian", "alternativo", "alternativos", "alternativas", "principal", "main"}


def _e_total_de_equipe(titulo):
    """Título de total com qualquer coisa além de 'Total(is)', período e '(inc OT)' = total de UM time
    ('2º quarto. Basquete De Barcelos total' — caso 14/11 15:37, janela 12 fabricada).
    Não depende de o nome do time bater com o do sinal."""
    n = _norm(_sem_acento(titulo).replace("º", "o").replace("°", "o").replace("ª", "a"))
    if not re.search(r"\btota(l|is|ls)\b", n):
        return False
    resto = [w for w in n.split() if w not in _PALAVRAS_TOTAL_JOGO]
    return bool(resto)


def _pedacos(h):
    return [p for p in re.split(r"[:\-–—|/()]", h) if len(p.strip()) >= 3]


def ofertas_de_enumeracao(itens, casa, times, quarto=None, aba=""):
    """Converte a enumeração crua em ofertas {tipo, periodo, lado, linha, odd, time,
    time_idx, _mid, _src, secao}. Classificação não é filtro: mercados que não são de
    linha simplesmente não geram oferta."""
    mortos = periodos_encerrados(quarto)
    out = []
    vistos = set()
    tem_id = any(it.get("aid") for it in itens)
    for it in itens:
        if tem_id and not it.get("aid"):
            continue  # PS: a verdade está no id do botão; célula solta (ex.: coluna HDP) não é odd
        odd = _num(it["odd"])
        if odd is None or odd < 1.01:
            continue
        heads = it.get("heads") or []
        if it.get("travado"):
            continue  # odd com cadeado (offline) não é clicável
        o = None
        if it.get("aid"):
            o = _oferta_id_ps(it, times)
            if o is None:
                continue
        elif it.get("linha") and it.get("hdrs"):
            o = _oferta_tabela(it, odd, heads, times)
        if o is None:
            o = _oferta_segmento(it, odd, heads, times)
        if not o:
            continue
        if o["periodo"] == "q4" or o["periodo"] in mortos:
            continue
        if o["tipo"] == "total" and o["periodo"] == "jogo" and not (FAIXA_TOTAL_JOGO[0] <= o["linha"] <= FAIXA_TOTAL_JOGO[1]):
            continue
        # invariante final: linha e odd coexistem no texto-fonte
        if not (_coexiste(o["linha"], o["_src"], usar_abs=True) and _coexiste(odd, o["_src"])):
            continue
        o.update({"odd": odd, "_mid": it["mid"], "casa": casa, "secao": " / ".join(heads[:3]), "aba": aba})
        chave = (o["tipo"], o["periodo"], o.get("lado"), o.get("time_idx"), o["linha"])
        if chave in vistos:
            continue
        vistos.add(chave)
        out.append(o)
    return out


_PS_PERIODOS = {0: "jogo", 1: "t1", 2: "t2", 3: "q1", 4: "q2", 5: "q3", 6: "q4"}


def _oferta_id_ps(it, times):
    """PS3838 real: <a id="evento|período|mercado|lado|alt|linha">. Mercado 2 = handicap
    (lado 0 = casa, 1 = visitante; a linha já vem com o sinal do próprio time);
    mercado 3 = total (lado 3 = acima, 4 = abaixo). Período 0 jogo, 1/2 tempos, 3..6 quartos.
    A tela mostra só '38.5' no mandante e 'u' no abaixo — a verdade está no id."""
    partes = it["aid"].split("|")
    if len(partes) < 6:
        return None
    try:
        per = _PS_PERIODOS.get(int(partes[1]))
        mercado, lado, linha = int(partes[2]), int(partes[3]), float(partes[5])
    except ValueError:
        return None
    if per is None:
        return None
    # deslogado / sem odd: o único número do botão é a própria linha — nunca vira odd
    nums_seg = _numeros(it.get("seg") or "")
    odd = _num(it.get("odd"))
    if odd is None or (len(nums_seg) <= 1 and abs(odd - abs(linha)) < 1e-9):
        return None
    src = "%s [%s]" % (it.get("seg") or "", it["aid"])
    if mercado == 3 and lado in (3, 4):
        return {"tipo": "total", "periodo": per, "lado": "acima" if lado == 3 else "abaixo", "linha": abs(linha),
                "time": None, "time_idx": None, "_src": src, "_aid": it["aid"]}
    if mercado == 2 and lado in (0, 1):
        ct = it.get("casa_times") or []
        nome = ct[lado] if len(ct) > lado and ct[lado] else (times[lado] if times and len(times) > lado else "")
        ti = _idx_time(nome, times)
        if ti is None:
            return None
        return {"tipo": "handicap", "periodo": per, "lado": "time%d" % ti, "linha": linha,
                "time": times[ti], "time_idx": ti, "_src": src, "_aid": it["aid"]}
    return None


def _oferta_tabela(it, odd, heads, times):
    """Layout por colunas (PS): HDP | time | time | Pontos | Acima | Abaixo.
    A coluna diz o que a odd é."""
    hdrs = [_sem_acento(h).lower() for h in it["hdrs"]]
    row = it["linha"]
    col = _sem_acento(it.get("col") or "").lower()
    src = " | ".join(row)
    fam, per, bloq = _cab_familia(heads, times)
    if bloq:
        return None

    def idx(rx):
        for i, h in enumerate(hdrs):
            if re.search(rx, h):
                return i
        return None
    if re.search(r"^(acima|mais|over)\b", col) or re.search(r"^(abaixo|menos|under)\b", col):
        ip = idx(r"^(pontos|total|points|linha)")
        if ip is None or ip >= len(row):
            return None
        linha = _num(row[ip])
        if linha is None:
            return None
        return {"tipo": "total", "periodo": per, "lado": _lado_total(col.split()[0]),
                "linha": abs(linha), "time": None, "time_idx": None, "_src": src}
    ih = idx(r"^(hdp|handicap|spread)")
    if ih is not None and it["ci"] not in (ih, idx(r"^(pontos|total|points)")):
        nome_col = it.get("col") or ""
        ti = _idx_time(nome_col, times)
        if ti is None or ih >= len(row):
            return None
        base = _num(row[ih])
        if base is None:
            return None
        cols_time = [i for i, h in enumerate(it["hdrs"]) if _idx_time(h, times) is not None]
        primeira = cols_time[0] if cols_time else it["ci"]
        linha = base if it["ci"] == primeira else -base
        return {"tipo": "handicap", "periodo": per, "lado": "time%d" % ti, "linha": linha,
                "time": times[ti], "time_idx": ti, "_src": src}
    return None


def _oferta_segmento(it, odd, heads, times):
    """Total/handicap nascido do SEGMENTO do botão (rótulo + linha + odd do mesmo trecho)."""
    seg = it.get("seg") or it.get("txt") or ""
    fam, per, bloq = _cab_familia(heads, times)
    if bloq:
        return None
    segn = _sem_acento(seg)
    if _RX_NAO_LINHA.search(segn):
        return None  # o próprio bloco do botão é Dicas de IA / Vencedor do Ponto / total de equipe…
    if times and re.search(r"\btotal", segn, re.I) and any(_nome_bate(p, t) for t in times for p in _pedacos(segn.split("\n")[0])):
        return None
    rotulos = list(_RX_LADO_TOTAL.finditer(segn))
    if rotulos and fam != "handicap":
        # cada rótulo de lado abre o SEU segmento; a odd pertence ao rótulo logo antes dela
        pos_odd = segn.rfind(it["odd"])
        m, fim = None, len(segn)
        for k, r in enumerate(rotulos):
            prox = rotulos[k + 1].start() if k + 1 < len(rotulos) else len(segn)
            if r.start() <= pos_odd < prox:
                m, fim = r, prox
        if m is None:
            return None
        trecho = segn[m.start():fim]
        linha = _num(m.group(2)) if m.group(2) else None
        if linha is None:
            resto = [v for v in _numeros(segn[m.end():fim]) if abs(v - odd) > 1e-9]
            linha = abs(resto[0]) if resto else None
        if linha is None or linha < 10:
            return None
        return {"tipo": "total", "periodo": per, "lado": _lado_total(m.group(1)), "linha": linha,
                "time": None, "time_idx": None, "_src": trecho}
    if fam == "handicap" or _RX_FAM_HCP.search(segn):
        pos_odd = segn.rfind(it["odd"])
        antes_odd = segn[:pos_odd] if pos_odd >= 0 else segn
        nums = list(re.finditer(r"[-+\u2212]?\s?\d{1,2}(?:[.,]\d{1,2})?(?![\d.,])", antes_odd))
        if not nums:
            return None
        mm = nums[-1]
        linha = _num(mm.group(0).replace(" ", ""))
        if linha is None or abs(linha) > 60:
            return None
        antes = antes_odd[:mm.start()].strip(" ([")
        ti = _idx_time(antes, times)
        if ti is None:
            mt = re.search(r"\b(?:h|handicap\s*)?([12])\b", antes, re.I)
            if mt:
                ti = int(mt.group(1)) - 1
        if ti is None:
            return None
        return {"tipo": "handicap", "periodo": per, "lado": "time%d" % ti, "linha": linha,
                "time": times[ti] if times and ti < len(times) else "time%d" % (ti + 1), "time_idx": ti,
                "_src": segn[:pos_odd + len(it["odd"])] if pos_odd >= 0 else seg}
    return None


# ---------------------------------------------------------------- memórias
def _ler_json(caminho, padrao):
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao


def _gravar_json(caminho, dados):
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        tmp = caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=1)
        os.replace(tmp, caminho)
    except Exception as e:
        log("aviso: não gravou %s: %s" % (os.path.basename(caminho), e))


_MEM_URLS = None  # {"1awin": {chave_jogo: url}, "ps3838": {...}}  (URLS_JOGOS / URLS_JOGOS_PS)


def _mem_urls():
    global _MEM_URLS
    if _MEM_URLS is None:
        _MEM_URLS = _ler_json(ARQ_URLS, {CASA_1W: {}, CASA_PS: {}})
        _MEM_URLS.setdefault(CASA_1W, {})
        _MEM_URLS.setdefault(CASA_PS, {})
    return _MEM_URLS


def chave_jogo(times):
    return "|".join(sorted(_norm(t) for t in times))


def memorizar_url(casa, times, url):
    if casa == CASA_PS and "/matchup/" not in (url or ""):
        return  # lista nunca é memorizada
    m = _mem_urls()
    m[casa][chave_jogo(times)] = url
    _gravar_json(ARQ_URLS, m)


def url_memorizada(casa, times):
    m = _mem_urls()[casa]
    k = chave_jogo(times)
    if k in m:
        return m[k]
    for k2, u in m.items():
        ts = k2.split("|")
        if len(ts) == 2 and all(any(_nome_bate(a, b) for b in ts) for a in times):
            return u
    return None


_PAISES = {"brasil": "brazil", "argentina": "argentina", "espanha": "spain", "italia": "italy",
           "franca": "france", "alemanha": "germany", "grecia": "greece", "turquia": "turkey",
           "russia": "russia", "china": "china", "japao": "japan", "coreia": "korea",
           "coreia do sul": "south korea", "australia": "australia", "eua": "usa", "estados unidos": "usa",
           "mexico": "mexico", "portugal": "portugal", "polonia": "poland", "lituania": "lithuania",
           "servia": "serbia", "croacia": "croatia", "israel": "israel", "filipinas": "philippines",
           "uruguai": "uruguay", "chile": "chile", "paraguai": "paraguay", "venezuela": "venezuela",
           "kosovo": "kosovo", "dinamarca": "denmark", "suecia": "sweden", "finlandia": "finland",
           "noruega": "norway", "belgica": "belgium", "holanda": "netherlands", "paises baixos": "netherlands",
           "republica tcheca": "czech republic", "eslovenia": "slovenia", "romenia": "romania",
           "bulgaria": "bulgaria", "hungria": "hungary", "austria": "austria", "suica": "switzerland",
           "ucrania": "ukraine", "bahrein": "bahrain", "arabia saudita": "saudi arabia", "catar": "qatar",
           "emirados arabes": "uae", "egito": "egypt", "indonesia": "indonesia", "taiwan": "chinese taipei",
           "mundo": "world", "europa": "europe", "internacional": "international", "inglaterra": "england",
           "reino unido": "uk", "colombia": "colombia", "peru": "peru", "canada": "canada", "islandia": "iceland",
           "estonia": "estonia", "letonia": "latvia", "georgia": "georgia", "cazaquistao": "kazakhstan",
           "irlanda": "ireland", "escocia": "scotland", "pais de gales": "wales", "eslovaquia": "slovakia",
           "montenegro": "montenegro", "bosnia": "bosnia", "macedonia do norte": "north macedonia",
           "macedonia": "macedonia", "albania": "albania", "bielorrussia": "belarus", "luxemburgo": "luxembourg",
           "chipre": "cyprus", "malta": "malta", "armenia": "armenia", "azerbaijao": "azerbaijan",
           "nova zelandia": "new zealand", "africa do sul": "south africa", "marrocos": "morocco",
           "tunisia": "tunisia", "argelia": "algeria", "nigeria": "nigeria", "angola": "angola",
           "senegal": "senegal", "cuba": "cuba", "porto rico": "puerto rico", "republica dominicana": "dominican republic",
           "libano": "lebanon", "jordania": "jordan", "ira": "iran", "iraque": "iraq", "india": "india",
           "tailandia": "thailand", "vietna": "vietnam", "malasia": "malaysia", "singapura": "singapore"}


_PAISES_CACHE = []


def _PAISES_ORDEM():
    if not _PAISES_CACHE:
        _PAISES_CACHE.extend(sorted(_PAISES.items(), key=lambda x: -len(x[0])))
    return _PAISES_CACHE


def _liga_norm(s):
    n = _norm(s)
    for pt, en in sorted(_PAISES.items(), key=lambda x: -len(x[0])):
        n = re.sub(r"\b" + pt + r"\b", en, n)
    n = re.sub(r"\b(women|feminino|feminina|fem|w)\b", "women", n)
    n = re.sub(r"\b(liga|league|campeonato|championship)\b", "league", n)
    return n


_ROMANOS = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}


def _divisao(s):
    """'1. Liga' = '1ª Liga' = '1st League' = 'I Liga' -> '1'; 'Liga B' -> 'b'."""
    n = _norm(s)
    m = re.search(r"\b(\d)(?:st|nd|rd|th|a|o)?\b", n)
    if m:
        return m.group(1)
    m = re.search(r"\b(i|ii|iii|iv)\b", n)
    if m:
        return _ROMANOS[m.group(1)]
    return "b" if re.search(r"\bb\b", n) else ""


def liga_parecida(a, b):
    """0..1 — país não contradiz, feminino/base/3x3/divisão fazem parte da identidade."""
    na, nb = _liga_norm(a), _liga_norm(b)
    if not na or not nb:
        return 0.0
    if _marcas(a) - {"reserva"} != _marcas(b) - {"reserva"} or _divisao(a) != _divisao(b):
        return 0.0
    paises_a = {v for v in _PAISES.values() if re.search(r"\b" + v + r"\b", na)}
    paises_b = {v for v in _PAISES.values() if re.search(r"\b" + v + r"\b", nb)}
    if paises_a and paises_b and not (paises_a & paises_b):
        return 0.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb:
        r = max(r, len(ta & tb) / max(1, min(len(ta), len(tb))) * 0.95)
    return r


# ---------------------------------------------------------------- textos/regex das casas
_RX_LIMITE = re.compile(r"excesso de requisi|too many requests|rate limit", re.I)
_RX_DESLOGADO = re.compile(r"\b(entrar|login|iniciar sess[aã]o|fazer login|sign in|log in)\b", re.I)
_RX_ODD_MUDOU = re.compile(r"odds? (have |has )?changed|odds? (mudou|mudaram|alterad)|cota[cç](ao|oes) (mudou|mudaram|alterad)|"
                           r"coeficiente (mudou|alterad)|price changed", re.I)
_RX_CUPOM_VAZIO = re.compile(r"selecione um resultado|nenhuma aposta selecionada|no (bets|selections) (selected|yet)|"
                             r"bet ?slip is empty|cupom (esta )?vazio|adicione (uma )?sele", re.I)
_RX_INDISPONIVEL = re.compile(r"sele[cç][aã]o indispon[ií]vel|selection (is )?(currently )?unavailable|"
                              r"indispon[ií]vel para apostas|aposta m[ií]nima [eé] maior que a aposta m[aá]xima", re.I)
_RX_EXCLUIDO = re.compile(r"evento exclu[ií]do|event (excluded|removed)|mercado (fechado|encerrado)|market closed", re.I)
_RX_REMOVER_TUDO = re.compile(r"^\s*(remover tudo|remove all|limpar tudo|clear all|apagar tudo)\s*$", re.I)
_RX_OK_MODAL = re.compile(r"^\s*(ok|sim|confirmar|yes)\s*$", re.I)
_RX_APOSTAR = re.compile(r"^\s*(fazer (a |uma )?aposta|apostar|place bet|confirmar aposta|realizar aposta)\b", re.I)
_RX_SUCESSO = re.compile(r"aposta (aceita|feita|realizada|registrada|confirmada|efetuada)|apostas? (foi|foram) (aceita|feita|realizada)|"
                         r"bet (accepted|placed)|sua aposta foi|successfully placed|aceit[ao] com sucesso", re.I)
_RX_RECUSA = re.compile(r"aposta (recusada|rejeitada|n[aã]o aceita)|bet (rejected|declined|not accepted)|saldo insuficiente|"
                        r"insufficient (funds|balance)|odds? (have |has )?changed|cota[cç](ao|oes) (mudou|mudaram)|"
                        r"sele[cç][aã]o indispon|limite excedido|max(imum)? stake", re.I)


def ps_confrontos(texto):
    """Cabeçalhos 'Time1 -vs- Time2' do texto + liga 'País - Liga' logo acima."""
    linhas = [l.strip() for l in (texto or "").splitlines() if l.strip()]
    out = []
    for i, l in enumerate(linhas):
        m = re.match(r"^(.+?)\s+-vs-\s+(.+?)$", l)
        if m:
            liga = ""
            for j in range(i - 1, max(-1, i - 3), -1):
                if re.search(r"\S\s+-\s+\S", linhas[j]) and "-vs-" not in linhas[j]:
                    liga = linhas[j]
                    break
            out.append((m.group(1).strip(), m.group(2).strip(), liga))
    return out


def ps_e_o_jogo(texto, times):
    """Só é a página do jogo se o texto trouxer o cabeçalho -vs- deste confronto."""
    conf = ps_confrontos(texto)
    for len_ in (False, True):
        for a, b, liga in conf:
            if (_nome_bate(a, times[0], len_) and _nome_bate(b, times[1], len_)) or \
                    (_nome_bate(a, times[1], len_) and _nome_bate(b, times[0], len_)):
                return {"times_casa": [a, b], "liga": liga}
    return None


def linha_confronto(texto, times, max_linhas=60):
    return _linha_confronto(texto, times, max_linhas, False) or _linha_confronto(texto, times, max_linhas, True)


def _linha_confronto(texto, times, max_linhas, len_):
    """Linha de título do evento (1aWin) que contém os dois times."""
    linhas = [l.strip() for l in (texto or "").splitlines() if l.strip()][:max_linhas]
    for l in linhas:
        partes = re.split(r"\s+(?:—|–|-|vs\.?|x|v)\s+", l, maxsplit=1, flags=re.I)
        if len(partes) == 2:
            a, b = partes
            if (_nome_bate(a, times[0], len_) and _nome_bate(b, times[1], len_)) or (_nome_bate(a, times[1], len_) and _nome_bate(b, times[0], len_)):
                return [a, b]
    # times em linhas separadas e próximas (cabeçalho do evento em blocos)
    curtas = [l for l in linhas if 3 <= len(l) <= 60]
    for i, l in enumerate(curtas):
        for j in range(i + 1, min(i + 4, len(curtas))):
            a, b = l, curtas[j]
            if (_nome_bate(a, times[0], len_) and _nome_bate(b, times[1], len_)) or (_nome_bate(a, times[1], len_) and _nome_bate(b, times[0], len_)):
                return [a, b]
    return None


def liga_da_url_ps(url):
    """Liga certa sai da URL do matchup (a PS às vezes imprime a liga errada no cabeçalho)."""
    partes = [p for p in urlparse(url or "").path.split("/") if p]
    if "matchup" not in partes:
        return ""
    i = partes.index("matchup")
    genericos = {"pt", "en", "es", "sports", "sport", "basketball", "basquete", "live", "standard", "compact", "asian"}
    if i > 0 and partes[i - 1].lower() not in genericos:
        return partes[i - 1].replace("-", " ")
    depois = partes[i + 1:]
    if len(depois) >= 2 and not depois[0].isdigit():
        return depois[0].replace("-", " ")
    return ""


def trocar_origem(url, base):
    """Mesmo caminho, no domínio do espelho atual."""
    u, b = urlparse(url or ""), urlparse(base or "")
    if not u.netloc or not b.netloc or u.netloc == b.netloc:
        return url
    return "%s://%s%s%s" % (b.scheme, b.netloc, u.path, ("?" + u.query) if u.query else "")


_RX_VIRTUAL = re.compile(r"(replay|reprise|cyber|e-?basket|ebasketball|\b2k\b|nba ?2k|virtual|simulad|simulated|esport|e-sport|"
                         r"fifa|h2h gg|battle|\bgt league\b|\bvbl\b)", re.I)


def _e_liga_virtual(txt):
    return bool(_RX_VIRTUAL.search(_sem_acento(txt or "")))


class FalhaCasa(Exception):
    pass


# ---------------------------------------------------------------- CASA (thread trabalhadora)
class Casa:
    def __init__(self, nome, cfg, base_url=None, perfil=None, headless=None):
        self.nome = nome
        self.cfg = cfg
        self.base = (base_url or cfg["url_1awin" if nome == CASA_1W else "url_ps3838"]).rstrip("/")
        self.perfil = perfil or os.path.join(DADOS, "perfil-" + nome)
        self.headless = cfg.get("headless") if headless is None else headless
        self.fila = queue.Queue()
        self.page = None
        self.ctx = None
        self._pw = None
        self.vivo = False
        self.limitada = False
        self.jogo = {}           # {"times": [...], "url": ..., "liga": ..., "times_casa": [...]}
        self.sel_cupom = cfg.get("seletor_cupom_" + ("1awin" if nome == CASA_1W else "ps")) or SEL_CUPOM_PADRAO[nome]
        # PS real: cada seleção é um .BetItemComponent (data-id = id do botão). 1aWin: classes
        # embaralhadas -> sem seletor, vale a heurística (menor bloco com mercado + odd).
        self.sel_item = ('[class*="BetItemComponent"], [class*="slip-sel" i]' if nome == CASA_PS else "")
        self.thread = threading.Thread(target=self._laco, name="casa-" + nome, daemon=True)

    @property
    def rotulo(self):
        return NOME_CASA[self.nome]

    # -------------------------------------------------- ciclo da thread
    def iniciar(self):
        self.thread.start()
        return self

    def parar(self):
        self.fila.put(None)

    def _abrir(self):
        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().start()
        os.makedirs(self.perfil, exist_ok=True)
        kw = opcoes_navegador(self.cfg, self.headless, {"width": 1400, "height": 900})
        self.ctx = self._pw.chromium.launch_persistent_context(self.perfil, **kw)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.set_default_timeout(15000)
        self.vivo = True
        try:
            self.ir(self.url_lista())
        except Exception as e:
            log("%s: lista não abriu na subida (%s)" % (self.rotulo, str(e)[:120]))

    def _viva(self):
        try:
            return self.page is not None and not self.page.is_closed() and self.ctx is not None and bool(self.ctx.pages)
        except Exception:
            return False

    def _laco(self):
        while True:
            try:
                if not self._viva():
                    if self.vivo:
                        log("%s: janela morreu — reabrindo em 5s" % self.rotulo)
                        time.sleep(5)
                    try:
                        if self.ctx:
                            self.ctx.close()
                    except Exception:
                        pass
                    self._abrir()
                    log("%s: janela aberta e aquecida" % self.rotulo)
            except Exception as e:
                log("%s: falha ao abrir navegador: %s" % (self.rotulo, e))
                time.sleep(5)
                continue
            job = self.fila.get()
            if job is None:
                try:
                    self.ctx.close()
                    self._pw.stop()
                except Exception:
                    pass
                return
            fn, args, ev, caixa = job
            try:
                caixa["r"] = fn(*args)
            except Exception as e:
                caixa["e"] = e
                caixa["tb"] = traceback.format_exc()
            ev.set()

    def executar(self, fn, *args, timeout=180):
        """Põe o job na fila desta casa e espera o resultado."""
        ev = threading.Event()
        caixa = {}
        self.fila.put((fn, args, ev, caixa))
        if not ev.wait(timeout):
            raise FalhaCasa("%s: job %s passou do prazo" % (self.rotulo, getattr(fn, "__name__", "?")))
        if "e" in caixa:
            raise caixa["e"]
        return caixa.get("r")

    def em_paralelo(self, fn, *args, timeout=180):
        """Dispara o job e devolve uma função que espera o resultado."""
        ev = threading.Event()
        caixa = {}
        self.fila.put((fn, args, ev, caixa))

        def esperar():
            if not ev.wait(timeout):
                raise FalhaCasa("%s: job passou do prazo" % self.rotulo)
            if "e" in caixa:
                raise caixa["e"]
            return caixa.get("r")
        return esperar

    # -------------------------------------------------- utilidades de página (rodam NA thread da casa)
    def url_lista(self):
        return self.base + self.cfg["caminho_lista_1awin" if self.nome == CASA_1W else "caminho_lista_ps"]

    def texto(self):
        try:
            return self.page.inner_text("body", timeout=5000)
        except Exception:
            return ""

    def foto(self, tag):
        os.makedirs(PRINTS, exist_ok=True)
        nome = "%s_%s_%s.png" % (datetime.now().strftime("%Y%m%d-%H%M%S"), self.nome, re.sub(r"[^\w-]+", "_", tag)[:40])
        try:
            self.page.wait_for_timeout(400)
            self.page.screenshot(path=os.path.join(PRINTS, nome))
            return nome
        except Exception:
            return ""

    def dump(self, tag):
        base = os.path.join(PRINTS, "%s_%s_%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), self.nome, tag))
        try:
            os.makedirs(PRINTS, exist_ok=True)
            with open(base + ".html", "w", encoding="utf-8") as f:
                f.write(self.page.content())
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(self.texto())
        except Exception:
            pass

    def dump_diag(self, tag, itens=None):
        """Raio-x para ajustar a leitura à casa real: foto + HTML + texto + o que a
        enumeração viu. No máximo 1 por tipo a cada 5 min (não enche o disco)."""
        chave = "_diag_" + tag
        if time.time() - getattr(self, chave, 0) < 300:
            return
        setattr(self, chave, time.time())
        self.dump(tag)
        self.foto(tag)
        try:
            if itens is None:
                itens = self.enumerar()
            base = os.path.join(PRINTS, "%s_%s_%s_itens.json" % (datetime.now().strftime("%Y%m%d-%H%M%S"), self.nome, tag))
            with open(base, "w", encoding="utf-8") as f:
                json.dump({"url": self.page.url, "n_itens": len(itens), "itens": itens[:200]}, f, ensure_ascii=False, indent=1)
            log("%s: raio-x salvo em dados/prints (%s, %d botões com odd vistos)" % (self.rotulo, tag, len(itens)))
        except Exception as e:
            log("%s: raio-x falhou: %s" % (self.rotulo, str(e)[:120]))

    def esta_limitada(self, texto=None):
        return bool(_RX_LIMITE.search(texto if texto is not None else self.texto()))

    def enumerar(self):
        return self._eval(JS_ENUMERAR, self.sel_cupom)

    def expandir(self):
        n = 0
        for _ in range(3):
            k = self._eval(JS_EXPANDIR, self.sel_cupom)
            n += k
            if not k:
                break
            self.page.wait_for_timeout(250)
        return n

    def _eval(self, js, arg=None, tentativas=6):
        """page.evaluate blindado: a página das casas se redesenha/troca de rota
        sozinha depois de carregar ('Execution context was destroyed'). Espera
        assentar e lê de novo."""
        ult = None
        for _ in range(tentativas):
            try:
                return self.page.evaluate(js) if arg is None else self.page.evaluate(js, arg)
            except Exception as e:
                msg = str(e)
                if not any(k in msg for k in ("Execution context was destroyed", "navigation", "Cannot find context",
                                              "frame was detached", "Target closed")) or "Target page" in msg:
                    raise
                ult = e
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                self.page.wait_for_timeout(500)
        raise ult

    def _esperar_url_parada(self, s_parada=1.0, teto=8.0):
        """Redirecionamentos em cadeia: só segue quando o endereço parou de mudar."""
        fim = time.time() + teto
        ult, desde = self.page.url, time.time()
        while time.time() < fim:
            self.page.wait_for_timeout(250)
            u = self.page.url
            if u != ult:
                ult, desde = u, time.time()
            elif time.time() - desde >= s_parada:
                break
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

    def ir(self, url):
        """Navega tolerando o redirecionamento da casa (a 1aWin manda do domínio de
        referência para o espelho atual no meio do carregamento)."""
        if self.nome == CASA_1W:
            url = trocar_origem(url, self.base)
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            msg = str(e)
            if "interrupted by another navigation" not in msg and "ERR_ABORTED" not in msg and "frame was detached" not in msg:
                raise
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
        self._esperar_url_parada()
        self._atualizar_espelho()

    def _atualizar_espelho(self):
        """Domínios da 1aWin são espelhos rotativos: a sessão mora no espelho atual."""
        if self.nome != CASA_1W:
            return
        try:
            u = urlparse(self.page.url)
        except Exception:
            return
        if u.scheme not in ("http", "https") or not u.netloc or "/betting" not in u.path:
            return
        nova = "%s://%s" % (u.scheme, u.netloc)
        atual = urlparse(self.base)
        if nova != "%s://%s" % (atual.scheme, atual.netloc):
            log("1aWin: espelho atual é %s (era %s) — sigo nele" % (u.netloc, atual.netloc))
            self.base = nova

    # -------------------------------------------------- cupom / boletim
    def fechar_aviso_indisponivel(self):
        """PS: janela 'Seleção Indisponível — a aposta mínima é maior que a aposta máxima'.
        Ela BLOQUEIA a página até alguém clicar OK. Fecha e devolve o texto (ou '')."""
        if self.nome != CASA_PS:
            return ""
        try:
            tx = self.texto()
            m = _RX_INDISPONIVEL.search(tx)
            if not m:
                return ""
            self._modal_ok()
            self.page.wait_for_timeout(300)
            if _RX_INDISPONIVEL.search(self.texto()):  # plano B: o X da janela
                self._eval("""() => { for (const e of document.querySelectorAll('[class*="modal" i] [class*="close" i], [role=dialog] button'))
                    { const r = e.getBoundingClientRect(); if (r.width > 0) { e.click(); return; } } }""")
            log("PS: fechei a janela 'Seleção Indisponível'")
            return m.group(0)
        except Exception:
            return ""

    def mostrar_boletim(self):
        """PS: o boletim mora numa aba da barra da esquerda ('BOLETIM DE APOSTAS'); com
        a aba 'Todos os eventos' ativa ele fica escondido."""
        if self.nome != CASA_PS:
            return
        try:
            loc = self.page.locator('.BET_SLIP, [title="BOLETIM DE APOSTAS"]').first
            if loc.count() and loc.is_visible():
                ativo = loc.evaluate("e => !!(e.closest('.selection-button') || e).classList.contains('active') || !!(e.closest('.active'))")
                if not ativo:
                    self.clicar_firme(loc)
                    self.page.wait_for_timeout(300)
        except Exception:
            pass

    def itens_cupom(self):
        """Seleções no cupom. Pelo seletor de item; se ele não achar nada (classes
        embaralhadas, como na 1aWin), pela forma: o MENOR bloco do cupom que tem um
        rótulo de mercado E uma odd. Cupom vazio ('Selecione um resultado') = [].
        PS: abre a aba do boletim antes — boletim escondido NÃO é boletim vazio."""
        self.mostrar_boletim()
        itens = self._eval(JS_ITENS_CUPOM, [self.sel_cupom, self.sel_item]) or []
        if itens:
            return itens
        # 'vazio' só com a PROVA da casa ('Selecione um resultado…' / 'Nenhuma aposta selecionada');
        # sem a frase e com cara de seleção = sujo, mesmo sem reconhecer o cartão
        # (caso 14/11 18:40: painel disse 'cupom vazio' com várias seleções presas na 1aWin)
        tx = self.texto_cupom() or ""
        if _RX_CUPOM_VAZIO.search(tx):
            return []
        if re.search(r"\b(acima|abaixo|mais|menos|over|under)\s+de\b|@\s*\d|\bevento exclu", _sem_acento(tx), re.I):
            return ["(seleção não reconhecida no cupom) " + " ".join(tx.split())[:120]]
        return []

    def texto_cupom(self):
        return self._eval("""(sc) => Array.from(document.querySelectorAll(sc))
            .filter(c => !Array.from(document.querySelectorAll(sc)).some(o => o !== c && o.contains(c)))
            .map(c => c.innerText).join('\\n')""", self.sel_cupom)

    def _clicar_no_cupom(self, rx, por_classe=False):
        """Clica controles DENTRO do cupom pelo texto. por_classe=True (só para limpar)
        aceita também ícones de remover/fechar sem texto."""
        alvos = self._eval("""([sc, src, pc]) => {
            document.querySelectorAll('[data-alvo-cupom]').forEach(e => e.removeAttribute('data-alvo-cupom'));
            const rx = new RegExp(src, 'i'); const out = [];
            let n = 0;
            for (const c of document.querySelectorAll(sc)) for (const e of c.querySelectorAll('button,a,[role=button],span,div,i')) {
                const t = (e.innerText || e.getAttribute('aria-label') || e.getAttribute('title') || '').trim();
                const cls = (e.className && e.className.baseVal === undefined ? e.className : '') + '';
                if ((rx.test(t) || (pc && /(^|\\s|-)(remove|delete|close|cancel)/i.test(cls))) && e.children.length <= 1) {
                    n++; e.setAttribute('data-alvo-cupom', String(n)); out.push(String(n));
                }
            }
            return out;
        }""", [self.sel_cupom, rx, por_classe])
        clicou = 0
        for a in alvos:
            try:
                loc = self.page.locator('[data-alvo-cupom="%s"]' % a)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    clicou += 1
                    self.page.wait_for_timeout(150)
            except Exception:
                pass
        return clicou

    def _clicar_x_ps(self):
        """O X de cada seleção do boletim da PS (.remove-icon dentro do cartão)."""
        n = self._eval("""() => {
            document.querySelectorAll('[data-alvo-x]').forEach(e => e.removeAttribute('data-alvo-x'));
            let n = 0;
            for (const it of document.querySelectorAll('[class*="BetItemComponent"]')) {
                const x = it.querySelector('.remove-icon, [class*="remove" i], [class*="close" i]');
                if (x) { n++; x.setAttribute('data-alvo-x', String(n)); }
            }
            return n;
        }""") or 0
        for i in range(1, n + 1):
            self.clicar_firme(self.page.locator('[data-alvo-x="%d"]' % i).first)
            self.page.wait_for_timeout(250)
        return n

    def _clicar_lixeira_1w(self):
        try:
            n = self._eval("""(sc) => {
                document.querySelectorAll('[data-alvo-lix]').forEach(e => e.removeAttribute('data-alvo-lix'));
                const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                // a lixeira mora no aside do cupom, na linha das abas Simples/Múltipla/Série
                const raiz = Array.from(document.querySelectorAll(sc)).map(c => c.closest('aside') || c.parentElement || c);
                for (const r of raiz) {
                    const abas = Array.from(r.querySelectorAll('*')).find(e => /^\s*simples\s*$/i.test(e.innerText || '') && e.children.length <= 1);
                    let linha = abas;
                    for (let k = 0; linha && k < 4; k++, linha = linha.parentElement) {
                        const bts = Array.from(linha.querySelectorAll('button, [role=button]')).filter(b => vis(b) && !(b.innerText || '').trim());
                        if (bts.length) { bts[bts.length - 1].setAttribute('data-alvo-lix', '1'); return 1; }
                    }
                    const tit = Array.from(r.querySelectorAll('button, [role=button]')).find(b => vis(b) &&
                        /lixeira|limpar|trash|delete|clear/i.test((b.getAttribute('aria-label') || '') + (b.getAttribute('title') || '')));
                    if (tit) { tit.setAttribute('data-alvo-lix', '1'); return 1; }
                }
                return 0;
            }""", self.sel_cupom)
            if n:
                log("1aWin: o X da seleção não limpou — usando a lixeira do cupom")
                return self.clicar_firme(self.page.locator('[data-alvo-lix="1"]').first)
        except Exception:
            pass
        return False

    def _clicar_icone_do_item(self):
        """O ✕ da seleção na 1aWin é um botão só com ícone (sem texto, classe embaralhada):
        clica o botão-ícone de cada cartão de seleção do cupom."""
        self.itens_cupom()
        n = self._eval("""() => {
            document.querySelectorAll('[data-alvo-x]').forEach(e => e.removeAttribute('data-alvo-x'));
            let n = 0;
            for (const card of document.querySelectorAll('[data-item-cupom]')) {
                // o X é o ícone MAIS À DIREITA do cartão (à esquerda fica o ícone de 'ao vivo' —
                // caso 14/11 18:21: o programa clicava nele e o cupom não esvaziava)
                const bts = Array.from(new Set(Array.from(card.querySelectorAll('button, [role=button], svg'))
                    .map(b => b.closest('button,[role=button]') || b)))
                    .filter(b => !(b.innerText || '').trim() && b.getBoundingClientRect().width > 0);
                if (bts.length) {
                    const x = bts.reduce((a, b) => b.getBoundingClientRect().right > a.getBoundingClientRect().right ? b : a);
                    n++; x.setAttribute('data-alvo-x', String(n));
                }
            }
            return n;
        }""")
        for i in range(1, (n or 0) + 1):
            try:
                self.clicar_firme(self.page.locator('[data-alvo-x="%d"]' % i).first)
                self.page.wait_for_timeout(200)
            except Exception:
                pass
        return n

    def _modal_ok(self):
        """Confirma o 'Quer limpar o seu boletim de apostas? CANCELAR / OK' (na PS o OK é
        só texto, não <button>). Nunca clica em CANCELAR."""
        for _ in range(10):
            # 1º: o TEXTO "OK" na tela (nó de texto), clicado com o mouse na posição dele —
            # funciona seja qual for a estrutura (span, div, texto solto ao lado de CANCELAR)
            pos = self._eval(r"""() => {
                const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const achados = [];
                for (let n = w.nextNode(); n; n = w.nextNode()) {
                    if (!/^\s*(ok|sim|yes)\s*$/i.test(n.textContent || '')) continue;
                    const r = document.createRange(); r.selectNodeContents(n);
                    const b = r.getBoundingClientRect();
                    if (b.width <= 0 || b.height <= 0) continue;
                    let perto = false;
                    for (let a = n.parentElement, k = 0; a && k < 6; a = a.parentElement, k++)
                        if (/limpar|remover|clear|apagar|excluir|tem certeza|deseja|indispon|unavailable/i.test(a.innerText || '') && (a.innerText || '').length < 300) { perto = true; break; }
                    achados.push({x: b.left + b.width / 2, y: b.top + b.height / 2, perto});
                }
                return achados.find(a => a.perto) || null;
            }""")
            if pos:
                try:
                    self.page.mouse.click(pos["x"], pos["y"])
                    self.page.wait_for_timeout(300)
                    return True
                except Exception:
                    pass
            n = self._eval(r"""() => {
                document.querySelectorAll('[data-alvo-ok]').forEach(e => e.removeAttribute('data-alvo-ok'));
                const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                const oks = Array.from(document.querySelectorAll('body *')).filter(e =>
                    vis(e) && /^\s*(ok|sim|yes|confirmar)\s*$/i.test(e.innerText || '') &&
                    !Array.from(e.children).some(c => /^\s*(ok|sim|yes|confirmar)\s*$/i.test(c.innerText || '')));
                if (!oks.length) return 0;
                // SÓ dentro da janelinha de confirmação ('Quer limpar…'). NUNCA o 'Sim' de um mercado
                // (caso 14/11 22:36: clicou 'A partida vai para a prorrogação? Sim' @14.6 na 1aWin)
                const perto = oks.find(e => { for (let a = e, k = 0; a && k < 6; a = a.parentElement, k++)
                    if (/limpar|remover|clear|apagar|excluir|tem certeza|deseja|indispon|unavailable/i.test(a.innerText || '') && (a.innerText || '').length < 300) return true; return false; });
                if (!perto) return 0;
                perto.setAttribute('data-alvo-ok', '1');
                return 1;
            }""")
            if n:
                if self.clicar_firme(self.page.locator('[data-alvo-ok="1"]').first):
                    return True
            self.page.wait_for_timeout(150)
        return False

    def esperar_cupom_vazio(self, s=3.0):
        fim = time.time() + s
        while time.time() < fim:
            if not self.itens_cupom():
                return True
            self.page.wait_for_timeout(200)
        return not self.itens_cupom()

    def limpar_cupom(self, perna=None):
        """Desfazer/limpar. Só conta com o cupom VAZIO de verdade."""
        self.mostrar_boletim()
        if not self.itens_cupom():
            return True
        if self.nome == CASA_PS:
            for rodada in range(3):
                # 1) "Remover tudo" -> "Quer limpar o seu boletim de apostas? CANCELAR / OK"
                if self._clicar_no_cupom(r"^(remover tudo|remove all|limpar tudo|clear all|apagar tudo)$"):
                    self.page.wait_for_timeout(300)
                    self._modal_ok()
                    if self.esperar_cupom_vazio(2.0):
                        return True
                # 2) plano B: o X (.remove-icon) de cada seleção
                self._clicar_x_ps()
                self._modal_ok()
                if self.esperar_cupom_vazio(2.0):
                    return True
            self.dump_diag("desfazer_falhou")
            return False
        if len(self.itens_cupom()) > 1 and self._clicar_lixeira_1w():  # várias presas: lixeira apaga tudo
            self._modal_ok()
            if self.esperar_cupom_vazio(2.0):
                return True
        for _ in range(5):
            if not self._clicar_no_cupom(r"^(×|✕|x|remover|excluir|cancelar|remove|delete|cancel|limpar tudo|remover tudo)$", True):
                self._clicar_icone_do_item()
            self._modal_ok_rapido()
            if self.esperar_cupom_vazio(1.5):
                return True
        # plano B: a LIXEIRA do cupom (botão-ícone ao lado de 'Simples / Múltipla / Série', limpa tudo)
        if self._clicar_lixeira_1w():
            self._modal_ok()
            if self.esperar_cupom_vazio(2.0):
                return True
        # plano C: desmarcar pelo próprio mercado — o botão da seleção que está no cupom, na página
        if self._desmarcar_pelo_mercado() and self.esperar_cupom_vazio(2.0):
            return True
        if perna and perna.get("_mid"):  # plano D: clicar a própria odd (desmarca)
            try:
                loc = self.page.locator('[data-mid="%s"]' % perna["_mid"])
                # só se o botão AINDA for a mesma seleção — senão o clique ADICIONA outra
                # (caso 14/11 18:21: o carimbo virou 'Abaixo De 194.5' e foi parar no cupom)
                if loc.count() and self._carimbo_vivo(loc.first, perna):
                    loc.first.click(timeout=2000)
            except Exception:
                pass
            if self.esperar_cupom_vazio(2.0):
                return True
        self.dump_diag("desfazer_falhou")
        return False

    def _desmarcar_pelo_mercado(self):
        """Lê no cupom '<Mercado>, <Acima De 235.5>' e clica de novo nesse botão da página
        (clicar numa seleção já marcada a desmarca). Só clica botão cujo texto começa EXATAMENTE
        pelo rótulo e cujo mercado (título da seção) é o mesmo — nunca outro."""
        try:
            n = self._eval(r"""(sc) => {
                document.querySelectorAll('[data-alvo-desm]').forEach(e => e.removeAttribute('data-alvo-desm'));
                const cups = Array.from(document.querySelectorAll(sc));
                const nrm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const sels = [];
                for (const c of cups) for (const m of (c.innerText || '').matchAll(/([^\n,]{3,80}),\s*((?:acima|abaixo|mais|menos|over|under) de [\d.,]+)/gi))
                    sels.push({mercado: nrm(m[1]), rotulo: nrm(m[2])});
                let n = 0;
                for (const s of sels) {
                    for (const b of document.querySelectorAll('button, [role=button]')) {
                        if (cups.some(c => c.contains(b))) continue;
                        const t = nrm(b.innerText);
                        if (!t.startsWith(s.rotulo + ' ') && t !== s.rotulo) continue;
                        // mercado: título da seção onde o botão mora
                        let titulo = '';
                        for (let a = b.parentElement, k = 0; a && k < 8 && !titulo; a = a.parentElement, k++)
                            for (const f of a.children) { if (f.contains(b)) break; const x = nrm(f.innerText); if (x && x.length < 90 && x.includes(s.mercado)) { titulo = x; break; } }
                        if (!titulo) continue;
                        n++; b.setAttribute('data-alvo-desm', String(n)); break;
                    }
                }
                return n;
            }""", self.sel_cupom) or 0
            for i in range(1, n + 1):
                log("%s: desmarcando a seleção pelo próprio mercado (o X do cupom não limpou)" % self.rotulo)
                self.clicar_firme(self.page.locator('[data-alvo-desm="%d"]' % i).first)
                self.page.wait_for_timeout(300)
            return n > 0
        except Exception:
            return False

    def _modal_ok_rapido(self):
        """Só confirma janelinha de confirmação de verdade (mesma regra do _modal_ok)."""
        if self.nome == CASA_PS:
            return False
        try:
            n = self._eval(r"""() => {
                document.querySelectorAll('[data-alvo-ok]').forEach(e => e.removeAttribute('data-alvo-ok'));
                for (const b of document.querySelectorAll('button, [role=button]')) {
                    if (!/^\s*(ok|sim|yes|confirmar)\s*$/i.test(b.innerText || '')) continue;
                    const r = b.getBoundingClientRect(); if (r.width <= 0) continue;
                    for (let a = b.parentElement, k = 0; a && k < 6; a = a.parentElement, k++)
                        if (/limpar|remover|clear|apagar|excluir|tem certeza|deseja|indispon|unavailable/i.test(a.innerText || '') && (a.innerText || '').length < 300) {
                            b.setAttribute('data-alvo-ok', '1'); return 1; }
                }
                return 0;
            }""")
            if n:
                return self.clicar_firme(self.page.locator('[data-alvo-ok="1"]').first)
        except Exception:
            pass
        return False

    # -------------------------------------------------- leitura das escadas
    def ler_escada(self, times):
        """Lê todas as linhas de total/handicap por período, com carimbo de cada botão."""
        r = {"ok": False, "ofertas": [], "motivo": "", "limitada": False, "quarto": None}
        tx = self.texto()
        if self.esta_limitada(tx):
            self.limitada = True
            r.update(limitada=True, motivo="casa limitada (Excesso de requisições)")
            return r
        if self.nome == CASA_PS:
            if self.fechar_aviso_indisponivel():
                tx = self.texto()
            if not ps_e_o_jogo(tx, times):
                r["motivo"] = "a página da PS não é o matchup deste jogo"
                self.dump_diag("ps_nao_e_jogo")
                return r
            self.expandir()
            self._sondar(lambda: self._eval("() => document.querySelectorAll('a.odds span, a[id*=\"|\"] span').length > 0"), 8)
            itens = self.enumerar()
            tx = self.texto()
            q = quarto_atual(tx)
            ofs = ofertas_de_enumeracao(itens, self.nome, times, q)
            if not ofs:
                logado = self._eval("() => !!document.querySelector('.loginId, .AccountInfoComponent .balance')")
                travados = self._eval("() => document.querySelectorAll('.style-lock:not(.lock-hidden)').length")
                if not logado and _RX_DESLOGADO.search(tx):
                    r["motivo"] = "PS deslogada — odds escondidas para visitante (rode 1-LOGIN.bat)"
                    return r
                if travados:
                    r.update(ok=True, ofertas=[], quarto=q, motivo="PS: mercado travado (cadeado em %d odds)" % travados)
                    return r
        else:
            if not linha_confronto(tx, times):
                r["motivo"] = "a página da 1aWin não é a deste jogo"
                self.dump_diag("1w_nao_e_jogo")
                return r
            q = quarto_atual(tx)
            ofs = []
            # a página do jogo aparece antes dos mercados: espera os botões de odd carregarem
            self._sondar(lambda: self._eval(r"""() => Array.from(document.querySelectorAll('button, [role=button]'))
                .some(b => /\d{1,2}[.,]\d{1,3}\s*$/.test((b.innerText || '').trim()))"""), 8)
            abas = self._abas()
            todos = [a for a in abas if re.match(r"^(todos|all|tudo)\b", a, re.I)]
            if todos:
                self._clicar_aba(todos[0])
            self.expandir()
            itens = self.enumerar()
            ofs = ofertas_de_enumeracao(itens, self.nome, times, q, aba=todos[0] if todos else "")
            if not ofs and abas:
                for a in abas:
                    if not re.search(r"principal|main|quarto|quarter|total|handicap|tempo|half", a, re.I):
                        continue
                    self._clicar_aba(a)
                    self.expandir()
                    ofs += ofertas_de_enumeracao(self.enumerar(), self.nome, times, q, aba=a)
        if not ofs:
            self.dump_diag("sem_ofertas", itens if self.nome == CASA_PS else None)
        r.update(ok=True, ofertas=ofs, quarto=q, motivo="" if ofs else "nenhuma oferta de linha legível")
        self.limitada = False
        return r

    def _abas(self):
        return self._eval(r"""() => {
            const out = [];
            for (const e of document.querySelectorAll('[role=tab], .tab, [class*="tabs" i] > *')) {
                const t = (e.innerText || '').trim();
                if (t && t.length < 30 && !out.includes(t)) out.push(t);
            }
            return out;
        }""")

    def _clicar_aba(self, nome):
        try:
            loc = self.page.locator('[role=tab], .tab, [class*="tabs" i] > *').filter(has_text=nome).first
            loc.click(timeout=3000)
            self.page.wait_for_timeout(300)
        except Exception:
            pass

    # -------------------------------------------------- montagem (motor-leitor)
    def _achar_por_identidade(self, perna, times):
        """Relê com a MESMA enumeração e acha a seleção por identidade. Não reclica aba que já
        está aberta (a 1aWin redesenha tudo) e tenta por até 3s enquanto a página assenta."""
        if perna.get("aba") and not self._aba_ativa(perna["aba"]):
            self._clicar_aba(perna["aba"])
        fim = time.time() + 3.0
        while True:
            o = self._identidade_uma_vez(perna, times)
            if o or time.time() >= fim:
                return o
            self.page.wait_for_timeout(400)

    def _aba_ativa(self, nome):
        try:
            return bool(self._eval("""(n) => Array.from(document.querySelectorAll('[role=tab], .tab, [class*="tabs" i] > *'))
                .some(e => (e.innerText || '').trim() === n && (e.getAttribute('aria-selected') === 'true' ||
                     /active|selected|checked/i.test(e.className + '')))""", nome))
        except Exception:
            return False

    def _identidade_uma_vez(self, perna, times):
        self.expandir()
        itens = self.enumerar()
        ofs = ofertas_de_enumeracao(itens, self.nome, times, quarto_atual(self.texto()), aba=perna.get("aba", ""))
        self._ultimas_ofs = ofs
        for o in ofs:
            if o["tipo"] != perna["tipo"] or o["periodo"] != perna["periodo"]:
                continue
            if abs(o["linha"] - perna["linha"]) > 0.001 or abs(o["odd"] - perna["odd"]) > TOL_ODD_IDENTIDADE + 1e-9:
                continue
            if o["tipo"] == "total" and o["lado"] != perna["lado"]:
                continue
            if o["tipo"] == "handicap" and not (o.get("time") and perna.get("time") and _nome_bate(o["time"], perna["time"])):
                continue
            if o.get("_mid"):
                return o
        return None

    def clicar_firme(self, loc):
        """Clique que insiste: elemento piscando com odds ao vivo ('not stable')
        -> clique forçado -> clique por JS."""
        try:
            loc.click(timeout=2500)
            return True
        except Exception:
            pass
        try:
            loc.click(timeout=2500, force=True)
            return True
        except Exception:
            pass
        try:
            loc.evaluate("e => e.click()")
            return True
        except Exception:
            return False

    def _carimbo_vivo(self, el, perna):
        """O botão carimbado ainda é a MESMA seleção? (a 1aWin redesenha ao vivo e o nó
        carimbado pode passar a mostrar outra linha — caso Ratiopharm 14/11 15:21)."""
        try:
            if perna.get("_aid"):  # PS: o id do botão é a identidade
                aid = el.evaluate("e => (e.closest('a[id]') || e.querySelector('a[id]') || {id: ''}).id")
                a, b = (aid or "").split("|"), perna["_aid"].split("|")
                return len(a) >= 6 and a[:4] == b[:4] and abs(float(a[5]) - float(b[5])) < 1e-6 \
                    and any(abs(v - perna["odd"]) <= TOL_ODD_CUPOM + 1e-9 for v in _numeros(el.inner_text()) if 1.01 <= v <= 30)
            tx = _sem_acento(el.inner_text())
            odds = [v for v in _numeros(tx) if 1.01 <= v <= 30 and abs(v - abs(perna["linha"])) > 1e-9]
            odd_ok = any(abs(v - perna["odd"]) <= TOL_ODD_CUPOM + 1e-9 for v in odds)
            if not (odd_ok and _coexiste(perna["linha"], tx, usar_abs=True)):
                return False
            if perna["tipo"] == "total":
                quer = r"\b(acima|mais|over)\b" if perna["lado"] == "acima" else r"\b(abaixo|menos|under)\b"
                return bool(re.search(quer, tx, re.I))
            return True
        except Exception:
            return False

    def _clicar_mid(self, mid, odd):
        loc = self.page.locator('[data-mid="%s"]' % mid)
        if not loc.count():
            return False
        el = loc.first
        if not el.is_visible():
            return False
        tag = el.evaluate("e => e.tagName")
        if tag == "TD":  # na tabela da PS o clique vai no subelemento cujo texto é a odd
            sub = el.evaluate_handle("""(e, odd) => {
                const w = document.createTreeWalker(e, NodeFilter.SHOW_ELEMENT);
                let best = null;
                for (let n = w.currentNode; n; n = w.nextNode()) if ((n.textContent||'').trim() === odd) best = n;
                return best || e; }""", odd)
            return self.clicar_firme(sub.as_element())
        return self.clicar_firme(el)

    def conferir_cupom(self, perna):
        """Lê o cupom e confere seleção, linha e odd (tol 0,05)."""
        tx = self.texto_cupom()
        if _RX_EXCLUIDO.search(tx):
            return False, "cupom mostra 'Evento excluído' — mercado encerrado, perna morta"
        if _RX_INDISPONIVEL.search(tx):
            return False, "a casa marcou a seleção como indisponível no cupom"
        m = _RX_ODD_MUDOU.search(_sem_acento(tx))
        if m:
            linha = next((l for l in tx.splitlines() if _RX_ODD_MUDOU.search(_sem_acento(l))), m.group(0))
            # "from A to B": aviso velho de outra seleção fica no boletim (caso 14/11 16:10:31,
            # 'from 1.689 to 1.909' numa perna @2.06). Só reprova se a odd NOVA saiu da tolerância.
            mm = re.search(r"(\d+[.,]\d+)\D+(\d+[.,]\d+)", linha)
            antiga, nova = (_num(mm.group(1)), _num(mm.group(2))) if mm else (None, None)
            desta = antiga is None or abs(antiga - perna["odd"]) <= TOL_ODD_CUPOM + 1e-9
            if desta and (nova is None or abs(nova - perna["odd"]) > TOL_ODD_CUPOM + 1e-9):
                return False, "a casa avisou no cupom que a odd mudou (%s)" % linha.strip()[:80]
            # aviso de OUTRA seleção (o "de X" não é a odd clicada): ignora; a odd do item é conferida abaixo
        itens = self.itens_cupom()
        if len(itens) != 1:
            return False, "cupom com %d seleções (esperado 1)" % len(itens)
        it = _sem_acento(itens[0])
        if perna.get("_aid"):
            ids = self._eval("() => window.__idsCupom || []") or []
            if ids and ids[0]:
                a, b = ids[0].split("|"), perna["_aid"].split("|")
                mesma = len(a) >= 6 and len(b) >= 6 and a[:4] == b[:4] and abs(float(a[5]) - float(b[5])) < 1e-6
                if not mesma:
                    return False, "boletim tem outra seleção (%s ≠ %s)" % (ids[0], perna["_aid"])
        if not _coexiste(perna["linha"], it, usar_abs=True):
            return False, "cupom sem a linha %s" % _fmt_linha(perna["linha"])
        odds = [v for v in _numeros(it) if 1.01 <= v <= 30 and abs(v - abs(perna["linha"])) > 1e-9]
        # +1e-9: 2.0 - 1.95 = 0.0500000000000004 no computador — 0,05 exato é DENTRO da tolerância
        if not any(abs(v - perna["odd"]) <= TOL_ODD_CUPOM + 1e-9 for v in odds):
            return False, "odd do cupom difere de %s (lidas: %s)" % (perna["odd"], ", ".join(map(str, odds[:4])) or "nenhuma")
        if perna["tipo"] == "total":
            quer = r"\b(acima|mais|over)\b" if perna["lado"] == "acima" else r"\b(abaixo|menos|under)\b"
            if not re.search(quer, it, re.I):
                return False, "cupom não mostra o lado '%s'" % perna["lado"]
        elif perna.get("time") and not any(_nome_bate(l, perna["time"]) for l in re.split(r"[\n(]", itens[0]) if l.strip()):
            return False, "cupom não mostra o time %s" % perna["time"]
        return True, ""

    def preencher_stake(self, valor):
        """PS: com PONTO e lido de volta (com vírgula a PS lê €500). 1aWin: idem + leitura de volta."""
        if not self.itens_cupom():
            return False, "a seleção sumiu do cupom antes da stake (a casa tirou a linha do ar)"
        loc = self.page.locator(", ".join("%s input" % s.strip() for s in self.sel_cupom.split(",")))
        alvo = None
        for i in range(loc.count()):
            el = loc.nth(i)
            if not el.is_visible() or (el.get_attribute("type") or "text") not in ("text", "number", "tel", "decimal"):
                continue
            rotulo = " ".join(filter(None, [el.get_attribute("placeholder"), el.get_attribute("name"),
                                            el.get_attribute("aria-label")])).lower()
            # NUNCA o campo de "Código da aposta"/promo (caso 14/11: stake digitada no código, cupom vazio)
            if re.search(r"c[oó]digo|code|promo|cupom|coupon|bonus|b[oô]nus|ganho|win", _sem_acento(rotulo)):
                continue
            alvo = el
            break
        if alvo is None:
            return False, "campo de stake não encontrado no cupom"
        formatos = ["%.2f" % valor] if self.nome == CASA_PS else ["%.2f" % valor, ("%.2f" % valor).replace(".", ",")]
        lido = None
        for f in formatos:
            alvo.click()
            alvo.fill("")
            alvo.type(f, delay=15)
            alvo.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles: true})); e.blur(); }")
            self.page.wait_for_timeout(250)
            lido = _num(alvo.input_value())
            if lido is not None and abs(lido - valor) < 0.011:
                return True, ""
        try:
            alvo.fill("")
        except Exception:
            pass
        return False, "stake lida de volta %s ≠ %s — não segue" % (lido, _fmt(valor))

    def montar(self, perna, times):
        """Monta UMA perna e registra quanto tempo levou (a casa mexe rápido no ao vivo)."""
        t0 = time.time()
        r = self._montar(perna, times)
        r["segundos"] = round(time.time() - t0, 1)
        log("%s: montagem levou %.1fs" % (self.rotulo, r["segundos"]))
        return r

    def _montar(self, perna, times):
        """Monta UMA perna, no mesmo fôlego da leitura. Retorna {ok, motivo, foto}."""
        r = {"ok": False, "motivo": "", "foto": ""}
        tx = self.texto()
        if self.esta_limitada(tx):
            r["motivo"] = "casa limitada (Excesso de requisições)"
            return r
        if self.nome == CASA_PS and not ps_e_o_jogo(tx, times):
            r["motivo"] = "PS não está no matchup deste jogo (a página é lista) — montagem abortada"
            return r
        self.fechar_aviso_indisponivel()
        self.mostrar_boletim()
        if self.itens_cupom():  # quarentena
            log("%s: cupom sujo antes de montar — limpando primeiro" % self.rotulo)
            if not self.limpar_cupom():
                r["motivo"] = "cupom sujo que não limpou — não monto por cima (viraria múltipla)"
                return r
        clicou = False
        mid = perna.get("_mid")
        if mid:
            try:
                loc = self.page.locator('[data-mid="%s"]' % mid)
                if loc.count() and self._carimbo_vivo(loc.first, perna):
                    txb = loc.first.inner_text()
                    oddb = min((v for v in _numeros(txb) if 1.01 <= v <= 30 and abs(v - abs(perna["linha"])) > 1e-9),
                               key=lambda v: abs(v - perna["odd"]), default=perna["odd"])
                    clicou = self._clicar_mid(mid, _odd_txt(txb, oddb))
            except Exception:
                clicou = False
        if not clicou:  # carimbo morto: relê com a mesma enumeração e clica por identidade
            o = self._achar_por_identidade(perna, times)
            if o:
                try:
                    clicou = self._clicar_mid(o["_mid"], _odd_txt(o["_src"], o["odd"]))
                    if clicou:
                        perna["odd_viva"] = o["odd"]
                except Exception:
                    clicou = False
        if not clicou:
            mesmas = sorted({o["linha"] for o in getattr(self, "_ultimas_ofs", []) or []
                             if o["tipo"] == perna["tipo"] and o["periodo"] == perna["periodo"]})
            if mesmas and perna["tipo"] == "total" and not any(abs(l - perna["linha"]) < 0.001 for l in mesmas):
                r["motivo"] = "a linha %s não está mais na página — a casa trocou as linhas (agora: %s)" % (
                    _fmt_linha(perna["linha"]), " / ".join(_fmt_linha(l) for l in mesmas))
            else:
                r["motivo"] = "a leitura não achou a seleção na página (%s)" % descricao_oferta(perna)
            r["foto"] = self.foto("falha")
            self.dump("falha")
            return r
        ok, mot = False, "cupom não recebeu a seleção"
        # a PS demora mais para jogar linha alternativa/quarto no boletim (spinner) — caso 14/11 16:05
        fim = time.time() + (7 if self.nome == CASA_PS else 4)
        while time.time() < fim:
            self.page.wait_for_timeout(250)
            if self.fechar_aviso_indisponivel():
                ok, mot = False, "a PS marcou a seleção como indisponível (aposta mínima maior que a máxima)"
                break
            self.mostrar_boletim()
            if _RX_EXCLUIDO.search(self.texto_cupom() or ""):
                ok, mot = False, "cupom mostra 'Evento excluído' — mercado encerrado, perna morta"
                break
            if self.itens_cupom():
                ok, mot = self.conferir_cupom(perna)
                if ok or "Evento exclu" in mot:
                    break
        if not ok and mot == "cupom não recebeu a seleção" and perna.get("_aid"):
            try:  # PS: travou o mercado (cadeado) no instante do clique?
                travado = self._eval("""(aid) => { const a = document.getElementById(aid);
                    return !!(a && a.parentElement && a.parentElement.querySelector(':scope > .style-lock:not(.lock-hidden)')); }""",
                                     perna["_aid"])
                if travado:
                    mot = "a PS travou o mercado (cadeado) no instante do clique"
            except Exception:
                pass
        if not ok:
            r["motivo"] = mot
            r["foto"] = self.foto("falha")
            self.dump("falha")
            if not self.limpar_cupom(perna):
                r["motivo"] += " — e o cupom NÃO esvaziou: limpe à mão"
                log("‼ %s: cupom NÃO esvaziou depois da falha — limpe à mão" % self.rotulo)
            return r
        valor = perna["stake"] / self.cfg["cambio_brl_eur"] if self.nome == CASA_PS else perna["stake"]
        ok, mot = self.preencher_stake(round(valor, 2))
        if ok:
            # reconfere DEPOIS da stake: a seleção tem que continuar lá, a mesma
            self.page.wait_for_timeout(300)
            if not self.itens_cupom():
                ok, mot = False, "a seleção sumiu do cupom depois da stake (a casa tirou a linha do ar)"
            else:
                ok, mot = self.conferir_cupom(perna)
                if not ok:
                    mot = "após a stake: " + mot
        if not ok:
            r["motivo"] = mot
            r["foto"] = self.foto("falha")
            self.dump("falha")
            if not self.limpar_cupom(perna):
                r["motivo"] += " — e o cupom NÃO esvaziou: limpe à mão"
                log("‼ %s: cupom NÃO esvaziou depois da falha — limpe à mão" % self.rotulo)
            return r
        r.update(ok=True, foto=self.foto("montado"))
        return r

    def desfazer(self, perna=None):
        ok = self.limpar_cupom(perna)
        return {"ok": ok, "foto": self.foto("desfeito"), "motivo": "" if ok else "cupom não esvaziou"}

    def _pendentes(self):
        """Contador de apostas pendentes (PS: 'APOSTAS PENDENTES' com o número ao lado)."""
        try:
            return self._eval(r"""() => {
                for (const e of document.querySelectorAll('.PENDING_BETS, [title*="PENDENTES" i], [title*="pending" i]')) {
                    const box = e.closest('.selection-button') || e.parentElement;
                    const n = box && box.querySelector('.bet-count');
                    if (n) return parseInt(n.innerText) || 0;
                    return 0;
                }
                return null;
            }""")
        except Exception:
            return None

    def _marcar_botao_apostar(self):
        """Marca UM único botão de apostar, perto do cupom. Nunca dois (dois cliques = duas apostas)."""
        return self._eval(r"""(sc) => {
            document.querySelectorAll('[data-alvo-apostar]').forEach(e => e.removeAttribute('data-alvo-apostar'));
            const RX = /^\s*(fazer\s+(a\s+|uma\s+|\d+\s+)?apostas?|apostar|place\s+(\d+\s+)?bets?|confirmar\s+aposta|realizar\s+aposta)\s*$/i;
            const raizes = [];
            for (const c of document.querySelectorAll(sc)) { const r = c.closest('aside') || c; if (!raizes.includes(r)) raizes.push(r); }
            for (const r of raizes) {
                const bts = Array.from(r.querySelectorAll('button, [role=button]')).filter(b => {
                    const rc = b.getBoundingClientRect();
                    return rc.width > 0 && rc.height > 0 && RX.test(b.innerText || '');
                });
                if (bts.length === 1) { bts[0].setAttribute('data-alvo-apostar', '1');
                    return {n: 1, desabilitado: bts[0].disabled || /disabled/.test(bts[0].className + '')}; }
                if (bts.length > 1) return {n: bts.length};
            }
            return {n: 0};
        }""", self.sel_cupom) or {"n": 0}

    def confirmar(self, perna=None):
        """Só no modo real. Reconfere a perna, clica UM botão de apostar e decide por PROVAS:
        ok=True (aceita), ok=False (recusada — nada apostado) ou ok=None (INCERTO: não sei se
        entrou; quem chamou para tudo e avisa, sem desfazer nada)."""
        if perna is not None:
            ok, mot = self.conferir_cupom(perna)
            if not ok:
                return {"ok": False, "motivo": "não confirmei — " + mot, "foto": self.foto("confirmar")}
        b = self._marcar_botao_apostar()
        if b.get("n") != 1:
            return {"ok": False, "motivo": "botão de apostar: achei %d (preciso de exatamente 1) — não cliquei" % b.get("n", 0),
                    "foto": self.foto("confirmar")}
        if b.get("desabilitado"):
            return {"ok": False, "motivo": "botão de apostar desabilitado — não cliquei", "foto": self.foto("confirmar")}
        antes = self._pendentes()
        self.dump("antes_confirmar")
        try:
            self.page.locator('[data-alvo-apostar="1"]').first.click(timeout=3000)
        except Exception as e:
            return {"ok": False, "motivo": "o clique em apostar não aconteceu (%s)" % str(e)[:80], "foto": self.foto("confirmar")}
        log("%s: cliquei em APOSTAR" % self.rotulo)
        fim = time.time() + 15
        while time.time() < fim:
            self.page.wait_for_timeout(400)
            tx = self.texto()
            if _RX_SUCESSO.search(tx):
                return {"ok": True, "motivo": "", "foto": self.foto("confirmado")}
            depois = self._pendentes()
            if antes is not None and depois is not None and depois > antes:
                return {"ok": True, "motivo": "apostas pendentes %d -> %d" % (antes, depois), "foto": self.foto("confirmado")}
            if _RX_RECUSA.search(tx):
                m = _RX_RECUSA.search(tx)
                if self.itens_cupom():  # recusou e a seleção continua no cupom: nada foi apostado
                    return {"ok": False, "motivo": "a casa recusou: %s" % m.group(0), "foto": self.foto("confirmar")}
        self.dump("incerto")
        vazio = not self.itens_cupom()
        return {"ok": None, "foto": self.foto("incerto"),
                "motivo": "INCERTO — sem mensagem de aceita/recusada em 15s (cupom %s). Confira em Apostas pendentes/Histórico" % (
                    "esvaziou" if vazio else "ainda com a seleção")}

    def recarregar(self):
        try:
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
        except Exception:
            pass
        lim = self.esta_limitada()
        self.limitada = lim
        return lim

    # -------------------------------------------------- caças
    def cacar(self, times, liga_dica=""):
        if self.nome == CASA_PS:
            return self._cacar_ps(times)
        return self._cacar_1w(times, liga_dica)

    def _sondar(self, teste, segundos):
        fim = time.time() + segundos
        while True:
            r = teste()
            if r or time.time() >= fim:
                return r
            self.page.wait_for_timeout(250)

    def _cacar_ps(self, times):
        t0 = time.time()
        prazo = t0 + PRAZO_CACA_S
        teste = lambda: ps_e_o_jogo(self.texto(), times)
        r = self._sondar(teste, 1.0)  # (a) já estou na página do jogo?
        if r:
            return self._achou_ps(r, times, t0, "já estava na página")
        if self.esta_limitada():
            return {"ok": False, "limitada": True, "motivo": "PS limitada (Excesso de requisições)"}
        url = url_memorizada(CASA_PS, times)  # (b) endereço decorado
        if url:
            try:
                self.ir(url)
                r = self._sondar(teste, 6)
                if r:
                    return self._achou_ps(r, times, t0, "endereço decorado")
            except Exception:
                pass
        try:  # (c) lista
            self.ir(self.url_lista())
        except Exception as e:
            return {"ok": False, "motivo": "lista da PS não abriu: %s" % str(e)[:100]}
        # a lista de basquete mistura ao vivo + o dia todo (centenas) e só desenha o que está na
        # tela: primeiro AO VIVO -> Basquetebol no menu da esquerda (caso Portimonense 14/11)
        self._ps_so_ao_vivo()
        rolagem = 0
        buscou = False
        while time.time() < prazo:
            if rolagem >= 6 and not buscou:  # plano B: a busca da própria PS
                buscou = self._ps_buscar(times)
            if self.esta_limitada():
                return {"ok": False, "limitada": True, "motivo": "PS limitada (Excesso de requisições)"}
            alvo = self._marcar_linha_jogo(times, prefer_href="/matchup/")
            if not alvo:  # rola a lista para a PS desenhar o resto
                fim = self._eval("() => { const y = window.scrollY; window.scrollBy(0, 1400); return window.scrollY === y; }")
                rolagem += 1
                if fim:
                    self._eval("() => window.scrollTo(0, 0)")
            if alvo:
                try:
                    self.clicar_firme(self.page.locator('[data-alvo-jogo="1"]').first)
                except Exception:
                    pass
                r = self._sondar(teste, 8)
                if r:
                    return self._achou_ps(r, times, t0, "lista")
                try:
                    self.ir(self.url_lista())
                except Exception:
                    pass
            self.page.wait_for_timeout(500)
        try:  # raio-x: todos os jogos que estavam na lista da PS (decide se o jogo estava lá)
            jogos = self._eval("""() => Array.from(document.querySelectorAll('[data-home-team]'))
                .map(e => (e.getAttribute('data-home-team') || '').trim() + ' x ' + (e.getAttribute('data-away-team') || '').trim())""") or []
            jogos = sorted(set(jogos))
            _gravar_json(os.path.join(PRINTS, "%s_ps_caca_falhou.json" % datetime.now().strftime("%Y%m%d-%H%M%S")),
                         {"procurado": times, "url": self.page.url, "jogos_na_lista": jogos})
            self.foto("caca_falhou")
        except Exception:
            jogos = []
        return {"ok": False, "motivo": "PS: jogo não encontrado na lista em %ds (%d jogos na lista; raio-x em dados/prints)" % (
            PRAZO_CACA_S, len(jogos))}

    def _ps_buscar(self, times):
        """Digita o nome do 1º time na busca da PS ('Pesquise por ligas e nomes de equipes')."""
        try:
            caixa = self.page.locator('input[placeholder*="esquise" i], input[placeholder*="earch" i]').first
            if not caixa.count() or not caixa.is_visible():
                return True
            palavra = max(_tokens(times[0]) or [times[0]], key=len)
            caixa.click()
            caixa.fill("")
            caixa.type(palavra, delay=30)
            self.page.wait_for_timeout(2500)
            log("PS: busca da casa por '%s'" % palavra)
        except Exception:
            pass
        return True

    def _ps_so_ao_vivo(self):
        """Clica 'Basquetebol' na seção AO VIVO do menu da esquerda (se existir)."""
        try:
            loc = self.page.locator('.SportLiveTabContentComponent .sp4 .SportMenuItemInfoComponent, '
                                    '.SportLiveTabContentComponent .sp4').first
            if loc.count() and loc.is_visible():
                self.clicar_firme(loc)
                self.page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
        return False

    def _achou_ps(self, r, times, t0, via):
        url = self.page.url
        liga = liga_da_url_ps(url) or r.get("liga", "")
        if r.get("liga") and liga_da_url_ps(url):
            liga = r["liga"] if liga_parecida(r["liga"], liga_da_url_ps(url)) > 0.5 else liga_da_url_ps(url)
        self.jogo = {"times": list(times), "url": url, "liga": liga, "times_casa": r["times_casa"]}
        memorizar_url(CASA_PS, times, url)
        log("PS: achei %s -vs- %s em %.1fs (%s) liga=%s" % (r["times_casa"][0], r["times_casa"][1], time.time() - t0, via, liga or "?"))
        return {"ok": True, "liga": liga, "url": url}

    def _marcar_linha_jogo(self, times, prefer_href=""):
        """Marca (data-alvo-jogo) o menor elemento clicável cujo texto tem os dois times.
        Filtro rápido na página: só volta elemento que tem uma PISTA de cada time (senão a
        lista da PS ao vivo, enorme, estoura o prazo — caso 14/11 15:04)."""
        # a cada 3 voltas, busca completa sem filtro (nome muito diferente entre as casas)
        self._voltas_caca = getattr(self, "_voltas_caca", 0) + 1
        if self._voltas_caca % 3 == 0:
            p1, p2 = pistas_nome(times[0], 3), pistas_nome(times[1], 3)  # filtro frouxo (3 letras), nunca sem filtro
        else:
            p1, p2 = pistas_nome(times[0]), pistas_nome(times[1])
        cands = self._eval(r"""([pref, p1, p2]) => {
            document.querySelectorAll('[data-alvo-jogo]').forEach(e => e.removeAttribute('data-alvo-jogo'));
            const out = [];
            const nrm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
                .replace(/ł/g, 'l').replace(/ø/g, 'o').replace(/ß/g, 'ss');
            const tem = (t, ps) => !ps.length || ps.some(p => t.includes(p));
            const els = Array.from(document.querySelectorAll('a, [role=button], [onclick], div, li, tr'));
            els.forEach((e, i) => {
                const tc = e.textContent || '';
                if (tc.length > 600) return;
                const n = nrm(tc);
                if (!tem(n, p1) || !tem(n, p2)) return;
                const t = (e.innerText || '').trim();
                if (!t || t.length > 260) return;
                const r = e.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                out.push({i, t, href: e.getAttribute('href') || '', n: e.querySelectorAll('*').length});
                e.setAttribute('data-cand-jogo', String(i));
            });
            return out;
        }""", [prefer_href, p1, p2])
        bons = []
        for len_ in (False, True):
            for c in cands:
                pedacos = [p for p in re.split(r"\n|\s+(?:—|–|-vs-|vs\.?|x|-)\s+", c["t"]) if p.strip()]
                a = any(_nome_bate(p, times[0], len_) for p in pedacos)
                b = any(_nome_bate(p, times[1], len_) for p in pedacos)
                if a and b:
                    bons.append(c)
            if bons:
                break
        if not bons:
            return None
        bons.sort(key=lambda c: (0 if prefer_href and prefer_href in c["href"] else 1, c["n"], len(c["t"])))
        self._eval("""(i) => { let e = document.querySelector('[data-cand-jogo="' + i + '"]');
            const a = e && e.closest('a[href]');
            if (a && a.innerText.length < 400) e = a;
            if (e) e.setAttribute('data-alvo-jogo', '1');
            document.querySelectorAll('[data-cand-jogo]').forEach(x => x.removeAttribute('data-cand-jogo')); }""", bons[0]["i"])
        return bons[0]

    def _e_jogo_1w(self, times):
        if "/betting/match/" not in (self.page.url or "") and "/match/" not in (self.page.url or ""):
            return None
        return linha_confronto(self.texto(), times)

    def _cacar_1w(self, times, liga_dica=""):
        t0 = time.time()
        prazo = t0 + PRAZO_CACA_S
        teste = lambda: self._e_jogo_1w(times)
        r = self._sondar(teste, 1.0)
        if r:
            return self._achou_1w(r, times, t0, "já estava na página", None)
        url = url_memorizada(CASA_1W, times)
        if url:
            try:
                self.ir(url)
                r = self._sondar(teste, 6)
                if r:
                    return self._achou_1w(r, times, t0, "endereço decorado", None)
            except Exception:
                pass
        try:
            self.ir(self.url_lista())
        except Exception as e:
            return {"ok": False, "motivo": "lista da 1aWin não abriu: %s" % str(e)[:100]}
        # o jogo pode já estar visível na lista ao vivo (ligas abertas com os jogos dentro):
        # espera a lista carregar de verdade — a 1aWin é lenta (caso Krosno x Inowroclaw 14/11)
        if self._sondar(lambda: self._marcar_linha_jogo(times, prefer_href="/match"), 5.0):
            self.clicar_firme(self.page.locator('[data-alvo-jogo="1"]').first)
            r = self._sondar(teste, 8)
            if r:
                return self._achou_1w(r, times, t0, "lista", None)
            self.ir(self.url_lista())
        ligas = self._sondar(self._mapa_ligas, 8)
        if not ligas:
            return {"ok": False, "motivo": "1aWin: lista ao vivo de basquete veio vazia (feed vazio)"}
        ordem = ordenar_ligas(ligas, liga_dica, fem=any("fem" in _marcas(t) for t in times))
        notas = {lg["url"]: (liga_parecida(liga_dica, lg["nome"]) if liga_dica else 0.0) for lg in ordem}
        log("1aWin: %d liga(s) de basquete; primeiras: %s" % (len(ordem), "; ".join(
            "%s (%.2f)" % (lg["nome"], notas[lg["url"]]) for lg in ordem[:4])))
        tentadas = []
        for lg in ordem:
            if time.time() >= prazo:
                break
            try:
                self.ir(lg["url"])
            except Exception:
                continue
            tentadas.append(lg["nome"])
            # liga provável: espera a página da liga carregar de verdade (a 1aWin é lenta)
            espera = 8.0 if notas.get(lg["url"], 0) >= 0.5 or len(tentadas) <= 2 else 3.0
            achou = self._sondar(lambda: self._marcar_linha_jogo(times, prefer_href="/match"), espera)
            if not achou:
                continue
            try:
                self.clicar_firme(self.page.locator('[data-alvo-jogo="1"]').first)
            except Exception:
                continue
            r = self._sondar(teste, 8)
            if r:
                return self._achou_1w(r, times, t0, "liga %s" % lg["nome"], (lg, liga_dica))
        _gravar_json(os.path.join(PRINTS, "%s_1awin_caca_falhou.json" % datetime.now().strftime("%Y%m%d-%H%M%S")),
                     {"times": times, "liga_dica": liga_dica, "tentadas": tentadas,
                      "ligas": [{"nome": lg["nome"], "nota": round(notas[lg["url"]], 2)} for lg in ordem]})
        return {"ok": False, "motivo": "1aWin: jogo não encontrado (%d de %d liga(s) olhadas em %ds; lista em dados/prints)" % (
            len(tentadas), len(ordem), PRAZO_CACA_S)}

    def _mapa_ligas(self):
        """Mapa de URLs reais das ligas extraído da lista de basquete (dedupe).
        Só basquete de verdade: cyber/e-basket/2K/virtual/simulado ficam fora."""
        caminho = self.cfg["caminho_lista_1awin"].rstrip("/")
        itens = self._eval("""(cam) => Array.from(document.querySelectorAll('a[href]'))
            .map(a => ({nome: (a.innerText||'').trim(), href: a.href}))
            .filter(x => x.nome && x.href.indexOf(cam + '/') >= 0)""", caminho)
        vistos, out = set(), []
        for it in itens:
            nome = re.sub(r"\s*\d+\s*$", "", it["nome"].split("\n")[0]).strip()
            if it["href"] in vistos or not nome or _e_liga_virtual(nome + " " + it["href"]):
                continue
            vistos.add(it["href"])
            out.append({"nome": nome, "url": it["href"]})
        return out

    def _achou_1w(self, partes, times, t0, via, liga_info):
        url = self.page.url
        self.jogo = {"times": list(times), "url": url, "times_casa": partes}
        memorizar_url(CASA_1W, times, url)
        if liga_info and liga_info[1]:
            confirmar_liga(liga_info[1], liga_info[0])
        log("1aWin: achei %s — %s em %.1fs (%s)" % (partes[0], partes[1], time.time() - t0, via))
        return {"ok": True, "url": url}


def _odd_txt(src, odd):
    for m in re.finditer(r"\d{1,2}[.,]\d{1,3}", src or ""):
        if abs(_num(m.group(0)) - odd) < 1e-9:
            return m.group(0)
    return "%.2f" % odd


_MEM_LIGAS = None


def _mem_ligas():
    global _MEM_LIGAS
    if _MEM_LIGAS is None:
        _MEM_LIGAS = _ler_json(ARQ_LIGAS, {})
    return _MEM_LIGAS


def confirmar_liga(liga_ps, liga_1w):
    """Equivalência PT<->EN só vira CONFIRMADA depois de um jogo validado dentro da liga."""
    m = _mem_ligas()
    m[_liga_norm(liga_ps)] = {"nome": liga_1w["nome"], "url": liga_1w["url"], "confirmada": True,
                              "ps": liga_ps, "quando": datetime.now().isoformat(timespec="seconds")}
    _gravar_json(ARQ_LIGAS, m)


def ordenar_ligas(ligas, liga_dica, limiar=0.63, margem=0.12, fem=False):
    """Liga confirmada na memória primeiro; depois por similaridade com a dica.
    Jogo feminino: ligas femininas na frente (caso Alges x Ferragudo 14/11)."""
    if fem:
        ligas = sorted(ligas, key=lambda lg: 0 if "fem" in _marcas(lg["nome"]) else 1)
    if not liga_dica:
        return ligas
    mem = _mem_ligas().get(_liga_norm(liga_dica))
    pont = []
    for lg in ligas:
        s = liga_parecida(liga_dica, lg["nome"])
        if mem and mem.get("confirmada") and (lg["url"] == mem.get("url") or _norm(lg["nome"]) == _norm(mem.get("nome", ""))):
            s = 2.0
        pont.append((s, lg))
    pont.sort(key=lambda x: -x[0])
    if len(pont) >= 2 and pont[0][0] >= limiar and pont[0][0] - pont[1][0] >= margem:
        log("1aWin: liga provável '%s' (%.2f) para '%s'" % (pont[0][1]["nome"], min(pont[0][0], 1), liga_dica))
    return [lg for _, lg in pont]


# ---------------------------------------------------------------- ESTADO (histórico do painel)
class Estado:
    def __init__(self, cfg, arquivo=None):
        self.cfg = cfg
        self.arquivo = arquivo or ARQ_HIST
        h = _ler_json(self.arquivo, {})
        self.bilhetes = h.get("bilhetes", [])
        self.resultados = h.get("resultados", [])
        self.backlog = []          # cartões de sinal antigo (só montam com clique)
        self.sinais = []           # sinais frescos recebidos (histórico curto)
        self.calculadora = None    # raio-x da última rodada sadia
        self.monitor = ""          # texto de status do monitoramento
        self._seq = max([b.get("id", 0) for b in self.bilhetes] + [0])

    def salvar(self):
        with _TRAVA:
            dados = {"bilhetes": self.bilhetes[-300:], "resultados": self.resultados[-300:]}
        _gravar_json(self.arquivo, dados)

    def registrar_bilhete(self, bil, origem):
        with _TRAVA:
            self._seq += 1
            bil.update({"id": self._seq, "origem": origem, "criado": datetime.now().strftime("%d/%m %H:%M:%S"),
                        "status": "registrado", "detalhe": {}, "fotos": [], "desfecho": ""})
            self.bilhetes.append(bil)
        self.salvar()
        return bil

    def atualizar(self, bil, **kw):
        with _TRAVA:
            bil.update(kw)
        self.salvar()

    def registrar_resultado(self, r):
        with _TRAVA:
            if any(x["id"] == r["id"] for x in self.resultados):
                return False
            r["quando"] = datetime.now().strftime("%d/%m %H:%M")
            self.resultados.append(r)
            for b in reversed(self.bilhetes):
                if b.get("times") and _nome_bate_jogo(r["jogo"], b["times"]):
                    b["desfecho"] = r["desfecho"]
                    break
        self.salvar()
        return True

    def set_calc(self, raiox, rodada):
        with _TRAVA:
            raiox = dict(raiox)
            raiox["hora"] = agora()
            raiox["rodada"] = rodada
            self.calculadora = raiox

    def set_monitor(self, txt):
        with _TRAVA:
            self.monitor = txt

    def foto_json(self):
        with _TRAVA:
            calc = None
            if self.calculadora:
                calc = dict(self.calculadora)
                if calc.get("par"):
                    calc["cenarios"] = cenarios(calc["par"])
                    calc["par"] = _sem_privados(calc["par"])
            return {
                "modo": "TESTE" if self.cfg["modo_teste"] else "REAL",
                "params": {k: self.cfg[k] for k in ("janela_min", "custo_max", "perna_ps_eur", "cambio_brl_eur",
                                                    "so_totais", "totais_periodos", "monitor_intervalo_s")},
                "bilhetes": [_sem_privados(b) for b in self.bilhetes[-60:]][::-1],
                "backlog": list(self.backlog),
                "sinais": self.sinais[-30:][::-1],
                "resultados": self.resultados[-30:][::-1],
                "calculadora": calc,
                "monitor": self.monitor,
                "log": _LOG_MEM[-80:],
            }


def _sem_privados(b):
    out = dict(b)
    if "pernas" in out:
        out["pernas"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in out["pernas"]]
    return out


def _mesmo_jogo(t1, t2):
    return (_nome_bate(t1[0], t2[0]) and _nome_bate(t1[1], t2[1])) or (_nome_bate(t1[0], t2[1]) and _nome_bate(t1[1], t2[0]))


def _nome_bate_jogo(jogo, times):
    t = _separar_times(jogo)
    return bool(t) and ((_nome_bate(t[0], times[0]) and _nome_bate(t[1], times[1])) or
                        (_nome_bate(t[0], times[1]) and _nome_bate(t[1], times[0])))


# ---------------------------------------------------------------- OPERADOR (monitoramento + execução)
class Operador:
    def __init__(self, cfg, estado, casas):
        self.cfg = cfg
        self.estado = estado
        self.casas = casas
        self._atual = None          # (thread, evento_cancelar, sinal)
        self._trava = threading.Lock()
        self._ultimo_por_id = {}

    # ---------------------------------------------- entrada de sinais
    def novo_sinal(self, sinal, origem="sinal"):
        """O sinal novo cancela o atual (inclusive estacionamento)."""
        ag = time.time()
        ult = self._ultimo_por_id.get(sinal["id"])
        if origem == "sinal" and ult and ag - ult < COOLDOWN_SINAL_S:
            log("sinal recusado (mesmo jogo+mercado há %ds, cooldown %ds): %s" % (ag - ult, COOLDOWN_SINAL_S, sinal["jogo"]))
            return False
        self._ultimo_por_id[sinal["id"]] = ag
        with self._trava:
            if origem == "sinal" and self._atual and self._atual[0].is_alive() and not self._atual[1].is_set() \
                    and _mesmo_jogo(self._atual[2]["times"], sinal["times"]):
                log("sinal do mesmo jogo (%s) — já estou monitorando %s: sigo sem reiniciar (todos os mercados já são varridos)" % (
                    sinal.get("mercado") or "—", self._atual[2]["jogo"]))
                return False
            if self._atual:
                self._atual[1].set()
                log("sinal novo assume — monitoramento de %s cancelado" % self._atual[2]["jogo"])
            ev = threading.Event()
            th = threading.Thread(target=self._monitorar, args=(sinal, ev, origem), name="monitor", daemon=True)
            self._atual = (th, ev, sinal)
        with _TRAVA:
            self.estado.sinais.append({"jogo": sinal["jogo"], "mercado": sinal.get("mercado", ""), "liga": sinal.get("liga", ""),
                                       "hora": agora(), "origem": origem})
        th.start()
        return True

    # ---------------------------------------------- monitoramento sem limite
    def _monitorar(self, sinal, cancel, origem):
        times = sinal["times"]
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        achado = {CASA_1W: False, CASA_PS: False}
        liga_ps = ""
        rodada = 0
        vazias = 0
        ultimo_motivo = None
        log("▶ %s: %s (mercado da dica: %s)" % ("SINAL" if origem == "sinal" else origem.upper(), sinal["jogo"], sinal.get("mercado") or "—"))
        while not cancel.is_set():
            try:
                if c1.limitada or cps.limitada:
                    self._estacionar(cancel, sinal)
                    continue
                if not achado[CASA_PS]:
                    self.estado.set_monitor("caçando %s na PS3838" % sinal["jogo"])
                    log("PS: caçando %s (até %ds)…" % (sinal["jogo"], PRAZO_CACA_S))
                    r = cps.executar(cps.cacar, times, timeout=PRAZO_CACA_S + 30)
                    if r.get("limitada"):
                        cps.limitada = True
                        continue
                    achado[CASA_PS] = r["ok"]
                    if r["ok"]:
                        liga_ps = r.get("liga", "")
                    else:
                        log("PS: " + r["motivo"])
                if cancel.is_set():
                    break
                if not achado[CASA_1W]:
                    self.estado.set_monitor("caçando %s na 1aWin" % sinal["jogo"])
                    log("1aWin: caçando %s (até %ds)…" % (sinal["jogo"], PRAZO_CACA_S))
                    r = c1.executar(c1.cacar, times, liga_ps or sinal.get("liga", ""), timeout=PRAZO_CACA_S + 30)
                    achado[CASA_1W] = r["ok"]
                    if not r["ok"]:
                        log("1aWin: " + r["motivo"])
                if cancel.is_set():
                    break
                rodada += 1
                esperas = {}
                for c in (c1, cps):
                    if achado[c.nome]:
                        esperas[c.nome] = c.em_paralelo(c.ler_escada, times, timeout=90)
                lt = {}
                for nome, w in esperas.items():
                    try:
                        lt[nome] = w()
                    except Exception as e:
                        lt[nome] = {"ok": False, "ofertas": [], "motivo": "erro na leitura: %s" % e, "limitada": False}
                for nome, l in lt.items():
                    if l.get("limitada"):
                        self.casas[nome].limitada = True
                    elif not l["ok"] and "não é" in l.get("motivo", ""):
                        achado[nome] = False  # saiu da página do jogo: caça de novo na próxima
                if c1.limitada or cps.limitada:
                    continue
                n1 = len(lt.get(CASA_1W, {}).get("ofertas", []))
                n2 = len(lt.get(CASA_PS, {}).get("ofertas", []))
                if n1 and n2:
                    tipo = "sadia"
                    vazias = 0
                elif n1 or n2:
                    tipo = "parcial"
                    vazias = 0
                else:
                    tipo = "vazia"
                    vazias += 1
                self.estado.set_monitor("monitorando %s — rodada %d (%s: 1aWin %d ofertas, PS %d ofertas)" % (
                    sinal["jogo"], rodada, tipo, n1, n2))
                if tipo == "sadia":
                    raiox = {"jogo": sinal["jogo"]}
                    bil, motivo = achar_par(lt[CASA_1W]["ofertas"], lt[CASA_PS]["ofertas"], sinal, self.cfg, raiox)
                    self.estado.set_calc(raiox, rodada)
                    if bil:
                        log("✔ PAR: %s @%s × %s @%s | janela %s | custo %s%%" % (
                            bil["pernas"][0]["selecao"], bil["pernas"][0]["odd"], bil["pernas"][1]["selecao"],
                            bil["pernas"][1]["odd"], _br(bil["janela"], 1), _br(bil["custo"])))
                        self.estado.registrar_bilhete(bil, origem)
                        self.estado.atualizar(bil, status="executando")
                        st = self.executar_bilhete(bil)
                        if st in ("testado", "confirmado", "incerto", "exposto"):
                            self.estado.set_monitor("%s: %s — monitoramento encerrado" % (sinal["jogo"], st))
                            log("■ %s: rodada %s — monitoramento encerrado" % (sinal["jogo"], st))
                            return
                        log("montagem falhou — o monitoramento reassume %s" % sinal["jogo"])
                        ultimo_motivo = None
                    elif motivo != ultimo_motivo or rodada % 3 == 0:
                        log("%s rodada %d: %s" % (sinal["jogo"], rodada, motivo))
                        ultimo_motivo = motivo
                else:
                    mot = "; ".join("%s: %s" % (NOME_CASA[n], l.get("motivo") or "%d ofertas" % len(l["ofertas"])) for n, l in lt.items())
                    if rodada % 3 == 1 or mot != ultimo_motivo:
                        log("%s rodada %d %s (%s)%s" % (sinal["jogo"], rodada, tipo, mot or "nenhuma casa achou o jogo",
                                                       " — %d vazias seguidas" % vazias if vazias > 1 else ""))
                        ultimo_motivo = mot
            except Exception as e:
                msg = str(e)
                if any(k in msg for k in ("has been closed", "Target closed", "Connection closed", "passou do prazo")):
                    # janela caiu no meio (o laço da casa reabre em 5s): caça de novo nas duas, sem esperar a rodada
                    log("janela de uma casa caiu no meio da rodada — reabre sozinha e eu retomo a caça (%s)" % msg.split("\n")[0][:90])
                    achado[CASA_1W] = achado[CASA_PS] = False
                    cancel.wait(6)
                    continue
                log("erro no monitoramento (segue): %s" % msg.split("\n")[0][:200])
                log(traceback.format_exc()[-600:])
            cancel.wait(self.cfg["monitor_intervalo_s"])

    def _estacionar(self, cancel, sinal):
        """Casa limitada: para de ler/navegar; 1 reload a cada 120s até liberar ou sinal novo."""
        lim = [c for c in self.casas.values() if c.limitada]
        nomes = ", ".join(c.rotulo for c in lim)
        log("⏸ %s limitada (Excesso de requisições) — monitoramento ESTACIONADO; sondando a cada %ds" % (nomes, SONDA_LIMITE_S))
        self.estado.set_monitor("ESTACIONADO: %s limitada — sondando a cada %ds (%s)" % (nomes, SONDA_LIMITE_S, sinal["jogo"]))
        while not cancel.is_set():
            if cancel.wait(SONDA_LIMITE_S):
                return
            ainda = []
            for c in lim:
                try:
                    if c.executar(c.recarregar, timeout=60):
                        ainda.append(c)
                except Exception:
                    ainda.append(c)
            if not ainda:
                log("▶ %s liberada — monitoramento retomado" % nomes)
                return
            log("%s ainda limitada — próxima sonda em %ds" % (", ".join(c.rotulo for c in ainda), SONDA_LIMITE_S))

    # ---------------------------------------------- execução de um bilhete
    def executar_bilhete(self, bil):
        """Monta as duas casas em paralelo. Teste: fotografa e desfaz. Real: confirma
        1aWin primeiro, PS depois. Qualquer montagem falhou -> nada confirma."""
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        p1, p2 = bil["pernas"]
        times = bil["times"]
        with _TRAVA_CLIQUES:
            w1 = c1.em_paralelo(c1.montar, p1, times, timeout=ESPERA_FASE1_S)
            w2 = cps.em_paralelo(cps.montar, p2, times, timeout=ESPERA_FASE1_S)
            res = {}
            for nome, w in ((CASA_1W, w1), (CASA_PS, w2)):
                try:
                    res[nome] = w()
                except Exception as e:
                    res[nome] = {"ok": False, "motivo": "erro: %s" % e, "foto": ""}
            if all(r["ok"] for r in res.values()):
                # CONFERÊNCIA FINAL, no instante em que o modo real confirmaria: as duas pernas
                # ainda são as mesmas, na mesma odd? (caso Ratiopharm 15:21: PS 2.000 -> 1.675
                # depois de montado)
                self._conferencia_final(res, bil)
            fotos = [r["foto"] for r in res.values() if r.get("foto")]
            detalhe = {NOME_CASA[n]: ("montou" if r["ok"] else r["motivo"]) for n, r in res.items()}
            for n, r in res.items():
                log("%s: %s" % (NOME_CASA[n], "montou %s" % ("1aWin" if n == CASA_1W else "PS") if r["ok"] else "FALHOU — " + r["motivo"]))
            if not all(r["ok"] for r in res.values()):
                self._desfazer(res, bil, fotos, detalhe)
                self.estado.atualizar(bil, status="falhou", detalhe=detalhe, fotos=fotos)
                return "falhou"
            if self.cfg["modo_teste"]:
                ok = self._desfazer(res, bil, fotos, detalhe)
                st = "testado" if ok else "falhou"
                self.estado.atualizar(bil, status=st, detalhe=detalhe, fotos=fotos)
                return st
            # MODO REAL — frágil primeiro, com a outra já montada
            r1 = c1.executar(c1.confirmar, p1, timeout=40)
            fotos.append(r1.get("foto", ""))
            if r1["ok"] is None:
                detalhe["1aWin"] = r1["motivo"]
                detalhe["PS3838"] = "NÃO confirmada (montada, parada esperando você)"
                log("‼ 1aWin INCERTA — não sei se a aposta entrou. PAREI: confira 'Apostas pendentes' na 1aWin AGORA. "
                    "A PS está montada e NÃO foi confirmada.")
                self.estado.atualizar(bil, status="INCERTO", detalhe=detalhe, fotos=fotos)
                return "incerto"
            if not r1["ok"]:
                detalhe["1aWin"] = "não confirmou: " + r1["motivo"]
                cps.executar(cps.desfazer, p2, timeout=30)
                self.estado.atualizar(bil, status="falhou", detalhe=detalhe, fotos=fotos)
                return "falhou"
            log("1aWin: aposta CONFIRMADA (%s) — confirmando a PS" % (r1["motivo"] or "mensagem da casa"))
            r2 = cps.executar(cps.confirmar, p2, timeout=40)
            fotos.append(r2.get("foto", ""))
            if r2["ok"] is None:
                detalhe["1aWin"] = "confirmada"
                detalhe["PS3838"] = r2["motivo"]
                log("‼ PS INCERTA — a 1aWin está apostada; confira 'Apostas pendentes' na PS AGORA")
                self.estado.atualizar(bil, status="INCERTO", detalhe=detalhe, fotos=fotos)
                return "incerto"
            if not r2["ok"]:
                detalhe["1aWin"] = "confirmada"
                detalhe["PS3838"] = "NÃO CONFIRMOU com a 1aWin já apostada: " + r2["motivo"]
                log("‼ ATENÇÃO: 1aWin confirmada e PS não — perna exposta, confira as casas AGORA")
                self.estado.atualizar(bil, status="exposto", detalhe=detalhe, fotos=fotos)
                return "exposto"
            detalhe = {"1aWin": "confirmada", "PS3838": "confirmada"}
            self.estado.atualizar(bil, status="confirmado", detalhe=detalhe, fotos=fotos)
            return "confirmado"

    def _conferencia_final(self, res, bil):
        esperas = []
        for (nome, perna) in zip((CASA_1W, CASA_PS), bil["pernas"]):
            c = self.casas[nome]
            esperas.append((nome, c.em_paralelo(c.conferir_cupom, perna, timeout=20)))
        for nome, w in esperas:
            try:
                ok, mot = w()
            except Exception as e:
                ok, mot = False, "conferência final falhou: %s" % str(e)[:80]
            if not ok:
                res[nome]["ok"] = False
                res[nome]["motivo"] = "montou, mas na conferência final: " + mot
                res[nome]["montada"] = True  # está no cupom: precisa ser desfeita
                log("%s: conferência final reprovou — %s" % (NOME_CASA[nome], mot))

    def _desfazer(self, res, bil, fotos, detalhe):
        ok_geral = True
        esperas = []
        for (nome, r), perna in zip(((CASA_1W, res[CASA_1W]), (CASA_PS, res[CASA_PS])), bil["pernas"]):
            if r["ok"] or r.get("montada"):
                c = self.casas[nome]
                esperas.append((nome, c.em_paralelo(c.desfazer, perna, timeout=40)))
        for nome, w in esperas:
            try:
                d = w()
            except Exception as e:
                d = {"ok": False, "motivo": str(e), "foto": ""}
            if d.get("foto"):
                fotos.append(d["foto"])
            if d["ok"]:
                detalhe[NOME_CASA[nome]] += " e desfez (cupom vazio)"
            else:
                ok_geral = False
                detalhe[NOME_CASA[nome]] += " — DESFAZER FALHOU: " + d["motivo"]
                log("‼ %s: desfazer não esvaziou o cupom" % NOME_CASA[nome])
        return ok_geral

    # ---------------------------------------------- calibração (sob demanda)
    def calibrar(self):
        threading.Thread(target=self._calibrar, daemon=True, name="calibrar").start()

    def _calibrar(self):
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        log("🧪 calibração: até 3 jogos ao vivo (um por liga) — monta 'acima' da linha principal, foto, desfaz")
        try:
            jogos = c1.executar(_calib_listar_jogos, c1, timeout=180)
            log("calibração: %d jogo(s) escolhido(s): %s" % (len(jogos), "; ".join("%s x %s" % tuple(t) for _, t in jogos) or "nenhum"))
        except Exception as e:
            log("calibração: lista falhou: %s" % str(e)[:200])
            return
        relatorio = []
        for url, times in jogos[:3]:
            item = {"jogo": "%s x %s" % tuple(times), "casas": {}}
            for c in (c1, cps):
                with _TRAVA_CLIQUES:
                    try:
                        item["casas"][c.rotulo] = c.executar(_calib_casa, c, url if c is c1 else None, times, timeout=150)
                    except Exception as e:
                        item["casas"][c.rotulo] = {"ok": False, "motivo": str(e)}
                log("calibração %s / %s: %s" % (item["jogo"], c.rotulo, item["casas"][c.rotulo].get("motivo") or "ok"))
            relatorio.append(item)
        # a PS precisa ser provada mesmo quando os jogos da 1aWin não existem nela
        ps_montou = any(("selecao" in it["casas"].get(cps.rotulo, {})) for it in relatorio)
        if not ps_montou:
            log("calibração: nenhum jogo da 1aWin estava na PS — testo a PS num jogo da lista dela")
            with _TRAVA_CLIQUES:
                try:
                    times = cps.executar(_calib_jogo_da_ps, cps, timeout=60)
                    if times:
                        r = cps.executar(_calib_casa, cps, None, times, timeout=150)
                    else:
                        r = {"ok": False, "motivo": "lista da PS sem jogo ao vivo"}
                except Exception as e:
                    times, r = None, {"ok": False, "motivo": str(e)[:200]}
            nome = "%s x %s" % tuple(times) if times else "(lista da PS)"
            log("calibração %s / %s: %s" % (nome, cps.rotulo, r.get("motivo") or "ok"))
            relatorio.append({"jogo": nome, "casas": {cps.rotulo: r}})
        arq = os.path.join(PRINTS, "calibracao_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        _gravar_json(arq, relatorio)
        log("🧪 calibração concluída — raio-x em %s" % arq)


_RX_STATUS = re.compile(r"(quarto|quarter|tempo|half|intervalo|finalizado|encerrado|ao vivo|live|prorroga|overtime|"
                        r"\d+\s*['’`´]|^\s*q[1-4]\b|^\s*\(f\)\s*$)", re.I)


def _e_status_jogo(txt):
    """Relógio/situação do jogo ('4º Quarto 1`', 'Finalizado', 'Intervalo') nunca é nome de time."""
    return bool(_RX_STATUS.search(_sem_acento(txt or "")))


def _calib_listar_jogos(c):
    c.ir(c.url_lista())
    ligas = c._sondar(c._mapa_ligas, 10) or []
    log("calibração: %d liga(s) na lista ao vivo da 1aWin" % len(ligas))
    jogos = []
    for lg in ligas[:6]:
        if len(jogos) >= 3:
            break
        try:
            c.ir(lg["url"])

            def linhas_da_liga():
                return c._eval(r"""() => Array.from(document.querySelectorAll('[data-scope*="Team" i], a[href*="/match/"], [class*="event" i], [class*="match" i], [class*="game" i], [class*="row" i]'))
                    .filter(e => e.tagName === 'A' || e.hasAttribute('data-scope') || getComputedStyle(e).cursor === 'pointer')
                    .map(e => (e.innerText || '').trim()).filter(t => t && t.length < 200)""")
            linhas = c._sondar(linhas_da_liga, 6) or []
            achou = False
            for t in linhas:
                if re.search(r"finalizado|encerrado|temporariamente", t, re.I):
                    continue
                partes = [p.strip() for p in re.split(r"\n|\s+(?:—|–|vs\.?|x)\s+", t)
                          if len(p.strip()) >= 3 and not re.match(r"^[\d\s.:'’`´,-]+$", p.strip()) and not _e_status_jogo(p)]
                if len(partes) >= 2 and not any(_mesmo_jogo(partes[:2], t) for _, t in jogos):
                    jogos.append((lg["url"], partes[:2]))
                    achou = True
                    break
            if not achou:
                log("calibração: liga %s — nenhuma linha de jogo reconhecida (%d elementos)" % (lg["nome"], len(linhas)))
                c.dump("calib_liga")
        except Exception:
            continue
    return jogos


def _calib_jogo_da_ps(c):
    """Um jogo ao vivo da própria lista da PS (times pelo data-home-team/away-team)."""
    c.ir(c.url_lista())

    def achar():
        return c._eval("""() => {
            for (const e of document.querySelectorAll('[data-home-team][data-away-team]')) {
                const r = e.getBoundingClientRect();
                const h = e.getAttribute('data-home-team'), a = e.getAttribute('data-away-team');
                if (r.width > 0 && h && a) return [h.trim(), a.trim()];
            }
            for (const a of document.querySelectorAll('a[href*="/matchup/"]')) {
                const t = (a.innerText || '').split(String.fromCharCode(10)).map(x => x.trim()).filter(x => x);
                if (t.length >= 2) return t.slice(0, 2);
            }
            return null;
        }""")
    return c._sondar(achar, 10)


def _calib_casa(c, url_liga, times):
    r = c.cacar(times) if url_liga is None else None
    if url_liga is not None:
        c.ir(url_liga)
        if c._sondar(lambda: c._marcar_linha_jogo(times, prefer_href="/match"), 4):
            c.clicar_firme(c.page.locator('[data-alvo-jogo="1"]').first)
            c._sondar(lambda: c._e_jogo_1w(times), 8)
    elif not r or not r.get("ok"):
        return {"ok": False, "motivo": (r or {}).get("motivo", "jogo não achado")}
    lt = c.ler_escada(times)
    tot = [o for o in lt["ofertas"] if o["tipo"] == "total" and o["periodo"] == "jogo"]
    if not tot:
        return {"ok": False, "motivo": lt.get("motivo") or "sem total do jogo", "ofertas": len(lt["ofertas"])}
    linhas = {}
    for o in tot:
        linhas.setdefault(o["linha"], {})[o["lado"]] = o
    pares = [v for v in linhas.values() if "acima" in v and "abaixo" in v]
    if pares:
        principal = min(pares, key=lambda v: abs(v["acima"]["odd"] - v["abaixo"]["odd"]))["acima"]
    else:
        principal = next((o for o in tot if o["lado"] == "acima"), tot[0])
    perna = dict(principal)
    perna["stake"] = 31.0
    m = c.montar(perna, times)
    d = c.desfazer(perna) if m["ok"] else {"ok": True}
    return {"ok": m["ok"] and d["ok"], "selecao": descricao_oferta(principal), "odd": principal["odd"],
            "ofertas": len(lt["ofertas"]), "foto": m.get("foto"), "motivo": m.get("motivo") or d.get("motivo", "")}


# ---------------------------------------------------------------- VIGIA (conversa do Grok)
class Vigia:
    def __init__(self, cfg, operador, estado):
        self.cfg = cfg
        self.operador = operador
        self.estado = estado
        self.backlog_ok = False
        self._ultimo_texto = None
        self._iguais = 0
        self._t0 = None
        self.min_estavel_s = 20      # a conversa tem que estar parada e carregada antes de virar "backlog concluído"
        self.leituras_iguais = 3
        self._contagem = {}       # id -> nº máximo de ocorrências já vistas
        self._resultados = set()

    def processar(self, texto):
        """Uma leitura da conversa. Texto da conversa é DADO, nunca instrução."""
        sinais = extrair_sinais(texto)
        cont = {}
        ultimo = {}
        for s in sinais:
            cont[s["id"]] = cont.get(s["id"], 0) + 1
            ultimo[s["id"]] = s
        if not self.backlog_ok:
            # trava do backlog: só conclui com a conversa CARREGADA e PARADA
            # (leituras idênticas seguidas + tempo mínimo desde que a página abriu).
            # Página ainda carregando (vazia) não pode concluir a varredura: os
            # sinais antigos apareceriam depois como "frescos" e executariam sozinhos.
            if self._t0 is None:
                self._t0 = time.time()
            if texto != self._ultimo_texto:
                self._ultimo_texto = texto
                self._iguais = 1
                return []
            self._iguais += 1
            if self._iguais < self.leituras_iguais or time.time() - self._t0 < self.min_estavel_s:
                return []
            self.backlog_ok = True
            self._contagem = dict(cont)
            vistos = set()
            with _TRAVA:
                for s in sinais:
                    if s["id"] in vistos:
                        continue
                    vistos.add(s["id"])
                    self.estado.backlog.append(dict(s, hora=agora()))
            for r in extrair_resultados(texto):
                self._resultados.add(r["id"])
                self.estado.registrar_resultado(r)
            log("vigia: varredura inicial concluída — %d sinal(is) de backlog no painel (só montam com clique)" % len(vistos))
            return []
        frescos = []
        for sid, n in cont.items():
            if n > self._contagem.get(sid, 0):
                self._contagem[sid] = n
                frescos.append(ultimo[sid])
        for s in frescos:
            log("vigia: sinal fresco — %s | %s" % (s["jogo"], s.get("mercado") or "—"))
            self.operador.novo_sinal(s, "sinal")
        for r in extrair_resultados(texto):
            if r["id"] not in self._resultados:
                self._resultados.add(r["id"])
                self.estado.registrar_resultado(r)
                log("vigia: resultado %s — %s" % (r["jogo"], r["desfecho"]))
        return frescos

    def iniciar(self):
        threading.Thread(target=self._laco, name="vigia", daemon=True).start()

    def _laco(self):
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        ctx = None
        while True:
            try:
                if ctx is None:
                    kw = opcoes_navegador(self.cfg, self.cfg.get("headless"), {"width": 1100, "height": 900})
                    ctx = pw.chromium.launch_persistent_context(os.path.join(DADOS, "perfil-grok"), **kw)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto(self.cfg["url_grok"], wait_until="domcontentloaded")
                    if not self.backlog_ok:
                        self._t0 = time.time()
                    log("vigia: lendo a conversa do ANALISTA a cada ~1s (varredura inicial: esperando a conversa carregar e parar)")
                tx = page.inner_text("body", timeout=5000)
                self.processar(tx)
            except Exception as e:
                log("vigia: %s — reabrindo em 5s" % str(e)[:150])
                try:
                    ctx.close()
                except Exception:
                    pass
                ctx = None
                time.sleep(5)
                continue
            time.sleep(1.0)


# ---------------------------------------------------------------- PAINEL (127.0.0.1)
PAINEL_HTML = r"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bot Operador</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--tx:#e6e6e6;--mut:#9aa0aa;--ok:#2ecc71;--no:#e74c3c;--am:#f1c40f;--ln:#2a2f3a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 system-ui,Segoe UI,Arial}
header{padding:12px 16px;border-bottom:1px solid var(--ln);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0}.tag{padding:2px 8px;border-radius:6px;font-weight:700}
.teste{background:#24425f}.real{background:#7a1f1f}main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:12px}
.card h2{font-size:15px;margin:0 0 8px}.mut{color:var(--mut)}table{width:100%;border-collapse:collapse}
td,th{padding:4px 6px;border-bottom:1px solid var(--ln);text-align:left;vertical-align:top}
.ok{color:var(--ok)}.no{color:var(--no)}.am{color:var(--am)}button{background:#2d5bd7;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer}
pre{white-space:pre-wrap;font-size:12px;max-height:340px;overflow:auto;margin:0}.full{grid-column:1/-1}
.ver{font-size:18px;font-weight:800;margin:8px 0}a{color:#7fb2ff}
</style></head><body>
<header><h1>🤖 Bot Operador de Middles</h1><span id="modo" class="tag teste">TESTE</span>
<span id="params" class="mut"></span><button onclick="calibrar()">🧪 Calibrar agora</button></header>
<main>
<div class="card full" id="mon"></div>
<div class="card" id="calc"><h2>🧮 Calculadora do motor</h2><div class="mut">aguardando a primeira rodada sadia…</div></div>
<div class="card"><h2>📥 Sinais de backlog (só montam com clique)</h2><div id="backlog" class="mut">—</div></div>
<div class="card full"><h2>🎫 Bilhetes</h2><div id="bil"></div></div>
<div class="card"><h2>🛰️ Sinais recebidos</h2><div id="sinais"></div></div>
<div class="card"><h2>🏁 Resultados</h2><div id="res"></div></div>
<div class="card full"><h2>📜 Log</h2><pre id="log"></pre></div>
</main>
<script>
const R=v=>'R$ '+Number(v).toFixed(2).replace('.',',');const P=(v,c=2)=>Number(v).toFixed(c).replace('.',',');
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function post(u){await fetch(u,{method:'POST'});tick()}
function calibrar(){post('/api/calibrar')}
function calc(c){if(!c)return '<h2>🧮 Calculadora do motor</h2><div class="mut">aguardando a primeira rodada sadia…</div>';
 let h='<h2>🧮 Calculadora do motor — '+esc(c.jogo)+'</h2><div class="mut">rodada '+c.rodada+' às '+c.hora+'</div>';
 const p=c.par;
 if(p){h+='<table><tr><th>Casa</th><th>Seleção</th><th>Odd</th><th>Stake</th></tr>';
  for(const l of p.pernas)h+='<tr><td>'+(l.casa=='1awin'?'1aWin':'PS3838')+'</td><td>'+esc(l.selecao)+'</td><td>'+l.odd+'</td><td>'+R(l.stake)+'</td></tr>';
  h+='</table><div>Janela <b>'+P(p.janela,1)+'</b> · Custo <b>'+P(p.custo)+'%</b> · Total '+R(p.total)+' · Retorno '+R(p.retorno)+'</div>';
  if(c.cenarios){h+='<table>';for(const s of c.cenarios)h+='<tr><td>'+esc(s.nome)+'</td><td class="'+(s.valor>=0?'ok':'no')+'">'+R(s.valor)+'</td></tr>';h+='</table>'}}
 else h+='<div>melhor janela: '+(c.melhor_janela==null?'—':P(c.melhor_janela,1))+' · menor custo c/ janela ok: '+(c.menor_custo==null?'—':P(c.menor_custo)+'%')+'</div>';
 h+=c.valido?'<div class="ver ok">✅ VALE A PENA</div>':'<div class="ver no">❌ Não vale agora</div><div class="mut">'+esc(c.motivo)+'</div>';
 return h}
async function tick(){try{const d=await (await fetch('/api/estado')).json();
 const m=document.getElementById('modo');m.textContent=d.modo;m.className='tag '+(d.modo=='REAL'?'real':'teste');
 const pa=d.params;document.getElementById('params').textContent='janela ≥ '+P(pa.janela_min,1)+' · custo ≤ '+P(pa.custo_max,1)+'% · PS €'+P(pa.perna_ps_eur)+' · câmbio '+P(pa.cambio_brl_eur)+(pa.so_totais?' · só totais '+pa.totais_periodos.join('/'):'');
 document.getElementById('mon').innerHTML='<b>Monitor:</b> '+esc(d.monitor||'parado — aguardando sinal');
 document.getElementById('calc').innerHTML=calc(d.calculadora);
 document.getElementById('backlog').innerHTML=d.backlog.length?d.backlog.map((s,i)=>'<div style="margin:4px 0">'+esc(s.jogo)+' <span class="mut">'+esc(s.mercado)+'</span> <button onclick="post(\'/api/backlog/'+i+'\')">MONTAR</button></div>').join(''):'—';
 document.getElementById('bil').innerHTML='<table><tr><th>#</th><th>Hora</th><th>Jogo</th><th>Pernas</th><th>Janela</th><th>Custo</th><th>Status</th><th>Detalhe</th></tr>'+d.bilhetes.map(b=>'<tr><td>'+b.id+'</td><td>'+esc(b.criado)+'</td><td>'+esc(b.jogo)+'</td><td>'+b.pernas.map(l=>(l.casa=='1awin'?'1aWin':'PS')+': '+esc(l.selecao)+' @'+l.odd+' '+R(l.stake)).join('<br>')+'</td><td>'+P(b.janela,1)+'</td><td>'+P(b.custo)+'%</td><td class="'+({testado:'ok',confirmado:'ok',falhou:'no',exposto:'no'}[b.status]||'am')+'">'+esc(b.status)+(b.desfecho?'<br>'+esc(b.desfecho):'')+'</td><td>'+Object.entries(b.detalhe||{}).map(([k,v])=>esc(k)+': '+esc(v)).join('<br>')+'<br>'+(b.fotos||[]).filter(x=>x).map(f=>'<a target="_blank" href="/prints/'+encodeURIComponent(f)+'">foto</a>').join(' ')+'</td></tr>').join('')+'</table>';
 document.getElementById('sinais').innerHTML=d.sinais.map(s=>'<div>'+s.hora+' '+esc(s.jogo)+' <span class="mut">'+esc(s.mercado)+' ('+s.origem+')</span></div>').join('')||'—';
 document.getElementById('res').innerHTML=d.resultados.map(r=>'<div>'+esc(r.quando)+' '+esc(r.jogo)+' — <b>'+esc(r.desfecho)+'</b></div>').join('')||'—';
 const lg=document.getElementById('log');const fim=lg.scrollTop+lg.clientHeight>=lg.scrollHeight-20;lg.textContent=d.log.join('\n');if(fim)lg.scrollTop=lg.scrollHeight;
}catch(e){document.getElementById('mon').textContent='painel sem contato com o programa'}}
tick();setInterval(tick,2000);
</script></body></html>"""


def criar_painel(estado, operador, porta):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _env(self, cod, corpo, tipo="application/json; charset=utf-8"):
            if isinstance(corpo, str):
                corpo = corpo.encode("utf-8")
            self.send_response(cod)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            p = urlparse(self.path).path
            if p == "/":
                return self._env(200, PAINEL_HTML, "text/html; charset=utf-8")
            if p == "/api/estado":
                return self._env(200, json.dumps(estado.foto_json(), ensure_ascii=False, default=str))
            if p.startswith("/prints/"):
                nome = os.path.basename(unquote(p[len("/prints/"):]))
                arq = os.path.join(PRINTS, nome)
                if os.path.isfile(arq):
                    with open(arq, "rb") as f:
                        return self._env(200, f.read(), "image/png" if nome.endswith(".png") else "text/plain; charset=utf-8")
            return self._env(404, '{"erro":"não achei"}')

        def do_POST(self):
            p = urlparse(self.path).path
            m = re.match(r"^/api/backlog/(\d+)$", p)
            if m and operador:
                i = int(m.group(1))
                with _TRAVA:
                    s = estado.backlog.pop(i) if i < len(estado.backlog) else None
                if s:
                    log("operador clicou MONTAR no backlog: %s" % s["jogo"])
                    operador.novo_sinal(s, "backlog")
                return self._env(200, '{"ok":true}')
            if p == "/api/calibrar" and operador:
                operador.calibrar()
                return self._env(200, '{"ok":true}')
            return self._env(404, '{"erro":"rota"}')

    return ThreadingHTTPServer(("127.0.0.1", porta), H)


# ---------------------------------------------------------------- subida
_CAMINHOS_NAVEGADOR = [
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def achar_navegador(cfg):
    """O navegador de verdade do operador (Brave, ou Chrome no PC do sócio).
    config "navegador_exe" manda; senão procura nos lugares de instalação padrão.
    "" = usa o Chromium do Playwright."""
    exe = (cfg.get("navegador_exe") or "").strip().strip('"')
    if exe:
        return exe if os.path.isfile(exe) else ""
    if os.name != "nt":
        return ""
    for c in _CAMINHOS_NAVEGADOR:
        c = os.path.expandvars(c)
        if os.path.isfile(c):
            return c
    return ""


def opcoes_navegador(cfg, headless, viewport=None):
    """Opções do Playwright: navegador de verdade + sem a marca de automação
    (sites como o login do Google recusam navegador 'controlado por automação')."""
    kw = {"headless": bool(headless), "ignore_default_args": ["--enable-automation"],
          "args": ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]}
    if viewport:
        kw["viewport"] = viewport
    exe = achar_navegador(cfg)
    if exe:
        kw["executable_path"] = exe
    elif cfg.get("canal_navegador"):
        kw["channel"] = cfg["canal_navegador"]
    return kw


def modo_login(cfg):
    """1-LOGIN.bat: abre as 3 janelas nos perfis DO PROGRAMA para o login à mão.
    Com Brave/Chrome instalado, abre o navegador NORMAL (sem automação nenhuma):
    o Google aceita o login e a sessão fica salva na pasta do perfil."""
    alvos = (("perfil-grok", cfg.get("url_grok") or "https://grok.com"),
             ("perfil-1awin", cfg["url_1awin"]), ("perfil-ps3838", cfg["url_ps3838"]))
    exe = achar_navegador(cfg)
    print("\nAbrindo as 3 janelas (Grok, 1aWin, PS3838) nos perfis DO PROGRAMA.")
    print("Faça o login à mão em cada uma. NÃO logue essas contas no seu navegador do dia a dia.\n")
    if exe:
        import subprocess
        print("Navegador: %s\n" % exe)
        for perfil, url in alvos:
            subprocess.Popen([exe, "--user-data-dir=" + os.path.join(DADOS, perfil),
                              "--no-first-run", "--no-default-browser-check", "--new-window", url])
            time.sleep(1.5)
        input("Quando terminar de logar nas 3 janelas, FECHE as 3 janelas e depois aperte ENTER aqui... ")
        print("Logins salvos nos perfis do programa. Agora rode 2-INICIAR.bat.")
        return
    print("AVISO: não achei Brave nem Chrome. Usando o navegador do Playwright —")
    print("o login com Google pode ser recusado. Coloque o caminho do brave.exe em")
    print('"navegador_exe" no config.json para resolver.\n')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctxs = []
        for perfil, url in alvos:
            ctx = pw.chromium.launch_persistent_context(os.path.join(DADOS, perfil), **opcoes_navegador(cfg, False))
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                pg.goto(url)
            except Exception:
                pass
            ctxs.append(ctx)
        input("Quando terminar de logar nas 3 janelas, aperte ENTER aqui para salvar e fechar... ")
        for c in ctxs:
            c.close()
    print("Logins salvos nos perfis do programa. Agora rode 2-INICIAR.bat.")


def _porta_livre(porta):
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", porta))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main(argv):
    os.makedirs(PRINTS, exist_ok=True)
    if "login" in argv:
        modo_login(carregar_config(avisar=False))
        return
    cfg = carregar_config()
    if not cfg["modo_teste"]:
        print("\n*** MODO REAL pedido no config.json. Isso CONFIRMA apostas de verdade. ***")
        print("Lembrete (lei): só depois de UMA rodada de teste limpa via sinal.")
        if input("Digite ARMAR para liberar o modo real (qualquer outra coisa = modo teste): ").strip() != "ARMAR":
            cfg["modo_teste"] = True
            log("ARMAR não digitado — seguindo em MODO TESTE")
        else:
            log("‼ MODO REAL ARMADO")
    log("modo: %s" % ("TESTE (monta, fotografa, desfaz — nunca confirma)" if cfg["modo_teste"] else "REAL"))
    porta = int(cfg.get("porta") or 8000)
    if not _porta_livre(porta):
        log("ERRO: a porta %d está ocupada — provavelmente um python.exe velho do programa. "
            "Feche-o (Gerenciador de Tarefas) e rode de novo." % porta)
        return
    exe = achar_navegador(cfg)
    log("navegador: %s" % (exe or "Chromium do Playwright (Brave/Chrome não encontrado)"))
    estado = Estado(cfg)
    casas = {CASA_PS: Casa(CASA_PS, cfg).iniciar(), CASA_1W: Casa(CASA_1W, cfg).iniciar()}
    operador = Operador(cfg, estado, casas)
    if cfg.get("url_grok"):
        Vigia(cfg, operador, estado).iniciar()
    else:
        log("AVISO: url_grok vazio no config.json — a vigia não vai ler sinais")
    srv = criar_painel(estado, operador, porta)
    log("painel: http://127.0.0.1:%d" % porta)
    try:
        import webbrowser
        webbrowser.open("http://127.0.0.1:%d" % porta)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("encerrando")


if __name__ == "__main__":
    main(sys.argv[1:])
