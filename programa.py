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
ARQ_PREFS = os.path.join(DADOS, "preferencias.json")
ARQ_OPER = os.path.join(DADOS, "operacao.json")  # jogo protegido, fila, contador e BLOQUEIO (sobrevivem ao reinício)  # escolhas feitas no painel (sobrevivem ao reinício)
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
    "custo_max": 11.0,           # 24/11: tabela C (fora das faixas da tabela vale este teto)
    # tabela do operador: [janela de, janela até (exclusiva), perda máxima %]
    # 24/11 (operador): TABELA C — 8 / 9 / 10 / 11 (antes: D 9/10/11/12; original 3/4,8/4,99/8)
    "custo_por_janela": [[1, 2, 8.0], [2, 3, 9.0], [3, 7, 10.0], [7, 11, 11.0]],
    # ---- monitoramento pós-entrada e cobertura dinâmica (ordem do operador 15/11)
    # ---- reentrada no mesmo jogo (estilo do parceiro, 15/11): novo middle em outras linhas
    "reentrar_mesmo_jogo": True,
    "max_entradas_por_jogo": 4,        # = max_pares_por_jogo (o nome novo também é aceito no config.json)
    "reentrada_mesma_janela": True,
    "reentrada_livre": True,
    "min_movimento_linha": 0.5,
    "max_repeticoes_mesma_perna": 4,   # 19/11: a mesma perna pode ser âncora de até 4 pares (se cada um ampliar)        # 19/11: a linha principal da PS tem que andar ≥ 0,5 desde a última entrada
    "reentrada_espera_min_s": 60,
    "protecao_sem_entrada_min": 0,     # 24/11 (operador): 0 = NUNCA encerra por tempo ("se eu quiser encerrar eu peço outro sinal")
    "protecao_vazias_lib": 20,         # ...ou se o mercado sumir (20 leituras seguidas sem ofertas)
    "fila_validade_min": 20,
    "erros_leitura_alerta": 10,        # 20/11: 10 leituras seguidas com erro -> pausa e alerta (não libera o jogo)
    "cobrir_perna_solta": False,       # 20/11: com posição INCOMPLETA tudo congela (nada de aposta automática)           # sinal na fila vale até 20 min (depois descarta)      # ...e com pelo menos 60 s de intervalo
    "reentrada_custo_max": 16.0,       # (sem uso desde 21/11: reentrada segue a tabela por janela)
    "reentrada_custo_bom": 5.0,        # (sem uso desde 21/11: o custo entra no desempate, sem faixa preferida)
    "reentrada_janela_vale": 3.0,      # acima de 4%: só duplo duplo ou janela ≥ 3 pontos           # 17/11: reentrada SÓ pela lógica do parceiro (duplo duplo / sequência / nunca repetido)    # reentrada só com janela ≥ a da 1ª entrada do jogo (operador 16/11)
    "retentar_selecao_sumida": False,  # 21/11: não insiste na mesma seleção — vai direto para a substituta   # 20/11: seleção sumiu da 1aWin num lance -> espera e tenta 1x de novo
    "retentar_espera_s": 1.5,
    # ---- execução do par em estados (operador 22/11, caso Bucks x Portland): PREPARAR -> BARREIRA_FINAL ->
    #      DISPARAR -> RECONCILIAR (-> PERNA_ORFA / INCERTO)
    "tempo_max_preparacao_seg": 3.0,   # cada cupom pronto (seleção + conferência + stake) neste prazo; o cronômetro
                                       # começa quando a casa COMEÇA a preparar (sem caça do jogo nem carregamento)
    "barreira_janela_ms": 300,         # as duas leituras finais dos cupons têm que terminar a até 300 ms uma da outra
    # "sequencial_processando": clica a 1aWin e dispara a PS assim que a 1aWin ACEITA ou mostra 'processando'.
    #     RISCO: se a 1aWin processar e não aceitar, a PS fica ÓRFÃ (caso 15/11 17:02).
    # "sequencial_aceita": a PS só dispara depois que a 1aWin ACEITA (mais lento, sem esse risco).
    # "rapida": os dois cliques juntos (100–300 ms); qualquer uma pode ficar órfã.
    "confirmacao": "sequencial_processando",
    "disparo_intervalo_max_ms": 1500,  # modos sequenciais: espera no máximo isto pela prova imediata da 1aWin
    "cooldown_par_s": 60,              # o MESMO par (assinatura) que falhou fica de fora este tempo
    "max_substituicoes_tentativa": 1,  # trocas de perna (âncora/escadinha) dentro de UMA tentativa
    "max_tentativas_por_minuto": 6,    # tentativas de montagem no MESMO jogo por minuto
    "orfa_tentativas": 2,              # PERNA_ORFA: quantas vezes tenta colocar a perna que faltou (mesmas regras)
    "cobrir_orfa_automatico": True,    # 24/11: sem cobertura nas tentativas -> segue no jogo tentando a cada rodada (não bloqueia)

    "odd_min": 1.78,                  # nenhuma perna abaixo disso (operador 17/11: 'sempre o mínimo 1.78/1.79')
    "odd_fantasma_margem": 0.15,       # odd da 1aWin > justo(PS) × 1,15 = tela velha, não entra (null desliga)
    "perda_max_por_jogo_brl": 100.0,   # pior caso da CARTEIRA inteira do jogo (operador 16/11: R$ 100)
    "pausa_ps_indisponivel_s": 45,
    "pausa_ps_periodo_indisponivel_s": 180,
    "pos_monitorar": False,           # 18/11: DESLIGADO por padrão
    "avulsas_permitidas": False,
      # 18/11: NUNCA aposta de uma perna só (só reentrada em par)
    "pos_modo": "auto",               # "auto" = aposta sozinho; "manual" = mostra no painel p/ confirmar
    "pos_intervalo_s": 15,
    "pos_max_coberturas": 3,          # por posição
    "pos_max_por_cobertura_brl": 40.0,
    "pos_max_total_coberturas_brl": 120.0,
    "pos_perda_max_total_brl": 100.0, # pior caso da carteira inteira (operador 16/11: R$ 100 por jogo)
    "perna_ps_eur_max": 6.0,          # avulsa na PS: de €5 até €6 (operador 16/11)
    "pos_stake_1w_brl": None,         # avulsa na 1aWin: até o equivalente a perna_ps_eur_max (€6 × câmbio = R$ 37,20)
    "pos_exigir_ev": False,           # true = só entra se o valor esperado não cair (antes travava tudo)
    "pos_ev_min_brl": 0.0,            # (só com pos_exigir_ev)
    "pos_reducao_risco_min": 0.05,    # (só com pos_exigir_ev)
    "pos_cooldown_s": 20,
    "pos_stake_min_1w_brl": 5.0,
    "pos_duracao_max_min": 150,
    "perna_ps_eur": 5.0,         # 0 = volta ao total fixo
    "total_operacao": 600.0,
    "cambio_brl_eur": 6.20,
    "monitor_intervalo_s": 10,
    "modo_teste": True,
    "auto_aprovar": False,       # legado da era do painel
    "so_totais": True,           # restrição temporária 13/11 23:26 (false/null religa handicap)
    "totais_periodos": ["jogo", "q1", "q2", "q3"],  # operador 23/11: jogo inteiro + quartos de novo (4º quarto: sempre proibido)
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
    # o que o operador escolheu no PAINEL (botão da Cobertura) vale no próximo início — 18/11: reabriu e a
    # Cobertura voltou sozinha para 'automático' (avulsas só na PS). O config.json, se tiver a chave, manda.
    if caminho == ARQ_CONFIG:
        for k, v in (_ler_json(ARQ_PREFS, {}) or {}).items():
            if k in ("pos_monitorar", "pos_modo"):
                cfg[k] = v
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
        if cfg.get("custo_por_janela"):
            log("PERDA MÁXIMA POR JANELA: " + "; ".join("%s–%s pts: %s%%" % (_br(a, 1), _br(b - 0.5, 1), _br(c, 2))
                                                       for a, b, c in cfg["custo_por_janela"]))
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
                 "lourenco": "lorenzo", "jose": "jose", "ironia": "ironi", "atletico": "atletico", "atl": "atletico",
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
    toks = [t for t in toks if t not in _PREFIXOS_CLUBE]
    return [t for t in toks if (len(t) >= 4 or t == "san") and not _generico(t)]


def _iniciais(s):
    return "".join(t[0] for t in _norm(s).split() if len(t) >= 3 and not _generico(t))


def _formas_sigla(w):
    """Sigla e a sigla sem o 'U' de University/Universidade (NCAA 23/11: 'VCU' = 'Virginia Commonwealth';
    'UCF' = 'Central Florida'). Regra geral de sigla, nenhum time no código."""
    out = [w]
    if len(w) >= 3 and w.isalpha():
        if w.endswith("u"):
            out.append(w[:-1])
        if w.startswith("u"):
            out.append(w[1:])
    return out


def _sigla_bate(a, b):
    """'Co Basketbol' x 'Cedi Osman Basketbol': a sigla curta de um = iniciais seguidas do outro."""
    ini = _iniciais(b)
    for w in _norm(a).split():
        if not (2 <= len(w) <= 4) or _generico(w) or [t for t in _tokens(a) if len(t) >= 4]:
            continue
        if any(len(f) >= 2 and len(f) <= len(ini) and f in ini for f in _formas_sigla(w)):
            return True
    return False


_CODIGOS_CLUBE = {"kk", "bc", "bk", "fc", "cb", "sk", "ks", "kb", "hc", "ac", "cd", "ud", "sd", "ok", "kos", "bkk"}


def _tem_sigla(nome):
    return any(2 <= len(w) <= 4 and not w.isdigit() and w not in _CODIGOS_CLUBE and w not in _PREFIXOS_CLUBE
               and w not in ("san",) for w in _norm(nome).split())


def _sigla_expandida(a, b):
    """Palavras de b cujas iniciais (seguidas) formam uma sigla curta de a. 'KK SC Derby' x 'Studentski Centar'
    -> ['studentski', 'centar']. As palavras de b têm que ser próprias (não genéricas) e seguidas."""
    pb = [t for t in _norm(b).split() if len(t) >= 3 and not _generico(t)]
    out = []
    for w in _norm(a).split():
        # 'sc' é genérico ('Sport Club'), mas aqui só vale se bater com as iniciais de palavras PRÓPRIAS de b
        if not (2 <= len(w) <= 4) or w in _PREFIXOS_CLUBE or w.isdigit() or w in _CODIGOS_CLUBE:
            continue
        for f in _formas_sigla(w):
            n = len(f)
            if n < 2:
                continue
            achou = False
            for i in range(len(pb) - n + 1):
                if "".join(t[0] for t in pb[i:i + n]) == f:
                    out += [t for t in pb[i:i + n] if t not in out]
                    achou = True
                    break
            if achou:
                break
    return out


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
    pa = {_ALIAS_TOKENS.get(t, t) for t in _norm(a).split()} & _PREFIXOS_CLUBE
    pb = {_ALIAS_TOKENS.get(t, t) for t in _norm(b).split()} & _PREFIXOS_CLUBE
    if pa and pb and not (pa & pb):
        return False  # Hapoel Tel Aviv ≠ Maccabi Tel Aviv (nenhum prefixo em comum)
    na, nb = _norm_paises(_norm(a)), _norm_paises(_norm(b))
    if na == nb:
        return True
    ta = [t for t in _tokens(a) if t != "san"] or _tokens(a)
    tb = [t for t in _tokens(b) if t != "san"] or _tokens(b)
    # sigla no MEIO do nome (16/11: 'KK SC Derby Podgorica' = 'Studentski Centar'): 'sc' = iniciais de palavras
    # seguidas do outro nome -> essas palavras contam como presentes dos dois lados
    # só na 2ª passada (leniente), quando o OUTRO time do jogo também tem que bater — sigla sozinha é pista fraca
    ea, eb = (_sigla_expandida(a, b), _sigla_expandida(b, a)) if leniente else ([], [])
    if ea or eb:
        ta, tb = ta + [t for t in ea if t not in ta], tb + [t for t in eb if t not in tb]
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


def custo_max_para(janela, cfg):
    """Perda máxima conforme a janela (tabela do operador 15/11):
    1 ponto até 2%; 2 pontos até 3,8%; 3–6 pontos abaixo de 4%; 7–10 pontos até 7%. Meio ponto fica na faixa de baixo (1,5 = faixa do 1)."""
    tab = cfg.get("custo_por_janela")
    if not tab:
        return cfg["custo_max"]
    for faixa in tab:  # [[janela_de, janela_ate_exclusiva, custo_max], ...]
        de, ate, cmax = faixa
        if de <= janela + 1e-9 and janela < ate - 1e-9:
            return float(cmax)
    return cfg["custo_max"]


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


def placares_dupla(acima, abaixo):
    """Placares (inteiros) em que as DUAS pernas de um par de totais ganham: La < k < Lb."""
    la, lb = acima["linha"], abaixo["linha"]
    return {k for k in range(int(la) + 1, int(lb) + 1) if la < k < lb}


def _chave_oferta(o):
    """Identidade de uma oferta para a geladeira: casa, mercado, período, lado e linha."""
    return (o["tipo"], o.get("periodo"), o.get("lado"), o.get("time_idx"), round(float(o["linha"]), 2))


def _tempo_geladeira(motivo):
    """Quanto tempo uma oferta que falhou fica de fora antes de tentar de novo (s). 0 = não gela.
    Odd mudou não gela: a próxima leitura já traz a odd nova e a conta é refeita."""
    m = (motivo or "").lower()
    if not m:
        return 0
    if "mínima maior que a máxima" in m or "indispon" in m:
        return 120   # a PS suspendeu (o jogo todo também descansa; ver pausa_ps_indisponivel_s)
    if "não recebeu a seleção" in m or "cadeado" in m or "limpou o cupom sem aceitar" in m \
            or "sumiu do cupom" in m or "0 seleções" in m or "exclu" in m:
        return 60    # linha travada/suspensa (a 1aWin atrasada costuma travar bem nessas odds)
    return 0


def achar_par(esc_1w, esc_ps, sinal, cfg, raiox=None, excluir=None, preferir=None):
    """Pareia as escadas reais das duas casas. Retorna (bilhete, None) ou (None, motivo).
    Varre todos os mercados/períodos permitidos — o sinal é só a pista."""
    permitidos = set(cfg.get("totais_periodos") or PERIODOS_TODOS) - {"q4"}
    so_totais = cfg.get("so_totais")
    candidatos = []
    recusados = []  # (janela, cálculo, perna 1aWin, perna PS, motivo) — para dizer POR QUE não entrou
    suspeitos = 0
    melhor_janela = None
    menor_custo_ok = None

    odd_min = cfg.get("odd_min")

    abaixo_piso = [0]

    def filtra(esc):
        out = []
        for o in esc or []:
            if odd_min and o.get("odd", 0) < float(odd_min) - 1e-9:
                abaixo_piso[0] += 1
                continue  # perna com odd baixa demais (ex.: 1aWin a 1.43 em 17/11 15:48)
            if o.get("periodo") == "q4":
                continue
            if o["tipo"] == "total" and o.get("periodo") not in permitidos:
                continue
            if o["tipo"] == "handicap" and so_totais:
                continue
            out.append(o)
        return out

    e1, e2 = filtra(esc_1w), filtra(esc_ps)
    # ODD FANTASMA (log 16/11 09:09): a 1aWin mostrava Abaixo 195.5 a 2.1 com a PS em 193 (justo ≈ 1.65) — era
    # tela velha; aceitou a 1.61. Odd da 1aWin muito ACIMA do justo calculado pela escada da PS = não confio.
    margem = cfg.get("odd_fantasma_margem", 0.15)
    modelos = {}
    fantasmas = 0

    def fantasma(o):
        if margem is None or o["tipo"] != "total":
            return False
        per = o["periodo"]
        if per not in modelos:
            modelos[per] = modelo_total(esc_ps, per)
        md = modelos[per]
        if not md:
            return False
        d = _ND(md[0], md[1])
        L = o["linha"]
        p = (1 - d.cdf(L)) if o["lado"] == "acima" else d.cdf(L)
        if p <= 0.02:
            return True
        return o["odd"] > (1.0 / p) * (1 + float(margem)) + 1e-9

    e1_ok = []
    for a in e1:
        if fantasma(a):
            fantasmas += 1
        else:
            e1_ok.append(a)
    e1 = e1_ok
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
                recusados.append((jan, c, a, b, "linha suspeita (custo negativo demais — tela velha?)"))
                continue
            melhor_janela = jan if melhor_janela is None else max(melhor_janela, jan)
            if jan >= cfg["janela_min"]:
                menor_custo_ok = c["custo"] if menor_custo_ok is None else min(menor_custo_ok, c["custo"])
            ps_ok = c["ps_eur"] + FOLGA_PS_EUR >= (cfg["perna_ps_eur"] or 5.0)
            lim = custo_max_para(jan, cfg)
            if jan < cfg["janela_min"] - 1e-9:
                recusados.append((jan, c, a, b, "janela abaixo do mínimo (%s)" % _br(cfg["janela_min"], 1)))
                continue
            if c["custo"] > lim + 1e-9:
                recusados.append((jan, c, a, b, "custo %s%% acima do limite de %s%% para janela %s" % (
                    _br(c["custo"]), _br(lim), _br(jan, 1))))
                continue
            if not ps_ok:
                recusados.append((jan, c, a, b, "stake incompatível (perna da PS abaixo do mínimo)"))
                continue
            # regras obrigatórias da reentrada / geladeira: o filtro devolve o MOTIVO (texto) quando recusa
            mot_ex = excluir(a, b, jan) if excluir else None
            if mot_ex:
                recusados.append((jan, c, a, b, mot_ex if isinstance(mot_ex, str) else "recusado pelas regras da reentrada"))
                continue
            candidatos.append((True, jan, c, a, b, preferir(a, b, c) if preferir else 0))

    # reentrada (parceiro 17/11): 'duplo duplo' primeiro (mais totais com 4+ greens), depois a maior janela, depois o custo
    validos = [x for x in candidatos if x[0]]
    restricao = ""
    if so_totais:
        restricao = " [restrição ativa: só totais %s; handicap desligado]" % "/".join(
            p for p in PERIODOS_TODOS if p in permitidos)
    if validos:
        if preferir and any(isinstance(x[5], tuple) for x in validos):
            validos.sort(key=lambda x: (x[5] if isinstance(x[5], tuple) else (), x[1], -x[2]["custo"]), reverse=True)
        else:
            validos.sort(key=lambda x: (-x[5], -x[1], x[2]["custo"]))
        _, jan, c, a, b, _ = validos[0]
        bil = _montar_bilhete(sinal, a, b, jan, c)
        if raiox is not None:
            raiox.update({"valido": True, "par": bil, "motivo": "par válido",
                          "melhor_janela": melhor_janela, "menor_custo": menor_custo_ok,
                          "suspeitos": suspeitos})
        return bil, None

    # 21/11: o motivo mostra UM par concreto (o melhor recusado) e o motivo real — antes 'melhor janela' e
    # 'menor custo' podiam ser de combinações diferentes
    if recusados:
        recusados.sort(key=lambda x: (x[0] < cfg["janela_min"] - 1e-9, -x[0], x[1]["custo"]))
        jr, cr, ar, br_, mr = recusados[0]
        motivo = "sem par: RECUSADO: %s | janela %s | custo %s%% | motivo: %s (%d combinação(ões) recusada(s)%s%s%s)%s" % (
            descricao_par(ar, br_), _br(jr, 1), _br(cr["custo"]), mr, len(recusados),
            ("; %d odd(s) abaixo do piso %s" % (abaixo_piso[0], odd_min)) if abaixo_piso[0] else "",
            ("; %d odd(s) fantasma da 1aWin" % fantasmas) if fantasmas else "",
            ("; %d suspeito(s) descartado(s)" % suspeitos) if suspeitos else "", restricao)
    else:
        motivo = "sem par: nenhuma combinação Over × Under entre as casas%s%s%s%s" % (
            ("; %d odd(s) abaixo do piso %s" % (abaixo_piso[0], odd_min)) if abaixo_piso[0] else "",
            ("; %d odd(s) fantasma da 1aWin" % fantasmas) if fantasmas else "",
            ("; %d suspeito(s) descartado(s)" % suspeitos) if suspeitos else "", restricao)
    if not e1 or not e2:
        motivo = "sem par: escada vazia em %s%s%s%s" % (
            " e ".join(n for n, e in ((NOME_CASA[CASA_1W], e1), (NOME_CASA[CASA_PS], e2)) if not e),
            (" (%d odd(s) fantasma da 1aWin ignorada(s))" % fantasmas) if fantasmas else "",
            (" (%d odd(s) abaixo do piso %s)" % (abaixo_piso[0], odd_min)) if abaixo_piso[0] else "", restricao)
    if raiox is not None:
        # o 'quase' da calculadora = o MESMO par que o motivo mostra (o melhor recusado)
        quase = recusados[0] if recusados else None
        raiox.update({"valido": False, "motivo": motivo, "melhor_janela": melhor_janela,
                      "menor_custo": menor_custo_ok, "suspeitos": suspeitos,
                      "motivo_recusa": quase[4] if quase else "",
                      "par": _montar_bilhete(sinal, quase[2], quase[3], quase[0], quase[1]) if quase else None})
    return None, motivo


def descricao_par(a, b):
    """'1aWin Over 169,5 @1,85 + PS Under 172 @1,97' (a = perna da 1aWin, b = perna da PS)."""
    def um(o, casa):
        if o.get("tipo") == "total":
            lado = "Over" if o["lado"] == "acima" else "Under"
        else:
            lado = o.get("time") or "Handicap"
        return "%s %s %s @%s" % (casa, lado, _br(o["linha"], 1).replace(",0", ""), _br(o["odd"], 3).rstrip("0").rstrip(","))
    return "%s + %s" % (um(a, "1aWin"), um(b, "PS"))


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


# ---------------------------------------------------------------- POSIÇÃO: resultado por placar
from statistics import NormalDist as _ND


def pagamento_perna(perna, k):
    """Quanto a perna devolve se o total final for k (inteiro). Linha inteira empatada = devolve."""
    L, st, od = perna["linha"], perna["stake"], perna["odd"]
    if abs(k - L) < 1e-9:
        return st
    ganha = k > L if perna["lado"] == "acima" else k < L
    return st * od if ganha else 0.0


def liquido_por_placar(pernas, ks):
    gasto = sum(p["stake"] for p in pernas)
    return {k: sum(pagamento_perna(p, k) for p in pernas) - gasto for k in ks}


def modelo_total(ofertas, periodo):
    """Distribuição do total final a partir da escada (PS = casa afiada): (média, desvio).
    Para cada linha com os dois lados: p_acima sem a margem; z = Φ⁻¹(1−p); linha = m + σ·z."""
    pares = {}
    for o in ofertas or []:
        if o.get("tipo") == "total" and o.get("periodo") == periodo:
            pares.setdefault(o["linha"], {})[o["lado"]] = o["odd"]
    pts = []
    for L, d in pares.items():
        if "acima" in d and "abaixo" in d:
            pa, pb = 1 / d["acima"], 1 / d["abaixo"]
            p = pa / (pa + pb)
            if 0.03 < p < 0.97:
                pts.append((L, _ND().inv_cdf(1 - p)))
    if not pts:
        return None
    sig_pad = 9.0 if periodo == "jogo" else (6.5 if periodo in ("t1", "t2") else 4.5)
    if len(pts) == 1 or max(z for _, z in pts) - min(z for _, z in pts) < 0.05:
        L, z = pts[0]
        return (L - sig_pad * z, sig_pad)
    n = len(pts)
    mz = sum(z for _, z in pts) / n
    mL = sum(L for L, _ in pts) / n
    cov = sum((z - mz) * (L - mL) for L, z in pts)
    var = sum((z - mz) ** 2 for _, z in pts)
    sig = cov / var if var > 0 else sig_pad
    if not (1.0 <= sig <= 40.0):
        sig = sig_pad
    return (mL - sig * mz, sig)


def prob_placar(modelo, ks):
    m, sg = modelo
    d = _ND(m, sg)
    return {k: d.cdf(k + 0.5) - d.cdf(k - 0.5) for k in ks}


def faixa_placares(pernas, modelo):
    ls = [p["linha"] for p in pernas]
    m, sg = modelo if modelo else (sum(ls) / len(ls), 10.0)
    lo = int(min(min(ls), m - 4 * sg)) - 3
    hi = int(max(max(ls), m + 4 * sg)) + 3
    return list(range(max(0, lo), hi + 1))


def resumo_posicao(pernas, modelo):
    ks = faixa_placares(pernas, modelo)
    liq = liquido_por_placar(pernas, ks)
    r = {"pior": round(min(liq.values()), 2), "melhor": round(max(liq.values()), 2),
         "gasto": round(sum(p["stake"] for p in pernas), 2)}
    lucro = [k for k in ks if liq[k] > 0.005]
    r["faixas_lucro"] = _faixas(lucro)
    if modelo:
        pr = prob_placar(modelo, ks)
        tot = sum(pr.values()) or 1.0
        r["ev"] = round(sum(pr[k] * liq[k] for k in ks) / tot, 2)
        r["p_preju"] = round(sum(pr[k] for k in ks if liq[k] < -0.005) / tot, 4)
        r["p_lucro"] = round(sum(pr[k] for k in ks if liq[k] > 0.005) / tot, 4)
        r["media"], r["desvio"] = round(modelo[0], 1), round(modelo[1], 1)
    return r


# ---------------------------------------------------------------- CARTEIRA DO JOGO (operador 16/11)
# Todas as apostas ACEITAS de um jogo/período, avaliadas juntas por total final. 'Janela' = totais em que a
# carteira INTEIRA dá lucro. Uma entrada nova só vale se criar totais novos com lucro sem tirar o de nenhum.
def resultado_perna(perna, k):
    """'G' ganha, 'P' perde, 'D' devolvida (linha inteira empatada) — para o total final k."""
    if abs(k - perna["linha"]) < 1e-9:
        return "D"
    ganha = k > perna["linha"] if perna["lado"] == "acima" else k < perna["linha"]
    return "G" if ganha else "P"


def faixa_carteira(pernas, margem=2):
    ls = [p["linha"] for p in pernas] or [0]
    return list(range(max(0, int(min(ls)) - margem), int(max(ls)) + margem + 2))


def greens_por_total(pernas, ks):
    return {k: sum(1 for p in pernas if resultado_perna(p, k) == "G") for k in ks}


def cobertura_2g(pernas, ks=None):
    """JANELA DE GREEN da carteira: totais finais em que a carteira INTEIRA dá lucro.
    (Até 16/11 era 'um Over e um Under ganhando juntos' — mas com vários pares isso não garante lucro:
    exemplo do operador com 6 apostas de R$ 30 a 1,85: 3 ganhando = 166,50 − 180 = prejuízo.)"""
    ks = ks if ks is not None else faixa_carteira(pernas)
    if not pernas:
        return set()
    liq = liquido_por_placar(pernas, ks)
    # só ENTRE a linha mais baixa e a mais alta: lucro numa ponta aberta (ex.: 'abaixo de tudo') é aposta de
    # direção, não janela — essas pontas continuam na tabela e no limite de perda, mas não contam como janela
    lo, hi = min(p["linha"] for p in pernas), max(p["linha"] for p in pernas)
    return {k for k in ks if liq[k] > 0.005 and lo - 1e-9 <= k <= hi + 1e-9}


def tabela_carteira(pernas, novas=None):
    """Uma linha por total final: resultado de cada aposta, nº de greens e líquido (R$).
    Com 'novas', compara antes x depois e marca onde a entrada nova AUMENTA a perda."""
    todas = list(pernas) + list(novas or [])
    ks = faixa_carteira(todas)
    liq = liquido_por_placar(todas, ks)
    janela = cobertura_2g(todas, ks)
    antes = liquido_por_placar(pernas, ks) if (novas and pernas) else {}
    linhas = []
    for k in ks:
        res = [resultado_perna(p, k) for p in todas]
        ln = {"total": k, "res": res, "greens": res.count("G"), "liquido": round(liq[k], 2),
              "cobre": k in janela}
        if antes:
            ln["antes"] = round(antes[k], 2)
            ln["piorou"] = liq[k] < antes[k] - 0.005
        linhas.append(ln)
    return linhas


def avaliar_nova_entrada(carteira, novas, cfg):
    """A entrada nova (par ou perna) amplia a cobertura da carteira inteira dentro do limite de perda?"""
    todas = list(carteira) + list(novas)
    ks = faixa_carteira(todas)
    cob_antes = cobertura_2g(carteira, ks) if carteira else set()
    cob_depois = cobertura_2g(todas, ks)
    novos = sorted(cob_depois - cob_antes)
    perdidos = sorted(cob_antes - cob_depois)  # totais que davam lucro e passariam a dar prejuízo
    liq = liquido_por_placar(todas, ks)
    pior = round(min(liq.values()), 2)
    lim = float(cfg.get("perda_max_por_jogo_brl", 100.0))
    tab = tabela_carteira(carteira, novas)
    piorou = [l["total"] for l in tab if l.get("piorou")]
    r = {"novos_totais": novos, "cobertura": sorted(cob_depois), "pior": pior, "piorou_em": piorou,
         "tres_ou_mais": [l["total"] for l in tab if l["greens"] >= 3]}
    r["perdidos"] = perdidos
    if not novos:
        r.update(ok=False, motivo="não cria nenhum total novo com lucro na carteira")
    elif perdidos:
        r.update(ok=False, motivo="tiraria o lucro de %s (que já davam green)" % _txt_totais(perdidos))
    elif pior < -lim - 1e-9:
        r.update(ok=False, motivo="carteira inteira passaria a perder até R$ %s (limite R$ %s por jogo)" % (_br(-pior), _br(lim)))
    else:
        r.update(ok=True, motivo="")
    return r


