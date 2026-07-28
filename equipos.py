# =============================================================================
#  CATALOGO DE EQUIPOS NFL - modulo compartido
# =============================================================================
#  Lo usan app.py (interfaz) y sincronizador.py (proceso automatico), por eso
#  NO importa streamlit ni pandas: debe poder correr en un runner de CI.
#
#  PROHIBIDO representar equipos con emojis: cada uno enlaza a la URL publica
#  de su logotipo oficial en el CDN de ESPN.
# =============================================================================

from __future__ import annotations

import re
import unicodedata

# Los logos se piden por el redimensionador del CDN de ESPN en lugar del PNG
# original de 500 px. El archivo grande pesa ~52 KB y en la app nunca se muestra
# a mas de 74 px: cargar los 32 costaba 1.6 MB por jornada. A 160 px (suficiente
# para pantallas retina) el total baja a ~290 KB, y el CDN los sirve con
# `Cache-Control: max-age=86395`, es decir 24 h de cache en el navegador.
_COMBINER = "https://a.espncdn.com/combiner/i?img={ruta}&w={px}&h={px}&scale=crop&cquality=90"
LOGO_PX = 160

LOGO_BASE = _COMBINER.format(ruta="/i/teamlogos/nfl/500/{}.png", px=LOGO_PX)
LOGO_NFL = _COMBINER.format(ruta="/i/teamlogos/leagues/500/nfl.png", px=LOGO_PX)

# Ruta cruda, sin redimensionar. Se conserva para reconocer avatares guardados
# con el formato anterior.
LOGO_ORIGINAL = "https://a.espncdn.com/i/teamlogos/nfl/500/{}.png"

# abrev -> (Ciudad, Apodo, Conferencia, Division, Color primario)
NFL_TEAMS: dict[str, dict] = {
    "ARI": dict(ciudad="Arizona",      apodo="Cardinals",  conf="NFC", div="Oeste", color="#97233F"),
    "ATL": dict(ciudad="Atlanta",      apodo="Falcons",    conf="NFC", div="Sur",   color="#A71930"),
    "BAL": dict(ciudad="Baltimore",    apodo="Ravens",     conf="AFC", div="Norte", color="#241773"),
    "BUF": dict(ciudad="Buffalo",      apodo="Bills",      conf="AFC", div="Este",  color="#00338D"),
    "CAR": dict(ciudad="Carolina",     apodo="Panthers",   conf="NFC", div="Sur",   color="#0085CA"),
    "CHI": dict(ciudad="Chicago",      apodo="Bears",      conf="NFC", div="Norte", color="#0B162A"),
    "CIN": dict(ciudad="Cincinnati",   apodo="Bengals",    conf="AFC", div="Norte", color="#FB4F14"),
    "CLE": dict(ciudad="Cleveland",    apodo="Browns",     conf="AFC", div="Norte", color="#311D00"),
    "DAL": dict(ciudad="Dallas",       apodo="Cowboys",    conf="NFC", div="Este",  color="#003594"),
    "DEN": dict(ciudad="Denver",       apodo="Broncos",    conf="AFC", div="Oeste", color="#FB4F14"),
    "DET": dict(ciudad="Detroit",      apodo="Lions",      conf="NFC", div="Norte", color="#0076B6"),
    "GB":  dict(ciudad="Green Bay",    apodo="Packers",    conf="NFC", div="Norte", color="#203731"),
    "HOU": dict(ciudad="Houston",      apodo="Texans",     conf="AFC", div="Sur",   color="#03202F"),
    "IND": dict(ciudad="Indianapolis", apodo="Colts",      conf="AFC", div="Sur",   color="#002C5F"),
    "JAX": dict(ciudad="Jacksonville", apodo="Jaguars",    conf="AFC", div="Sur",   color="#006778"),
    "KC":  dict(ciudad="Kansas City",  apodo="Chiefs",     conf="AFC", div="Oeste", color="#E31837"),
    "LV":  dict(ciudad="Las Vegas",    apodo="Raiders",    conf="AFC", div="Oeste", color="#000000"),
    "LAC": dict(ciudad="Los Angeles",  apodo="Chargers",   conf="AFC", div="Oeste", color="#0080C6"),
    "LAR": dict(ciudad="Los Angeles",  apodo="Rams",       conf="NFC", div="Oeste", color="#003594"),
    "MIA": dict(ciudad="Miami",        apodo="Dolphins",   conf="AFC", div="Este",  color="#008E97"),
    "MIN": dict(ciudad="Minnesota",    apodo="Vikings",    conf="NFC", div="Norte", color="#4F2683"),
    "NE":  dict(ciudad="New England",  apodo="Patriots",   conf="AFC", div="Este",  color="#002244"),
    "NO":  dict(ciudad="New Orleans",  apodo="Saints",     conf="NFC", div="Sur",   color="#D3BC8D"),
    "NYG": dict(ciudad="New York",     apodo="Giants",     conf="NFC", div="Este",  color="#0B2265"),
    "NYJ": dict(ciudad="New York",     apodo="Jets",       conf="AFC", div="Este",  color="#125740"),
    "PHI": dict(ciudad="Philadelphia", apodo="Eagles",     conf="NFC", div="Este",  color="#004C54"),
    "PIT": dict(ciudad="Pittsburgh",   apodo="Steelers",   conf="AFC", div="Norte", color="#FFB612"),
    "SF":  dict(ciudad="San Francisco",apodo="49ers",      conf="NFC", div="Oeste", color="#AA0000"),
    "SEA": dict(ciudad="Seattle",      apodo="Seahawks",   conf="NFC", div="Oeste", color="#002244"),
    "TB":  dict(ciudad="Tampa Bay",    apodo="Buccaneers", conf="NFC", div="Sur",   color="#D50A0A"),
    "TEN": dict(ciudad="Tennessee",    apodo="Titans",     conf="AFC", div="Sur",   color="#0C2340"),
    "WSH": dict(ciudad="Washington",   apodo="Commanders", conf="NFC", div="Este",  color="#5A1414"),
}