def _txt_totais(ks):
    """[223, 224, 225, 228] -> '223–225, 228'"""
    return ", ".join(("%d" % a) if a == b else ("%d–%d" % (a, b)) for a, b in _faixas(sorted(ks))) or "—"


# ---------------------------------------------------------------- ESCADINHA (especificação do operador 19/11)
def janela_continua(pernas, ks=None):
    """JANELA da carteira = faixa CONTÍNUA de totais finais com saldo líquido > 0, só entre a linha mais baixa e a
    mais alta (as pontas abertas contam para o risco, não para a janela). Com mais de uma faixa, a maior.
    Devolve (ini, fim) ou None."""
    if not pernas:
        return None
    ks = ks if ks is not None else faixa_carteira(pernas)
    liq = liquido_por_placar(pernas, ks)
    lo, hi = min(p["linha"] for p in pernas), max(p["linha"] for p in pernas)
    corridas, atual = [], []
    for k in sorted(ks):
        if lo - 1e-9 <= k <= hi + 1e-9 and liq[k] > 0.005:
            if atual and k == atual[-1] + 1:
                atual.append(k)
            else:
                if atual:
                    corridas.append(atual)
                atual = [k]
        elif atual:
            corridas.append(atual)
            atual = []
    if atual:
        corridas.append(atual)
    if not corridas:
        return None
    melhor = max(corridas, key=len)
    return (melhor[0], melhor[-1])


def _nome_perna(p):
    return "%s %s" % ("Over" if p["lado"] == "acima" else "Under", _fmt_linha(p["linha"]))


def _mesma_perna(p, q):
    """'Mesma perna' = mesmo LADO + mesma LINHA + mesmo MERCADO (tipo + período). Casa e odd não entram."""
    return (p["lado"] == q["lado"] and abs(p["linha"] - q["linha"]) < 1e-9
            and p.get("tipo", "total") == q.get("tipo", "total") and p.get("periodo", "jogo") == q.get("periodo", "jogo"))


def avaliar_reentrada(carteira, novas, cfg):
    """REGRAS OBRIGATÓRIAS da reentrada (filtro). Simula a carteira INTEIRA (apostas aceitas) + o par candidato.
    - A ampliação é decidida pelo LUCRO LÍQUIDO da carteira em cada total final, nunca pela linha nominal.
    - Âncora: uma perna repetida (mesmo lado + linha + mercado) pode ficar PARADA; basta a outra perna ter ido
      para fora e o lucro da carteira cobrir totais novos contíguos.
    - Escadinha: as duas linhas se movem; mesmas exigências.
    A ORDEM DE ESCOLHA (nota_reentrada) só é aplicada depois, entre os candidatos que passaram aqui.
    Devolve dict: ok, motivo, tipo, janela_antiga, janela_nova, novos, piorados, pior_antes, pior_depois,
    melhor_depois, investido_antes, investido_depois, saldos [(total, antes, depois)]."""
    r = {"ok": False, "motivo": "", "tipo": None, "janela_antiga": None, "janela_nova": None, "novos": [],
         "piorados": [], "pior_antes": None, "pior_depois": None, "melhor_depois": None, "saldos": [],
         "investido_antes": round(sum(p["stake"] for p in carteira), 2),
         "investido_depois": round(sum(p["stake"] for p in list(carteira) + list(novas)), 2)}
    ov = [p for p in novas if p["lado"] == "acima"]
    un = [p for p in novas if p["lado"] == "abaixo"]
    if len(novas) != 2 or len(ov) != 1 or len(un) != 1:
        r["motivo"] = "o par precisa ter um Over e um Under"
        return r
    lo, hi = ov[0]["linha"], un[0]["linha"]
    # par idêntico: o mesmo Over E o mesmo Under já entraram JUNTOS (mesmo par); sem marca de par, as duas linhas
    grupos = {}
    for p in carteira:
        grupos.setdefault(p.get("par", id(p)), []).append(p)
    tem_par = any(p.get("par") is not None for p in carteira)
    identico = any(any(_mesma_perna(ov[0], q) for q in g) and any(_mesma_perna(un[0], q) for q in g)
                   for g in grupos.values()) if tem_par else \
        (any(_mesma_perna(ov[0], q) for q in carteira) and any(_mesma_perna(un[0], q) for q in carteira))
    if identico:
        r["motivo"] = "par idêntico já utilizado (%s + %s)" % (_nome_perna(ov[0]), _nome_perna(un[0]))
        return r
    # repetir uma perna é PERMITIDO (âncora) enquanto amplia; o limite é configurável (operador 19/11: 4)
    max_rep = int(cfg.get("max_repeticoes_mesma_perna", 4))
    usos = {}
    for p in (ov[0], un[0]):
        n = sum(1 for q in carteira if _mesma_perna(p, q))
        usos[_nome_perna(p)] = n + 1
        if n >= max_rep:
            r["motivo"] = "%s já usado %d vezes (máximo %d)" % (_nome_perna(p), n, max_rep)
            return r
    r["ancora"] = [k for k, v in usos.items() if v > 1]
    r["tipo"] = "âncora" if r["ancora"] else "escadinha"
    todas = list(carteira) + list(novas)
    ks = faixa_carteira(todas)
    antes, depois = liquido_por_placar(carteira, ks), liquido_por_placar(todas, ks)
    r["saldos"] = [(k, round(antes[k], 2), round(depois[k], 2)) for k in ks]
    r["pior_antes"], r["pior_depois"] = round(min(antes.values()), 2), round(min(depois.values()), 2)
    r["melhor_depois"] = round(max(depois.values()), 2)
    ja = janela_continua(carteira, ks)
    r["janela_antiga"] = ja
    if ja:
        r["piorados"] = [k for k in range(ja[0], ja[1] + 1) if depois[k] <= 0.005]
        if r["piorados"]:
            r["motivo"] = "transforma total %s de lucro em prejuízo" % _txt_totais(r["piorados"])
            return r
    # faixa contínua NOVA que contém a antiga
    lo_t, hi_t = min(p["linha"] for p in todas), max(p["linha"] for p in todas)
    pos = {k for k in ks if lo_t - 1e-9 <= k <= hi_t + 1e-9 and depois[k] > 0.005}
    if ja:
        ini, fim = ja
        while ini - 1 in pos:
            ini -= 1
        while fim + 1 in pos:
            fim += 1
        jn = (ini, fim)
    else:
        jn = janela_continua(todas, ks)
    r["janela_nova"] = jn
    r["novos"] = [k for k in range(jn[0], jn[1] + 1) if not (ja and ja[0] <= k <= ja[1])] if jn else []
    if not r["novos"]:
        ilha = sorted(k for k in pos if antes[k] <= 0.005 and not (jn and jn[0] <= k <= jn[1]))
        r["motivo"] = ("cria ilha separada da janela (%s)" % _txt_totais(ilha)) if ilha else \
            "não acrescenta total lucrativo novo"
        return r
    lim = float(cfg.get("perda_max_por_jogo_brl", 100.0))
    if r["pior_depois"] < -lim - 1e-9:
        r["motivo"] = "pior caso ultrapassa −R$ %s (ficaria %s)" % (_br(lim), _br(r["pior_depois"]))
        return r
    # métricas da escolha entre candidatos (operador 19/11)
    r["capital"] = round(sum(p["stake"] for p in novas), 2)
    r["lucro_min_janela"] = round(min(depois[k] for k in range(jn[0], jn[1] + 1)), 2)
    conc = {}
    for p in todas:
        conc[_nome_perna(p)] = conc.get(_nome_perna(p), 0) + 1
    r["concentracao"] = conc
    r["concentracao_max"] = max(conc.values())
    r["ok"] = True
    r["motivo"] = "amplia a janela %s → %s (%s%s)" % (
        ("%d–%d" % ja) if ja else "—", "%d–%d" % jn, r["tipo"],
        (": repete " + ", ".join(r["ancora"])) if r["ancora"] else "")
    return r


def nota_reentrada(av, custo=0.0, odd_min_par=0.0):
    """Só para candidatos APROVADOS. Maior = melhor (operador 21/11), SEM preferência por âncora ou escadinha:
    1) mais totais finais novos com lucro; 2) maior ampliação da janela contínua; 3) menor pior prejuízo da
    carteira; 4) menor custo; 5) (estabilidade: todos já passaram pelas 2 leituras); 6) maior odd mínima do par."""
    jn = av.get("janela_nova")
    largura = (jn[1] - jn[0] + 1) if jn else 0
    return (len(av.get("novos") or []),
            largura,
            round(av.get("pior_depois") or -1e9, 2),
            -round(custo or 0.0, 3),
            round(odd_min_par or 0.0, 3))


def pernas_aceitas(bil):
    """As pernas como a CASA aceitou: odd do recibo (odd_aceita) > odd viva no cupom > odd lida; stake digitada."""
    return [{"casa": p["casa"], "tipo": p["tipo"], "periodo": p["periodo"], "lado": p.get("lado"), "linha": p["linha"],
             "odd": p.get("odd_aceita") or p.get("odd_viva") or p["odd"], "stake": p["stake"], "par": bil.get("id")}
            for p in bil["pernas"]]


# ---------------------------------------------------------------- EXECUÇÃO EM ESTADOS (operador 22/11)
def _hms_ms(t):
    """23:08:50.120"""
    return datetime.fromtimestamp(t).strftime("%H:%M:%S.") + "%03d" % int((t % 1) * 1000)


def validar_par_vivo(bil, odd1, odd2, cfg, carteira=None):
    """BARREIRA FINAL: o par refeito SÓ com as odds que os cupons mostram agora e as stakes já digitadas.
    Reaplica odd mínima, tabela de custo da janela, janela lucrativa contínua, perda máxima e (com carteira) todas as
    regras de reentrada. Devolve (ok, motivo, custo_vivo, pior)."""
    p1, p2 = bil["pernas"]
    om = cfg.get("odd_min")
    for p, o in ((p1, odd1), (p2, odd2)):
        piso = float(om) if om else p["odd"] - TOL_ODD_CUPOM
        if o is None or o < piso - 1e-9:
            return False, "odd viva %s @%s abaixo do mínimo %s (%s)" % (
                _nome_perna(p).replace(".", ",") if p.get("tipo") == "total" else p.get("selecao", "?"),
                _br(o, 3).rstrip("0").rstrip(",") if o is not None else "?", _br(piso), NOME_CASA[p["casa"]]), None, None
    s1, s2 = p1["stake"], p2["stake"]
    tot = s1 + s2
    custo = (tot - min(s1 * odd1, s2 * odd2)) / tot * 100 if tot else 999.0
    teto = custo_max_para(bil["janela"], cfg)
    if custo > teto + 1e-9:
        return False, "custo vivo %s%% > limite %s%% (janela %s)" % (_br(custo), _br(teto), _br(bil["janela"], 1)), custo, None
    if bil.get("tipo", "total") != "total":
        return True, "", custo, None
    novas = [dict(p1, odd=odd1), dict(p2, odd=odd2)]
    if carteira:
        av = avaliar_reentrada(carteira, novas, cfg)
        if not av["ok"]:
            return False, av["motivo"], custo, av.get("pior_depois")
        return True, "", custo, av["pior_depois"]
    ks = faixa_carteira(novas)
    liq = liquido_por_placar(novas, ks)
    pior = round(min(liq.values()), 2)
    lim = float(cfg.get("perda_max_por_jogo_brl", 100.0))
    if pior < -lim - 1e-9:
        return False, "pior caso ultrapassa −R$ %s (ficaria %s)" % (_br(lim), _br(pior)), custo, pior
    if bil.get("janela", 0) >= 1 and not janela_continua(novas, ks):
        return False, "sem janela lucrativa contínua com as odds vivas", custo, pior
    return True, "", custo, pior


def assinatura_par(times_ou_jogo, a, b):
    """evento + mercado + lado1 + linha1 + lado2 + linha2 (a casa e a odd não entram)."""
    ev = chave_jogo(times_ou_jogo) if isinstance(times_ou_jogo, (list, tuple)) else _norm(times_ou_jogo or "")
    def um(o):
        return "%s|%s" % (o.get("lado") or o.get("time_idx") or o.get("time") or "?", round(float(o["linha"]), 2))
    return "%s|%s|%s|%s|%s" % (ev, a.get("tipo", "total"), a.get("periodo", "jogo"), um(a), um(b))


class ControleTentativas:
    """Evita o ciclo infinito: o MESMO par que falhou fica em cooldown; tentativas por minuto no mesmo jogo.
    Nunca esconde posição confirmada/incerta — isso é o bloqueio, que vem antes e para tudo."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.cool = {}   # assinatura -> até quando
        self.hist = {}   # jogo -> [instantes das tentativas]

    def falhou(self, ass, agora=None):
        self.cool[ass] = (agora or time.time()) + float(self.cfg.get("cooldown_par_s", 60))

    def cooldown(self, ass, agora=None):
        """Segundos que faltam (0 = livre)."""
        return max(0.0, self.cool.get(ass, 0) - (agora or time.time()))

    def registrar(self, jogo, agora=None):
        agora = agora or time.time()
        self.hist.setdefault(jogo, []).append(agora)

    def excedeu(self, jogo, agora=None):
        agora = agora or time.time()
        lst = [t for t in self.hist.get(jogo, []) if agora - t < 60]
        self.hist[jogo] = lst
        return len(lst) >= int(self.cfg.get("max_tentativas_por_minuto", 6))


def classificar_reconciliacao(r1, r2):
    """r = True (aceita) / False (recusada, nada apostado) / None (incerto) / 'nao_disparada'."""
    vals = (r1, r2)
    if any(v is None for v in vals):
        return "INCERTO"
    if r1 is True and r2 is True:
        return "AMBAS_CONFIRMADAS"
    if r1 is True or r2 is True:
        return "UMA_CONFIRMADA"
    return "AMBAS_RECUSADAS"


def portao_reentrada(ultima_t, ultima_ml, agora, ml, cfg):
    """Depois de um par aceito: espera mínima E movimento mínimo da linha principal da PS. (ok, motivo)"""
    if ultima_t is None:
        return True, ""
    espera = float(cfg.get("reentrada_espera_min_s", 60))
    if agora - ultima_t < espera - 1e-9:
        return False, "intervalo mínimo ainda não terminou (%.0fs de %.0fs)" % (agora - ultima_t, espera)
    mov = float(cfg.get("min_movimento_linha", cfg.get("reentrada_movimento_min", 0.5)))
    if ml is None or ultima_ml is None or abs(ml - ultima_ml) < mov - 1e-9:
        return False, "linha não se movimentou o mínimo configurado (%s → %s; mínimo %s)" % (
            _fmt_linha(ultima_ml) if ultima_ml is not None else "?", _fmt_linha(ml) if ml is not None else "?", _br(mov, 1))
    return True, ""


# ---------------------------------------------------------------- SEGURANÇA OPERACIONAL (operador 20/11)
OFERTAS_ENCONTRADAS, VAZIO_CONFIRMADO, SUSPENSO, ERRO_DE_LEITURA = (
    "OFERTAS_ENCONTRADAS", "VAZIO_CONFIRMADO", "SUSPENSO", "ERRO_DE_LEITURA")


def classificar_leitura(l):
    """Uma leitura de casa -> OFERTAS_ENCONTRADAS | VAZIO_CONFIRMADO | SUSPENSO | ERRO_DE_LEITURA.
    VAZIO só quando a leitura CONCLUIU na página do jogo e não há linha nenhuma. Deslogada, limitada/captcha,
    'não é o jogo', erro, timeout, sem resposta = ERRO (nunca conta como mercado vazio)."""
    if not l or not isinstance(l, dict):
        return ERRO_DE_LEITURA
    mot = _sem_acento(l.get("motivo") or "").lower()
    if l.get("limitada") or not l.get("ok"):
        return ERRO_DE_LEITURA
    if l.get("ofertas"):
        return OFERTAS_ENCONTRADAS
    if re.search(r"travad|cadeado|suspens|bloquead", mot):
        return SUSPENSO
    return VAZIO_CONFIRMADO


def contar_vazio(classes, cont, erros, cfg):
    """Contador do 'mercado sumiu'. classes = {casa: classe}. Só VAZIO_CONFIRMADO nas DUAS casas soma;
    qualquer oferta zera; suspenso não soma nem zera; erro não soma (conta erros seguidos à parte).
    Devolve (cont, erros, acao) com acao: 'libera' | 'alerta_erro' | None."""
    vals = list(classes.values())
    if any(v == OFERTAS_ENCONTRADAS for v in vals):
        cont, erros = 0, 0
    elif vals and all(v == VAZIO_CONFIRMADO for v in vals):
        cont, erros = cont + 1, 0
    elif any(v == ERRO_DE_LEITURA for v in vals):
        erros += 1
    else:
        erros = 0  # suspenso: o jogo está vivo; não soma vazio
    if cont >= int(cfg.get("protecao_vazias_lib", 20)):
        return cont, erros, "libera"
    if erros >= int(cfg.get("erros_leitura_alerta", 10)):
        return cont, erros, "alerta_erro"
    return cont, erros, None


def linha_principal(ofertas, periodo="jogo"):
    """A linha 'do meio' da escada (Acima e Abaixo com odds mais parecidas) — é ela que mostra se o jogo andou."""
    pares = {}
    for o in ofertas or []:
        if o.get("tipo") == "total" and o.get("periodo") == periodo:
            pares.setdefault(round(o["linha"], 2), {})[o["lado"]] = o["odd"]
    melhores = [(abs(1 / d["acima"] - 1 / d["abaixo"]), L) for L, d in pares.items() if "acima" in d and "abaixo" in d]
    return min(melhores)[1] if melhores else None


def tipo_reentrada(carteira, novas):
    """Lógica do PARCEIRO (17/11) para a reentrada de um par (Over + Under) — a única regra das reentradas:
    - nunca o MESMO par (as duas linhas já apostadas nos mesmos lados);
    - 'duplo duplo': repete/encosta numa linha e cria totais com 4+ greens (os dois pares ganhando dos dois lados);
    - senão 'sequência': a janela do par novo encosta (ou sobrepõe) na janela que a carteira já tem, para cima ou
      para baixo — 200/202 -> 202/204 -> 204/206.
    Devolve 'duplo duplo' | 'sequência' | None (não entra)."""
    ov = [p for p in novas if p["lado"] == "acima"]
    un = [p for p in novas if p["lado"] == "abaixo"]
    if not ov or not un or not carteira:
        return None
    lo, hi = ov[0]["linha"], un[0]["linha"]
    if any(p["lado"] == "acima" and abs(p["linha"] - lo) < 1e-9 for p in carteira) and \
            any(p["lado"] == "abaixo" and abs(p["linha"] - hi) < 1e-9 for p in carteira):
        return None  # o mesmo par de novo
    # 19/11 (Pacers x Rockets): repetiu o Acima 231.5 QUATRO vezes. A mesma linha (mesmo lado) só repete UMA vez
    # — é o duplo duplo do parceiro ('misturo linha igual com uma linha maior'); a 3ª vez não.
    for p in novas:
        iguais = sum(1 for q in carteira if q["lado"] == p["lado"] and abs(q["linha"] - p["linha"]) < 1e-9)
        if iguais >= 2:
            return None
    # ESCADINHA: o par novo tem que ESTICAR a janela para fora (cima ou baixo). Par que fica dentro da janela que
    # a carteira já cobre (ex.: Under 233 depois de Over 231.5/Under 234) não entra.
    ovs0 = [p["linha"] for p in carteira if p["lado"] == "acima"]
    uns0 = [p["linha"] for p in carteira if p["lado"] == "abaixo"]
    if ovs0 and uns0:
        ini, fim = min(ovs0), max(uns0)  # faixa que a carteira já cobre
        if not (hi > fim + 1e-9 or lo < ini - 1e-9):
            return None
    todas = list(carteira) + list(novas)
    ks = faixa_carteira(todas)
    g, g0 = greens_por_total(todas, ks), greens_por_total(carteira, ks)
    if any(g[k] >= 4 and g0[k] < 4 for k in ks):
        return "duplo duplo"
    # janelas como intervalos de totais onde Over e Under do MESMO par ganham (ou devolvem) juntos
    jan_nova = [k for k in ks if lo <= k <= hi]
    ovs = [p["linha"] for p in carteira if p["lado"] == "acima"]
    uns = [p["linha"] for p in carteira if p["lado"] == "abaixo"]
    if not ovs or not uns or not jan_nova:
        return None
    jan_atual = [k for k in ks if min(ovs) <= k <= max(uns)]
    if not jan_atual:
        return None
    if min(jan_nova) <= max(jan_atual) + 1 and max(jan_nova) >= min(jan_atual) - 1:
        return "sequência"
    return None


def _faixas(ks):
    out, ini, ant = [], None, None
    for k in ks:
        if ini is None:
            ini = ant = k
        elif k == ant + 1:
            ant = k
        else:
            out.append([ini, ant]); ini = ant = k
    if ini is not None:
        out.append([ini, ant])
    return out


def atualizar_tabela_pos(pos):
    """Tabela por total final da carteira inteira; compara com antes da ÚLTIMA entrada (marca onde piorou)."""
    pernas = pos.get("pernas") or []
    n = int(pos.get("ultimo_n") or 0)
    if 0 < n < len(pernas):
        pos["tabela"] = tabela_carteira(pernas[:-n], pernas[-n:])
    else:
        pos["tabela"] = tabela_carteira(pernas)
    pos["cobertura_2g"] = _txt_totais(cobertura_2g(pernas))


def over_original(pos):
    """Referência do filtro: o Over da ENTRADA ORIGINAL (nunca o Under já apostado)."""
    ov = [p for p in pos["pernas"] if p.get("origem") == "entrada" and p["lado"] == "acima"]
    return ov[0]["linha"] if ov else None


def under_original(pos):
    un = [p for p in pos["pernas"] if p.get("origem") == "entrada" and p["lado"] == "abaixo"]
    return un[0]["linha"] if un else None


def avaliar_cobertura(pos, ofertas_por_casa, modelo, cfg):
    """Procura UMA aposta avulsa (mesmo mercado/período) que AMPLIE A JANELA da carteira inteira
    (sequência manual do operador 15–16/11: Under 229.5 sozinho abriu 227–229; Under 236.5 abriu até 236).
    Critério: cria totais NOVOS com lucro na carteira inteira, sem tirar o lucro de nenhum + pior caso dentro do limite.
    SEM distância mínima fixa (operador 16/11: a regra 'Under ≥ 4 pts acima do Over original' foi REMOVIDA) —
    a comparação é com a JANELA ATUAL da carteira inteira; ela pode crescer 1 total por vez.
    EV e chance de prejuízo são MOSTRADOS, não travam (pos_exigir_ev liga).
    Devolve (melhor_candidato | None, lista_de_avaliados_para_o_painel)."""
    pernas = pos["pernas"]
    if not pernas:
        return None, []
    base = resumo_posicao(pernas, modelo) if modelo else None
    gasto_cob = sum(p["stake"] for p in pernas if p.get("origem") == "cobertura")
    resta = cfg["pos_max_total_coberturas_brl"] - gasto_cob
    ja = {(p["casa"], p["lado"], round(p["linha"], 3)) for p in pernas}
    cob_atual = cobertura_2g(pernas)
    topo = max(cob_atual) if cob_atual else None
    fundo = min(cob_atual) if cob_atual else None
    lim = min(float(cfg.get("pos_perda_max_total_brl", 100.0)), float(cfg.get("perda_max_por_jogo_brl", 100.0)))
    avaliados, melhor, chave_melhor = [], None, None
    for casa, ofs in ofertas_por_casa.items():
        for o in ofs or []:
            if o.get("tipo") != "total" or o.get("periodo") != pos["periodo"]:
                continue
            # só para o painel: quanto a linha está além da borda atual da janela
            if o["lado"] == "abaixo":
                dist = (o["linha"] - topo) if topo is not None else 0.0
            else:
                dist = (fundo - o["linha"]) if fundo is not None else 0.0
            if (casa, o["lado"], round(o["linha"], 3)) in ja:
                continue  # nada de repetir a mesma aposta
            if cfg.get("odd_min") and o.get("odd", 0) < float(cfg["odd_min"]) - 1e-9:
                continue  # odd mínima (operador 17/11)
            # stake: PS = perna mínima (€5); 1aWin = a maior da grade que cabe no limite de perda da carteira
            eur_max = float(cfg.get("perna_ps_eur_max") or cfg["perna_ps_eur"])
            if casa == CASA_PS:  # €6, €5,5 ... até a perna mínima (€5)
                grade, e = [], eur_max
                while e >= cfg["perna_ps_eur"] - 1e-9:
                    grade.append(round(e * cfg["cambio_brl_eur"], 2))
                    e -= 0.5
            else:
                mx = float(cfg.get("pos_stake_1w_brl") or round(eur_max * cfg["cambio_brl_eur"], 2))
                mn = float(cfg.get("pos_stake_min_1w_brl", 5.0))
                grade = []
                v = mx
                while v >= mn - 1e-9:
                    grade.append(round(v, 2))
                    v -= 5.0
            cand = None
            for st in grade:
                if st > cfg["pos_max_por_cobertura_brl"] + 1e-9 or st > resta + 1e-9:
                    continue
                nova = dict(o, casa=casa, stake=st, origem="cobertura")
                av = avaliar_nova_entrada(pernas, [nova], dict(cfg, perda_max_por_jogo_brl=lim))
                r = resumo_posicao(pernas + [nova], modelo) if modelo else {"pior": av["pior"]}
                r["novos_totais"], r["piorou_em"] = av["novos_totais"], av["piorou_em"]
                ok = av["ok"]
                if ok and cfg.get("pos_exigir_ev") and base and "ev" in r:
                    ok = (r["ev"] - base["ev"] >= cfg["pos_ev_min_brl"] - 1e-9
                          and base["p_preju"] - r["p_preju"] >= cfg["pos_reducao_risco_min"] - 1e-9)
                cand = {"casa": casa, "lado": o["lado"], "linha": o["linha"], "odd": o["odd"], "stake": st, "ok": ok,
                        "dist": round(dist, 1), "antes": base or {}, "depois": r, "oferta": o, "motivo": av["motivo"]}
                if ok:
                    break
            if cand is None:
                continue
            avaliados.append(cand)
            if ok:
                # 'de pouco em pouco': primeiro o que CONTINUA a janela sem buraco, depois a odd mais alta (a linha viva)
                novos = av["novos_totais"]
                cola = (topo is not None and o["lado"] == "abaixo" and novos and novos[0] == topo + 1) or \
                       (fundo is not None and o["lado"] == "acima" and novos and novos[-1] == fundo - 1)
                chave = (1 if cola else 0, o["odd"])
                if melhor is None or chave > chave_melhor:
                    melhor, chave_melhor = cand, chave
    return melhor, avaliados


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
      if (/evento exclu[ií]do|event (excluded|removed)/i.test(t)) {
        // 1aWin: '5 · Evento excluído · Cancelar' é o aviso de DESFAZER de quem acabou de tirar a seleção:
        // NÃO é seleção (e 'Cancelar' a devolveria ao cupom)
        let p = e, toast = false;
        for (let k = 0; k < 4 && p && !cups.includes(p); k++, p = p.parentElement) {
          const pt = (p.innerText || '');
          if (pt.length < 80 && /(^|\s)(cancelar|desfazer|undo)(\s|$)/i.test(pt)) { toast = true; break; }
        }
        if (!toast) cand.push(e);  // seleção morta também é seleção
        continue;
      }
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
# 1aWin (print do operador 15/11 21:1x): ao TIRAR uma seleção aparece '5 · Evento excluído · Cancelar' por 5s.
# É o aviso de DESFAZER ('Cancelar' devolve a seleção ao cupom!) — não é mercado encerrado e o cupom está vazio.
_RX_TOAST_DESFAZER = re.compile(r"evento exclu[ií]do\s*\n?\s*(cancelar|desfazer|undo)", re.I)


def _sem_toast_desfazer(tx):
    """Tira do texto do cupom o aviso de desfazer da 1aWin ('Evento excluído · Cancelar')."""
    tx = tx or ""
    if not re.search(r"evento exclu[ií]do", tx, re.I) or not re.search(r"\b(cancelar|desfazer|undo)\b", tx, re.I):
        return tx
    return "\n".join(l for l in tx.splitlines()
                     if not re.search(r"evento exclu[ií]do|^\s*(cancelar|desfazer|undo)\s*$|^\s*\d{1,2}\s*$", l, re.I))


# motivos de montagem em que a CASA mexeu na linha (vale tentar a âncora na montagem)
_RX_LINHA_MUDOU = re.compile(r"não está mais na página|cupom sem a linha|não recebeu a seleção|sumiu do cupom|desapareceu|"
                             r"odd mudou|odd do cupom caiu|travou o mercado|leitura não achou a seleção", re.I)
_RX_EXCLUIDO = re.compile(r"evento exclu[ií]do|event (excluded|removed)|mercado (fechado|encerrado)|market closed", re.I)
_RX_REMOVER_TUDO = re.compile(r"^\s*(remover tudo|remove all|limpar tudo|clear all|apagar tudo)\s*$", re.I)
_RX_OK_MODAL = re.compile(r"^\s*(ok|sim|confirmar|yes)\s*$", re.I)
_RX_APOSTAR = re.compile(r"^\s*(fazer (a |uma )?aposta|apostar|place bet|confirmar aposta|realizar aposta)\b", re.I)
_RX_SUCESSO = re.compile(r"aposta (aceita|feita|realizada|registrada|confirmada|efetuada)|ir para o hist[oó]rico|apostas? (foi|foram) (aceita|feita|realizada)|"
                         r"bet (accepted|placed)|sua aposta foi|successfully placed|aceit[ao] com sucesso", re.I)
_RX_RECUSA = re.compile(r"aposta (foi )?(recusada|rejeitada|n[aã]o aceita)|bet (has been |was )?(rejected|declined|not accepted)|saldo insuficiente|"
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


class CacaInterrompida(Exception):
    """Sinal novo assumiu: a caça do jogo velho para na hora (log 20/11 07:12–07:15)."""


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
        # 1aWin real (HTML de 14/11 23:58): cada seleção é um [data-scope="CouponCard"]
        self.sel_item = ('[class*="BetItemComponent"], [class*="slip-sel" i]' if nome == CASA_PS
                         else '[data-scope="CouponCard"]')
        self.thread = threading.Thread(target=self._laco, name="casa-" + nome, daemon=True)
        self.parar_caca = threading.Event()  # sinal novo assumiu: a caça/leitura do jogo velho para e sai da fila

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

    def enfileirar(self, fn, *args):
        """Põe o job na fila e devolve (evento de pronto, caixa do resultado) — para quem quer acompanhar SEM bloquear
        (PREPARAR: a primeira casa que falhar cancela a outra na hora)."""
        ev = threading.Event()
        caixa = {}
        self.fila.put((fn, args, ev, caixa))
        return ev, caixa

    def em_paralelo(self, fn, *args, timeout=180):
        """Dispara o job e devolve uma função que espera o resultado."""
        ev, caixa = self.enfileirar(fn, *args)

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
    def _aviso_indisponivel_rapido(self):
        """Checagem LEVE (só janelas/diálogos visíveis, não a página inteira da PS, que é enorme)."""
        try:
            return bool(self._eval(r"""() => {
                const RX = /sele[cç][aã]o indispon[ií]vel|selection (is )?(currently )?unavailable|indispon[ií]vel para apostas|aposta m[ií]nima [eé] maior/i;
                for (const e of document.querySelectorAll('[role=dialog], [class*="modal" i], [class*="popup" i], [class*="dialog" i], [class*="alert" i]')) {
                    const r = e.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0 && RX.test(e.innerText || '')) return true;
                }
                return false; }"""))
        except Exception:
            return False

    def fechar_aviso_indisponivel(self, tx=None, rapido=False):
        """PS: janela 'Seleção Indisponível — a aposta mínima é maior que a aposta máxima'.
        Ela BLOQUEIA a página até alguém clicar OK. Fecha e devolve o texto (ou '')."""
        if self.nome != CASA_PS:
            return ""
        if rapido and not self._aviso_indisponivel_rapido():
            return ""
        try:
            tx = tx if (tx is not None and not rapido) else self.texto()
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
        # aviso de desfazer da 1aWin ('5 · Evento excluído · Cancelar') pode vir no próprio cartão: não é seleção
        itens = [i for i in itens if not (_RX_TOAST_DESFAZER.search(i or "") and len(i) < 80
                                          and not re.search(r"\b(acima|abaixo|mais|menos|over|under)\b", i, re.I))]
        if itens:
            return itens
        # 'vazio' só com a PROVA da casa ('Selecione um resultado…' / 'Nenhuma aposta selecionada');
        # sem a frase e com cara de seleção = sujo, mesmo sem reconhecer o cartão
        # (caso 14/11 18:40: painel disse 'cupom vazio' com várias seleções presas na 1aWin)
        tx = _sem_toast_desfazer(self.texto_cupom() or "")
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
                    const abas = Array.from(r.querySelectorAll('*')).find(e => /^\\s*simples\\s*$/i.test(e.innerText || '') && e.children.length <= 1);
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
            // 1aWin real: o X é o <svg> do cabeçalho do [data-scope="SelectionInfo"]
            for (const card of document.querySelectorAll('[data-scope="CouponCard"]')) {
                const x = card.querySelector('[data-scope="SelectionInfo"] [class*="header" i] > svg, [data-scope="SelectionInfo"] svg');
                if (x && x.getBoundingClientRect().width > 0) { n++; x.setAttribute('data-alvo-x', String(n)); }
            }
            if (n) return n;
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

    def _tem_toast_desfazer(self):
        tx = self.texto_cupom() or ""
        return bool(re.search(r"evento exclu[ií]do", tx, re.I) and re.search(r"\b(cancelar|desfazer|undo)\b", tx, re.I))

    def _esperar_toast_desfazer(self, s=7.0):
        """1aWin: '5 · Evento excluído · Cancelar' some sozinho em ~5s. NUNCA clicamos 'Cancelar' (devolveria
        a seleção). Espera sumir; se não sumir, a lixeira do cupom (apaga tudo, sem devolver nada)."""
        if self.nome != CASA_1W or not self._tem_toast_desfazer():
            return True
        fim = time.time() + s
        while time.time() < fim:
            self.page.wait_for_timeout(250)
            if not self._tem_toast_desfazer():
                return True
        log("1aWin: o aviso 'Evento excluído · Cancelar' não sumiu em %.0fs — uso a lixeira do cupom" % s)
        self._clicar_lixeira_1w()
        self._modal_ok_rapido()
        self.page.wait_for_timeout(500)
        return not self._tem_toast_desfazer()

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
        # recibo de aposta já feita ('Aposta aceita' / 'Ir para o histórico' — 1aWin real 15/11):
        # não é seleção pendente; recarregar a página devolve o cupom vazio
        try:
            if re.search(r"aposta aceita|ir para o hist", self.texto_cupom() or "", re.I):
                log("%s: cupom mostra o recibo de aposta aceita — recarregando para esvaziar" % self.rotulo)
                self.page.reload(wait_until="domcontentloaded")
                self.page.wait_for_timeout(1500)
                if self.esperar_cupom_vazio(4.0):
                    return True
        except Exception:
            pass
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
            if not self._clicar_no_cupom(r"^(×|✕|x|remover|excluir|remove|delete|limpar tudo|remover tudo)$", True):
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
        self._garantir_na_frente()
        tx = self.texto()
        if self.esta_limitada(tx):
            self.limitada = True
            r.update(limitada=True, motivo="casa limitada (Excesso de requisições)")
            return r
        if self.nome == CASA_PS:
            if self.fechar_aviso_indisponivel():
                tx = self.texto()
            if not ps_e_o_jogo(tx, times):  # página pode estar carregando (aba recém-aberta)
                if self._sondar(lambda: ps_e_o_jogo(self.texto(), times), 6):
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
            if not linha_confronto(tx, times):  # página pode estar carregando (aba recém-aberta)
                if self._sondar(lambda: linha_confronto(self.texto(), times), 6):
                    tx = self.texto()
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
        # log 15/11 22:43: o clique normal esperou 2,5s o botão 'parar de piscar' (odd ao vivo) e, nesse
        # tempo, a 1aWin trocou a linha 226.5 -> outra. Ao vivo cada segundo conta: tentativa curta e força.
        try:
            loc.click(timeout=600)
            return True
        except Exception:
            pass
        try:
            loc.click(timeout=1200, force=True)
            return True
        except Exception:
            pass
        try:
            loc.evaluate("e => e.click()")
            return True
        except Exception:
            return False

    def selecao_viva(self, perna):
        """Releitura RÁPIDA antes de montar (sem reenumerar a página): se o botão carimbado ainda existe e já
        mostra OUTRA linha, a seleção sumiu -> False. Sem carimbo ou botão redesenhado: não sei -> True (a
        montagem relê pela identidade). Odd diferente não reprova aqui (a conferência do cupom decide)."""
        mid = perna.get("_mid")
        if not mid:
            return True
        try:
            loc = self.page.locator('[data-mid="%s"]' % mid)
            if not loc.count():
                return True
            if perna.get("_aid"):
                aid = loc.first.evaluate("e => (e.closest('a[id]') || e.querySelector('a[id]') || {id: ''}).id")
                a, b = (aid or "").split("|"), perna["_aid"].split("|")
                return not (len(a) >= 6 and (a[:4] != b[:4] or abs(float(a[5]) - float(b[5])) > 1e-6))
            return _coexiste(perna["linha"], _sem_acento(loc.first.inner_text()), usar_abs=True)
        except Exception:
            return True

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
        # 22/11: o botão tem que estar HABILITADO (sem disabled/aria-disabled/cadeado) antes do clique
        try:
            bloqueado = el.evaluate("""e => { const a = e.closest('a[id]') || e.querySelector('a[id]');
                const lock = a && a.parentElement && a.parentElement.querySelector(':scope > .style-lock:not(.lock-hidden)');
                return !!(e.disabled || e.getAttribute('aria-disabled') === 'true' || lock); }""")
        except Exception:
            bloqueado = False
        if bloqueado:
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
        tx = _sem_toast_desfazer(self.texto_cupom())
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
            if desta and (nova is None or nova < self._piso_odd(perna) - 1e-9):
                return False, "a casa avisou no cupom que a odd mudou (%s)" % linha.strip()[:80]
            if desta and nova is not None and nova < perna["odd"] - 1e-9:
                # operador 18/11: 'até 1.78 pode pegar' — caiu mas ficou no piso: aceita e o par é recalculado
                log("%s: odd caiu %s -> %s (piso %s) — aceita" % (self.rotulo, perna["odd"], nova, self._piso_odd(perna)))
                perna["odd_viva"] = nova
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
        # ordem do operador 15/11: odd que SUBIU é aceita (melhor para nós); que CAIU, só até 0,05.
        # A odd 'subida' só vale lida do lugar exato da odd no cupom (senão placar/stake passariam).
        exata = self._odd_do_cupom()
        if exata is not None:
            if exata < self._piso_odd(perna) - 1e-9:
                return False, "odd do cupom caiu: %s -> %s (abaixo do piso %s)" % (perna["odd"], exata, self._piso_odd(perna))
            if exata < perna["odd"] - TOL_ODD_CUPOM - 1e-9:
                log("%s: odd caiu %s -> %s (piso %s) — aceita" % (self.rotulo, perna["odd"], exata, self._piso_odd(perna)))
                perna["odd_viva"] = exata
            if exata > perna["odd"] + TOL_ODD_CUPOM + 1e-9:
                log("%s: odd subiu %s -> %s (melhor) — aceita" % (self.rotulo, perna["odd"], exata))
        # +1e-9: 2.0 - 1.95 = 0.0500000000000004 no computador — 0,05 exato é DENTRO da tolerância
        elif not any(abs(v - perna["odd"]) <= TOL_ODD_CUPOM + 1e-9 or self._piso_odd(perna) - 1e-9 <= v < perna["odd"]
                     for v in odds):
            return False, "odd do cupom difere de %s (lidas: %s)" % (perna["odd"], ", ".join(map(str, odds[:4])) or "nenhuma")
        if perna["tipo"] == "total":
            quer = r"\b(acima|mais|over)\b" if perna["lado"] == "acima" else r"\b(abaixo|menos|under)\b"
            if not re.search(quer, it, re.I):
                return False, "cupom não mostra o lado '%s'" % perna["lado"]
        elif perna.get("time") and not any(_nome_bate(l, perna["time"]) for l in re.split(r"[\n(]", itens[0]) if l.strip()):
            return False, "cupom não mostra o time %s" % perna["time"]
        return True, ""

    def _piso_odd(self, perna):
        """Até onde a odd pode CAIR no cupom e ainda valer. Operador 18/11: 'até 1.78 pode pegar' (odd_min).
        Sem odd_min no config: a regra antiga (cai no máximo 0,05)."""
        om = self.cfg.get("odd_min")
        if om:
            return float(om)
        return perna["odd"] - TOL_ODD_CUPOM

    def _odd_do_cupom(self):
        """A odd no lugar exato do cupom (PS: '.odds-info .odds'; 1aWin: [data-scope=SelectionCfBadge])."""
        try:
            t = self._eval("""() => { const e = document.querySelector('.BetItemComponent .odds-info .odds, [data-scope="CouponCard"] [data-scope="SelectionCfBadge"]');
                return e ? (e.innerText || '').trim() : ''; }""")
            v = _num(t) if t else None
            return v if v and 1.01 <= v <= 100 else None
        except Exception:
            return None

    def _odd_do_recibo(self, perna):
        """Recibo real da 1aWin (print 16/11): '1.61' no topo (a odd ACEITA) e '2.1' embaixo, em 'Evento' (a da seleção).
        Pega todas as odds soltas do recibo; se alguma está abaixo da calculada, é ela a aceita."""
        try:
            self.page.wait_for_timeout(400)
            tx = self.texto_cupom() or ""
            vals = [v for l in tx.splitlines() if re.fullmatch(r"\s*\d{1,2}[.,]\d{1,3}\s*", l)
                    for v in [_num(l.strip())] if v and 1.01 <= v <= 30]
            if not vals:
                return self._odd_do_cupom()
            planejada = (perna or {}).get("odd") or 0
            menor = min(vals)
            return menor if menor < planejada - 1e-9 else max(vals, key=lambda v: -abs(v - planejada))
        except Exception:
            return None

    def preencher_stake(self, valor, rapido=False):
        """PS: com PONTO e lido de volta (com vírgula a PS lê €500). 1aWin: idem + leitura de volta."""
        if not self.itens_cupom():
            return False, "a seleção sumiu do cupom antes da stake (a casa tirou a linha do ar)"
        # NUNCA o campo de "Código da aposta"/promo (caso 14/11: stake digitada no código, cupom vazio).
        # print 15/11 23:4x: '24.40' foi parar no 'Código da aposta'. O locator nth(i) é PREGUIÇOSO: a 1aWin
        # tirou a linha do ar, o cupom redesenhou e o 1º campo passou a ser o do código. Fixa o ELEMENTO.
        alvo = self._campo_stake()
        if alvo is None:
            return False, "campo de stake não encontrado no cupom"

        def _ainda_e_stake():
            try:
                if not alvo.evaluate("e => e.isConnected"):
                    return False
                rot = " ".join(filter(None, [alvo.get_attribute("placeholder"), alvo.get_attribute("name"),
                                             alvo.get_attribute("aria-label")])).lower()
                return not re.search(r"c[oó]digo|code|promo|cupom|coupon|bonus|b[oô]nus|ganho|win", _sem_acento(rot))
            except Exception:
                return False
        formatos = ["%.2f" % valor] if self.nome == CASA_PS else ["%.2f" % valor, ("%.2f" % valor).replace(".", ",")]
        lido = None
        for f in formatos:
            if not _ainda_e_stake():
                return False, "a seleção sumiu do cupom durante a stake (a casa tirou a linha do ar)"
            try:
                if rapido:  # 22/11: clique curto (o clique comum espera o campo 'parar' e gastava ~1s na PS)
                    try:
                        alvo.click(timeout=400)
                    except Exception:
                        alvo.focus()
                else:
                    alvo.click()
                alvo.fill("")
                alvo.type(f, delay=5 if rapido else 15)
                alvo.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles: true})); e.blur(); }")
                self.page.wait_for_timeout(120 if rapido else 250)
                lido = _num(alvo.input_value())
            except Exception:
                return False, "a seleção sumiu do cupom durante a stake (a casa tirou a linha do ar)"
            if lido is not None and abs(lido - valor) < 0.011:
                return True, ""
        try:
            if _ainda_e_stake():
                alvo.fill("")
        except Exception:
            pass
        return False, "stake lida de volta %s ≠ %s — não segue" % (lido, _fmt(valor))

    def montar(self, perna, times):
        """Monta UMA perna e registra quanto tempo levou (a casa mexe rápido no ao vivo)."""
        t0 = time.time()
        r = self._montar(perna, times)
        # 20/11: a 1aWin tira a seleção do cupom por 1–2 s num lance ('sumiu durante/depois da stake') e devolve o
        # mercado logo depois. Espera um pouco e tenta UMA vez de novo a MESMA seleção (relida pela identidade).
        if not r["ok"] and self.nome == CASA_1W and re.search(r"sumiu|não recebeu a seleção", r.get("motivo") or "") \
                and not perna.get("_retentou") and self.cfg.get("retentar_selecao_sumida", True):
            perna["_retentou"] = True
            log("%s: %s — espero %.1fs e tento a mesma seleção mais uma vez" % (
                self.rotulo, r["motivo"][:60], float(self.cfg.get("retentar_espera_s", 1.5))))
            self.page.wait_for_timeout(int(float(self.cfg.get("retentar_espera_s", 1.5)) * 1000))
            mid = perna.pop("_mid", None)
            try:
                r = self._montar(perna, times)
            finally:
                if mid and "_mid" not in perna:
                    perna["_mid"] = mid
            if r["ok"]:
                log("%s: segunda tentativa montou" % self.rotulo)
        r["segundos"] = round(time.time() - t0, 1)
        log("%s: montagem levou %.1fs" % (self.rotulo, r["segundos"]))
        return r

    def _montar(self, perna, times, cancel=None, prazo=None, rapido=False):
        """Monta UMA perna, no mesmo fôlego da leitura. Retorna {ok, motivo, foto}.
        PREPARAR (22/11): cancel (Event) = a outra casa falhou -> para no próximo ponto de checagem e limpa o cupom;
        prazo (instante) = tempo máximo de preparação; rapido = sem foto, sem esperas longas, no máximo UMA nova busca
        imediata se o botão foi redesenhado."""
        r = {"ok": False, "motivo": "", "foto": ""}
        self._t_montar = time.time()
        clicado = [False]

        def parar():
            """Ponto de checagem: devolve o motivo para parar (ou '')."""
            if cancel is not None and cancel.is_set():
                return "cancelada (a outra casa falhou ou o par mudou)"
            if prazo is not None and time.time() > prazo:
                return "passou do prazo de preparação (%.1fs)" % float(self.cfg.get("tempo_max_preparacao_seg", 3.0))
            return ""

        def abortar(mot):
            r["motivo"] = mot
            r["cancelada"] = True
            if clicado[0] and not self.limpar_cupom(perna):
                r["motivo"] += " — e o cupom NÃO esvaziou: limpe à mão"
                log("‼ %s: cupom NÃO esvaziou depois de cancelar — limpe à mão" % self.rotulo)
            return r
        self._garantir_na_frente()
        no_jogo = False
        if rapido and perna.get("_aid"):
            # PS: o id do botão já carrega o evento — se ele está na página, é o jogo certo (sem ler a página inteira)
            try:
                no_jogo = bool(self._eval("(aid) => !!document.getElementById(aid)", perna["_aid"]))
            except Exception:
                no_jogo = False
        if not no_jogo:
            tx = self.texto()
            if self.esta_limitada(tx):
                r["motivo"] = "casa limitada (Excesso de requisições)"
                return r
            if self.nome == CASA_PS and not ps_e_o_jogo(tx, times):
                r["motivo"] = "PS não está no matchup deste jogo (a página é lista) — montagem abortada"
                return r
            self.fechar_aviso_indisponivel(tx)
        else:
            self.fechar_aviso_indisponivel(rapido=True)
        self.mostrar_boletim()
        self._esperar_toast_desfazer()
        if parar():
            return abortar(parar())
        if self.itens_cupom():  # quarentena
            log("%s: cupom sujo antes de montar — limpando primeiro" % self.rotulo)
            if not self.limpar_cupom():
                r["motivo"] = "cupom sujo que não limpou — não monto por cima (viraria múltipla)"
                return r
        clicou = False
        mid = perna.get("_mid")
        relida = False
        # 22/11: NUNCA clica num elemento de leitura antiga sem revalidar — o carimbo é relocalizado agora e precisa
        # mostrar o MESMO evento/mercado/período/lado/linha (PS: pelo id do botão) e estar visível e habilitado
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
        if not clicou and parar():
            return abortar(parar())
        if not clicou:  # carimbo morto/redesenhado: relê com a mesma enumeração e clica por identidade
            o = self._identidade_uma_vez(perna, times) if rapido else self._achar_por_identidade(perna, times)
            relida = True
            if o:
                try:
                    clicou = self._clicar_mid(o["_mid"], _odd_txt(o["_src"], o["odd"]))
                    if clicou:
                        perna["odd_viva"] = o["odd"]
                        perna["_mid"] = o["_mid"]
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
            if not (cancel is not None and cancel.is_set()):
                r["foto"] = self.foto("falha")
                self.dump("falha")
            return r
        clicado[0] = True
        r["t_clique_sel"] = round(time.time() - self._t_montar, 2)
        log("%s: cliquei a seleção em %.1fs (%s)" % (self.rotulo, time.time() - self._t_montar,
                                                     "relida" if relida else "carimbo"))
        ok, mot = False, "cupom não recebeu a seleção"
        # a PS demora mais para jogar linha alternativa/quarto no boletim (spinner) — caso 14/11 16:05
        fim = time.time() + (7 if self.nome == CASA_PS else 4)
        if prazo is not None:
            fim = min(fim, prazo + 0.05)
        voltas = 0
        while time.time() < fim:
            if parar():
                return abortar(parar())
            self.page.wait_for_timeout(100 if rapido else 150)
            voltas += 1
            # 16/11: aqui relia a página INTEIRA da PS a cada volta só para achar a janela de aviso (lento)
            # a classe da janela real não é garantida: a cada 4 voltas (~0,6s) confere também pela página inteira
            if self.fechar_aviso_indisponivel(rapido=(rapido or voltas % 4 != 0)):
                ok, mot = False, "a PS marcou a seleção como indisponível (aposta mínima maior que a máxima)"
                break
            if voltas == 1 or voltas % 6 == 0:
                self.mostrar_boletim()
            if _RX_EXCLUIDO.search(_sem_toast_desfazer(self.texto_cupom())):
                ok, mot = False, "cupom mostra 'Evento excluído' — mercado encerrado, perna morta"
                break
            if self.itens_cupom():
                ok, mot = self.conferir_cupom(perna)
                # odd que mudou para PIOR já é veredito: não fica esperando o resto dos 7s (log 16/11 08:39)
                if ok or "Evento exclu" in mot or "odd mudou" in mot or "odd do cupom caiu" in mot:
                    break
        if not ok and parar():
            return abortar(parar())
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
            if not (cancel is not None and cancel.is_set()):
                r["foto"] = self.foto("falha")
                self.dump("falha")
            if not self.limpar_cupom(perna):
                r["motivo"] += " — e o cupom NÃO esvaziou: limpe à mão"
                log("‼ %s: cupom NÃO esvaziou depois da falha — limpe à mão" % self.rotulo)
            return r
        r["t_conferida"] = round(time.time() - self._t_montar, 2)
        log("%s: seleção conferida no cupom em %.1fs" % (self.rotulo, time.time() - self._t_montar))
        if parar():
            return abortar(parar())
        valor = perna["stake"] / self.cfg["cambio_brl_eur"] if self.nome == CASA_PS else perna["stake"]
        ok, mot = self.preencher_stake(round(valor, 2), rapido=rapido)
        r["t_stake"] = round(time.time() - self._t_montar, 2)
        log("%s: stake digitada em %.1fs" % (self.rotulo, time.time() - self._t_montar))
        if ok:
            # reconfere DEPOIS da stake: a seleção tem que continuar lá, a mesma
            self.page.wait_for_timeout(120 if rapido else 300)
            if not self.itens_cupom():
                ok, mot = False, "a seleção sumiu do cupom depois da stake (a casa tirou a linha do ar)"
            else:
                ok, mot = self.conferir_cupom(perna)
                if not ok:
                    mot = "após a stake: " + mot
        if not ok:
            r["motivo"] = mot
            if not (cancel is not None and cancel.is_set()):
                r["foto"] = self.foto("falha")
                self.dump("falha")
            if not self.limpar_cupom(perna):
                r["motivo"] += " — e o cupom NÃO esvaziou: limpe à mão"
                log("‼ %s: cupom NÃO esvaziou depois da falha — limpe à mão" % self.rotulo)
            return r
        if parar():
            return abortar(parar())
        # modo real: a foto do cupom montado custava 0,1–0,4s ANTES do clique de apostar (operador 16/11:
        # 'não perder milissegundo'). No real a prova é a foto depois do clique; no teste a foto é o produto.
        # PREPARAR (rapido): foto só depois da barreira (não gasta o prazo da preparação)
        r.update(ok=True, foto=self.foto("montado") if (self.cfg.get("modo_teste", True) and not rapido) else "")
        return r

    def preparar(self, perna, times, cancel=None, prazo_s=None, marcas=None):
        """Estado PREPARAR (22/11): seleção + conferência + stake, SEM confirmar, com cancelamento e prazo.
        O cronômetro (prazo_s) começa AQUI, quando a casa começa a preparar — não conta caça nem fila da casa."""
        t0 = time.time()
        if marcas is not None:
            marcas[self.nome] = t0
        prazo = t0 + float(prazo_s) if prazo_s else None
        log("%s: PREPARAR início %s" % (self.rotulo, _hms_ms(t0)))
        try:
            r = self._montar(perna, times, cancel=cancel, prazo=prazo, rapido=True)
        except Exception as e:
            r = {"ok": False, "motivo": "erro: %s" % str(e).split("\n")[0][:100], "foto": ""}
        r["t_ini"], r["t_fim"] = t0, time.time()
        r["segundos"] = round(r["t_fim"] - t0, 2)
        log("%s: PREPARAR fim %s (%s em %.2fs)" % (self.rotulo, _hms_ms(r["t_fim"]),
                                                  "pronta" if r["ok"] else "falhou: " + r["motivo"][:70], r["segundos"]))
        return r

    def _campo_stake(self):
        loc = self.page.locator(", ".join("%s input" % s.strip() for s in self.sel_cupom.split(",")))
        for i in range(loc.count()):
            el = loc.nth(i)
            if not el.is_visible() or (el.get_attribute("type") or "text") not in ("text", "number", "tel", "decimal"):
                continue
            rotulo = " ".join(filter(None, [el.get_attribute("placeholder"), el.get_attribute("name"),
                                            el.get_attribute("aria-label")])).lower()
            if re.search(r"c[oó]digo|code|promo|cupom|coupon|bonus|b[oô]nus|ganho|win", _sem_acento(rotulo)):
                continue
            return el.element_handle()
        return None

    def ler_cupom(self, perna):
        """BARREIRA FINAL (22/11): lê o cupom AGORA — a seleção é a mesma (evento, mercado/período, lado, linha)?
        Qual a odd mostrada e a stake no campo? Não clica em nada."""
        t0 = time.time()
        try:
            ok, mot = self.conferir_cupom(perna)
            odd = self._odd_do_cupom()
            if odd is None:
                odd = perna.get("odd_viva") or perna["odd"]
            stake = None
            try:
                el = self._campo_stake()
                stake = _num(el.input_value()) if el else None
            except Exception:
                stake = None
        except Exception as e:
            ok, mot, odd, stake = False, "leitura do cupom falhou: %s" % str(e).split("\n")[0][:80], None, None
        return {"ok": ok, "motivo": mot, "odd": odd, "stake": stake, "t0": t0, "t": time.time()}

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

    def _saldo(self):
        """Saldo mostrado pela casa (1aWin: 'BRL 120,00' no cupom/topo; PS: '20.87 EUR')."""
        try:
            t = self._eval(r"""() => {
                const sels = ['.AccountInfoComponent .balance .total', '.balance .total', '[class*="amount" i]', '[class*="balance" i]'];
                for (const q of sels) for (const e of document.querySelectorAll(q)) {
                    const r = e.getBoundingClientRect(); const t = (e.innerText || '').trim();
                    if (r.width > 0 && /\d/.test(t) && t.length < 30) return t;
                }
                return '';
            }""")
            return _num(t) if t else None
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

    # ------------------------------------------------ DISPARO em partes (22/11): armar -> disparar -> apurar
    def armar_disparo(self, perna=None):
        """Marca o ÚNICO botão de apostar e guarda o 'antes' (pendentes, texto, saldo) — sem clicar."""
        b = self._marcar_botao_apostar()
        if b.get("n") != 1:
            return {"ok": False, "motivo": "botão de apostar: achei %d (preciso de exatamente 1) — não cliquei" % b.get("n", 0)}
        if b.get("desabilitado"):
            return {"ok": False, "motivo": "botão de apostar desabilitado — não cliquei"}
        self._disp = {"perna": perna, "antes": self._pendentes(), "texto_antes": self.texto(), "saldo0": self._saldo(),
                      "valor": (perna or {}).get("stake", 0) / (self.cfg["cambio_brl_eur"] if self.nome == CASA_PS else 1),
                      "talvez": False, "t": None}
        self._texto_antes = self._disp["texto_antes"]
        self._logou_novas = False
        self._ok_confirmar = False
        log("%s: saldo antes de apostar = %s" % (self.rotulo, self._disp["saldo0"]))
        return {"ok": True, "motivo": ""}

    def ler_e_armar(self, perna, armar=True):
        """BARREIRA: leitura do cupom (com o instante) e, se pedido, o botão já marcado para o disparo."""
        r = self.ler_cupom(perna)
        if r["ok"] and armar:
            a = self.armar_disparo(perna)
            r["armado"], r["motivo_armar"] = a["ok"], a["motivo"]
        return r

    def _modal_confirmar_visivel(self):
        try:
            return bool(self._eval(r"""() => { const RX = /confirmar apostas?|tem certeza de que deseja arriscar|are you sure/i;
                for (const e of document.querySelectorAll('[role=dialog], [class*="modal" i], [class*="popup" i]')) {
                    const r = e.getBoundingClientRect(); if (r.width > 0 && r.height > 0 && RX.test(e.innerText || '')) return true; }
                return false; }"""))
        except Exception:
            return False

    def disparar(self, perna, odd_ref=None, sincronia=None):
        """DISPARAR: checagem relâmpago (1 seleção, odd não caiu desde a barreira) e UM clique no botão marcado.
        Não espera o resultado. sincronia (threading.Barrier) = modo rápido: as duas casas clicam juntas.
        Devolve {clicou, talvez, t, motivo}."""
        d = getattr(self, "_disp", None)
        if not d or d.get("perna") is not perna:
            a = self.armar_disparo(perna)
            if not a["ok"]:
                if sincronia is not None:
                    sincronia.abort()
                return {"clicou": False, "talvez": False, "t": None, "motivo": a["motivo"]}
            d = self._disp
        try:
            n = len(self._eval(JS_ITENS_CUPOM, [self.sel_cupom, self.sel_item]) or [])
        except Exception:
            n = 1
        odd = self._odd_do_cupom()
        piso = self._piso_odd(perna) if perna else 0
        mot = ""
        if n != 1:
            mot = "o cupom tem %d seleções no instante do disparo" % n
        elif odd is not None and odd_ref is not None and odd < odd_ref - TOL_ODD_CUPOM - 1e-9:
            mot = "a odd caiu entre a barreira e o disparo (%s -> %s)" % (odd_ref, odd)
        elif odd is not None and perna and odd < piso - 1e-9:
            mot = "a odd do cupom ficou abaixo do mínimo no disparo (%s < %s)" % (odd, piso)
        if mot:
            if sincronia is not None:
                sincronia.abort()
            return {"clicou": False, "talvez": False, "t": None, "motivo": mot + " — não cliquei"}
        if sincronia is not None:
            try:
                sincronia.wait(timeout=1.5)
            except threading.BrokenBarrierError:
                return {"clicou": False, "talvez": False, "t": None, "motivo": "a outra casa não ficou pronta para o disparo — não cliquei"}
        clicou, erro, talvez = False, "", False
        t_clique = None
        forcar = False
        for tentativa in range(3):
            try:
                t_clique = time.time()
                # 20/11 19:55:51: o botão da PS 'piscava' ('element is not stable') e o clique nunca saiu nas 3 tentativas.
                # Depois de um erro SEM clique (comprovado pelo registro), a próxima tentativa força o clique.
                self.page.locator('[data-alvo-apostar="1"]').first.click(timeout=1500, no_wait_after=True, force=forcar)
                clicou = True
                break
            except Exception as e:
                erro = str(e)
                # 19/11 21:06:46 (Tatuí x Assis): o clique ACONTECEU, a aposta entrou, e o Playwright ainda devolveu erro
                # de tempo; 'waiting for locator' aparece em TODO erro dele, então não prova nada. Só é 'não cliquei'
                # se o registro do Playwright NÃO mostra a ação de clique. Mostrou ('performing click action'/'click
                # action done') ou não dá para saber -> trato como CLICADO e decido pelas provas da casa.
                # 20/11 16:33/16:43/16:47: 'attempting click action' aparece em TODA tentativa (o Playwright ainda checa se
                # o botão está habilitado/estável); só 'performing click action'/'click action done' provam o clique.
                # Botão DESABILITADO / coberto / instável sem esse registro = o clique NÃO saiu (não é INCERTO).
                clicou_real = re.search(r"performing click action|click action done|dispatching", erro, re.I)
                nao_saiu = re.search(r"not enabled|intercepts pointer events|not stable|not visible|outside of the viewport|"
                                     r"waiting for locator|not attached|detached|resolved to 0 elements", erro, re.I)
                log("%s: o clique em apostar deu erro — registro do navegador: %s" % (
                    self.rotulo, " | ".join(l.strip(" -") for l in erro.splitlines()[1:8] if l.strip())[:300] or erro[:120]))
                if clicou_real or not nao_saiu:
                    talvez = True
                    break
                forcar = bool(re.search(r"not stable|intercepts pointer events|outside of the viewport", erro, re.I))
                b = self._marcar_botao_apostar()
                if b.get("n") != 1 or b.get("desabilitado"):
                    # o botão sumiu/desabilitou: pode ser porque a aposta JÁ foi feita -> provas, nunca 'não apostou'
                    talvez = True
                    break
                log("%s: o botão de apostar foi redesenhado antes do clique — marquei de novo (%d)" % (self.rotulo, tentativa + 1))
        d["talvez"], d["t"] = talvez, t_clique or time.time()
        if not clicou and not talvez:
            self._disp = None
            return {"clicou": False, "talvez": False, "t": None,
                    "motivo": "o clique em apostar não aconteceu (%s)" % erro.split("\n")[0][:80]}
        if talvez:
            log("%s: o clique deu erro, mas pode ter acontecido — decido pelas provas da casa (%s)" % (
                self.rotulo, erro.split("\n")[0][:80]))
        log("%s: cliquei em APOSTAR %s" % (self.rotulo, _hms_ms(d["t"])))
        if self.nome == CASA_PS:
            # PS real (15/11 15:21): 'Confirmar Apostas — Tem certeza…? OK' — sem o OK a aposta não sai: clica JÁ
            fim = time.time() + 0.6
            while time.time() < fim:
                if self._modal_confirmar_visivel():
                    if self._modal_ok():
                        self._ok_confirmar = True
                        log("%s: cliquei OK em 'Confirmar Apostas'" % self.rotulo)
                    break
                self.page.wait_for_timeout(80)
        return {"clicou": True, "talvez": talvez, "t": d["t"], "motivo": ""}

    def evidencia_imediata(self, limite_s):
        """Conservadora: a prova IMEDIATA de que a casa recebeu o clique (aceita / processando / recusada) ou None."""
        fim = time.time() + max(0.0, limite_s)
        linhas0 = set(l.strip() for l in ((getattr(self, "_disp", None) or {}).get("texto_antes") or "").splitlines())
        while True:
            try:
                tx = self.texto_cupom() or ""
                if _RX_SUCESSO.search(tx):
                    return "aceita"
                if re.search(r"sendo processada|aguardando|being processed|pending acceptance", tx, re.I):
                    return "processando"
                if self._eval("""() => !!document.querySelector('aside [aria-busy="true"], [data-scope="Betslip"] [aria-busy="true"]')"""):
                    return "processando"
                novas = "\n".join(l.strip() for l in tx.splitlines() if l.strip() and l.strip() not in linhas0)
                m = _RX_RECUSA.search(novas)
                if m and re.search(r"rejected|declined|not accepted|recusada|rejeitada|n[aã]o aceita", m.group(0), re.I):
                    return "recusada"
            except Exception:
                pass
            if time.time() >= fim:
                return None
            self.page.wait_for_timeout(60)

    def disparar_e_sentir(self, perna, odd_ref, limite_s):
        """Conservadora (1aWin): clica e espera SÓ a prova imediata (não a confirmação definitiva)."""
        d = self.disparar(perna, odd_ref)
        if d["clicou"]:
            d["evidencia"] = self.evidencia_imediata(limite_s)
        return d

    def apurar(self, perna=None):
        """RECONCILIAR: depois do clique, decide por PROVAS: ok=True (aceita), False (recusada — nada apostado) ou
        None (INCERTO). Nunca 'aceita' só porque o cupom sumiu."""
        d = getattr(self, "_disp", None) or {}
        antes, saldo0, valor, talvez = d.get("antes"), d.get("saldo0"), d.get("valor", 0), d.get("talvez", False)
        linhas0 = set(l.strip() for l in (d.get("texto_antes") or "").splitlines())
        novas, fotos_rapidas = [], 0
        viu_processando = False
        t_clique = d.get("t") or time.time()
        fim = t_clique + 25  # ao vivo tem atraso de aceitação
        self._disp = None
        while time.time() < fim:
            self.page.wait_for_timeout(400)
            tx = self.texto()
            # registra TODO texto novo que aparecer (avisos/toasts somem rápido) — diagnóstico
            for l in tx.splitlines():
                l = l.strip()
                if l and l not in linhas0 and l not in novas and len(l) < 160 and not re.fullmatch(r"[\d.,:\s'`´-]+", l):
                    novas.append(l)
            if fotos_rapidas < 2 and time.time() - t_clique > (1.0 if fotos_rapidas == 0 else 3.0):
                self.foto("apos_clique_%d" % (fotos_rapidas + 1))
                fotos_rapidas += 1
            # PS real (15/11 15:21): janela 'Confirmar Apostas — Tem certeza de que deseja arriscar
            # 5.00 EUR…? OK / CANCELAR' — sem o OK a aposta NÃO sai
            if re.search(r"confirmar apostas?|tem certeza de que deseja arriscar|are you sure", tx, re.I) \
                    and not getattr(self, "_ok_confirmar", False):
                if self._modal_ok():
                    self._ok_confirmar = True
                    log("%s: cliquei OK em 'Confirmar Apostas'" % self.rotulo)
            if novas and not getattr(self, "_logou_novas", False) and time.time() - t_clique > 3:
                log("%s: textos novos depois do clique: %s" % (self.rotulo, " | ".join(novas[:12])))
                self._logou_novas = True
            if _RX_SUCESSO.search(tx):
                # log 16/11 09:10: conferido a 2.1, a 1aWin ACEITOU a 1.61 (aceita mudança de odd sozinha). Lê a odd do
                # recibo e avisa — a aposta já existe, não dá para desfazer; quem decide o cash out é o operador.
                return {"ok": True, "motivo": "", "foto": self.foto("confirmado"), "odd_aceita": self._odd_do_recibo(perna)}
            # PS ao vivo: 'Aguarde… sendo processada… Aguardando -> Pendente' — o saldo JÁ desconta e
            # a aposta ainda pode ser rejeitada depois (15/11 15:57). Espera o resultado final.
            if re.search(r"sendo processada|aguardando|being processed|pending acceptance", tx, re.I):
                viu_processando = True
                fim = min(max(fim, time.time() + 10), t_clique + 60)
                continue
            # 1aWin processando (botão com spinner / aria-busy) — o saldo já cai e a aposta ainda pode
            # não entrar (15/11 17:02: saldo 102,53 -> 66,73 e NADA no histórico). Só a mensagem prova.
            try:
                ocupado = self._eval("""() => !!document.querySelector('aside [aria-busy="true"], [data-scope="Betslip"] [aria-busy="true"]')""")
            except Exception:
                ocupado = False
            if ocupado:
                viu_processando = True
                fim = min(max(fim, time.time() + 10), t_clique + 60)
                continue
            depois = self._pendentes()
            if antes is not None and depois is not None and depois > antes \
                    and not re.search(r"sendo processada|aguardando", tx, re.I):
                return {"ok": True, "motivo": "apostas pendentes %d -> %d" % (antes, depois), "foto": self.foto("confirmado")}
            # recusa: só texto NOVO depois do clique (o 'Odds have changed 2.080 to 2.100' que já estava
            # no boletim antes do clique fez a PS ser dada como recusada em 2s — caso 15/11 15:21)
            m = _RX_RECUSA.search("\n".join(novas))
            if m:
                explicita = re.search(r"rejected|declined|not accepted|recusada|rejeitada|n[aã]o aceita", m.group(0), re.I)
                # recusa EXPLÍCITA da casa ('Your bet has been rejected.') = nada apostado, já
                if explicita or (time.time() - t_clique > 2 and self.itens_cupom()):
                    # 'comprovada' = a casa DISSE que recusou ('rejected', 'recusada'...). Só ela autoriza tentar de novo.
                    # 20/11 08:26 (Hapoel x Ironi, print da PS): 'Odds have changed from 1.813 to 1.800' com a seleção
                    # AINDA no boletim e o botão esperando outro clique = a PS NÃO apostou. Também é recusa comprovada
                    # (desde que as apostas pendentes não tenham aumentado).
                    mudou_odd = re.search(r"odds? (have |has )?changed|cota[cç](ao|oes) (mudou|mudaram)", m.group(0), re.I)
                    depois_p = self._pendentes()
                    sem_pendente = antes is None or depois_p is None or depois_p <= antes
                    comp = bool(explicita) or bool(mudou_odd and self.itens_cupom() and sem_pendente)
                    return {"ok": False, "motivo": "a casa recusou: %s" % m.group(0), "foto": self.foto("confirmar"),
                            "comprovada": comp}
        if novas:
            log("%s: textos novos depois do clique: %s" % (self.rotulo, " | ".join(novas[:20])))
        vazio = not self.itens_cupom()
        s1 = self._saldo()
        # log 15/11 20:46 (1aWin): depois do clique apareceu 'BRL 136,15' = 159,64 − 23,49 (a stake), mas
        # o leitor de saldo ainda devolvia 159,64 e o programa disse 'nada foi apostado'. Saldo não PROVA
        # aceite (17:02), mas um saldo novo descontando a stake também não prova o contrário: INCERTO.
        desconto_visto = None
        if saldo0 is not None and valor:
            for l in novas:
                if not re.search(r"BRL|R\$|EUR|€", l):
                    continue
                for v in _numeros(l):
                    if abs((saldo0 - valor) - v) < 0.05:
                        desconto_visto = l
                        break
                if desconto_visto:
                    break
        if desconto_visto:
            # 1aWin conferido pelo operador (20:46): o saldo cai durante o processamento e VOLTA quando a casa
            # não aceita. Se o valor descontado ainda está na página agora, não sei: INCERTO. Se voltou, nada entrou.
            ainda = any(abs((saldo0 - valor) - v) < 0.05
                        for l in self.texto().splitlines() if re.search(r"BRL|R\$|EUR|€", l) for v in _numeros(l))
            if not ainda:
                log("%s: o saldo caiu durante o processamento ('%s') e voltou para %s — a casa não aceitou" % (
                    self.rotulo, desconto_visto, _br(saldo0)))
                # 15/11 20:46 conferido pelo operador: caiu e VOLTOU = nada entrou — mesmo com a seleção ainda no cupom
                # (log 20/11 07:47: a 1aWin manteve a seleção e o programa travou tudo como INCERTO à toa)
                if s1 is None or abs(s1 - saldo0) < 0.01:
                    return {"ok": False, "foto": self.foto("confirmar"), "comprovada": True,
                            "motivo": "o saldo caiu durante o processamento e voltou para %s — nada foi apostado" % _br(saldo0)}
                desconto_visto = None
        if desconto_visto:
            self.dump("incerto")
            return {"ok": None, "foto": self.foto("incerto"),
                    "motivo": "INCERTO — sem mensagem de aceita, mas a casa mostrou saldo descontado da stake ('%s'; "
                              "antes %s). Confira em Apostas pendentes/Histórico" % (desconto_visto, _br(saldo0))}
        if not vazio and not viu_processando and not talvez and saldo0 is not None and s1 is not None \
                and abs(saldo0 - s1) < 0.01 and not any(re.search(r"BRL|R\$|EUR|€", l) for l in novas):
            # 20/11 16:42 e 16:47 (#244/#247): nenhuma mensagem, saldo IGUAL, a seleção CONTINUA no cupom e nada entrou em
            # processamento em 25 s = a casa não reagiu ao clique; nada foi apostado (era INCERTO e travava tudo)
            return {"ok": False, "foto": self.foto("confirmar"), "comprovada": True,
                    "motivo": "a casa não reagiu ao clique (sem mensagem, saldo continua %s, seleção ainda no cupom) — "
                              "nada foi apostado" % _br(s1)}
        if vazio and saldo0 is not None and s1 is not None and abs(saldo0 - s1) < 0.01 and not talvez:
            # 1aWin 14/11 23:58: limpou o cupom SEM apostar (histórico vazio, saldo igual)
            return {"ok": False, "foto": self.foto("confirmar"),
                    "motivo": "a casa limpou o cupom sem aceitar (saldo continua %s) — nada foi apostado" % _br(s1)}
        self.dump("incerto")
        return {"ok": None, "foto": self.foto("incerto"),
                "motivo": "INCERTO — sem mensagem de aceita/recusada em 15s (cupom %s). Confira em Apostas pendentes/Histórico" % (
                    "esvaziou" if vazio else "ainda com a seleção") + "; saldo %s -> %s" % (saldo0, s1)}

    def confirmar(self, perna=None):
        """Só no modo real (uma casa sozinha: recuperação de perna órfã, testes). Reconfere a perna, clica UM botão de
        apostar e decide por PROVAS: ok=True (aceita), ok=False (recusada — nada apostado) ou ok=None (INCERTO)."""
        if perna is not None:
            ok, mot = self.conferir_cupom(perna)
            if not ok:
                return {"ok": False, "motivo": "não confirmei — " + mot, "foto": self.foto("confirmar"), "comprovada": True}  # não clicou
        a = self.armar_disparo(perna)
        if not a["ok"]:
            return {"ok": False, "motivo": a["motivo"], "foto": self.foto("confirmar"), "comprovada": True}  # não clicou
        d = self.disparar(perna)
        if not d["clicou"]:
            return {"ok": False, "motivo": d["motivo"], "foto": self.foto("confirmar"), "comprovada": True}  # não clicou
        return self.apurar(perna)

    def recolocar(self, perna, times):
        """Depois de uma recusa: 'Reutilizar seleção' (ou monta de novo) e a stake — SEM confirmar."""
        self._clicar_no_cupom(r"^(reutilizar sele[cç][aã]o|reuse selection)$")
        self.page.wait_for_timeout(800)
        if not self.itens_cupom():
            m = self.montar(perna, times)
            if not m["ok"]:
                return {"ok": False, "motivo": "remontar falhou: " + m["motivo"], "foto": m.get("foto", "")}
        else:
            ok, mot = self.preencher_stake(round(perna["stake"] / self.cfg["cambio_brl_eur"], 2) if self.nome == CASA_PS
                                          else perna["stake"])
            if not ok:
                return {"ok": False, "motivo": mot, "foto": self.foto("confirmar")}
        return {"ok": True, "motivo": "", "foto": ""}

    def reapostar(self, perna, times):
        """Depois de uma recusa: 'Reutilizar seleção' (ou monta de novo) e confirma outra vez.
        A conferência é a mesma: odd que subiu passa, que caiu além de 0,05 não."""
        r = self.recolocar(perna, times)
        if not r["ok"]:
            return r
        return self.confirmar(perna)

    def na_aba(self, chave, url, fn, *args):
        """Roda fn numa ABA SEPARADA (posição aberta), sem mexer na aba principal dos sinais."""
        if not hasattr(self, "abas_pos"):
            self.abas_pos = {}
        pg = self.abas_pos.get(chave)
        if pg is None or pg.is_closed():
            pg = self.ctx.new_page()
            pg.set_default_timeout(15000)
            self.abas_pos[chave] = pg
        principal = self.page
        self.page = pg
        try:
            if url and (not pg.url or pg.url == "about:blank"):
                self.ir(url)
            return fn(*args)
        finally:
            self.page = principal
            try:  # a aba da posição NÃO pode ficar na frente da aba dos sinais (odds congelariam)
                principal.bring_to_front()
            except Exception:
                pass

    def _garantir_na_frente(self):
        """Aba escondida (atrás de outra aba) = o site para de redesenhar e as odds na tela ficam VELHAS.
        Antes de ler ou montar, a aba de trabalho tem que estar na frente."""
        try:
            vis = self.page.evaluate("() => document.visibilityState")
            if vis != "visible":
                self.page.bring_to_front()
                self.page.wait_for_timeout(400)
                log("%s: a aba de trabalho estava escondida (%s) — trouxe para a frente" % (self.rotulo, vis))
        except Exception:
            pass

    def fechar_aba(self, chave):
        pg = getattr(self, "abas_pos", {}).pop(chave, None)
        try:
            if pg and not pg.is_closed():
                pg.close()
        except Exception:
            pass

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
    def interromper_caca(self):
        """Log 20/11 07:12–07:15: 6 sinais em 90 s; a PS continuou caçando 'clara', 'stanford', 'milwaukee' (jogos já
        cancelados) e a caça do jogo NOVO estourou o prazo na fila. Agora: a caça em andamento para no próximo ponto de
        checagem e as caças/leituras velhas que ainda esperavam na fila são descartadas."""
        self.parar_caca.set()
        guardar, descartadas = [], 0
        while True:
            try:
                job = self.fila.get_nowait()
            except queue.Empty:
                break
            if job is not None and getattr(job[0], "__name__", "") in ("cacar", "ler_escada"):
                job[3]["e"] = CacaInterrompida("descartada: sinal novo assumiu")
                job[2].set()
                descartadas += 1
            else:
                guardar.append(job)
        for job in guardar:
            self.fila.put(job)
        if descartadas:
            log("%s: %d caça(s)/leitura(s) do jogo velho descartada(s) da fila" % (self.rotulo, descartadas))

    def _checar_caca(self):
        if getattr(self, "_em_caca", False) and self.parar_caca.is_set():
            raise CacaInterrompida("caça interrompida: sinal novo assumiu")

    def cacar(self, times, liga_dica=""):
        self.parar_caca.clear()  # esta é a caça do sinal ATUAL (as velhas já pararam ou saíram da fila)
        self._em_caca = True     # só a CAÇA é interrompida (leitura de posição/calibração não)
        try:
            if self.nome == CASA_PS:
                return self._cacar_ps(times)
            return self._cacar_1w(times, liga_dica)
        except CacaInterrompida as e:
            return {"ok": False, "interrompida": True, "motivo": str(e)}
        finally:
            self._em_caca = False

    def _sondar(self, teste, segundos):
        fim = time.time() + segundos
        while True:
            self._checar_caca()
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
            self._checar_caca()
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
            # time com SIGLA no nome ('KK SC Derby Podgorica' = 'Studentski Centar', 16/11): a sigla não aparece
            # como pedaço do nome na outra casa — esse time fica sem filtro e o OUTRO time segura a busca
            if p2 and _tem_sigla(times[0]):
                p1 = []
            elif p1 and _tem_sigla(times[1]):
                p2 = []
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
            self._checar_caca()
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
        self.posicoes = h.get("posicoes", [])     # middles abertos sob monitoramento pós-entrada
        self.backlog = []          # cartões de sinal antigo (só montam com clique)
        self.sinais = []           # sinais frescos recebidos (histórico curto)
        self.calculadora = None    # raio-x da última rodada sadia
        self.monitor = ""          # texto de status do monitoramento
        self.reentrada = None      # última avaliação de reentrada (escadinha) para o painel
        self.protecao = None       # jogo protegido (par confirmado), fila de sinais e último motivo de liberação
        self._seq = max([b.get("id", 0) for b in self.bilhetes] + [0])
        self._trava_gravar = threading.Lock()
        self._sujo = threading.Event()

    def salvar(self):
        """Grava o histórico POR FORA (thread), sem segurar quem está apostando (operador 16/11)."""
        self._sujo.set()
        threading.Thread(target=self.salvar_agora, daemon=True, name="gravar").start()

    def salvar_agora(self):
        with self._trava_gravar:
            if not self._sujo.is_set():
                return  # outra gravação já levou a versão nova
            self._sujo.clear()
            with _TRAVA:
                dados = json.loads(json.dumps({"bilhetes": self.bilhetes[-300:], "resultados": self.resultados[-300:],
                                               "posicoes": self.posicoes[-100:]}, default=str))
            _gravar_json(self.arquivo, dados)

    def registrar_bilhete(self, bil, origem):
        with _TRAVA:
            self._seq += 1
            bil.update({"id": self._seq, "origem": origem, "criado": datetime.now().strftime("%d/%m %H:%M:%S"),
                        "criado_ts": time.time(),
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
                "params": {k: self.cfg.get(k) for k in ("janela_min", "custo_max", "perna_ps_eur", "cambio_brl_eur",
                                                        "so_totais", "totais_periodos", "monitor_intervalo_s",
                                                        "pos_monitorar", "pos_modo", "avulsas_permitidas",
                                                        "pos_max_por_cobertura_brl", "pos_max_total_coberturas_brl",
                                                        "pos_perda_max_total_brl", "pos_max_coberturas")},
                "bilhetes": [_sem_privados(b) for b in self.bilhetes[-60:]][::-1],
                "backlog": list(self.backlog),
                "sinais": self.sinais[-30:][::-1],
                "resultados": self.resultados[-30:][::-1],
                "calculadora": calc,
                "monitor": self.monitor,
                "reentrada": json.loads(json.dumps(self.reentrada, default=str)) if self.reentrada else None,
                "protecao": dict(self.protecao) if self.protecao else None,
                "posicoes": [_pos_painel(p) for p in self.posicoes[-20:]][::-1],
                "log": _LOG_MEM[-80:],
            }


def _ts_bilhete(b):
    """Quando o bilhete foi criado (s). Bilhetes antigos só têm o texto 'dd/mm HH:MM:SS'."""
    if b.get("criado_ts"):
        return float(b["criado_ts"])
    try:
        return datetime.strptime("%d/%s" % (datetime.now().year, b.get("criado", "")), "%Y/%d/%m %H:%M:%S").timestamp()
    except Exception:
        return 0.0


def _pos_painel(p):
    out = {k: v for k, v in p.items() if k not in ("candidatos_raw",)}
    out["pernas"] = [{k: v for k, v in x.items() if not k.startswith("_")} for x in p.get("pernas", [])]
    if out.get("pendente"):
        out["pendente"] = {k: v for k, v in out["pendente"].items() if k != "oferta"}
    return out


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
        self.gerente = None         # GerentePosicoes (monitoramento pós-entrada), se ligado
        self._atual = None          # (thread, evento_cancelar, sinal)
        self._trava = threading.Lock()
        self._ultimo_por_id = {}
        self._fila = []             # [(quando, sinal)] sinais que chegaram com um jogo PROTEGIDO (par confirmado)
        self._prot = None           # {"jogo", "pares", "max", "ultima"}: jogo atual protegido
        self._motivo_lib = ""       # por que o último jogo protegido foi liberado
        self.bloqueio = None        # {"estado": INCOMPLETO|INCERTO, "jogo", "confirmada", "falhou", ...}: tudo congelado
        self._vazio = [0, 0]        # [leituras VAZIO_CONFIRMADO seguidas, erros de leitura seguidos] do jogo protegido
        self._retomar = None
        self._tent = ControleTentativas(cfg)  # 22/11: cooldown por assinatura do par + tentativas por minuto no jogo
        self._carregar_oper()

    def _posicao_aberta(self, times, periodo):
        for p in self.estado.posicoes:
            if p.get("aberta") and p.get("periodo") == periodo and _mesmo_jogo(p.get("times") or ["", ""], times) \
                    and bool(p.get("simulada")) == bool(self.cfg.get("modo_teste", True)):
                return p
        return None

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
            if self.bloqueio:
                if origem in ("sinal", "fila"):
                    self._fila = [(t, x) for t, x in self._fila if not _mesmo_jogo(x["times"], sinal["times"])]
                    self._fila.append((time.time(), sinal))
                    log("📥 BLOQUEADO (%s em %s): sinal %s vai para a fila" % (
                        self.bloqueio["estado"], self.bloqueio["jogo"], sinal["jogo"]))
                    self._publicar_protecao()
                else:
                    log("BLOQUEADO (%s em %s): nada começa até resolver no painel" % (self.bloqueio["estado"], self.bloqueio["jogo"]))
                return False
            if origem == "sinal" and self._prot and self._prot.get("perna_solta") and self._atual \
                    and self._atual[0].is_alive() and not self._atual[1].is_set():
                # 24/11 (operador): sinal novo ASSUME (é assim que ele encerra um jogo). Só fica na FILA enquanto há uma
                # PERNA SOLTA sendo coberta — não abandona aposta descoberta.
                self._fila = [(t, x) for t, x in self._fila if not _mesmo_jogo(x["times"], sinal["times"])]
                self._fila.append((time.time(), sinal))
                log("📥 sinal novo na FILA (%s) — %s está protegido (%d/%d pares)" % (
                    sinal["jogo"], self._prot["jogo"], self._prot["pares"], self._prot["max"]))
                self._publicar_protecao()
                return False
            if self._atual:
                self._atual[1].set()
                log("sinal novo assume — monitoramento de %s cancelado" % self._atual[2]["jogo"])
                for c in self.casas.values():
                    if hasattr(c, "interromper_caca"):
                        c.interromper_caca()
            ev = threading.Event()
            th = threading.Thread(target=self._rodar_monitor, args=(sinal, ev, origem), name="monitor", daemon=True)
            self._atual = (th, ev, sinal)
        with _TRAVA:
            self.estado.sinais.append({"jogo": sinal["jogo"], "mercado": sinal.get("mercado", ""), "liga": sinal.get("liga", ""),
                                       "hora": agora(), "origem": origem})
        th.start()
        return True

    def _carregar_oper(self):
        d = _ler_json(ARQ_OPER, {}) or {}
        self.bloqueio = d.get("bloqueio")
        self._fila = [(float(t), x) for t, x in (d.get("fila") or [])]
        self._retomar = d.get("prot")
        if self._retomar:
            self._vazio = list(self._retomar.get("vazio") or [0, 0])

    def _salvar_oper(self):
        try:
            prot = None
            if self._prot:
                prot = {k: v for k, v in self._prot.items()}
                prot["vazio"] = list(self._vazio)
            _gravar_json(ARQ_OPER, {"bloqueio": self.bloqueio, "fila": [[t, x] for t, x in self._fila], "prot": prot})
        except Exception as e:
            log("aviso: não salvei o estado da operação (%s)" % str(e)[:80])

    def bloquear(self, estado_pos, bil, confirmada, falhou):
        """INCOMPLETO / INCERTO: congela TODA aposta nova (todos os jogos), não puxa a fila, alerta no painel."""
        self.bloqueio = {"estado": estado_pos, "jogo": bil.get("jogo"), "bilhete": bil.get("id"), "desde": agora(),
                         "confirmada": confirmada, "falhou": falhou}
        # 23/11 (operador): 'deu INCERTO e ele esqueceu a escadinha/âncora'. Guarda a CARTEIRA do jogo (pares já
        # confirmados, última entrada) junto do bloqueio; ao resolver, o programa VOLTA para este jogo com ela.
        if self._prot and _mesmo_jogo(self._prot.get("sinal", {}).get("times") or bil.get("times") or ["", ""],
                                      bil.get("times") or ["", ""]):
            cart = {k: v for k, v in self._prot.items() if k in ("jogo", "max", "sinal", "bilhetes", "ultima", "linha_ultima")}
        else:
            cart = {"jogo": bil.get("jogo"), "bilhetes": [], "ultima": time.time(), "linha_ultima": None,
                    "max": int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)),
                    "sinal": {"id": "retomada-%s" % bil.get("id"), "jogo": bil.get("jogo", ""), "times": bil.get("times") or [],
                              "liga": bil.get("liga", ""), "mercado": "retomada após bloqueio"}}
        cart["bilhetes"] = list(cart.get("bilhetes") or [])
        self.bloqueio["carteira"] = cart
        log("‼‼ BLOQUEADO — %s em %s | confirmada: %s | falhou/incerta: %s — NADA de aposta nova em jogo nenhum até "
            "resolver (confira o histórico das casas e use os botões do painel)" % (estado_pos, bil.get("jogo"), confirmada, falhou))
        self._publicar_protecao()

    def retomar_perna(self, bid, casa):
        """Operador 20/11 08:26: 'só UMA perna entrou' (conferido no histórico). A perna aceita volta para a carteira, o jogo
        volta a ser monitorado e, a cada rodada, o programa tenta COBRIR essa perna com a outra casa (mesmas regras:
        odd mínima, tabela de custo, perda máxima; nunca repete a perna aceita). Coberta -> par completo -> segue com
        escadinha/âncora normalmente."""
        if self.cfg.get("modo_teste", True):
            log("retomar perna: só faz sentido no modo REAL (no teste nada foi apostado)")
            return False
        bil = next((b for b in self.estado.bilhetes if str(b.get("id")) == str(bid)), None)
        if not bil or casa not in (CASA_1W, CASA_PS) or len(bil.get("pernas") or []) != 2:
            return False
        falta = CASA_PS if casa == CASA_1W else CASA_1W
        with _TRAVA:
            bil["status"] = "exposto"
            bil.setdefault("detalhe", {})
            bil["detalhe"][NOME_CASA[casa]] = "confirmada (conferida pelo operador)"
            bil["detalhe"][NOME_CASA[falta]] = "NÃO entrou (conferido pelo operador) — cobrindo"
            bil["_orfa"] = {"aceita": casa, "falta": falta}
            bil.setdefault("avisos", []).append("operador: só a %s entrou — retomo o jogo para cobrir a perna" % NOME_CASA[casa])
        self.estado.salvar()
        if self.bloqueio:
            self.bloqueio = None
            self._publicar_protecao()
        sinal = {"id": "perna-%s" % bil.get("id"), "jogo": bil.get("jogo", ""), "times": bil.get("times") or [],
                 "liga": bil.get("liga", ""), "mercado": "cobrir perna solta da %s" % NOME_CASA[casa]}
        self._solta_pendente = (bil, falta)
        # enquanto a perna está solta o jogo fica PROTEGIDO: sinal novo vai para a fila (não abandona a perna)
        self._prot = {"jogo": bil.get("jogo", ""), "pares": 0,
                      "max": int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)),
                      "ultima": time.time(), "sinal": sinal, "bilhetes": [], "linha_ultima": None, "perna_solta": True}
        log("🧩 operador: só a %s entrou em %s (%s) — volto ao jogo para cobrir com a %s" % (
            NOME_CASA[casa], bil.get("jogo"), bil["pernas"][0 if casa == CASA_1W else 1]["selecao"], NOME_CASA[falta]))
        return self.novo_sinal(sinal, origem="perna_solta")

    def resolver(self, acao):
        """Saída do BLOQUEIO: 'concluido' (as duas aceitas), 'nao_apostado' (nenhuma aceita), 'liberar' (manual),
        'so_1awin' / 'so_ps' (só uma entrou: cobre a perna e segue no jogo)."""
        b = self.bloqueio
        if not b:
            return False
        cart = b.get("carteira")
        if acao in ("so_1awin", "so_ps"):
            if cart:
                self._retomar = dict(cart, ultima=time.time())
            return self.retomar_perna(b.get("bilhete"), CASA_1W if acao == "so_1awin" else CASA_PS)
        for bil in self.estado.bilhetes:
            if bil.get("id") == b.get("bilhete"):
                with _TRAVA:
                    bil["status"] = {"concluido": "confirmado", "nao_apostado": "falhou"}.get(acao, bil["status"])
                    bil.setdefault("avisos", []).append("%s resolvido pelo operador: %s" % (
                        b["estado"], {"concluido": "as duas pernas aceitas", "nao_apostado": "nenhuma perna aceita",
                                      "liberar": "liberação manual"}[acao]))
        self.estado.salvar()
        self.bloqueio = None
        self._motivo_lib = "%s (%s) resolvido: %s" % (b["jogo"], b["estado"], acao)
        self._publicar_protecao()
        r = dict(cart or {}, ultima=time.time())
        r["bilhetes"] = list(r.get("bilhetes") or [])
        if acao == "concluido" and b.get("bilhete") not in r["bilhetes"]:
            r["bilhetes"].append(b.get("bilhete"))
        if cart and acao in ("concluido", "nao_apostado", "liberar") and r["bilhetes"] and cart.get("sinal", {}).get("times"):
            # o jogo já tinha par(es) confirmado(s): volta para ELE com a carteira (escadinha/âncora continuam)
            self._retomar = r
            log("🔓 bloqueio resolvido (%s) — volto para %s com a carteira (%d par(es)) para seguir escadinha/âncora" % (
                acao, r.get("jogo"), len(r["bilhetes"])))
            self.novo_sinal(r["sinal"], origem="retomada")
            return True
        log("🔓 bloqueio resolvido (%s) — a fila volta a andar" % acao)
        if not (self._atual and self._atual[0].is_alive() and not self._atual[1].is_set()):
            self._puxar_fila()
        return True

    def retomar(self):
        """Depois de reiniciar o programa: bloqueio continua; jogo protegido volta a ser acompanhado; senão, a fila."""
        if self.bloqueio:
            log("‼ o programa reabriu BLOQUEADO (%s em %s) — resolva no painel" % (self.bloqueio["estado"], self.bloqueio["jogo"]))
            self._publicar_protecao()
            return
        r = self._retomar
        lim_ret = float(self.cfg.get("protecao_sem_entrada_min", 0)) or float(self.cfg.get("pos_duracao_max_min", 150))
        if r and r.get("sinal") and time.time() - float(r.get("ultima") or 0) <= lim_ret * 60:
            log("↻ retomo o jogo protegido %s (%d/%d pares, contador de vazio %d)" % (
                r["jogo"], r.get("pares", 0), r.get("max", 4), self._vazio[0]))
            self.novo_sinal(r["sinal"], origem="retomada")
            return
        self._retomar = None
        self._puxar_fila()

    def _publicar_protecao(self):
        self._salvar_oper()
        p = self._prot
        with _TRAVA:
            self.estado.protecao = {
                "jogo": (p or {}).get("jogo"), "pares": (p or {}).get("pares", 0),
                "max": (p or {}).get("max", int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4))),
                "ultima": (p or {}).get("ultima"),
                "fila": [{"jogo": x["jogo"], "chegou": datetime.fromtimestamp(t).strftime("%H:%M:%S")} for t, x in self._fila],
                "liberado": self._motivo_lib, "bloqueio": self.bloqueio,
                "vazio": list(self._vazio), "alerta": getattr(self, "_alerta", "")}

    def _rodar_monitor(self, sinal, ev, origem):
        try:
            self._monitorar(sinal, ev, origem)
        except Exception as e:
            # 20/11 22:12: KeyError 'max' derrubou o monitoramento da perna solta sem aviso no painel
            log("‼ ERRO no monitoramento de %s: %s — o jogo NÃO está sendo acompanhado; me mande o log" % (
                sinal.get("jogo"), str(e)[:120]))
            log(traceback.format_exc()[-800:])
        finally:
            cancelado_por_sinal = ev.is_set()
            if self._prot and self._prot.get("jogo") == sinal["jogo"]:
                self._motivo_lib = "%s liberado: %s" % (sinal["jogo"], self._motivo_lib or "monitoramento encerrado")
                log("🔓 " + self._motivo_lib)
                self._prot = None
            self._publicar_protecao()
            if not cancelado_por_sinal and not self.bloqueio:
                self._puxar_fila()

    def _puxar_fila(self):
        """Jogo liberado: pega o sinal MAIS RECENTE da fila que ainda vale e começa do zero (relê as linhas)."""
        if self.bloqueio:
            return
        with self._trava:
            validade = float(self.cfg.get("fila_validade_min", 20)) * 60
            vivos = [(t, x) for t, x in self._fila if time.time() - t <= validade]
            for t, x in self._fila:
                if time.time() - t > validade:
                    log("⌛ fila: sinal %s expirou (chegou às %s)" % (x["jogo"], datetime.fromtimestamp(t).strftime("%H:%M:%S")))
            self._fila = []
        self._publicar_protecao()
        if not vivos:
            return
        t, sinal = max(vivos, key=lambda tx: tx[0])
        log("📤 fila: começo %s (sinal das %s) — linhas relidas agora, validação completa" % (
            sinal["jogo"], datetime.fromtimestamp(t).strftime("%H:%M:%S")))
        threading.Thread(target=self.novo_sinal, args=(sinal, "fila"), daemon=True).start()

    # ---------------------------------------------- monitoramento sem limite
    def _monitorar(self, sinal, cancel, origem):
        times = sinal["times"]
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        achado = {CASA_1W: False, CASA_PS: False}
        liga_ps = ""
        rodada = 0
        vazias = 0
        ultimo_motivo = None
        entradas = []   # bilhetes ACEITOS neste jogo (real: confirmados; teste: testados = simulados)
        carteira_local = []  # as pernas desses bilhetes (quando não há posição aberta para o jogo)
        janela_padrao = {}   # período -> janela da 1ª entrada aceita (padrão das reentradas)

        def carteira(periodo):
            """Todas as apostas aceitas deste jogo/período: as entradas deste monitoramento (SEMPRE) + as avulsas
            da posição. 17/11 15:48: com a Cobertura desligada a posição parou de receber as reentradas e o mesmo
            par (186/195.5) entrou DUAS vezes — a lista local é a fonte, a posição só soma as avulsas."""
            base = [p for p in carteira_local if p.get("periodo") == periodo]
            pos = self._posicao_aberta(times, periodo)
            if pos:  # junta sem duplicar (a mesma aposta pode estar nas duas listas)
                ja = {(p["casa"], p["lado"], round(p["linha"], 2), round(p["stake"], 2)) for p in base}
                for p in pos["pernas"]:
                    k = (p["casa"], p["lado"], round(p["linha"], 2), round(p["stake"], 2))
                    if k not in ja:
                        base.append(dict(p))
                        ja.add(k)
            return base

        geladeira = {}  # chave da oferta -> até quando fica de fora (falhou agora há pouco)
        ultima = [None, None]  # (quando, linha principal da PS) da última entrada aceita — 'esperar a linha andar'
        avals = []             # avaliações de reentrada da rodada (painel)
        cand_ant = [None]      # candidato da leitura anterior (precisa aparecer em 2 leituras seguidas)
        incompleta = [False]   # uma perna entrou e a outra não: sem novas reentradas neste jogo
        solta = [None, None]  # [perna da 1aWin sem par, bilhete] — 'sempre buscar pares para cobrir' (18/11)
        indisp_ps = [0]  # quantas vezes a PS disse 'mínima maior que a máxima' neste jogo

        def excluir(a, b, jan):
            """Regras obrigatórias (além da odd mínima e da tabela de custo, que o achar_par já aplica).
            Devolve o MOTIVO (texto) quando recusa, ou None."""
            agora_s = time.time()
            if geladeira.get((CASA_PS, "*"), 0) > agora_s or geladeira.get((CASA_PS, "per", b.get("periodo")), 0) > agora_s:
                return "PS em pausa neste jogo (acabou de recusar com 'mínima maior que a máxima')"
            for casa_n, o in ((CASA_1W, a), (CASA_PS, b)):
                if geladeira.get((casa_n, _chave_oferta(o)), 0) > agora_s:
                    return "linha da %s falhou há pouco (geladeira)" % NOME_CASA[casa_n]
            maxe = int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4))
            if len(entradas) >= maxe:
                return "este jogo já tem %d de %d pares confirmados" % (len(entradas), maxe)
            falta = self._tent.cooldown(assinatura_par(times, a, b), agora_s)
            if falta:
                return "o mesmo par falhou há pouco — cooldown de mais %ds (não repito o mesmo par na hora)" % int(falta + 0.99)
            if not entradas:
                return None
            if a["tipo"] != "total":
                return "reentrada só em totais"
            cart = carteira(a["periodo"])
            if not cart:
                return None
            c = _calcular(a, b, self.cfg)
            novas = [dict(a, casa=CASA_1W, stake=c["stake_1w"]), dict(b, casa=CASA_PS, stake=c["stake_ps"])]
            # 21/11: âncora e escadinha passam pelas MESMAS regras; o rótulo só explica (nenhum tipo tem prioridade)
            av = avaliar_reentrada(cart, novas, self.cfg)
            avals.append({"pernas": [{"casa": x["casa"], "lado": x["lado"], "linha": x["linha"], "odd": x["odd"],
                                      "stake": x["stake"]} for x in novas], "custo": c["custo"], **av})
            return None if av["ok"] else av["motivo"]

        def preferir(a, b, c):
            """Escolha entre candidatos JÁ APROVADOS pela qualidade da carteira (ordem do operador 21/11)."""
            if a["tipo"] != "total":
                return ()
            cart = carteira(a["periodo"])
            if not cart:
                return ()
            novas = [dict(a, casa=CASA_1W, stake=c["stake_1w"]), dict(b, casa=CASA_PS, stake=c["stake_ps"])]
            return nota_reentrada(avaliar_reentrada(cart, novas, self.cfg), c["custo"], min(a["odd"], b["odd"]))

        log("▶ %s: %s (mercado da dica: %s)" % ("SINAL" if origem == "sinal" else origem.upper(), sinal["jogo"], sinal.get("mercado") or "—"))
        orfa_ret = None
        if origem == "perna_solta" and getattr(self, "_solta_pendente", None):
            orfa_ret, self._solta_pendente = self._solta_pendente, None
        if origem in ("retomada", "perna_solta") and self._retomar:
            r = self._retomar
            for bid in r.get("bilhetes") or []:
                b = next((x for x in self.estado.bilhetes if x.get("id") == bid), None)
                if b and b.get("status") in ("confirmado", "testado"):
                    carteira_local.extend(pernas_aceitas(b))
                    entradas.append(bid)
            ultima[0], ultima[1] = float(r.get("ultima") or time.time()), r.get("linha_ultima")
            self._prot = dict(r)
            self._prot["pares"] = len(entradas)
            self._prot.setdefault("max", int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)))
            self._prot.setdefault("jogo", sinal["jogo"])
            self._prot.setdefault("sinal", sinal)
            self._prot.setdefault("ultima", time.time())
            if orfa_ret is not None:
                self._prot["perna_solta"] = True
            self._retomar = None
            log("↻ carteira retomada: %d par(es), contador de vazio %d" % (len(entradas), self._vazio[0]))
            self._publicar_protecao()
        # 23/11 (print dos bilhetes #245/#250): depois de 'liberação manual' o mesmo jogo voltou por outro sinal e
        # recomeçou do ZERO — entrou ilha (148 com 147 no prejuízo) e par sem total novo. Agora TODO monitoramento
        # carrega os pares JÁ CONFIRMADOS deste jogo (as regras de reentrada valem sempre, venha de onde vier o sinal).
        idade_max = float(self.cfg.get("pos_duracao_max_min", 150)) * 60
        st_ok = "testado" if self.cfg.get("modo_teste", True) else "confirmado"
        for b in list(self.estado.bilhetes):
            if b.get("id") in entradas or b.get("status") != st_ok or not _mesmo_jogo(b.get("times") or ["", ""], times):
                continue
            if time.time() - _ts_bilhete(b) > idade_max:
                continue
            carteira_local.extend(pernas_aceitas(b))
            entradas.append(b.get("id"))
        if entradas and ultima[0] is None:
            ult_b = max((b for b in self.estado.bilhetes if b.get("id") in entradas), key=_ts_bilhete)
            ultima[0] = _ts_bilhete(ult_b)
            ultima[1] = round(sum(p["linha"] for p in ult_b["pernas"]) / 2 * 2) / 2   # meio do último par (aprox. da linha)
            if not self._prot:
                self._prot = {"jogo": sinal["jogo"], "pares": len(entradas),
                              "max": int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)),
                              "ultima": time.time(), "sinal": sinal, "bilhetes": list(entradas), "linha_ultima": ultima[1]}
                self._publicar_protecao()
        if entradas and origem not in ("retomada", "perna_solta"):
            log("↻ %s: %d par(es) já confirmado(s) neste jogo entram na carteira — reentrada segue as regras "
                "(escadinha/âncora, sem ilha, sem par repetido)" % (sinal["jogo"], len(entradas)))
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
                if solta[0] is not None and n2:
                    if self._cobrir_perna_solta(solta, lt[CASA_PS]["ofertas"], times, sinal, carteira_local, entradas):
                        self.estado.set_monitor("%s: perna solta COBERTA — posição incompleta resolvida; sem novas "
                                                "reentradas neste jogo" % sinal["jogo"])
                        log("■ %s: perna solta coberta — monitoramento encerrado (posição incompleta não reentra)" % sinal["jogo"])
                        return
                    cancel.wait(self.cfg["monitor_intervalo_s"])
                    continue
                if orfa_ret is not None:
                    bo, falta = orfa_ret
                    if not lt.get(falta, {}).get("ofertas"):
                        cancel.wait(self.cfg["monitor_intervalo_s"])
                        continue
                    with _TRAVA_CLIQUES:
                        rec, mot = self._recuperar_orfa(bo, falta, [])
                    if rec == "confirmada":
                        with _TRAVA:
                            bo.pop("_orfa", None)
                        self.estado.atualizar(bo, status="confirmado", detalhe={"1aWin": "confirmada", "PS3838": "confirmada"})
                        if self.gerente:
                            self.gerente.abrir(bo, self._urls(), simulada=False)   # o par coberto entra no painel de posições
                        novas = pernas_aceitas(bo)
                        carteira_local.extend(novas)
                        entradas.append(bo.get("id"))
                        self._prot = {"jogo": sinal["jogo"], "pares": len(entradas),
                                      "max": int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)),
                                      "ultima": time.time(), "sinal": sinal, "bilhetes": list(entradas), "linha_ultima": None}
                        ultima[0] = time.time()
                        ultima[1] = linha_principal(lt[CASA_PS]["ofertas"], "jogo") if lt.get(CASA_PS) else None
                        self._prot["linha_ultima"] = ultima[1]
                        janela_padrao.setdefault(bo["periodo"], bo["janela"])
                        self._publicar_protecao()
                        orfa_ret = None
                        log("✔ %s: perna solta COBERTA — par completo (%s + %s); sigo no jogo com escadinha/âncora" % (
                            sinal["jogo"], bo["pernas"][0]["selecao"], bo["pernas"][1]["selecao"]))
                        self.estado.set_monitor("%s: perna coberta — procurando reentrada" % sinal["jogo"])
                        cancel.wait(self.cfg["monitor_intervalo_s"])
                        continue
                    if rec == "incerto":
                        p1x, p2x = bo["pernas"]
                        self.bloquear("INCERTO", bo, "%s: %s" % (NOME_CASA[CASA_PS if falta == CASA_1W else CASA_1W],
                                                                (p2x if falta == CASA_1W else p1x)["selecao"]),
                                      "%s (incerta, cobertura): %s" % (NOME_CASA[falta], mot[:80]))
                        log("■ %s: cobertura da perna solta INCERTA — bloqueado" % sinal["jogo"])
                        return
                    if rodada % 3 == 1:
                        log("%s: perna solta ainda sem cobertura segura (%s) — tento de novo na próxima rodada" % (
                            sinal["jogo"], (mot or "")[:120]))
                    self.estado.set_monitor("%s: PERNA SOLTA — procurando cobertura na %s" % (sinal["jogo"], NOME_CASA[falta]))
                    cancel.wait(self.cfg["monitor_intervalo_s"])
                    continue
                if self._prot and self._prot.get("jogo") == sinal["jogo"]:
                    # 20/11: liberação do jogo protegido — 15 min sem NOVA dupla confirmada, ou o mercado REALMENTE
                    # encerrado (20 leituras VAZIO_CONFIRMADO nas duas casas; erro/suspenso/carregando não contam)
                    classes = {n: classificar_leitura(lt.get(n)) for n in (CASA_1W, CASA_PS)}
                    antes_v = list(self._vazio)
                    self._vazio[0], self._vazio[1], acao = contar_vazio(classes, self._vazio[0], self._vazio[1], self.cfg)
                    if self._vazio != antes_v:
                        self._publicar_protecao()
                    sem = time.time() - self._prot["ultima"]
                    lim = float(self.cfg.get("protecao_sem_entrada_min", 0)) * 60
                    if lim > 0 and sem > lim:
                        self._motivo_lib = "%d min sem nova entrada confirmada" % int(sem / 60)
                        self.estado.set_monitor("%s: liberado — %s" % (sinal["jogo"], self._motivo_lib))
                        return
                    if acao == "libera":
                        self._motivo_lib = "mercado encerrado (%d leituras VAZIO_CONFIRMADO nas duas casas)" % self._vazio[0]
                        self.estado.set_monitor("%s: liberado — %s" % (sinal["jogo"], self._motivo_lib))
                        return
                    if acao == "alerta_erro":
                        self._alerta = "⚠ %d leituras seguidas com ERRO em %s (%s) — pausado; o jogo NÃO foi liberado" % (
                            self._vazio[1], sinal["jogo"], ", ".join("%s: %s" % (NOME_CASA[n], c) for n, c in classes.items()))
                        log(self._alerta)
                        self._publicar_protecao()
                        cancel.wait(30)
                        continue
                    if getattr(self, "_alerta", "") and self._vazio[1] == 0:
                        self._alerta = ""
                        self._publicar_protecao()
                if tipo == "sadia":
                    raiox = {"jogo": sinal["jogo"]}
                    cfg_par = self.cfg
                    # 17/11 15:48: sem a tabela entraram pares de CUSTO 22% (1aWin a 1.43). A tabela de custo voltou
                    # nas reentradas; só sai com "reentrada_sem_tabela_custo": true no config.json
                    # 21/11: a reentrada usa a MESMA tabela de custo por tamanho de janela da 1ª entrada
                    del avals[:]
                    bil, motivo = achar_par(lt[CASA_1W]["ofertas"], lt[CASA_PS]["ofertas"], sinal, cfg_par, raiox,
                                            excluir=excluir, preferir=preferir if entradas else None)
                    if entradas and self.cfg.get("reentrada_livre", True):
                        # especificação 19/11: espera mínima + movimento mínimo da linha + 2 leituras seguidas
                        ml = linha_principal(lt[CASA_PS]["ofertas"], "jogo")
                        livre, por_que = portao_reentrada(ultima[0], ultima[1], time.time(), ml, self.cfg)
                        chave = None
                        if bil:
                            chave = tuple((p["lado"], round(p["linha"], 2)) for p in bil["pernas"])
                        escolhido = next((v for v in avals if v["ok"] and chave and
                                          tuple((x["lado"], round(x["linha"], 2)) for x in v["pernas"]) == chave), None)
                        self.estado.reentrada = {"jogo": sinal["jogo"], "hora": agora(), "linha_ps": ml,
                                                 "linha_ultima": ultima[1], "portao": por_que or "liberado",
                                                 "janela": janela_continua(carteira(bil["periodo"] if bil else "jogo")),
                                                 "candidatos": sorted(avals, key=lambda v: (not v["ok"], -len(v["novos"])))[:6],
                                                 "escolhido": escolhido}
                        if bil:
                            par_txt = "RECUSADO: %s | janela %s | custo %s%%" % (
                                descricao_par(bil["pernas"][0], bil["pernas"][1]), _br(bil["janela"], 1), _br(bil["custo"]))
                        if bil and not livre:
                            log("%s: %s | motivo: %s (candidata registrada, reavalio depois)" % (sinal["jogo"], par_txt, por_que))
                            cand_ant[0] = chave
                            bil = None
                            motivo = por_que
                        elif bil and cand_ant[0] != chave:
                            mot2 = "aguardando segunda leitura" if cand_ant[0] is None else \
                                "par mudou entre as leituras (não repetiu a mesma combinação na segunda leitura)"
                            cand_ant[0] = chave  # 1ª leitura (ou mudou): confirma na próxima
                            log("%s: %s | motivo: %s" % (sinal["jogo"], par_txt, mot2))
                            bil = None
                            motivo = mot2
                        elif not bil:
                            cand_ant[0] = None
                            if not livre:
                                motivo = por_que
                    self.estado.set_calc(raiox, rodada)
                    if bil and self._tent.excedeu(sinal["jogo"]):
                        motivo = "limite de %d tentativas por minuto neste jogo (evita repetição sem fim)" % int(
                            self.cfg.get("max_tentativas_por_minuto", 6))
                        log("%s: RECUSADO: %s | janela %s | custo %s%% | motivo: %s" % (
                            sinal["jogo"], descricao_par(bil["pernas"][0], bil["pernas"][1]), _br(bil["janela"], 1),
                            _br(bil["custo"]), motivo))
                        bil = None
                        ultimo_motivo = motivo
                    if bil:
                        log("✔ PAR: %s @%s × %s @%s | janela %s | custo %s%%" % (
                            bil["pernas"][0]["selecao"], bil["pernas"][0]["odd"], bil["pernas"][1]["selecao"],
                            bil["pernas"][1]["odd"], _br(bil["janela"], 1), _br(bil["custo"])))
                        self.estado.registrar_bilhete(bil, origem)
                        self.estado.atualizar(bil, status="executando")
                        cart_antes = carteira(bil["periodo"])  # antes: a posição ganha as pernas novas ao confirmar
                        cfg_sub = cfg_par

                        def substituir(nome_f, b0, _cfg=cfg_sub):
                            """Relê a casa que falhou e procura, com as MESMAS regras, o melhor par com a perna montada."""
                            cc = self.casas[nome_f]
                            lf = cc.executar(cc.ler_escada, times, timeout=60)
                            if not lf.get("ok") or not lf.get("ofertas"):
                                return None
                            idx_ok = 1 if nome_f == CASA_1W else 0
                            ok_of = {k: v for k, v in b0["pernas"][idx_ok].items() if k not in ("stake", "selecao", "casa")}
                            e1 = lf["ofertas"] if nome_f == CASA_1W else [ok_of]
                            e2 = [ok_of] if nome_f == CASA_1W else lf["ofertas"]
                            novo, _m = achar_par(e1, e2, sinal, _cfg, None, excluir=excluir,
                                                 preferir=preferir if entradas else None)
                            if novo and any(abs(novo["pernas"][i]["linha"] - b0["pernas"][i]["linha"]) < 1e-9 and
                                            novo["pernas"][i]["lado"] == b0["pernas"][i]["lado"] for i in (1 - idx_ok,)):
                                return None  # a mesma linha que acabou de cair
                            return novo

                        bil["_reentrada"] = bool(entradas)
                        ass0 = assinatura_par(times, bil["pernas"][0], bil["pernas"][1])
                        self._tent.registrar(sinal["jogo"])
                        st = self.executar_bilhete(bil, substituir=substituir, carteira=cart_antes if entradas else None)
                        if st == "falhou":
                            # 22/11: o par que falhou (e a troca, se houve) fica em cooldown — sem ciclo infinito no mesmo par
                            self._tent.falhou(ass0)
                            self._tent.falhou(assinatura_par(times, bil["pernas"][0], bil["pernas"][1]))
                        if st in ("testado", "confirmado"):
                            novas = pernas_aceitas(bil)
                            if bil["tipo"] == "total":
                                av = avaliar_nova_entrada(cart_antes, novas, self.cfg)
                                with _TRAVA:
                                    bil["carteira"] = {k: av[k] for k in ("novos_totais", "cobertura", "pior", "piorou_em", "tres_ou_mais")}
                                self.estado.salvar()
                                log("🧮 carteira %s (%s): %d aposta(s) | lucro em %s | 3+ greens em %s | pior caso R$ %s%s" % (
                                    sinal["jogo"], NOME_PERIODO.get(bil["periodo"], bil["periodo"]), len(cart_antes) + 2,
                                    _txt_totais(av["cobertura"]), _txt_totais(av["tres_ou_mais"]), _br(av["pior"]),
                                    (" | ⚠ esta entrada AUMENTOU a perda em %s" % _txt_totais(av["piorou_em"])) if av["piorou_em"] else ""))
                            for x in novas:
                                x["par"] = bil.get("id")
                            carteira_local.extend(novas)
                            entradas.append(bil.get("id"))
                            self._prot = {"jogo": sinal["jogo"], "pares": len(entradas),
                                          "max": int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)),
                                          "ultima": time.time(), "sinal": sinal, "bilhetes": list(entradas),
                                          "linha_ultima": ultima[1]}
                            self._motivo_lib = ""
                            self._publicar_protecao()
                            cand_ant[0] = None
                            ultima[0], ultima[1] = time.time(), linha_principal(lt[CASA_PS]["ofertas"], "jogo")
                            janela_padrao.setdefault(bil["periodo"], bil["janela"])
                            maxe = int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 1))
                            # 18/11: 'e assim por diante até bater 4 linhas' — o limite de 4 entradas por jogo voltou
                            if not self.cfg.get("reentrar_mesmo_jogo") or len(entradas) >= maxe:
                                self._motivo_lib = "completou %d/%d pares" % (len(entradas), maxe)
                                self.estado.set_monitor("%s: %s — monitoramento encerrado (%d entrada(s))" % (
                                    sinal["jogo"], st, len(entradas)))
                                log("■ %s: rodada %s — monitoramento encerrado (%d entrada(s) neste jogo)" % (
                                    sinal["jogo"], st, len(entradas)))
                                return
                            log("➕ %s: entrada %d de %d aceita — continuo no jogo procurando duplo duplo ou sequência "
                                "(nunca o mesmo par)" % (sinal["jogo"], len(entradas), maxe))
                            self.estado.set_monitor("%s: %d entrada(s) — procurando reentrada" % (sinal["jogo"], len(entradas)))
                            ultimo_motivo = None
                            cancel.wait(self.cfg["monitor_intervalo_s"])
                            continue
                        orfa = bil.get("_orfa") or {"aceita": CASA_1W, "falta": CASA_PS}
                        pa = bil["pernas"][0 if orfa["aceita"] == CASA_1W else 1]
                        pf = bil["pernas"][0 if orfa["falta"] == CASA_1W else 1]
                        if st == "exposto":
                            incompleta[0] = True
                            with _TRAVA:
                                bil.setdefault("avisos", []).append(
                                    "‼ PERNA ÓRFÃ (POSIÇÃO INCOMPLETA) — aceita: %s (%s); falhou: %s (%s). Sem novas entradas" % (
                                        pa["selecao"], NOME_CASA[orfa["aceita"]], pf["selecao"], NOME_CASA[orfa["falta"]]))
                            self.estado.salvar()
                            log("‼ %s: PERNA ÓRFÃ — ACEITA %s na %s; falhou %s na %s. Novas entradas CONGELADAS" % (
                                sinal["jogo"], pa["selecao"], NOME_CASA[orfa["aceita"]], pf["selecao"], NOME_CASA[orfa["falta"]]))
                        if st == "exposto" and self.cfg.get("cobrir_orfa_automatico", True):
                            # 24/11 (operador): 'só foi em 1 casa, já pega e corrige automático'. Uma casa ACEITOU e a outra
                            # RECUSOU (comprovado): em vez de bloquear e esperar o painel, segue no jogo e tenta cobrir a
                            # perna a CADA rodada (mesmas regras), igual ao botão 'Só a X entrou'. INCERTO continua bloqueando.
                            orfa_ret = (bil, orfa["falta"])
                            if not self._prot or self._prot.get("jogo") != sinal["jogo"]:
                                self._prot = {"jogo": sinal["jogo"], "pares": len(entradas),
                                              "max": int(self.cfg.get("max_pares_por_jogo") or self.cfg.get("max_entradas_por_jogo", 4)),
                                              "ultima": time.time(), "sinal": sinal, "bilhetes": list(entradas),
                                              "linha_ultima": ultima[1]}
                            self._prot["ultima"] = time.time()
                            self._prot["perna_solta"] = True
                            self._publicar_protecao()
                            log("🧩 %s: só a %s entrou (%s) — sigo no jogo e tento COBRIR a perna na %s a cada rodada, "
                                "sozinho (sinais novos ficam na fila)" % (sinal["jogo"], NOME_CASA[orfa["aceita"]], pa["selecao"],
                                                                         NOME_CASA[orfa["falta"]]))
                            self.estado.set_monitor("%s: PERNA SOLTA — procurando cobertura na %s" % (
                                sinal["jogo"], NOME_CASA[orfa["falta"]]))
                            cancel.wait(self.cfg["monitor_intervalo_s"])
                            continue
                        if st == "exposto" and orfa["aceita"] == CASA_1W and self.cfg.get("cobrir_perna_solta", True):
                            # 18/11, operador: 'sempre buscar pares para cobrir' — a 1aWin entrou e a PS não: NÃO para;
                            # a cada rodada procura na PS uma linha do lado oposto que forme par com a perna solta
                            solta[0] = dict(bil["pernas"][0], odd=bil["pernas"][0].get("odd_aceita") or bil["pernas"][0]["odd"])
                            solta[1] = bil
                            log("‼ %s: perna SOLTA na 1aWin (%s) — vou procurar na PS, a cada rodada, o par para cobrir" % (
                                sinal["jogo"], bil["pernas"][0]["selecao"]))
                            self.estado.set_monitor("%s: PERNA SOLTA na 1aWin — procurando o par na PS" % sinal["jogo"])
                            cancel.wait(self.cfg["monitor_intervalo_s"])
                            continue
                        if st in ("incerto", "exposto") and not (st == "exposto" and solta[0] is not None):
                            det = bil.get("detalhe") or {}
                            if st == "exposto":
                                self.bloquear("INCOMPLETO", bil, "%s: %s" % (NOME_CASA[orfa["aceita"]], pa["selecao"]),
                                              "%s: %s" % (NOME_CASA[orfa["falta"]], pf["selecao"]))
                            else:
                                conf = [n for n in (CASA_1W, CASA_PS) if det.get(NOME_CASA[n]) == "confirmada"]
                                txt = {n: "%s%s: %s" % (NOME_CASA[n], "" if n in conf else (
                                    " NÃO confirmada" if "NÃO confirmada" in (det.get(NOME_CASA[n]) or "") else " (incerta)"),
                                    bil["pernas"][0 if n == CASA_1W else 1]["selecao"]) for n in (CASA_1W, CASA_PS)}
                                self.bloquear("INCERTO", bil, " · ".join(txt[n] for n in conf) or "nenhuma comprovada",
                                              " · ".join(txt[n] for n in (CASA_1W, CASA_PS) if n not in conf))
                            self._motivo_lib = "BLOQUEADO (%s) — a fila fica parada" % self.bloqueio["estado"]
                        if st in ("incerto", "exposto"):
                            if st == "exposto":
                                st = "POSIÇÃO INCOMPLETA"
                            self.estado.set_monitor("%s: %s — monitoramento encerrado" % (sinal["jogo"], st))
                            log("■ %s: rodada %s — monitoramento encerrado" % (sinal["jogo"], st))
                            return
                        for (casa_n, perna) in ((CASA_1W, bil["pernas"][0]), (CASA_PS, bil["pernas"][1])):
                            mot_f = (bil.get("_falhas") or {}).get(casa_n)
                            seg = _tempo_geladeira(mot_f)
                            if seg:
                                geladeira[(casa_n, _chave_oferta(perna))] = time.time() + seg
                                log("🧊 %s: %s fica %ds de fora (%s)" % (
                                    NOME_CASA[casa_n], perna["selecao"], seg, mot_f[:70]))
                        if "mínima maior que a máxima" in ((bil.get("_falhas") or {}).get(CASA_PS) or ""):
                            # log 15/11 20:50–21:01: a PS suspende o jogo inteiro por instantes (lance, revisão,
                            # lance livre) e responde 'mínima maior que a máxima' em QUALQUER linha; às 21:05 já
                            # aceitava de novo. Então a PS inteira descansa um pouco neste jogo, sem largar o jogo.
                            seg_ps = int(self.cfg.get("pausa_ps_indisponivel_s", 45))
                            geladeira[(CASA_PS, "*")] = time.time() + seg_ps
                            # print 15/11 21:0x: com o 2º quarto rolando (Q2 3') a PS mostrava as linhas do 2º quarto
                            # mas o mercado do quarto estava no cadeado — o PERÍODO inteiro fica de fora mais tempo
                            per_ps = bil["pernas"][1].get("periodo")
                            seg_per = int(self.cfg.get("pausa_ps_periodo_indisponivel_s", 180))
                            geladeira[(CASA_PS, "per", per_ps)] = time.time() + seg_per
                            log("⏳ PS suspendeu as apostas deste jogo por instantes ('mínima maior que a máxima') — "
                                "espero %ds antes de tentar qualquer linha da PS de novo (e %ds neste período)" % (
                                    seg_ps, int(self.cfg.get("pausa_ps_periodo_indisponivel_s", 180))))
                            indisp_ps[0] += 1
                            # 17/11 00:46 (Denver x Phoenix): a PS já tinha uma aposta SUA neste jogo e recusou a
                            # reentrada com 'mínima maior que a máxima' — é o teto da PS por jogo/mercado para a conta
                            # (o máximo que sobra fica abaixo dos €5 mínimos). Insistir não adianta: PS fora DESTE jogo.
                            lim_ind = int(self.cfg.get("max_indisponivel_ps_com_entrada", 2))
                            if entradas and indisp_ps[0] >= lim_ind:
                                geladeira[(CASA_PS, "*")] = time.time() + 12 * 3600
                                log("■ %s: a PS recusou %d vezes ('mínima maior que a máxima') e você JÁ tem aposta na PS "
                                    "neste jogo — é o limite da PS para a sua conta neste jogo. Sem mais reentradas "
                                    "com a PS aqui; mande outro sinal" % (sinal["jogo"], indisp_ps[0]))
                                self.estado.set_monitor("%s: %d entrada(s) — a PS não aceita mais apostas suas neste "
                                                        "jogo (limite da PS)" % (sinal["jogo"], len(entradas)))
                        log("montagem falhou — o monitoramento reassume %s" % sinal["jogo"])
                        if any(_RX_LINHA_MUDOU.search(str(v)) or "desapareceu" in str(v)
                               for v in (bil.get("detalhe") or {}).values()):
                            ultimo_motivo = None
                            continue  # a linha mudou: relê e procura outro par JÁ, sem esperar o intervalo
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

    def _cobrir_perna_solta(self, solta, ofertas_ps, times, sinal, carteira_local, entradas):
        """Perna da 1aWin ficou sem par (a PS recusou). Procura na PS o lado OPOSTO, mesmo mercado/período, numa
        linha que forme par (janela ≥ 0: Over L pede Under ≥ L; Under L pede Over ≤ L), odd ≥ odd_min.
        Preferência: janela ≥ 1 (middle de verdade), depois a odd mais alta. Stake: a perna padrão da PS (€5)."""
        p1, bil = solta
        lado = "abaixo" if p1["lado"] == "acima" else "acima"
        om = float(self.cfg.get("odd_min") or 1.01)
        cands = []
        for o in ofertas_ps or []:
            if o.get("tipo") != p1["tipo"] or o.get("periodo") != p1["periodo"] or o.get("lado") != lado:
                continue
            if o["odd"] < om - 1e-9:
                continue
            jan = (o["linha"] - p1["linha"]) if p1["lado"] == "acima" else (p1["linha"] - o["linha"])
            if jan < -1e-9:
                continue
            cands.append((1 if jan >= float(self.cfg.get("janela_min", 1.0)) - 1e-9 else 0, o["odd"], jan, o))
        if not cands:
            log("perna solta: nenhuma linha na PS forma par agora (%s) — tento na próxima rodada" % p1["selecao"])
            return False
        cands.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        _, _, jan, o = cands[0]
        cps = self.casas[CASA_PS]
        perna = _limpa_oferta(o, CASA_PS, round(self.cfg["perna_ps_eur"] * self.cfg["cambio_brl_eur"], 2))
        log("perna solta: tentando cobrir com %s @%s na PS (janela %s)" % (perna["selecao"], o["odd"], _br(jan, 1)))
        with _TRAVA_CLIQUES:
            m = cps.executar(cps.montar, perna, times, timeout=ESPERA_FASE1_S)
            if not m["ok"]:
                log("perna solta: a PS não montou (%s) — tento na próxima rodada" % m["motivo"])
                return False
            if self.cfg["modo_teste"]:
                cps.executar(cps.desfazer, perna, timeout=30)
                ok = True
            else:
                r = cps.executar(cps.confirmar, perna, timeout=120)
                ok = r["ok"]
                if ok is None:
                    log("‼ perna solta: PS INCERTA ao cobrir — confira 'Apostas pendentes' na PS AGORA")
                    solta[0] = None
                    return False
                if not ok:
                    cps.executar(cps.limpar_cupom, timeout=40)
                    log("perna solta: a PS recusou a cobertura (%s) — tento na próxima rodada" % r["motivo"])
                    return False
        with _TRAVA:
            bil["pernas"][1] = perna
            bil["detalhe"]["PS3838"] = "COBERTA depois: %s @%s" % (perna["selecao"], o["odd"])
            bil["status"] = "confirmado" if not self.cfg["modo_teste"] else "testado"
        self.estado.salvar()
        carteira_local.extend([{"casa": x["casa"], "tipo": x["tipo"], "periodo": x["periodo"], "lado": x.get("lado"),
                                "linha": x["linha"], "odd": x.get("odd_aceita") or x.get("odd_viva") or x["odd"],
                                "stake": x["stake"]} for x in (p1, perna)])
        entradas.append(bil.get("id"))
        log("✔ perna solta COBERTA: %s + %s (janela %s)" % (p1["selecao"], perna["selecao"], _br(jan, 1)))
        solta[0] = solta[1] = None
        return True

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
    # ================================================= EXECUÇÃO DO PAR EM ESTADOS (operador 22/11)
    # PREPARAR -> BARREIRA_FINAL -> DISPARAR -> RECONCILIAR (-> AMBAS_CONFIRMADAS / AMBAS_RECUSADAS / PERNA_ORFA / INCERTO)
    # São DUAS CASAS INDEPENDENTES: não existe operação atômica — uma pode aceitar e a outra recusar. Por isso a
    # reconciliação decide pelas provas de cada casa e a perna órfã congela tudo.
    def _transicao(self, ex, novo, extra=""):
        log("ESTADO: %s → %s%s" % (ex.get("estado") or "INÍCIO", novo, extra))
        ex["estado"] = novo

    def executar_bilhete(self, bil, substituir=None, carteira=None):
        """substituir(casa_que_falhou, bil) -> bilhete novo | None: troca de perna (âncora ou escadinha, sem
        prioridade entre os tipos) quando a casa tira a linha antes dos disparos — o par novo passa por TODAS as regras e
        a barreira final é refeita do zero. carteira = apostas já aceitas neste jogo/período (regras de reentrada)."""
        with _TRAVA_CLIQUES:
            ex = {"estado": None, "subs": 0, "reinicios": 0, "t0": time.time()}
            st = None
            try:
                st = self._executar(bil, substituir, list(carteira or []), ex)
                return st
            finally:
                with _TRAVA:
                    bil["estado_execucao"] = ex.get("estado")
                if st in ("confirmado", "falhou", "testado"):
                    self._limpar_depois()

    def _limpar_depois(self):
        """20/11 20:08: a PS começava a preparação com o cupom SUJO (recibo/seleção da tentativa anterior) e gastava 2–3 s
        limpando dentro do prazo de 3 s. Agora limpa logo depois de cada tentativa, fora do cronômetro. (INCERTO/órfã: o
        cupom fica como está, para o operador conferir.)"""
        ws = []
        for c in self.casas.values():
            if hasattr(c, "limpar_cupom") and hasattr(c, "em_paralelo"):
                try:
                    ws.append(c.em_paralelo(c.limpar_cupom, timeout=40))
                except Exception:
                    pass
        for w in ws:
            try:
                w()
            except Exception:
                pass

    def _preparar_duas(self, bil):
        """PREPARAR: as DUAS casas ao mesmo tempo (uma não espera a outra). A primeira que falhar (ou o prazo) cancela a
        outra na hora — nenhuma tarefa segue com um candidato velho."""
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        p1, p2 = bil["pernas"]
        prazo_s = float(self.cfg.get("tempo_max_preparacao_seg", 3.0))
        cancel = threading.Event()
        t0 = time.time()
        marcas = {}  # instante em que CADA casa começou a preparar (o cronômetro é de cada uma)
        jobs = {CASA_1W: c1.enfileirar(c1.preparar, p1, bil["times"], cancel, prazo_s, marcas),
                CASA_PS: cps.enfileirar(cps.preparar, p2, bil["times"], cancel, prazo_s, marcas)}
        res = {}
        teto = t0 + prazo_s + 40  # uma casa presa numa chamada do navegador
        while len(res) < 2:
            for n, (ev, caixa) in jobs.items():
                if n in res or not ev.is_set():
                    continue
                r = caixa.get("r") or {"ok": False, "motivo": "erro: %s" % str(caixa.get("e")).split("\n")[0][:100], "foto": ""}
                res[n] = r
                if not r["ok"] and not cancel.is_set():
                    cancel.set()
                    log("PREPARAR: %s falhou (%s) — cancelo a tarefa da outra casa" % (NOME_CASA[n], (r["motivo"] or "")[:80]))
            if len(res) < 2:
                agora_t = time.time()
                estouro = [n for n in jobs if n not in res and n in marcas and agora_t > marcas[n] + prazo_s + 0.15]
                if not cancel.is_set() and estouro:
                    cancel.set()
                    log("PREPARAR: prazo de %ss estourado na %s — cancelo o que ainda não ficou pronto" % (
                        _br(prazo_s, 1), " e ".join(NOME_CASA[n] for n in estouro)))
                if time.time() > teto:
                    for n in jobs:
                        res.setdefault(n, {"ok": False, "motivo": "a casa não respondeu à preparação", "foto": "", "montada": True})
                    break
                time.sleep(0.02)

        def txt(r):
            seg = r.get("segundos")
            if r["ok"]:
                return "pronta %ss" % _br(seg)
            return "falhou%s (%s)" % ((" %ss" % _br(seg)) if seg is not None else "", (r.get("motivo") or "")[:70])
        fim = max([r.get("t_fim") or time.time() for r in res.values()])
        ini = min([r.get("t_ini") or t0 for r in res.values()])
        log("PREPARAR: 1aWin %s | PS %s | total %ss (cronômetro só da preparação dos cupons; prazo %ss)" % (
            txt(res[CASA_1W]), txt(res[CASA_PS]), _br(fim - ini), _br(prazo_s, 1)))
        with _TRAVA:
            bil["tempos_preparacao"] = {NOME_CASA[n]: r.get("segundos") for n, r in res.items()}
            bil["tempos_preparacao"]["total"] = round(fim - ini, 2)
        return res

    def _barreira(self, bil, carteira, armar):
        """BARREIRA_FINAL: lê os DOIS cupons ao mesmo tempo (seleção, odd, stake), exige leituras a até
        barreira_janela_ms uma da outra e refaz a conta SÓ com o que os cupons mostram. Mudou durante a barreira ->
        relê UMA vez com as linhas atuais; mudou de novo -> aborta."""
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        p1, p2 = bil["pernas"]
        janela_ms = float(self.cfg.get("barreira_janela_ms", 300))
        anterior = None
        for n_leitura in (1, 2):
            ws = {CASA_1W: c1.em_paralelo(c1.ler_e_armar, p1, armar, timeout=20),
                  CASA_PS: cps.em_paralelo(cps.ler_e_armar, p2, armar, timeout=20)}
            L = {}
            for n, w in ws.items():
                try:
                    L[n] = w()
                except Exception as e:
                    L[n] = {"ok": False, "motivo": "leitura falhou: %s" % str(e).split("\n")[0][:80], "odd": None,
                            "stake": None, "t": time.time()}
            dt = abs(L[CASA_1W]["t"] - L[CASA_PS]["t"]) * 1000
            def tx(p, l):
                nome = _nome_perna(p).replace(".", ",") if p["tipo"] == "total" else p["selecao"]
                odd = _br(l["odd"], 3).rstrip("0").rstrip(",") if l.get("odd") is not None else "?"
                stake = _br(l["stake"]) if l.get("stake") is not None else "?"
                return "%s @%s stake %s %s (%s)%s" % (nome, odd, stake, "€" if p["casa"] == CASA_PS else "R$",
                                                      _hms_ms(l["t"]), "" if l["ok"] else " — " + (l.get("motivo") or "")[:60])
            log("BARREIRA leitura %d: 1aWin %s | PS %s | %d ms entre as leituras" % (
                n_leitura, tx(p1, L[CASA_1W]), tx(p2, L[CASA_PS]), dt))
            for n in (CASA_1W, CASA_PS):
                if not L[n]["ok"]:
                    mot = L[n]["motivo"] or "?"
                    linha = bool(_RX_LINHA_MUDOU.search(mot) or re.search(r"seleç|outra seleção|lado|Evento exclu", mot))
                    return {"ok": False, "motivo": "%s: %s" % (NOME_CASA[n], mot), "casa": n, "linha": linha}
            for n, p in ((CASA_1W, p1), (CASA_PS, p2)):
                esperado = round(p["stake"] / self.cfg["cambio_brl_eur"], 2) if n == CASA_PS else round(p["stake"], 2)
                lida = L[n].get("stake")
                if lida is None or abs(lida - esperado) > 0.011:
                    return {"ok": False, "motivo": "%s: stake alterada (digitada %s, o cupom mostra %s)" % (
                        NOME_CASA[n], _fmt(esperado), lida)}
            if armar:
                for n in (CASA_1W, CASA_PS):
                    if not L[n].get("armado"):
                        return {"ok": False, "motivo": "%s: %s" % (NOME_CASA[n], L[n].get("motivo_armar") or "botão de apostar não ficou pronto")}
            o1, o2 = L[CASA_1W]["odd"], L[CASA_PS]["odd"]
            if n_leitura == 1:
                mudou = []
                if dt > janela_ms:
                    mudou.append("leituras a %d ms uma da outra (máximo %d)" % (dt, janela_ms))
                for p, o in ((p1, o1), (p2, o2)):
                    if abs(o - p["odd"]) > 1e-9:
                        mudou.append("%s %s -> %s" % (NOME_CASA[p["casa"]], p["odd"], o))
                if mudou:
                    log("BARREIRA: os dados mudaram (%s) — reinicio a barreira UMA vez com as linhas atuais" % "; ".join(mudou))
                    anterior = (o1, o2)
                    continue
            else:
                if dt > janela_ms or (o1, o2) != anterior:
                    return {"ok": False, "instavel": True,
                            "motivo": "os dados mudaram de novo durante a barreira (odds %s × %s -> %s × %s; %d ms entre as leituras)" % (
                                anterior[0], anterior[1], o1, o2, dt)}
            ok, mot, custo, pior = validar_par_vivo(bil, o1, o2, self.cfg, carteira)
            if not ok:
                return {"ok": False, "motivo": mot, "custo": custo}
            with _TRAVA:
                p1["odd_viva"], p2["odd_viva"] = o1, o2
                bil["custo_vivo"] = round(custo, 2)
            log("BARREIRA OK: %s | custo vivo %s%% (antes %s%%; limite %s%%)%s" % (
                descricao_par(dict(p1, odd=o1), dict(p2, odd=o2)), _br(custo), _br(bil["custo"]),
                _br(custo_max_para(bil["janela"], self.cfg)), (" | pior caso R$ %s" % _br(pior)) if pior is not None else ""))
            return {"ok": True, "o1": o1, "o2": o2, "custo": custo}
        return {"ok": False, "motivo": "barreira sem leitura válida"}

    def _urls(self):
        urls = {}
        for nome, c in self.casas.items():
            try:
                urls[nome] = c.executar(lambda c=c: c.page.url, timeout=10)
            except Exception:
                urls[nome] = ""
        return urls

    def _falhar(self, bil, res, extra_fotos=None):
        fotos = [r["foto"] for r in res.values() if r.get("foto")] + [f for f in (extra_fotos or []) if f]
        detalhe = {NOME_CASA[n]: ("montou" if r["ok"] else r["motivo"]) for n, r in res.items()}
        for n, r in res.items():
            log("%s: %s" % (NOME_CASA[n], "montou %s" % ("1aWin" if n == CASA_1W else "PS") if r["ok"] else "FALHOU — " + r["motivo"]))
        bil["_falhas"] = {n: r["motivo"] for n, r in res.items() if not r["ok"]}
        self._desfazer(res, bil, fotos, detalhe)
        self.estado.atualizar(bil, status="falhou", detalhe=detalhe, fotos=fotos)
        return "falhou"

    def _executar(self, bil, substituir, carteira, ex):
        c1, cps = self.casas[CASA_1W], self.casas[CASA_PS]
        max_subs = int(self.cfg.get("max_substituicoes_tentativa", 1))
        # ---------------------------------------------------------------- PREPARAR
        self._transicao(ex, "PREPARAR")
        res = self._preparar_duas(bil)
        while not all(r["ok"] for r in res.values()):
            falhas = [n for n, r in res.items() if not r["ok"]]
            if len(falhas) == 1 and substituir and ex["subs"] < max_subs:
                nf = falhas[0]
                if _RX_LINHA_MUDOU.search(res[nf].get("motivo") or "") and not res[nf].get("cancelada"):
                    ex["subs"] += 1
                    if self._ancora_na_montagem(bil, res, nf, substituir):
                        continue
            break
        if not all(r["ok"] for r in res.values()):
            for n, r in res.items():
                if r.get("cancelada") and not r["ok"]:
                    r["motivo"] = "não montada — " + r["motivo"]
            return self._falhar(bil, res)
        # ---------------------------------------------------------------- BARREIRA_FINAL
        armar = not self.cfg["modo_teste"]
        while True:
            self._transicao(ex, "BARREIRA_FINAL")
            b = self._barreira(bil, carteira, armar)
            if b["ok"]:
                break
            nf = b.get("casa")
            if b.get("linha") and nf and substituir and ex["subs"] < max_subs and ex["reinicios"] < 1:
                # a casa tirou/trocou a seleção ANTES dos disparos: troca só essa perna e refaz a barreira do zero
                ex["reinicios"] += 1
                ex["subs"] += 1
                idx = 0 if nf == CASA_1W else 1
                c = self.casas[nf]
                try:
                    c.executar(c.limpar_cupom, bil["pernas"][idx], timeout=30)
                except Exception:
                    pass
                res[nf] = {"ok": False, "motivo": b["motivo"], "foto": ""}
                if self._ancora_na_montagem(bil, res, nf, substituir):
                    continue
                res[nf]["montada"] = True
            log("ABORTADO NA BARREIRA: %s" % b["motivo"])
            for n in res:
                if res[n].get("ok"):
                    res[n].update(ok=False, montada=True)
                # a casa que mudou leva o motivo (e a geladeira); a outra só registra que a barreira abortou
                res[n]["motivo"] = "ABORTADO NA BARREIRA: " + (
                    b["motivo"] if not nf or n == nf else "a %s mudou a seleção antes dos disparos" % NOME_CASA[nf])
            if b.get("instavel"):
                bil["_instavel"] = True
            return self._falhar(bil, res)
        if self.cfg["modo_teste"]:
            urls = self._urls()
            fotos = []
            ws = [c.em_paralelo(c.foto, "montado", timeout=20) for c in (c1, cps)]
            for w in ws:
                try:
                    fotos.append(w())
                except Exception:
                    pass
            detalhe = {"1aWin": "montou", "PS3838": "montou"}
            ok = self._desfazer(res, bil, fotos, detalhe)
            st = "testado" if ok else "falhou"
            self.estado.atualizar(bil, status=st, detalhe=detalhe, fotos=fotos)
            if ok and self.gerente:
                self.gerente.abrir(bil, urls, simulada=True)
            return st
        # ---------------------------------------------------------------- DISPARAR
        p1, p2 = bil["pernas"]
        modo = (self.cfg.get("confirmacao") or "sequencial_processando").lower()
        if modo == "conservadora":  # nome antigo (22/11): era exatamente este comportamento
            modo = "sequencial_processando"
        if modo not in ("sequencial_processando", "sequencial_aceita", "rapida"):
            log("confirmacao '%s' desconhecida — uso 'sequencial_aceita' (o mais seguro)" % modo)
            modo = "sequencial_aceita"
        self._transicao(ex, "DISPARAR", " (modo %s)" % modo)
        disp, final, fotos = {}, {}, []
        if modo == "rapida":
            sinc = threading.Barrier(2)
            w1 = c1.em_paralelo(c1.disparar, p1, b["o1"], sinc, timeout=30)
            w2 = cps.em_paralelo(cps.disparar, p2, b["o2"], sinc, timeout=30)
            for n, w in ((CASA_1W, w1), (CASA_PS, w2)):
                try:
                    disp[n] = w()
                except Exception as e:
                    disp[n] = {"clicou": False, "talvez": True, "t": None, "motivo": "erro no disparo: %s" % str(e)[:80]}
        else:
            lim = float(self.cfg.get("disparo_intervalo_max_ms", 1500)) / 1000.0
            try:
                d1 = c1.executar(c1.disparar_e_sentir, p1, b["o1"], lim, timeout=30)
            except Exception as e:
                d1 = {"clicou": False, "talvez": True, "t": None, "motivo": "erro no disparo: %s" % str(e)[:80]}
            disp[CASA_1W] = d1
            # sequencial_aceita: a PS SÓ dispara com a aceitação DEFINITIVA da 1aWin (apurar) + validação final da PS
            provas = ("aceita", "processando") if modo == "sequencial_processando" else ()
            if (d1["clicou"] or d1["talvez"]) and d1.get("evidencia") in provas:
                log("%s: 1aWin %s em %d ms — disparo a PS JÁ" % (modo,
                    "aceitou" if d1["evidencia"] == "aceita" else "recebeu o clique (processando)",
                    (time.time() - d1["t"]) * 1000 if d1.get("t") else -1))
                disp[CASA_PS] = cps.executar(cps.disparar, p2, b["o2"], timeout=30)
            elif not d1["clicou"] and not d1["talvez"]:
                disp[CASA_PS] = None  # a 1aWin nem clicou: a PS não é disparada
            else:
                # recusa imediata, clique duvidoso ou nenhuma prova no limite: a PS espera a prova DEFINITIVA da 1aWin
                log("%s: sem a prova imediata exigida da 1aWin em %d ms (%s) — a PS espera o resultado definitivo" % (
                    modo, lim * 1000, d1.get("evidencia") or "nenhum sinal"))
                try:
                    final[CASA_1W] = c1.executar(c1.apurar, p1, timeout=120)
                except Exception as e:
                    final[CASA_1W] = {"ok": None, "motivo": "INCERTO — erro ao apurar: %s" % str(e)[:80], "foto": ""}
                if final[CASA_1W]["ok"] is True:
                    disp[CASA_PS], f_ps = self._ps_depois_da_1awin_aceita(bil, carteira, final[CASA_1W])
                    if f_ps:
                        final[CASA_PS] = f_ps
                else:
                    disp[CASA_PS] = None
        t1, t2 = (disp.get(CASA_1W) or {}).get("t"), (disp.get(CASA_PS) or {}).get("t")
        if t1 and t2:
            log("DISPAROS: 1aWin %s | PS %s | diferença %d ms" % (_hms_ms(t1), _hms_ms(t2), abs(t2 - t1) * 1000))
        else:
            log("DISPAROS: 1aWin %s | PS %s" % (_hms_ms(t1) if t1 else "não clicou (%s)" % (disp.get(CASA_1W) or {}).get("motivo", ""),
                                               _hms_ms(t2) if t2 else "NÃO disparada"))
        with _TRAVA:
            bil["disparos"] = {"1aWin": _hms_ms(t1) if t1 else None, "PS3838": _hms_ms(t2) if t2 else None,
                               "diferenca_ms": int(abs(t2 - t1) * 1000) if t1 and t2 else None, "modo": modo}
        # ---------------------------------------------------------------- RECONCILIAR
        self._transicao(ex, "RECONCILIAR")
        esperas = {}
        for n, p in ((CASA_1W, p1), (CASA_PS, p2)):
            if n in final:
                continue
            d = disp.get(n)
            if not d or (not d["clicou"] and not d["talvez"]):
                final[n] = {"ok": False, "nao_disparada": True, "comprovada": True, "foto": "",
                            "motivo": (d or {}).get("motivo") or "NÃO disparada"}
            else:
                c = self.casas[n]
                esperas[n] = c.em_paralelo(c.apurar, p, timeout=150)
        for n, w in esperas.items():
            try:
                final[n] = w()
            except Exception as e:
                final[n] = {"ok": None, "motivo": "INCERTO — erro ao apurar: %s" % str(e)[:80], "foto": ""}
        fotos += [r.get("foto", "") for r in final.values() if r.get("foto")]
        for n, p in ((CASA_1W, p1), (CASA_PS, p2)):
            oa = final[n].get("odd_aceita") if final[n]["ok"] is True else None
            if oa:
                if oa < p["odd"] - TOL_ODD_CUPOM - 1e-9:
                    aviso = "‼ a %s ACEITOU a %s (o par foi calculado a %s) — a casa aceitou a odd que caiu. " \
                            "Confira na casa a opção de aceitar mudanças de cotação (deixe 'só se aumentar')" % (NOME_CASA[n], oa, p["odd"])
                    log("%s: %s" % (NOME_CASA[n], aviso))
                    with _TRAVA:
                        bil.setdefault("avisos", []).append(aviso)
                p["odd_aceita"] = oa  # a carteira usa a odd que a casa ACEITOU
        cls = classificar_reconciliacao(final[CASA_1W]["ok"], final[CASA_PS]["ok"])

        def rot(r):
            if r["ok"] is True:
                return "ACEITA"
            if r["ok"] is None:
                return "INCERTA"
            return "não disparada" if r.get("nao_disparada") else "recusada (%s)" % (r.get("motivo") or "")[:60]
        log("RECONCILIAR: 1aWin %s | PS %s → %s" % (rot(final[CASA_1W]), rot(final[CASA_PS]), cls))
        if cls == "UMA_CONFIRMADA":
            aceita = CASA_1W if final[CASA_1W]["ok"] is True else CASA_PS
            falta = CASA_PS if aceita == CASA_1W else CASA_1W
            self._transicao(ex, "PERNA_ORFA")
            pa = bil["pernas"][0 if aceita == CASA_1W else 1]
            pf = bil["pernas"][0 if falta == CASA_1W else 1]
            log("‼ PERNA_ORFA: ACEITA %s @%s na %s; faltou %s na %s (%s). Novas entradas CONGELADAS; nunca repito a perna "
                "aceita" % (pa["selecao"], pa.get("odd_aceita") or pa.get("odd_viva") or pa["odd"], NOME_CASA[aceita],
                            pf["selecao"], NOME_CASA[falta], (final[falta].get("motivo") or "")[:80]))
            with _TRAVA:
                bil["_orfa"] = {"aceita": aceita, "falta": falta}
            rf = final[falta]
            if rf.get("nao_disparada") or rf.get("comprovada"):
                rec, mot = self._recuperar_orfa(bil, falta, carteira)
            else:
                # processando / sem ID / cupom sumiu / ambíguo: pode ter entrado — repetir seria aposta DUPLICADA
                rec = "incerto"
                mot = "resultado ambíguo na %s (%s) — sem recusa comprovada, NÃO repito" % (
                    NOME_CASA[falta], (rf.get("motivo") or "?")[:90])
                log("‼ PERNA_ORFA: " + mot)
            if rec == "confirmada":
                final[falta] = {"ok": True, "motivo": "", "foto": ""}
                bil.pop("_orfa", None)
                cls = "AMBAS_CONFIRMADAS"
                log("PERNA_ORFA resolvida: %s colocada na %s dentro das regras" % (pf["selecao"], NOME_CASA[falta]))
            elif rec == "incerto":
                final[falta] = {"ok": None, "motivo": "INCERTO — " + mot, "foto": ""}
                cls = "INCERTO"
            else:
                final[falta]["motivo"] = (final[falta].get("motivo") or "") + ("; recuperação: " + mot if mot else "")
        if cls == "AMBAS_CONFIRMADAS":
            self._transicao(ex, "AMBAS_CONFIRMADAS")
            detalhe = {"1aWin": "confirmada", "PS3838": "confirmada"}
            self.estado.atualizar(bil, status="confirmado", detalhe=detalhe, fotos=fotos)
            if self.gerente:
                self.gerente.abrir(bil, self._urls(), simulada=False)
            return "confirmado"

        def det(n, r):
            if r["ok"] is True:
                return "confirmada"
            if r.get("nao_disparada") and (r.get("motivo") or "").startswith("validação final"):
                return "NÃO confirmada (não disparada; cupom limpo) — %s" % r["motivo"]
            if r.get("nao_disparada"):
                return "NÃO confirmada (montada, parada esperando você)" + (" — %s" % r["motivo"] if r.get("motivo") and
                                                                          r["motivo"] != "NÃO disparada" else "")
            if r["ok"] is None:
                return r.get("motivo") or "INCERTO"
            return "não confirmou: " + (r.get("motivo") or "")
        detalhe = {NOME_CASA[n]: det(n, final[n]) for n in (CASA_1W, CASA_PS)}
        if cls == "INCERTO":
            self._transicao(ex, "INCERTO")
            log("‼ INCERTO — não sei se %s entrou. PAREI TUDO (nenhum outro jogo é assumido): confira 'Apostas pendentes'/"
                "Histórico AGORA" % " e ".join(NOME_CASA[n] for n in final if final[n]["ok"] is None))
            self.estado.atualizar(bil, status="INCERTO", detalhe=detalhe, fotos=fotos)
            return "incerto"
        if cls == "UMA_CONFIRMADA":
            self._transicao(ex, "PERNA_ORFA", " — sem recuperação segura: intervenção")
            o = bil.get("_orfa") or {}
            log("‼ ATENÇÃO: %s confirmada e %s não — perna ÓRFÃ, confira as casas AGORA" % (
                NOME_CASA[o.get("aceita", CASA_1W)], NOME_CASA[o.get("falta", CASA_PS)]))
            self.estado.atualizar(bil, status="exposto", detalhe=detalhe, fotos=fotos)
            return "exposto"
        # AMBAS_RECUSADAS: nada apostado — limpa e volta a monitorar
        self._transicao(ex, "AMBAS_RECUSADAS")
        res2 = {n: {"ok": False, "montada": True, "motivo": final[n].get("motivo") or "recusada"} for n in final}
        bil["_falhas"] = {n: final[n].get("motivo") or "recusada" for n in final}
        self._desfazer(res2, bil, fotos, detalhe)
        self.estado.atualizar(bil, status="falhou", detalhe=detalhe, fotos=fotos)
        return "falhou"

    def _candidato_orfa(self, bil, falta, acc, carteira, tentadas):
        """Perna para proteger a perna ACEITA (acc = linha, odd e stake realmente aceitas). Pode ser a mesma linha que
        faltou ou outra linha da casa que faltou; o par (aceita + nova) passa por TODAS as regras com a carteira do jogo
        (odd mínima, tabela de custo, janela, perda máxima de R$ 100). Nunca a perna já aceita. -> (perna, motivo)"""
        c = self.casas[falta]
        idx = 0 if falta == CASA_1W else 1
        orig = bil["pernas"][idx]
        try:
            lf = c.executar(c.ler_escada, bil["times"], timeout=60)
        except Exception as e:
            return None, "não consegui reler a %s (%s)" % (NOME_CASA[falta], str(e)[:60])
        ofs = (lf or {}).get("ofertas") or []
        lado_quero = "abaixo" if acc.get("lado") == "acima" else "acima"
        melhor, motivo = None, "nenhuma linha da %s protege a perna aceita dentro das regras" % NOME_CASA[falta]
        for o in ofs:
            if o.get("tipo") != acc.get("tipo") or o.get("periodo") != acc.get("periodo") or o.get("lado") != lado_quero:
                continue
            chave = (o["lado"], round(o["linha"], 2))
            if chave in tentadas:
                continue
            if falta == CASA_PS:
                st = round(self.cfg["perna_ps_eur"] * self.cfg["cambio_brl_eur"], 2)
            else:
                st = round(acc["stake"] * acc["odd"] / o["odd"], 2)
            nova = _limpa_oferta(o, falta, st)
            p1, p2 = (nova, acc) if falta == CASA_1W else (acc, nova)
            ov, un = (p1, p2) if p1["lado"] == "acima" else (p2, p1)
            jan = _janela_total(ov, un)
            if jan < float(self.cfg.get("janela_min", 1.0)) - 1e-9:
                continue
            b2 = dict(bil, pernas=[p1, p2], janela=jan)
            ok, mot, custo, pior = validar_par_vivo(b2, p1["odd"], p2["odd"], self.cfg, carteira)
            if not ok:
                motivo = mot
                continue
            mesma = abs(o["linha"] - orig["linha"]) < 1e-9 and o["lado"] == orig["lado"]
            nota = (1 if mesma else 0, -custo)
            if melhor is None or nota > melhor[0]:
                melhor = (nota, nova, mesma, jan)
        if not melhor:
            return None, motivo
        _, nova, mesma, jan = melhor
        nova["_mesma"] = mesma
        nova["_janela"] = jan
        return nova, ""

    def _ps_depois_da_1awin_aceita(self, bil, carteira, r1):
        """A 1aWin JÁ ESTÁ ACEITA: a barreira de antes não basta. Validação final EXCLUSIVA da PS, imediatamente antes do
        disparo dela: seleção, linha, odd e stake no cupom; custo e perda recalculados com a aposta REAL da 1aWin (linha,
        odd do recibo, stake). Reprovou -> a PS não dispara: a reconciliação vira PERNA_ORFA, que procura cobertura
        alternativa dentro das regras (ou para e pede você). A 1aWin nunca é repetida. -> (disparo | None, final | None)"""
        cps = self.casas[CASA_PS]
        p1, p2 = bil["pernas"]
        oa = r1.get("odd_aceita")
        if oa:
            p1["odd_aceita"] = oa
        o1 = p1.get("odd_aceita") or p1.get("odd_viva") or p1["odd"]
        try:
            L = cps.executar(cps.ler_cupom, p2, timeout=20)
        except Exception as e:
            L = {"ok": False, "motivo": "leitura falhou: %s" % str(e)[:80], "odd": None, "stake": None}
        motivo = ""
        if not L["ok"]:
            motivo = L["motivo"] or "seleção da PS mudou"
        else:
            esperado = round(p2["stake"] / self.cfg["cambio_brl_eur"], 2)
            if L.get("stake") is None or abs(L["stake"] - esperado) > 0.011:
                motivo = "stake alterada (digitada %s, o cupom mostra %s)" % (_fmt(esperado), L.get("stake"))
            else:
                ok, mot, custo, pior = validar_par_vivo(bil, o1, L["odd"], self.cfg, carteira)
                if not ok:
                    motivo = mot
                else:
                    log("VALIDAÇÃO FINAL DA PS OK (1aWin aceita @%s): %s @%s stake %s € | custo vivo %s%% | pior caso R$ %s" % (
                        _br(o1, 3).rstrip("0").rstrip(","), _nome_perna(p2).replace(".", ",") if p2["tipo"] == "total"
                        else p2["selecao"], _br(L["odd"], 3).rstrip("0").rstrip(","), _br(L["stake"]), _br(custo),
                        _br(pior) if pior is not None else "—"))
                    with _TRAVA:
                        p2["odd_viva"] = L["odd"]
                        bil["custo_vivo"] = round(custo, 2)
                    try:
                        return cps.executar(cps.disparar, p2, L["odd"], timeout=30), None
                    except Exception as e:
                        return {"clicou": False, "talvez": True, "t": None, "motivo": "erro no disparo: %s" % str(e)[:80]}, None
        log("‼ VALIDAÇÃO FINAL DA PS REPROVOU com a 1aWin JÁ ACEITA @%s: %s — a PS NÃO dispara; procuro cobertura "
            "alternativa dentro das regras (a 1aWin nunca é repetida)" % (_br(o1, 3).rstrip("0").rstrip(","), motivo))
        try:
            cps.executar(cps.limpar_cupom, p2, timeout=40)
        except Exception:
            pass
        return None, {"ok": False, "nao_disparada": True, "comprovada": True, "foto": "",
                      "motivo": "validação final da PS reprovou: " + motivo}

    def _recuperar_orfa(self, bil, falta, carteira):
        """PERNA_ORFA (só depois de recusa COMPROVADA da perna que faltou): protege a perna aceita com a MESMA linha ou
        outra linha da casa que faltou, recalculando a carteira com a linha, odd e stake realmente ACEITAS. Respeita
        odd mínima, tabela de custo e perda máxima; nunca repete a perna aceita; resultado ambíguo -> INCERTO (não repete).
        Devolve (confirmada|incerto|recusada|falhou, motivo)."""
        n_max = int(self.cfg.get("orfa_tentativas", 2))
        c = self.casas[falta]
        idx = 0 if falta == CASA_1W else 1
        pa = bil["pernas"][1 - idx]
        acc = dict(pa, odd=pa.get("odd_aceita") or pa.get("odd_viva") or pa["odd"])
        tentadas = set()
        motivo = ""
        for i in range(1, n_max + 1):
            nova, mot = self._candidato_orfa(bil, falta, acc, carteira, tentadas)
            if not nova:
                mot = "; ".join(x for x in (motivo, mot) if x)
                log("PERNA_ORFA: sem proteção segura — %s" % mot)
                return "recusada", mot
            tentadas.add((nova["lado"], round(nova["linha"], 2)))
            log("PERNA_ORFA: tentativa %d/%d — protejo %s @%s (aceita, stake R$ %s) com %s @%s na %s (%s)" % (
                i, n_max, acc["selecao"], acc["odd"], _br(acc["stake"]), nova["selecao"], nova["odd"], NOME_CASA[falta],
                "mesma linha" if nova["_mesma"] else "outra linha"))
            if not (nova["_mesma"] and i == 1):
                # 20/11 20:05:13: a 2ª tentativa achou no boletim a seleção da 1ª (116.5 ≠ 119) — limpa antes de outra seleção
                try:
                    c.executar(c.limpar_cupom, timeout=40)
                except Exception:
                    pass
            try:
                rr = c.executar(c.recolocar if (nova["_mesma"] and i == 1) else c.montar, nova, bil["times"], timeout=ESPERA_FASE1_S)
            except Exception as e:
                rr = {"ok": False, "motivo": "erro: %s" % str(e)[:80]}
            if not rr["ok"]:
                motivo = rr["motivo"]
                log("PERNA_ORFA: não consegui montar (%s)" % motivo[:80])
                c.executar(c.limpar_cupom, nova, timeout=30)
                continue
            L = c.executar(c.ler_cupom, nova, timeout=20)
            p1, p2 = (nova, acc) if falta == CASA_1W else (acc, nova)
            b2 = dict(bil, pernas=[p1, p2], janela=nova["_janela"])
            ok, mot, custo, pior = (False, L["motivo"], None, None) if not L["ok"] else validar_par_vivo(
                b2, L["odd"] if falta == CASA_1W else acc["odd"], acc["odd"] if falta == CASA_1W else L["odd"],
                self.cfg, carteira)
            if not ok:
                motivo = mot
                log("PERNA_ORFA: recuperação RECUSADA pelas regras — %s" % mot)
                c.executar(c.desfazer, nova, timeout=30)
                continue
            r = c.executar(c.confirmar, nova, timeout=150)
            if r["ok"] is True:
                nova["odd_aceita"] = r.get("odd_aceita") or L["odd"]
                with _TRAVA:
                    if not nova["_mesma"]:
                        bil.setdefault("avisos", []).append("PERNA_ORFA protegida com OUTRA linha: %s" % nova["selecao"])
                    bil["pernas"][idx] = {k: v for k, v in nova.items() if not k.startswith("_") or k in ("_mid", "_aid")}
                    bil["janela"] = nova["_janela"]
                    bil["custo_vivo"] = round(custo, 2)
                return "confirmada", ""
            if r["ok"] is None:
                return "incerto", r["motivo"]
            if not r.get("comprovada"):
                return "incerto", "resultado ambíguo na nova tentativa (%s) — não repito" % (r.get("motivo") or "?")[:80]
            motivo = r["motivo"]
            log("PERNA_ORFA: a %s recusou de novo, comprovado (%s)" % (NOME_CASA[falta], motivo[:80]))
        return "falhou", motivo

    def _ancora_na_montagem(self, bil, res, nome_f, substituir):
        """Segura a perna montada e tenta trocar a que caiu (âncora ou escadinha — o substituir escolhe sem prioridade
        de tipo, com TODAS as regras). Prepara a perna nova com o mesmo prazo. Atualiza bil/res. Nunca aposta nada aqui."""
        nome_ok = CASA_PS if nome_f == CASA_1W else CASA_1W
        idx_f, idx_ok = (0, 1) if nome_f == CASA_1W else (1, 0)
        try:
            novo = substituir(nome_f, bil)
        except Exception as e:
            log("âncora na montagem: erro ao procurar troca (%s)" % str(e)[:100])
            return False
        if not novo:
            log("âncora na montagem: %s caiu (%s) e nenhuma outra linha da %s forma par válido com %s" % (
                bil["pernas"][idx_f]["selecao"], (res[nome_f]["motivo"] or "")[:60], NOME_CASA[nome_f], bil["pernas"][idx_ok]["selecao"]))
            return False
        c, c_ok = self.casas[nome_f], self.casas[nome_ok]
        perna_nova, perna_ok = novo["pernas"][idx_f], bil["pernas"][idx_ok]
        log("⚓ âncora na montagem: seguro %s na %s e troco %s por %s @%s na %s" % (
            perna_ok["selecao"], NOME_CASA[nome_ok], bil["pernas"][idx_f]["selecao"], perna_nova["selecao"],
            perna_nova["odd"], NOME_CASA[nome_f]))
        m = c.executar(c.preparar, perna_nova, bil["times"], None, float(self.cfg.get("tempo_max_preparacao_seg", 3.0)),
                       timeout=ESPERA_FASE1_S)
        if not m["ok"]:
            log("âncora na montagem: a troca também não montou (%s)" % m["motivo"])
            return False
        st_ok = novo["pernas"][idx_ok]["stake"]
        if abs(st_ok - perna_ok["stake"]) > 0.009:
            # a stake da perna segura depende da odd da outra (retorno igual): redigita
            valor = st_ok / self.cfg["cambio_brl_eur"] if nome_ok == CASA_PS else st_ok
            ok, mot = c_ok.executar(c_ok.preencher_stake, round(valor, 2), timeout=30)
            if not ok:
                log("âncora na montagem: não consegui ajustar a stake da perna segura (%s) — desfaço a troca" % mot)
                c.executar(c.desfazer, perna_nova, timeout=30)
                return False
            perna_ok["stake"] = st_ok
        with _TRAVA:
            bil["pernas"][idx_f] = perna_nova
            for k in ("janela", "custo", "total", "retorno", "ps_eur"):
                bil[k] = novo[k]
            bil.setdefault("avisos", []).append("⚓ âncora na montagem: %s trocada por %s (a casa mudou a linha)" % (
                NOME_CASA[nome_f], perna_nova["selecao"]))
        res[nome_f] = m
        return True

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


# ---------------------------------------------------------------- MONITORAMENTO PÓS-ENTRADA
class GerentePosicoes:
    """Depois do middle, continua no jogo (aba separada em cada casa) e procura Unders novos
    que AMPLIEM A JANELA da carteira inteira (sem distância mínima fixa — operador 16/11).
    Nunca troca/substitui aposta: só soma cobertura; recalcula tudo a cada rodada."""

    def __init__(self, cfg, estado, casas):
        self.cfg = cfg
        self.estado = estado
        self.casas = casas
        self._trava = threading.Lock()
        self._vazias = {}

    def abrir(self, bil, urls, simulada):
        if bil.get("tipo") != "total":
            return None
        pernas = []
        for p in bil["pernas"]:
            pernas.append({"casa": p["casa"], "tipo": "total", "periodo": p["periodo"], "lado": p["lado"],
                           "linha": p["linha"], "odd": p.get("odd_aceita") or p.get("odd_viva") or p["odd"], "stake": p["stake"],
                           "origem": "entrada", "simulada": simulada, "hora": agora()})
        # reentrada no mesmo jogo/período: as pernas novas entram na MESMA carteira (posição), que é avaliada inteira
        for pos in self.estado.posicoes:
            if pos.get("aberta") and pos.get("periodo") == bil["periodo"] and bool(pos.get("simulada")) == bool(simulada) \
                    and _mesmo_jogo(pos.get("times") or ["", ""], bil["times"]):
                with _TRAVA:
                    for p in pernas:
                        p["origem"] = "reentrada"
                    pos["pernas"].extend(pernas)
                    pos["ultimo_n"] = len(pernas)
                    atualizar_tabela_pos(pos)
                    pos["eventos"].append("%s reentrada: %s" % (agora(), " × ".join(
                        descricao_oferta(x) + " @" + str(x["odd"]) for x in pernas)))
                    for nome, u in (urls or {}).items():
                        pos.setdefault("urls", {}).setdefault(nome, u)
                self.estado.salvar()
                log("📌 posição %d: reentrada somada à carteira (%d apostas)" % (pos["id"], len(pos["pernas"])))
                return pos
        # a posição abre sempre (mostra a tabela da carteira); apostas AVULSAS só com a Cobertura ligada
        with _TRAVA:
            pid = max([x.get("id", 0) for x in self.estado.posicoes] + [0]) + 1
            pos = {"id": pid, "bilhete": bil.get("id"), "jogo": bil["jogo"], "times": bil["times"],
                   "periodo": bil["periodo"], "urls": urls, "pernas": pernas, "aberta": True, "simulada": simulada,
                   "aberta_em": time.time(), "aberta_hora": agora(), "ultima_cob": 0.0, "status": "monitorando",
                   "eventos": ["%s entrada: %s" % (agora(), " × ".join(descricao_oferta(x) + " @" + str(x["odd"]) for x in pernas))]}
            pos["ultimo_n"] = len(pernas)
            atualizar_tabela_pos(pos)
            self.estado.posicoes.append(pos)
        self.estado.salvar()
        log("📌 posição %d aberta (%s%s) — janela %s; procurando apostas que acrescentem totais novos" % (
            pid, bil["jogo"], ", SIMULADA" if simulada else "", pos.get("cobertura_2g") or "—"))
        return pos

    def iniciar(self):
        # log 16/11 09:07: posição SIMULADA (aberta no modo teste às 08:48) foi retomada no modo REAL e ganhou
        # uma cobertura DE VERDADE (R$ 10 no Chiba). Posição simulada nunca atravessa para o modo real.
        if not self.cfg.get("modo_teste", True):
            for pos in [p for p in self.estado.posicoes if p.get("aberta") and p.get("simulada")]:
                with _TRAVA:
                    pos["aberta"] = False
                    pos["status"] = "encerrada: era simulada (modo teste) — nunca vira aposta real"
                    pos["eventos"].append("%s encerrada — simulada não continua no modo real" % agora())
                log("📌 posição %d (%s) era SIMULADA — encerrada: o modo real não mexe nela" % (pos["id"], pos.get("jogo")))
            self.estado.salvar()
        threading.Thread(target=self._laco, name="posicoes", daemon=True).start()
        return self

    def encerrar_velhas(self, horas=None):
        """Fecha posições abertas há mais que o tempo máximo (pos_duracao_max_min) — ou que 'horas', no botão
        Limpar. 19/11: com a cobertura desligada o laço não rodava e posições de ontem ficavam 'monitorando'."""
        lim = (horas * 3600.0) if horas is not None else float(self.cfg.get("pos_duracao_max_min", 150)) * 60
        n = 0
        for pos in [p for p in list(self.estado.posicoes) if p.get("aberta")]:
            if time.time() - float(pos.get("aberta_em") or 0) > lim:
                self.fechar(pos, "jogo antigo (aberta há mais de %d min)" % int(lim / 60))
                n += 1
        return n

    def _laco(self):
        while True:
            try:
                self.encerrar_velhas()
            except Exception:
                pass
            if not self.cfg.get("pos_monitorar", True):  # desligado pelo painel/config
                time.sleep(5)
                continue
            real = not self.cfg.get("modo_teste", True)
            for pos in [p for p in list(self.estado.posicoes) if p.get("aberta") and not (real and p.get("simulada"))]:
                try:
                    self.rodada(pos)
                except Exception as e:
                    log("posição %s: erro na rodada (segue): %s" % (pos.get("id"), str(e).split("\n")[0][:150]))
            time.sleep(float(self.cfg.get("pos_intervalo_s", 15)))

    def fechar(self, pos, motivo):
        with _TRAVA:
            pos["aberta"] = False
            pos["status"] = "encerrada: " + motivo
            pos["eventos"].append("%s encerrada — %s" % (agora(), motivo))
        for c in self.casas.values():
            try:
                c.executar(c.fechar_aba, "pos%d" % pos["id"], timeout=20)
            except Exception:
                pass
        self.estado.salvar()
        log("📌 posição %d encerrada — %s" % (pos["id"], motivo))

    def _ler(self, pos):
        chave = "pos%d" % pos["id"]
        lt = {}
        for nome, c in self.casas.items():
            url = (pos.get("urls") or {}).get(nome)
            if not url:
                continue
            try:
                r = c.executar(c.na_aba, chave, url, c.ler_escada, pos["times"], timeout=90)
                if not r["ok"] and "não é" in (r.get("motivo") or ""):
                    c.executar(c.na_aba, chave, url, c.ir, url, timeout=60)
                    r = c.executar(c.na_aba, chave, url, c.ler_escada, pos["times"], timeout=90)
            except Exception as e:
                r = {"ok": False, "ofertas": [], "motivo": str(e)[:100], "quarto": None}
            lt[nome] = r
        return lt

    def rodada(self, pos):
        cfg = self.cfg
        if time.time() - pos.get("aberta_em", time.time()) > cfg.get("pos_duracao_max_min", 150) * 60:
            return self.fechar(pos, "tempo máximo de monitoramento")
        if pos.get("status") == "PAUSADA":
            return
        lt = self._ler(pos)
        quarto = next((l.get("quarto") for l in lt.values() if l.get("quarto") is not None), None)
        if pos["periodo"] in periodos_encerrados(quarto):
            return self.fechar(pos, "período %s acabou" % NOME_PERIODO.get(pos["periodo"], pos["periodo"]))
        ofertas = {n: [o for o in l.get("ofertas", []) if o.get("tipo") == "total" and o.get("periodo") == pos["periodo"]]
                   for n, l in lt.items()}
        if not any(ofertas.values()):
            self._vazias[pos["id"]] = self._vazias.get(pos["id"], 0) + 1
            if self._vazias[pos["id"]] >= 20:
                return self.fechar(pos, "mercado sumiu (jogo acabou ou foi retirado)")
            return
        self._vazias[pos["id"]] = 0
        modelo = modelo_total(ofertas.get(CASA_PS), pos["periodo"]) or modelo_total(ofertas.get(CASA_1W), pos["periodo"])
        with _TRAVA:
            pos["resumo"] = resumo_posicao(pos["pernas"], modelo)
            pos["atualizada"] = agora()
        if not cfg.get("avulsas_permitidas", False):
            # 18/11, operador: 'a ideia era fazer LINHAS' — nunca aposta de uma perna só. A posição só mostra a
            # carteira; quem amplia a janela são as reentradas EM PAR (1aWin + PS), no monitoramento do sinal.
            pos["status"] = "monitorando (carteira)"
            return
        n_cob = len([p for p in pos["pernas"] if p.get("origem") == "cobertura"])
        if n_cob >= cfg["pos_max_coberturas"]:
            pos["status"] = "monitorando (limite de coberturas atingido)"
            return
        if time.time() - pos.get("ultima_cob", 0) < cfg["pos_cooldown_s"]:
            return
        melhor, avaliados = avaliar_cobertura(pos, ofertas, modelo, cfg)
        with _TRAVA:
            top = sorted(avaliados, key=lambda c: (not c["ok"], -len(c["depois"].get("novos_totais") or []), -c["odd"]))[:5]
            pos["candidatos"] = [{k: v for k, v in c.items() if k != "oferta"} for c in top]
        if not melhor:
            pos["status"] = "monitorando"
            return
        lado_txt = "Abaixo" if melhor["lado"] == "abaixo" else "Acima"
        desc = "%s de %s (%s) @%s R$ %s na %s" % (lado_txt, _fmt_linha(melhor["linha"]), NOME_PERIODO.get(pos["periodo"]),
                                                melhor["odd"], _br(melhor["stake"]), NOME_CASA[melhor["casa"]])
        dep = melhor["depois"]
        log("📌 posição %d: aposta avulsa amplia a janela — %s | totais novos com Over e Under: %s | pior caso da carteira "
            "%s → %s%s%s" % (
                pos["id"], desc, _txt_totais(dep.get("novos_totais") or []), _br((melhor["antes"] or {}).get("pior", 0)),
                _br(dep["pior"]),
                (" | EV %s → %s" % (_br(melhor["antes"]["ev"]), _br(dep["ev"]))) if melhor["antes"].get("ev") is not None and dep.get("ev") is not None else "",
                (" | ⚠ aumenta a perda em %s" % _txt_totais(dep.get("piorou_em") or [])) if dep.get("piorou_em") else ""))
        if not cfg["modo_teste"] and cfg.get("pos_modo", "auto") == "manual":
            with _TRAVA:
                pos["pendente"] = dict(melhor, descricao=desc, hora=agora())
                pos["status"] = "AGUARDANDO SUA CONFIRMAÇÃO"
            return
        self.executar_cobertura(pos, melhor)

    def executar_cobertura(self, pos, cand):
        if pos.get("simulada") and not self.cfg.get("modo_teste", True):
            log("‼ posição %d é SIMULADA — cobertura real recusada (nunca aposto de verdade em cima de simulação)" % pos["id"])
            with _TRAVA:
                pos["aberta"] = False
                pos["status"] = "encerrada: era simulada (modo teste) — nunca vira aposta real"
            self.estado.salvar()
            return False
        c = self.casas[cand["casa"]]
        chave, url = "pos%d" % pos["id"], (pos.get("urls") or {}).get(cand["casa"])
        perna = dict(cand["oferta"], casa=cand["casa"], stake=cand["stake"])
        simulada = self.cfg["modo_teste"]
        with _TRAVA_CLIQUES:
            m = c.executar(c.na_aba, chave, url, c.montar, perna, pos["times"], timeout=90)
            if not m["ok"]:
                pos["eventos"].append("%s cobertura não montou: %s" % (agora(), m["motivo"]))
                log("📌 posição %d: cobertura não montou — %s" % (pos["id"], m["motivo"]))
                pos["ultima_cob"] = time.time()
                return False
            odd_aceita = None
            if simulada:
                c.executar(c.na_aba, chave, url, c.desfazer, perna, timeout=40)
                ok = True
            else:
                r = c.executar(c.na_aba, chave, url, c.confirmar, perna, timeout=120)
                ok = r["ok"]
                odd_aceita = r.get("odd_aceita")
                if ok is None:
                    with _TRAVA:
                        pos["status"] = "PAUSADA"
                        pos["eventos"].append("%s cobertura INCERTA — confira a casa: %s" % (agora(), r["motivo"]))
                    log("‼ posição %d: cobertura INCERTA na %s — confira a casa. Posição pausada." % (pos["id"], c.rotulo))
                    self.estado.salvar()
                    return False
                if not ok:
                    c.executar(c.na_aba, chave, url, c.limpar_cupom, timeout=40)
                    pos["eventos"].append("%s cobertura recusada: %s" % (agora(), r["motivo"]))
                    pos["ultima_cob"] = time.time()
                    return False
        with _TRAVA:
            pos["pernas"].append({"casa": cand["casa"], "tipo": "total", "periodo": pos["periodo"],
                                  "lado": cand.get("lado", "abaixo"), "linha": cand["linha"],
                                  "odd": odd_aceita or cand["odd"], "stake": cand["stake"],
                                  "origem": "cobertura", "simulada": simulada, "hora": agora()})
            pos["ultimo_n"] = 1
            atualizar_tabela_pos(pos)
            pos["ultima_cob"] = time.time()
            pos.pop("pendente", None)
            pos["status"] = "monitorando"
            pos["eventos"].append("%s cobertura %s: %s de %s @%s R$ %s (%s)" % (
                agora(), "SIMULADA" if simulada else "APOSTADA", "Acima" if cand.get("lado") == "acima" else "Abaixo",
                _fmt_linha(cand["linha"]), odd_aceita or cand["odd"],
                _br(cand["stake"]), NOME_CASA[cand["casa"]]))
        self.estado.salvar()
        log("📌 posição %d: cobertura %s" % (pos["id"], "simulada (modo teste)" if simulada else "APOSTADA"))
        return True

    def confirmar_manual(self, pid):
        pos = next((p for p in self.estado.posicoes if p.get("id") == pid and p.get("aberta")), None)
        if not pos or not pos.get("pendente"):
            return False
        cand = pos["pendente"]
        threading.Thread(target=self.executar_cobertura, args=(pos, cand), daemon=True).start()
        return True


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
<span id="params" class="mut"></span><button onclick="calibrar()">🧪 Calibrar agora</button> <button onclick="post('/api/limpar')">🧹 Limpar painel</button></header>
<main>
<div class="card full" id="mon"></div>
<div class="card" id="calc"><h2>🧮 Calculadora do motor</h2><div class="mut">aguardando a primeira rodada sadia…</div></div>
<div class="card"><h2>📥 Sinais de backlog (só montam com clique)</h2><div id="backlog" class="mut">—</div></div>
<div class="card full"><h2>🪜 Escadinha — candidatos de reentrada</h2><div id="reent" class="mut">—</div></div>
<div class="card full"><h2>📌 Posições (monitoramento pós-entrada)</h2><div id="posmodo"></div><div id="pos" class="mut">—</div></div>
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
 {const pr=d.protecao;let ph='';
  if(pr&&pr.jogo){const seg=pr.ultima?Math.max(0,Math.round(Date.now()/1000-pr.ultima)):0;
   ph='<div style="margin-top:4px">🛡️ <b>Jogo protegido:</b> '+esc(pr.jogo)+' · pares confirmados: <b>'+pr.pares+'/'+pr.max+'</b> · última entrada confirmada há <b>'+Math.floor(seg/60)+'m'+String(seg%60).padStart(2,'0')+'s</b> (encerra só com outro sinal, 4 pares ou mercado fechado)</div>';}
  if(pr&&pr.fila&&pr.fila.length)ph+='<div>📥 <b>Sinais na fila ('+pr.fila.length+'):</b> '+pr.fila.map(f=>esc(f.jogo)+' <span class="mut">('+f.chegou+')</span>').join(' · ')+'</div>';
  if(pr&&pr.liberado)ph+='<div class="mut">🔓 último: '+esc(pr.liberado)+'</div>';
  if(pr&&pr.alerta)ph+='<div class="no"><b>'+esc(pr.alerta)+'</b></div>';
  const bq=pr&&pr.bloqueio;
  if(bq){ph='<div style="background:#7a0010;color:#fff;padding:10px;border-radius:6px;margin:6px 0"><div style="font-size:18px"><b>🚨 BLOQUEADO — '+esc(bq.estado)+'</b> em '+esc(bq.jogo)+' (desde '+esc(bq.desde)+')</div>'+
    '<div>✔ confirmada: <b>'+esc(bq.confirmada)+'</b></div><div>✖ falhou / incerta: <b>'+esc(bq.falhou)+'</b></div>'+
    '<div style="margin:4px 0">Nenhuma aposta nova em jogo nenhum até resolver. Confira o histórico das duas casas e escolha:</div>'+
    '<button onclick="if(confirm(\'As DUAS pernas aparecem aceitas no histórico?\'))post(\'/api/resolver/concluido\')">As duas foram aceitas (CONCLUÍDO)</button> '+
    '<button onclick="if(confirm(\'NENHUMA perna foi aceita?\'))post(\'/api/resolver/nao_apostado\')">Nenhuma foi aceita (NÃO APOSTADO)</button> '+
    '<button onclick="if(confirm(\'SÓ a 1aWin aparece aceita no histórico (a PS não)?\'))post(\'/api/resolver/so_1awin\')">Só a 1aWin entrou (cobrir e seguir)</button> '+
    '<button onclick="if(confirm(\'SÓ a PS aparece aceita no histórico (a 1aWin não)?\'))post(\'/api/resolver/so_ps\')">Só a PS entrou (cobrir e seguir)</button> '+
    '<button onclick="if(confirm(\'Liberar manualmente? Só faça isso se já resolveu a posição nas casas.\'))post(\'/api/resolver/liberar\')">Liberação manual</button></div>'+ph;
    if(!window._bip||Date.now()-window._bip>8000){window._bip=Date.now();try{const a=new AudioContext(),o=a.createOscillator();o.frequency.value=880;o.connect(a.destination);o.start();setTimeout(()=>o.stop(),600);}catch(e){}}}
  document.getElementById('mon').innerHTML='<b>Monitor:</b> '+esc(d.monitor||'parado — aguardando sinal')+ph;}
 document.getElementById('calc').innerHTML=calc(d.calculadora);
 document.getElementById('backlog').innerHTML=d.backlog.length?d.backlog.map((s,i)=>'<div style="margin:4px 0">'+esc(s.jogo)+' <span class="mut">'+esc(s.mercado)+'</span> <button onclick="post(\'/api/backlog/'+i+'\')">MONTAR</button></div>').join(''):'—';
 const _nf=d.bilhetes.filter(b=>b.status=='falhou').length; const _umaEntrou=b=>Object.values(b.detalhe||{}).some(v=>/^confirmada/.test(v)); const _bs=window.verFalhas?d.bilhetes:d.bilhetes.filter(b=>b.status!='falhou'||_umaEntrou(b));
 document.getElementById('bil').innerHTML='<div class="mut" style="margin-bottom:6px">'+(window.verFalhas?'mostrando tudo · <a href="#" onclick="window.verFalhas=0;return false">esconder falhas</a>':(_nf?_nf+' tentativa(s) que falharam escondida(s) (nada apostado) · <a href="#" onclick="window.verFalhas=1;return false">mostrar</a>':''))+'</div><table><tr><th>#</th><th>Hora</th><th>Jogo</th><th>Pernas</th><th>Janela</th><th>Custo</th><th>Status</th><th>Detalhe</th></tr>'+_bs.map(b=>'<tr><td>'+b.id+'</td><td>'+esc(b.criado)+'</td><td>'+esc(b.jogo)+'</td><td>'+b.pernas.map(l=>(l.casa=='1awin'?'1aWin':'PS')+': '+esc(l.selecao)+' @'+l.odd+' '+R(l.stake)).join('<br>')+'</td><td>'+P(b.janela,1)+'</td><td>'+P(b.custo)+'%</td><td class="'+({testado:'ok',confirmado:'ok',falhou:'no',exposto:'no'}[b.status]||'am')+'">'+esc(({confirmado:'CONCLUÍDO',falhou:'NÃO APOSTADO',exposto:'INCOMPLETO',INCERTO:'INCERTO',testado:'testado (simulado)'})[b.status]||b.status)+(b.desfecho?'<br>'+esc(b.desfecho):'')+'</td><td>'+Object.entries(b.detalhe||{}).map(([k,v])=>esc(k)+': '+esc(v)).join('<br>')+(b.avisos&&b.avisos.length?'<br><b class="no">'+b.avisos.map(esc).join('<br>')+'</b>':'')+(b.carteira?'<br><span class="mut">carteira: lucro em '+(b.carteira.cobertura||[]).length+' total(is)'+(b.carteira.novos_totais&&b.carteira.novos_totais.length?' (novos: '+b.carteira.novos_totais.join(', ')+')':'')+' · pior '+R(b.carteira.pior)+'</span>'+(b.carteira.piorou_em&&b.carteira.piorou_em.length?' <b class="no">⚠ aumentou a perda em '+b.carteira.piorou_em.join(', ')+'</b>':''):'')+'<br>'+(b.fotos||[]).filter(x=>x).map(f=>'<a target="_blank" href="/prints/'+encodeURIComponent(f)+'">foto</a>').join(' ')+(d.modo==='REAL'&&b.status!=='confirmado'&&b.status!=='testado'&&(['INCERTO','exposto'].includes(b.status)||Object.values(b.detalhe||{}).some(v=>/^confirmada/.test(v))||(b.avisos||[]).some(a=>/resolvido pelo operador/.test(a)))?'<br><span class="mut">Uma perna entrou de verdade? </span><button onclick="if(confirm(\'Confirmou no histórico que SÓ a 1aWin entrou?\'))post(\'/api/perna/'+b.id+'/1awin\')">Só a 1aWin entrou</button> <button onclick="if(confirm(\'Confirmou no histórico que SÓ a PS entrou?\'))post(\'/api/perna/'+b.id+'/ps3838\')">Só a PS entrou</button>':'')+'</td></tr>').join('')+'</table>';
 {const on=pa.pos_monitorar, md=on?pa.pos_modo:'off';
  const nome={auto:'AUTOMÁTICO (aposta sozinho)',manual:'MANUAL (mostra a oportunidade e espera o seu clique)',off:'DESLIGADO'}[md];
  const bt=(m,t)=>'<button style="margin-left:6px;'+(md==m?'background:#1e8e3e':'background:#555')+'" onclick="post(\'/api/pos_modo/'+m+'\')">'+t+'</button>';
  if(!pa.avulsas_permitidas){document.getElementById('posmodo').innerHTML='<div class="mut" style="margin-bottom:6px">Apostas avulsas (de uma perna só): <b>NUNCA</b>. A janela cresce só com reentradas EM PAR (1aWin + PS). Aqui fica a carteira de cada jogo.</div>';}
  else document.getElementById('posmodo').innerHTML='<div style="margin-bottom:6px">Cobertura: <b class="'+(md=='off'?'no':'ok')+'">'+nome+'</b>'+(d.modo=='TESTE'&&on?' <span class="mut">— modo teste: só SIMULA</span>':'')+bt('auto','Automático')+bt('manual','Manual')+bt('off','Desligar')+
   '</div><div class="mut" style="margin-bottom:6px">Regras: aposta avulsa só se abrir totais NOVOS com lucro na carteira, sem tirar o lucro de nenhum · sem distância mínima: compara com a janela atual da carteira · até '+R(pa.pos_max_por_cobertura_brl)+' por aposta · '+R(pa.pos_max_total_coberturas_brl)+' no total · pior caso da carteira até −'+R(pa.pos_perda_max_total_brl)+' · máx. '+pa.pos_max_coberturas+' avulsas</div>';}
 const _pa=(d.posicoes||[]).filter(p=>p.aberta), _pe=(d.posicoes||[]).filter(p=>!p.aberta);
 const _posHtml=p=>{const r=p.resumo||{};
  let h='<div style="border-bottom:1px solid var(--ln);padding:6px 0"><b>#'+p.id+' '+esc(p.jogo)+'</b> <span class="mut">('+esc(p.periodo)+')'+(p.simulada?' SIMULADA':'')+' — '+esc(p.status)+(p.atualizada?' · '+p.atualizada:'')+'</span>';
  h+='<div>'+p.pernas.map((l,i)=>'<b>A'+(i+1)+'</b> '+(l.origem=='entrada'?'entrada':l.origem=='reentrada'?'reentrada':'<b>cobertura</b>')+': '+(l.casa=='1awin'?'1aWin':'PS')+' '+(l.lado=='acima'?'Acima':'Abaixo')+' de '+l.linha+' @'+l.odd+' '+R(l.stake)).join('<br>')+'</div>';
  if(p.tabela&&p.tabela.length){const tb=p.tabela,gr=[];for(const t of tb){const u=gr[gr.length-1];if(u&&Math.abs(u.liquido-t.liquido)<0.005)u.ate=t.total;else gr.push({de:t.total,ate:t.total,liquido:t.liquido,cobre:t.cobre});}
   h+='<table style="font-size:13px;max-width:520px;margin:4px 0"><tr><th>Total final de pontos</th><th style="text-align:right">Resultado da carteira</th></tr>'+gr.map((g,i)=>'<tr style="'+(g.cobre?'background:rgba(30,142,62,.18)':'')+'"><td>'+(i==0?g.ate+' ou menos':i==gr.length-1?g.de+' ou mais':g.de==g.ate?'Exatamente '+g.de:g.de+' a '+g.ate)+'</td><td style="text-align:right" class="'+(g.liquido>=0?'ok':'no')+'"><b>'+(g.liquido>=0?'+':'−')+R(Math.abs(g.liquido))+'</b></td></tr>').join('')+'</table><details><summary class="mut">detalhe por aposta</summary>';
   const tem=p.tabela.some(t=>t.antes!=null);
   h+='<div style="margin:4px 0">Janela de green da carteira (totais com lucro): <b class="ok">'+esc(p.cobertura_2g||'—')+'</b></div><div style="overflow-x:auto"><table style="font-size:12px"><tr><th>Total final</th>'+p.pernas.map((l,i)=>'<th>A'+(i+1)+'</th>').join('')+'<th>Greens</th><th>Líquido</th>'+(tem?'<th>antes da última</th>':'')+'</tr>'+
   p.tabela.map(t=>'<tr style="'+(t.cobre&&t.greens>=3?'background:rgba(30,142,62,.35)':t.cobre?'background:rgba(30,142,62,.18)':'')+'"><td>'+t.total+'</td>'+t.res.map(x=>'<td class="'+(x=='G'?'ok':x=='P'?'no':'am')+'">'+({G:'ganha',P:'perde',D:'devolve'}[x])+'</td>').join('')+'<td>'+t.greens+'</td><td class="'+(t.liquido>=0?'ok':'no')+'">'+R(t.liquido)+'</td>'+(tem?'<td class="mut">'+R(t.antes)+(t.piorou?' <b class="no">⚠ piorou</b>':'')+'</td>':'')+'</tr>').join('')+'</table></div></details>';}
  if(r.pior!=null)h+='<div>pior '+R(r.pior)+' · melhor '+R(r.melhor)+(r.ev!=null?' · EV '+R(r.ev)+' · P(lucro) '+P(100*r.p_lucro,0)+'% · P(prejuízo) '+P(100*r.p_preju,0)+'%':'')+' · lucro se terminar em: '+(r.faixas_lucro||[]).map(f=>f[0]==f[1]?f[0]:f[0]+'–'+f[1]).join(', ')+'</div>';
  if(p.candidatos&&p.candidatos.length)h+='<div class="mut">avulsas avaliadas: '+p.candidatos.slice(0,3).map(c=>(c.ok?'✅':'✖')+' '+(c.lado=='acima'?'Acima':'Abaixo')+' '+c.linha+' @'+c.odd+' '+R(c.stake)+' ('+c.dist+' pts além da borda) → janela nova: '+((c.depois.novos_totais||[]).length?c.depois.novos_totais[0]+'–'+c.depois.novos_totais[c.depois.novos_totais.length-1]:'nenhuma')+' · pior '+R(c.depois.pior)+(c.ok?'':' <i>('+esc(c.motivo||'')+')</i>')).join(' | ')+'</div>';
  if(p.pendente)h+='<div class="am">Oportunidade: '+esc(p.pendente.descricao)+' <button onclick="post(\'/api/cobertura/'+p.id+'\')">APOSTAR COBERTURA</button></div>';
  return h+'</div>'};
 document.getElementById('pos').innerHTML=(_pa.map(_posHtml).join('')||'Nenhuma posição aberta. Ela abre sozinha logo depois de um middle (confirmado no real, ou testado no modo teste).')+
   (_pe.length?'<details style="margin-top:6px"><summary class="mut">encerradas ('+_pe.length+')</summary>'+_pe.slice(0,10).map(_posHtml).join('')+'</details>':'');
 {const re=d.reentrada;const J=j=>j?(j[0]==j[1]?j[0]:j[0]+'–'+j[1]):'—';
  if(!re)document.getElementById('reent').innerHTML='Sem reentrada em avaliação (aparece depois do 1º par aceito num jogo).';
  else{let h='<div><b>'+esc(re.jogo)+'</b> · '+esc(re.hora)+' · janela atual: <b class="ok">'+J(re.janela)+'</b> · linha da PS: '+(re.linha_ps??'?')+' (na última entrada: '+(re.linha_ultima??'?')+') · espera: <b class="'+(re.portao=='liberado'?'ok':'am')+'">'+esc(re.portao)+'</b></div>';
   h+='<table style="font-size:12px;margin-top:4px"><tr><th></th><th>Tipo</th><th>Par</th><th>Custo</th><th>Janela atual → simulada</th><th>Novos</th><th>Pior antes → depois</th><th>Capital do par</th><th>Concentração</th><th>Motivo</th></tr>'+
   (re.candidatos||[]).map(c=>'<tr><td>'+(c.ok?'✅':'✖')+'</td><td>'+esc(c.tipo||'—')+'</td><td>'+c.pernas.map(x=>(x.casa=='1awin'?'1aWin':'PS')+' '+(x.lado=='acima'?'Over':'Under')+' '+x.linha+' @'+x.odd+' '+R(x.stake)).join('<br>')+'</td><td>'+P(c.custo)+'%</td><td>'+J(c.janela_antiga)+' → '+J(c.janela_nova)+'</td><td>'+((c.novos||[]).join(', ')||'—')+'</td><td>'+(c.pior_antes!=null?R(c.pior_antes)+' → '+R(c.pior_depois):'—')+'</td><td>'+(c.capital!=null?R(c.capital):'—')+'</td><td>'+(c.concentracao?Object.entries(c.concentracao).filter(([k,v])=>v>1).map(([k,v])=>esc(k)+' ×'+v).join('<br>')||'nenhuma repetida':'—')+'</td><td>'+esc(c.motivo||'')+'</td></tr>').join('')+'</table>';
   const e=re.escolhido||(re.candidatos||[]).find(c=>c.saldos&&c.saldos.length);
   if(e&&e.saldos&&e.saldos.length)h+='<details><summary class="mut">saldo por total final (antes → depois) do '+(re.escolhido?'escolhido':'1º candidato')+'</summary><table style="font-size:12px">'+e.saldos.map(t=>'<tr><td>'+t[0]+'</td><td class="'+(t[1]>0?'ok':'no')+'">'+R(t[1])+'</td><td>→</td><td class="'+(t[2]>0?'ok':'no')+'">'+R(t[2])+'</td></tr>').join('')+'</table></details>';
   document.getElementById('reent').innerHTML=h;}}
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
            m2 = re.match(r"^/api/cobertura/(\d+)$", p)
            if m2 and operador and getattr(operador, "gerente", None):
                ok = operador.gerente.confirmar_manual(int(m2.group(1)))
                return self._env(200, json.dumps({"ok": ok}))
            m3 = re.match(r"^/api/pos_modo/(auto|manual|off)$", p)
            if m3:
                modo = m3.group(1)
                with _TRAVA:
                    estado.cfg["pos_monitorar"] = modo != "off"
                    if modo != "off":
                        estado.cfg["pos_modo"] = modo
                try:
                    prefs = _ler_json(ARQ_PREFS, {}) or {}
                    prefs.update({"pos_monitorar": estado.cfg["pos_monitorar"], "pos_modo": estado.cfg.get("pos_modo", "auto")})
                    _gravar_json(ARQ_PREFS, prefs)
                except Exception:
                    pass
                log("📌 cobertura pós-entrada: %s (pelo painel — fica assim também quando o programa reabrir)" % {
                    "auto": "AUTOMÁTICA", "manual": "MANUAL", "off": "DESLIGADA"}[modo])
                return self._env(200, '{"ok":true}')
            m5 = re.match(r"^/api/perna/(\w+)/(1awin|ps3838)$", p)
            if m5 and operador:
                ok = operador.retomar_perna(m5.group(1), m5.group(2))
                return self._env(200, '{"ok":%s}' % ("true" if ok else "false"))
            m4 = re.match(r"^/api/resolver/(concluido|nao_apostado|liberar|so_1awin|so_ps)$", p)
            if m4 and operador:
                ok = operador.resolver(m4.group(1))
                return self._env(200, '{"ok":%s}' % ("true" if ok else "false"))
            if p == "/api/limpar":
                # 🧹 só a TELA: posições velhas (> 3 h) encerradas, backlog e escadinha limpos — nada nas casas
                n = 0
                if operador and getattr(operador, "gerente", None):
                    n = operador.gerente.encerrar_velhas(horas=3)
                with _TRAVA:
                    estado.backlog = []
                    estado.reentrada = None
                estado.salvar()
                log("🧹 painel limpo: %d posição(ões) antiga(s) encerrada(s), backlog e escadinha zerados" % n)
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
    operador.gerente = GerentePosicoes(cfg, estado, casas).iniciar()  # sempre (o painel liga/desliga)
    operador.retomar()  # 20/11: bloqueio / jogo protegido / fila sobrevivem ao reinício
    if cfg.get("pos_monitorar", True):
        log("📌 monitoramento pós-entrada LIGADO (modo %s; entra o que acrescentar totais novos à janela da carteira)" % (
            cfg.get("pos_modo", "auto")))
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
    finally:
        try:
            estado.salvar_agora()
        except Exception:
            pass


if __name__ == "__main__":
    main(sys.argv[1:])