# Alias para tolerar cualquier formato que venga desde la base de datos:
# abreviatura, apodo, ciudad, nombre completo, variantes historicas o en espanol.
ALIAS_EXTRA: dict[str, str] = {
    "arizona": "ARI", "cardenales": "ARI",
    "atlanta": "ATL", "halcones": "ATL",
    "baltimore": "BAL", "cuervos": "BAL",
    "buffalo": "BUF",
    "carolina": "CAR", "panteras": "CAR",
    "chicago": "CHI", "osos": "CHI",
    "cincinnati": "CIN", "cincinatti": "CIN",
    "cleveland": "CLE",
    "dallas": "DAL", "vaqueros": "DAL",
    "denver": "DEN", "broncos de denver": "DEN",
    "detroit": "DET", "leones": "DET",
    "green bay": "GB", "greenbay": "GB", "gnb": "GB", "empacadores": "GB",
    "houston": "HOU",
    "indianapolis": "IND", "potros": "IND",
    "jacksonville": "JAX", "jac": "JAX",
    "kansas city": "KC", "kansascity": "KC", "kan": "KC", "jefes": "KC",
    "las vegas": "LV", "oakland": "LV", "lvr": "LV", "oak": "LV", "raiders": "LV",
    "los angeles chargers": "LAC", "san diego": "LAC", "sd": "LAC", "sdg": "LAC", "cargadores": "LAC",
    "los angeles rams": "LAR", "st louis": "LAR", "st. louis": "LAR", "ram": "LAR", "carneros": "LAR",
    "miami": "MIA", "delfines": "MIA",
    "minnesota": "MIN", "vikingos": "MIN",
    "new england": "NE", "newengland": "NE", "nwe": "NE", "patriotas": "NE",
    "new orleans": "NO", "neworleans": "NO", "nor": "NO", "santos": "NO",
    "new york giants": "NYG", "gigantes": "NYG",
    "new york jets": "NYJ", "jets": "NYJ",
    "philadelphia": "PHI", "philly": "PHI", "aguilas": "PHI",
    "pittsburgh": "PIT", "acereros": "PIT",
    "san francisco": "SF", "sanfrancisco": "SF", "sfo": "SF", "49": "SF",
    "seattle": "SEA",
    "tampa bay": "TB", "tampabay": "TB", "tam": "TB", "bucaneros": "TB",
    "tennessee": "TEN", "titanes": "TEN",
    "washington": "WSH", "was": "WSH", "wft": "WSH", "redskins": "WSH", "commanders": "WSH",
}

DIVISIONES = ["Este", "Norte", "Sur", "Oeste"]
CONFERENCIAS = ["AFC", "NFC"]


def _normaliza(texto: str) -> str:
    """Minusculas, sin acentos, sin puntuacion. Base del matching de equipos."""
    if texto is None:
        return ""
    txt = unicodedata.normalize("NFKD", str(texto))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", "", txt.lower().strip())


# Indice de busqueda construido una sola vez al importar el modulo.
_INDICE_EQUIPOS: dict[str, str] = {}
for _abrev, _info in NFL_TEAMS.items():
    _completo = f"{_info['ciudad']} {_info['apodo']}"
    for _clave in (_abrev, _info["apodo"], _completo):
        _INDICE_EQUIPOS[_normaliza(_clave)] = _abrev
for _alias, _abrev_alias in ALIAS_EXTRA.items():
    _INDICE_EQUIPOS.setdefault(_normaliza(_alias), _abrev_alias)


def resolver_equipo(nombre: str) -> str | None:
    """Convierte cualquier representacion textual de un equipo en su abreviatura."""
    if not nombre:
        return None
    clave = _normaliza(nombre)
    if clave in _INDICE_EQUIPOS:
        return _INDICE_EQUIPOS[clave]
    # Coincidencia parcial: 'Los Angeles Rams (LAR)' o 'Rams - LA'
    for idx_clave, abrev in _INDICE_EQUIPOS.items():
        if len(idx_clave) >= 4 and idx_clave in clave:
            return abrev
    return None


def logo_url(nombre: str) -> str:
    """URL del logotipo oficial. Nunca devuelve emoji; si falla usa el escudo NFL."""
    abrev = resolver_equipo(nombre)
    return LOGO_BASE.format(abrev.lower()) if abrev else LOGO_NFL


def nombre_display(nombre: str) -> str:
    """Nombre canonico 'Ciudad Apodo'. Si no se reconoce, devuelve el original."""
    abrev = resolver_equipo(nombre)
    if abrev:
        info = NFL_TEAMS[abrev]
        return f"{info['ciudad']} {info['apodo']}"
    return str(nombre or "")


def nombre_corto(nombre: str) -> str:
    """Solo el apodo, util para espacios reducidos."""
    abrev = resolver_equipo(nombre)
    return NFL_TEAMS[abrev]["apodo"] if abrev else str(nombre or "")


def color_equipo(nombre: str) -> str:
    abrev = resolver_equipo(nombre)
    return NFL_TEAMS[abrev]["color"] if abrev else "#4B5563"


def equipos_por_conferencia(conf: str) -> list[str]:
    return [a for a, i in NFL_TEAMS.items() if i["conf"] == conf]
