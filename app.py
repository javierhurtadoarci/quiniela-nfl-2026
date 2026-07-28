# =============================================================================
#  QUINIELA NFL 2026  -  Pick'em de Temporada Regular + Playoffs + Super Bowl
# =============================================================================
#  Stack : Streamlit + Supabase (PostgreSQL + Auth) + extra_streamlit_components
#  Autor : Plataforma comunitaria de pronosticos deportivos
#
#  ESTRUCTURA DEL ARCHIVO
#  ---------------------------------------------------------------------------
#   1. IMPORTS Y CONFIGURACION GLOBAL
#   2. CATALOGO DE EQUIPOS NFL (logos oficiales por URL - SIN EMOJIS)
#   3. ESTILOS CSS PROFESIONALES
#   4. CONEXION A SUPABASE
#   5. GESTION DE COOKIES Y SESION
#   6. CAPA DE ACCESO A DATOS (queries cacheadas, siempre con .limit(10000))
#   7. UTILIDADES DE TIEMPO Y ZONAS HORARIAS
#   8. MOTOR DE PUNTUACION
#   9. COMPONENTES DE UI REUTILIZABLES
#  10. PANTALLA DE AUTENTICACION (login / registro)
#  11. PESTANA: MIS PICKS
#  12. PESTANA: POSICIONES NFL
#  13. PESTANA: SUPER BOWL
#  14. PESTANA: RANKING GLOBAL
#  15. PESTANA: PANEL ADMIN
#  16. ORQUESTADOR PRINCIPAL
# =============================================================================

# -----------------------------------------------------------------------------
#  1. IMPORTS Y CONFIGURACION GLOBAL
# -----------------------------------------------------------------------------
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from html import escape as html_escape   # los nombres de usuario van dentro de HTML

import pandas as pd
import streamlit as st
import extra_streamlit_components as stx
from supabase import create_client, Client

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # pragma: no cover - Python < 3.9
    from backports.zoneinfo import ZoneInfo, available_timezones  # type: ignore


st.set_page_config(
    page_title="Quiniela NFL 2026",
    page_icon="https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Parametros de negocio ---------------------------------------------------
PUNTOS_ACIERTO_REGULAR = 1     # 1 punto por ganador directo (Moneyline)
PUNTOS_CAMPEON = 10            # Super Bowl: acertar campeon
PUNTOS_SUBCAMPEON = 5          # Super Bowl: acertar subcampeon

# Los playoffs valen mas conforme avanza el torneo: sin esto la quiniela queda
# practicamente decidida en noviembre y las rondas finales dejan de importar.
PUNTOS_POR_RONDA = {
    19: 2,   # Comodines
    20: 3,   # Divisional
    21: 4,   # Final de Conferencia
    22: 5,   # Super Bowl
}


def puntos_de_semana(semana) -> int:
    """Valor de acertar un partido segun la jornada."""
    try:
        return PUNTOS_POR_RONDA.get(int(semana), PUNTOS_ACIERTO_REGULAR)
    except (TypeError, ValueError):
        return PUNTOS_ACIERTO_REGULAR

SEMANAS_REGULARES = list(range(1, 19))          # Semanas 1 a 18 (272 juegos)
SEMANAS_PLAYOFFS = [19, 20, 21, 22]             # Wild Card -> Super Bowl

ETIQUETA_SEMANA = {
    19: "Comodines (Wild Card)",
    20: "Divisional",
    21: "Final de Conferencia",
    22: "Super Bowl LX",
}

# Valores canonicos almacenados en predictions.prediction y results.ganador_oficial
PICK_LOCAL = "LOCAL"
PICK_VISITANTE = "VISITANTE"
PICK_EMPATE = "EMPATE"

ZONAS_SUGERIDAS = [
    "America/Mexico_City",
    "America/Tijuana",
    "America/Monterrey",
    "America/Cancun",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Bogota",
    "America/Lima",
    "America/Santiago",
    "America/Buenos_Aires",
    "Europe/Madrid",
    "Europe/London",
    "UTC",
]

# -----------------------------------------------------------------------------
#  2. CATALOGO DE EQUIPOS NFL
#     Vive en equipos.py para que el sincronizador automatico pueda reutilizarlo
#     sin arrastrar streamlit. Los logotipos son URLs oficiales: nunca emojis.
# -----------------------------------------------------------------------------
from equipos import (
    LOGO_BASE,
    LOGO_NFL,
    LOGO_ORIGINAL,
    NFL_TEAMS,
    DIVISIONES,
    CONFERENCIAS,
    resolver_equipo,
    logo_url,
    nombre_display,
    nombre_corto,
    color_equipo,
    equipos_por_conferencia,
)
import avatares

# Colores oficiales de las conferencias: rojo la AFC, azul la NFC.
COLOR_CONFERENCIA = {"AFC": "#D50A0A", "NFC": "#013369"}


def equipos_en_descanso(partidos: pd.DataFrame, semana: int) -> list[str]:
    """
    Equipos con semana de descanso (bye): los que no aparecen en ningun partido
    de la jornada. Solo aplica a temporada regular; en playoffs la ausencia
    significa eliminacion, no descanso.
    """
    if int(semana) > 18:
        return []
    de_semana = partidos[partidos["semana"] == semana]
    jugando = set()
    for fila in de_semana.to_dict("records"):
        for columna in ("equipo_local", "equipo_visitante"):
            abrev = resolver_equipo(fila.get(columna, ""))
            if abrev:
                jugando.add(abrev)
    return sorted(set(NFL_TEAMS) - jugando, key=nombre_display)


# -----------------------------------------------------------------------------
#  3. ESTILOS CSS PROFESIONALES
# -----------------------------------------------------------------------------
CSS = """
<style>
    /* ---------- Tipografia y lienzo ---------- */
    html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1280px; }

    /* ---------- Encabezado principal ---------- */
    .nfl-hero {
        background: linear-gradient(120deg, #013369 0%, #024a8f 55%, #D50A0A 100%);
        border-radius: 18px; padding: 26px 32px; margin-bottom: 22px;
        box-shadow: 0 12px 32px rgba(1,51,105,.32);
        display: flex; align-items: center; gap: 20px;
    }
    .nfl-hero img { height: 58px; }
    .nfl-hero h1 { color: #fff; margin: 0; font-size: 1.85rem; font-weight: 800; letter-spacing: -.5px; }
    .nfl-hero p  { color: rgba(255,255,255,.82); margin: 4px 0 0; font-size: .95rem; }

    /* ---------- Tarjeta de partido ---------- */
    .match-card {
        background: var(--background-color, #ffffff);
        border: 1px solid rgba(128,128,128,.22);
        border-radius: 16px; padding: 16px 20px; margin-bottom: 6px;
        box-shadow: 0 2px 10px rgba(0,0,0,.06);
    }
    .match-meta {
        display: flex; justify-content: space-between; align-items: center;
        font-size: .78rem; letter-spacing: .4px; text-transform: uppercase;
        opacity: .72; margin-bottom: 10px; font-weight: 600;
    }
    .badge {
        display: inline-block; padding: 3px 11px; border-radius: 999px;
        font-size: .70rem; font-weight: 700; letter-spacing: .6px;
    }
    .badge-open   { background: #DCFCE7; color: #166534; }
    .badge-locked { background: #FEE2E2; color: #991B1B; }
    .badge-final  { background: #E0E7FF; color: #3730A3; }

    /* Respaldo si el CDN de logos no responde: recuadro con la abreviatura */
    .logo-fallback {
        height: 46px; width: 46px; border-radius: 10px; flex-shrink: 0;
        background: rgba(1,51,105,.12); color: #013369;
        display: flex; align-items: center; justify-content: center;
        font-weight: 800; font-size: .82rem; letter-spacing: .5px;
    }

    /* ---------- Fila de equipo ---------- */
    .team-row { display: flex; align-items: center; gap: 12px; }
    .team-row img { height: 46px; width: 46px; object-fit: contain; }
    .team-name { font-weight: 700; font-size: 1.02rem; line-height: 1.15; }
    .team-sub  { font-size: .76rem; opacity: .62; }
    .vs-pill {
        text-align: center; font-weight: 800; opacity: .45; font-size: .95rem;
        letter-spacing: 1px;
    }
    .score-big { font-size: 1.9rem; font-weight: 800; line-height: 1; }

    /* ---------- Barras de votacion ---------- */
    .vote-track {
        height: 10px; border-radius: 999px; background: rgba(128,128,128,.16);
        overflow: hidden; margin: 5px 0 3px;
    }
    .vote-fill { height: 100%; border-radius: 999px; transition: width .4s ease; }
    .vote-label { display: flex; justify-content: space-between; font-size: .78rem; font-weight: 600; }

    /* ---------- Tablas / posiciones ---------- */
    .div-title {
        font-weight: 800; font-size: .95rem; letter-spacing: .5px;
        border-left: 4px solid #013369; padding-left: 10px; margin: 18px 0 8px;
    }

    /* ---------- Siglas de conferencia ---------- */
    .conf-head {
        display: flex; align-items: center; gap: 12px; margin: 4px 0 10px;
    }
    .conf-sigla {
        font-size: 2.1rem; font-weight: 900; letter-spacing: -1px; line-height: 1;
        padding: 6px 16px; border-radius: 12px; color: #fff;
    }
    .conf-linea { flex: 1; height: 3px; border-radius: 999px; opacity: .28; }

    /* ---------- Equipos en descanso (bye week) ---------- */
    .bye-box {
        border: 1px dashed rgba(128,128,128,.38);
        border-radius: 14px; padding: 12px 18px; margin: 4px 0 18px;
        background: rgba(128,128,128,.05);
    }
    .bye-head {
        font-size: .74rem; font-weight: 800; letter-spacing: .9px;
        text-transform: uppercase; opacity: .62; margin-bottom: 10px;
    }
    .bye-list { display: flex; flex-wrap: wrap; gap: 10px 22px; }
    .bye-team { display: flex; align-items: center; gap: 8px; }
    .bye-team img {
        height: 34px; width: 34px; object-fit: contain;
        filter: grayscale(55%); opacity: .85;
    }
    .bye-team span { font-size: .86rem; font-weight: 600; }

    /* ---------- Avatares ---------- */
    .avatar-fila {
        height: 44px; width: 44px; border-radius: 50%; object-fit: cover;
        border: 2px solid rgba(128,128,128,.25); background: #fff; flex-shrink: 0;
    }
    .avatar-podio {
        height: 74px; width: 74px; border-radius: 50%; object-fit: cover;
        border: 3px solid rgba(128,128,128,.25); background: #fff;
        display: block; margin: 0 auto 6px;
    }

    /* ---------- Ranking ---------- */
    .rank-chip {
        display:inline-flex; align-items:center; justify-content:center;
        width:30px; height:30px; border-radius:50%; font-weight:800; font-size:.85rem; color:#fff;
    }
    .r1 { background: linear-gradient(135deg,#D4AF37,#F7DF8B); color:#5a4500; }
    .r2 { background: linear-gradient(135deg,#9CA3AF,#E5E7EB); color:#374151; }
    .r3 { background: linear-gradient(135deg,#B45309,#F0A868); }
    .rn { background: rgba(128,128,128,.22); color: inherit; }

    /* ---------- Navegacion principal ---------- */
    /* segmented_control en lugar de st.tabs: conserva la seccion tras un rerun */
    div[data-testid="stSegmentedControl"] button {
        font-weight: 600; padding: 9px 18px;
    }
    div[data-testid="stSegmentedControl"] button[aria-checked="true"] {
        background: rgba(1,51,105,.12);
    }

    /* ---------- Pestanas (donde queden) ---------- */
    .stTabs [data-baseweb="tab-list"] { gap: 6px; }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px 10px 0 0; padding: 10px 18px; font-weight: 600;
    }
    .stTabs [aria-selected="true"] { background: rgba(1,51,105,.10); }

    /* ---------- Auth ---------- */
    .auth-wrap { max-width: 460px; margin: 4vh auto 0; }
    footer, #MainMenu { visibility: hidden; }
</style>
"""


# -----------------------------------------------------------------------------
#  4. CONEXION A SUPABASE
#     Credenciales en .streamlit/secrets.toml:
#       SUPABASE_URL = "https://xxxx.supabase.co"
#       SUPABASE_KEY = "eyJhbGciOi..."      (anon public key)
#       ADMIN_EMAIL  = "admin@dominio.com"
# -----------------------------------------------------------------------------
def get_supabase() -> Client:
    """
    Cliente Supabase POR SESION de navegador (no @st.cache_resource).

    Es deliberado: tras `sign_in_with_password` el cliente conserva el access
    token del usuario y lo envia en cada peticion, que es lo que evaluan las
    politicas RLS via `auth.jwt() ->> 'email'`. Un cliente cacheado a nivel de
    proceso seria compartido por todos los usuarios concurrentes y el token del
    ultimo login se aplicaria a los demas.
    """
    if "sb_client" not in st.session_state:
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        except (KeyError, FileNotFoundError):
            st.error(
                "Faltan credenciales de Supabase. Crea `.streamlit/secrets.toml` con "
                "`SUPABASE_URL`, `SUPABASE_KEY` y `ADMIN_EMAIL`."
            )
            st.stop()
        st.session_state["sb_client"] = create_client(url, key)
    return st.session_state["sb_client"]


def admin_email() -> str:
    return str(st.secrets.get("ADMIN_EMAIL", "")).lower().strip()


def es_admin(email: str | None) -> bool:
    return bool(email) and email.lower().strip() == admin_email() and admin_email() != ""


def sin_admin(df: pd.DataFrame, columna: str = "email") -> pd.DataFrame:
    """
    Excluye al administrador de cualquier conjunto de participantes.

    El admin arbitra: captura marcadores y declara al campeon, asi que no
    compite ni aparece en el ranking, en las barras de votacion ni en la lista
    de "quien voto que". Se filtra aqui, en un solo punto, para que ninguna
    vista pueda mostrarlo por descuido.
    """
    correo = admin_email()
    if df.empty or not correo or columna not in df.columns:
        return df
    return df[df[columna].astype(str).str.lower().str.strip() != correo]


# -----------------------------------------------------------------------------
#  5. GESTION DE COOKIES Y SESION
#     extra_streamlit_components mantiene al usuario logueado entre recargas.
# -----------------------------------------------------------------------------
COOKIE_EMAIL = "nfl26_email"
COOKIE_TOKEN = "nfl26_refresh"


def get_cookie_manager() -> stx.CookieManager:
    """Una instancia por sesion, por el mismo motivo que `get_supabase`."""
    if "cookie_mgr" not in st.session_state:
        st.session_state["cookie_mgr"] = stx.CookieManager(key="nfl26_cookies")
    return st.session_state["cookie_mgr"]


def guardar_cookies(email: str, refresh_token: str | None) -> None:
    cm = get_cookie_manager()
    expira = datetime.now() + timedelta(days=30)
    cm.set(COOKIE_EMAIL, email.lower(), expires_at=expira, key="set_email")
    if refresh_token:
        cm.set(COOKIE_TOKEN, refresh_token, expires_at=expira, key="set_token")


def limpiar_cookies() -> None:
    cm = get_cookie_manager()
    for nombre, k in ((COOKIE_EMAIL, "del_email"), (COOKIE_TOKEN, "del_token")):
        try:
            cm.delete(nombre, key=k)
        except Exception:
            pass


def restaurar_sesion() -> None:
    """Reconstruye la sesion desde la cookie de refresh token de Supabase Auth."""
    if st.session_state.get("usuario"):
        return
    cm = get_cookie_manager()
    cookies = cm.get_all(key="get_all_cookies") or {}
    email = cookies.get(COOKIE_EMAIL)
    token = cookies.get(COOKIE_TOKEN)
    if not email:
        return
    sb = get_supabase()
    if token:
        try:
            sb.auth.refresh_session(token)
        except Exception:
            limpiar_cookies()
            return
    perfil = obtener_perfil(str(email).lower())
    if perfil:
        st.session_state["usuario"] = perfil


# -----------------------------------------------------------------------------
#  6. CAPA DE ACCESO A DATOS
#     REGLA: toda consulta a tablas con volumen usa .limit(10000) para evitar
#     que la API de Supabase pagine y corte registros silenciosamente.
# -----------------------------------------------------------------------------
LIMITE = 10000


@st.cache_data(ttl=300, show_spinner=False)
def cargar_partidos() -> pd.DataFrame:
    """Los 272 juegos de temporada regular + playoffs. Nunca hardcodeados."""
    sb = get_supabase()
    res = (
        sb.table("matches")
        .select("id, semana, equipo_local, equipo_visitante, fecha_hora, bloqueado")
        .order("semana")
        .order("fecha_hora")
        .limit(LIMITE)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    if df.empty:
        return pd.DataFrame(
            columns=["id", "semana", "equipo_local", "equipo_visitante", "fecha_hora", "bloqueado"]
        )
    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], utc=True, errors="coerce")
    df["semana"] = pd.to_numeric(df["semana"], errors="coerce").fillna(0).astype(int)
    df["bloqueado"] = df["bloqueado"].fillna(False).astype(bool)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def cargar_predicciones() -> pd.DataFrame:
    sb = get_supabase()
    res = (
        sb.table("predictions")
        .select("id, email, match_id, prediction")
        .limit(LIMITE)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    if df.empty:
        return pd.DataFrame(columns=["id", "email", "match_id", "prediction"])
    df["email"] = df["email"].astype(str).str.lower().str.strip()
    df["prediction"] = df["prediction"].astype(str).str.upper().str.strip()
    return df


@st.cache_data(ttl=60, show_spinner=False)
def cargar_resultados() -> pd.DataFrame:
    sb = get_supabase()
    res = (
        sb.table("results")
        .select("match_id, marcador_local, marcador_visitante, ganador_oficial")
        .limit(LIMITE)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    if df.empty:
        return pd.DataFrame(
            columns=["match_id", "marcador_local", "marcador_visitante", "ganador_oficial"]
        )
    df["ganador_oficial"] = df["ganador_oficial"].astype(str).str.upper().str.strip()
    return df


@st.cache_data(ttl=120, show_spinner=False)
def cargar_usuarios() -> pd.DataFrame:
    sb = get_supabase()
    columnas = "id, username, email, avatar_url"
    try:
        res = sb.table("users").select(columnas).limit(LIMITE).execute()
    except Exception as exc:
        # Tolerancia si aun no se ejecuto sql/05_avatares.sql
        if "avatar_url" not in str(exc):
            raise
        res = sb.table("users").select("id, username, email").limit(LIMITE).execute()

    df = pd.DataFrame(res.data or [])
    if df.empty:
        return pd.DataFrame(columns=["id", "username", "email", "avatar_url"])
    df["email"] = df["email"].astype(str).str.lower().str.strip()
    if "avatar_url" not in df.columns:
        df["avatar_url"] = None
    return df


@st.cache_data(ttl=60, show_spinner=False)
def cargar_super_bowl() -> pd.DataFrame:
    sb = get_supabase()
    res = (
        sb.table("super_bowl_predictions")
        .select("email, campeon, subcampeon")
        .limit(LIMITE)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    if df.empty:
        return pd.DataFrame(columns=["email", "campeon", "subcampeon"])
    df["email"] = df["email"].astype(str).str.lower().str.strip()
    return df


@st.cache_data(ttl=60, show_spinner=False)
def cargar_configuracion() -> dict:
    sb = get_supabase()
    res = (
        sb.table("tournament_settings")
        .select("id, actual_champion, actual_subcampeon")
        .order("id")
        .limit(LIMITE)
        .execute()
    )
    filas = res.data or []
    return filas[-1] if filas else {"id": 1, "actual_champion": None, "actual_subcampeon": None}


def invalidar_cache() -> None:
    """Se llama tras cada escritura para que la UI refleje el dato nuevo."""
    for fn in (
        cargar_partidos, cargar_predicciones, cargar_resultados,
        cargar_usuarios, cargar_super_bowl, cargar_configuracion,
    ):
        fn.clear()


def obtener_perfil(email: str) -> dict | None:
    """Perfil publico desde `users` (sin cache: se usa en el flujo de login)."""
    sb = get_supabase()
    email = email.lower().strip()
    try:
        res = (
            sb.table("users")
            .select("id, username, email, avatar_url")
            .eq("email", email).limit(1).execute()
        )
    except Exception as exc:
        if "avatar_url" not in str(exc):
            raise
        res = (
            sb.table("users").select("id, username, email")
            .eq("email", email).limit(1).execute()
        )
    return res.data[0] if res.data else None


def auth_uid() -> str | None:
    """
    Identificador del usuario en Supabase Auth. Nombra su archivo de avatar y
    es lo que valida la politica de Storage, asi que sin el no se puede subir.
    """
    try:
        usuario = get_supabase().auth.get_user()
        return getattr(getattr(usuario, "user", None), "id", None)
    except Exception:
        return None


def guardar_avatar_url(email: str, url: str | None) -> None:
    sb = get_supabase()
    sb.table("users").update({"avatar_url": url}).eq("email", email.lower().strip()).execute()
    invalidar_cache()


# --- Escrituras --------------------------------------------------------------
def guardar_pick(email: str, match_id, prediccion: str) -> None:
    """Inserta o actualiza el pick del usuario para un partido."""
    sb = get_supabase()
    email = email.lower().strip()
    if es_admin(email):   # el arbitro no compite
        return
    existente = (
        sb.table("predictions")
        .select("id")
        .eq("email", email)
        .eq("match_id", match_id)
        .limit(1)
        .execute()
    )
    if existente.data:
        sb.table("predictions").update({"prediction": prediccion}).eq(
            "id", existente.data[0]["id"]
        ).execute()
    else:
        sb.table("predictions").insert(
            {"email": email, "match_id": match_id, "prediction": prediccion}
        ).execute()
    invalidar_cache()


def guardar_resultado(match_id, local: int, visitante: int, ganador: str,
                      manual: bool = True) -> None:
    """
    Guarda un marcador. Por defecto marca `editado_manual = True`: si un humano
    lo capturo desde el Panel Admin, el sincronizador automatico debe respetarlo
    y no volver a pisarlo con lo que diga ESPN.
    """
    sb = get_supabase()
    payload = {
        "match_id": match_id,
        "marcador_local": int(local),
        "marcador_visitante": int(visitante),
        "ganador_oficial": ganador,
        "editado_manual": bool(manual),
    }
    existente = sb.table("results").select("match_id").eq("match_id", match_id).limit(1).execute()
    if existente.data:
        sb.table("results").update(payload).eq("match_id", match_id).execute()
    else:
        sb.table("results").insert(payload).execute()
    invalidar_cache()


def guardar_super_bowl_pick(email: str, campeon: str, subcampeon: str) -> None:
    sb = get_supabase()
    email = email.lower().strip()
    if es_admin(email):   # el arbitro no compite
        return
    payload = {"email": email, "campeon": campeon, "subcampeon": subcampeon}
    existente = sb.table("super_bowl_predictions").select("email").eq("email", email).limit(1).execute()
    if existente.data:
        sb.table("super_bowl_predictions").update(payload).eq("email", email).execute()
    else:
        sb.table("super_bowl_predictions").insert(payload).execute()
    invalidar_cache()


def guardar_configuracion(campeon: str | None, subcampeon: str | None) -> None:
    sb = get_supabase()
    cfg = cargar_configuracion()
    payload = {"actual_champion": campeon, "actual_subcampeon": subcampeon}
    if cfg.get("id"):
        sb.table("tournament_settings").update(payload).eq("id", cfg["id"]).execute()
    else:
        sb.table("tournament_settings").insert({"id": 1, **payload}).execute()
    invalidar_cache()


def set_bloqueo_partido(match_id, bloqueado: bool) -> None:
    sb = get_supabase()
    sb.table("matches").update({"bloqueado": bool(bloqueado)}).eq("id", match_id).execute()
    invalidar_cache()


# -----------------------------------------------------------------------------
#  7. UTILIDADES DE TIEMPO Y ZONAS HORARIAS
#     `matches.fecha_hora` se interpreta siempre como UTC y se convierte a la
#     zona horaria que el usuario elija en la barra lateral.
# -----------------------------------------------------------------------------
DIAS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def tz_usuario() -> ZoneInfo:
    nombre = st.session_state.get("tz", "America/Mexico_City")
    try:
        return ZoneInfo(nombre)
    except Exception:
        return ZoneInfo("UTC")


def a_local(dt_utc) -> datetime | None:
    if dt_utc is None or pd.isna(dt_utc):
        return None
    if isinstance(dt_utc, pd.Timestamp):
        dt_utc = dt_utc.to_pydatetime()
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(tz_usuario())


def formato_fecha(dt_utc) -> str:
    loc = a_local(dt_utc)
    if loc is None:
        return "Fecha por confirmar"
    return (
        f"{DIAS_ES[loc.weekday()]} {loc.day} {MESES_ES[loc.month - 1]} "
        f"{loc.year} - {loc.strftime('%I:%M %p').lstrip('0')}"
    )


def ya_inicio(dt_utc) -> bool:
    if dt_utc is None or pd.isna(dt_utc):
        return False
    if isinstance(dt_utc, pd.Timestamp):
        dt_utc = dt_utc.to_pydatetime()
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= dt_utc


def esta_bloqueado(fila) -> bool:
    """Bloqueo por bandera manual del admin O por kickoff ya alcanzado."""
    return bool(fila.get("bloqueado")) or ya_inicio(fila.get("fecha_hora"))


def etiqueta_semana(semana: int) -> str:
    return ETIQUETA_SEMANA.get(int(semana), f"Semana {int(semana)}")


def inicio_temporada(partidos: pd.DataFrame):
    """Kickoff del primer partido de la semana 1. None si no hay calendario."""
    if partidos.empty:
        return None
    semana1 = partidos[(partidos["semana"] == 1) & partidos["fecha_hora"].notna()]
    if semana1.empty:
        return None
    return semana1["fecha_hora"].min()


def super_bowl_cerrado(partidos: pd.DataFrame) -> bool:
    """
    Los pronosticos de Super Bowl se cierran con el primer kickoff de la
    temporada. Es una apuesta de pretemporada: si se pudiera ajustar despues,
    bastaria esperar a conocer a los finalistas para asegurar los 10 puntos.
    """
    arranque = inicio_temporada(partidos)
    return ya_inicio(arranque) if arranque is not None else False


# -----------------------------------------------------------------------------
#  8. MOTOR DE PUNTUACION
#     Regular : 1 punto por acertar al ganador directo (Moneyline, sin spread).
#     Empates : contemplados como opcion valida (raros pero posibles).
#     Super Bowl : 10 pts campeon + 5 pts subcampeon.
# -----------------------------------------------------------------------------
def ganador_por_marcador(local: int, visitante: int) -> str:
    if local > visitante:
        return PICK_LOCAL
    if visitante > local:
        return PICK_VISITANTE
    return PICK_EMPATE


def _normaliza_ganador(valor, fila_partido: dict | None = None) -> str | None:
    """
    Acepta que `ganador_oficial` venga como LOCAL/VISITANTE/EMPATE o como el
    nombre del equipo (p.ej. 'Kansas City Chiefs'), y lo lleva al valor canonico.
    """
    if valor is None or str(valor).strip() in ("", "None", "nan"):
        return None
    txt = str(valor).upper().strip()
    if txt in (PICK_LOCAL, PICK_VISITANTE, PICK_EMPATE):
        return txt
    if txt in ("EMPATE", "TIE", "DRAW", "T"):
        return PICK_EMPATE
    if txt in ("HOME", "H", "L"):
        return PICK_LOCAL
    if txt in ("AWAY", "A", "V"):
        return PICK_VISITANTE
    if fila_partido:
        abrev = resolver_equipo(valor)
        if abrev and abrev == resolver_equipo(fila_partido.get("equipo_local", "")):
            return PICK_LOCAL
        if abrev and abrev == resolver_equipo(fila_partido.get("equipo_visitante", "")):
            return PICK_VISITANTE
    return None


def mapa_ganadores(partidos: pd.DataFrame, resultados: pd.DataFrame) -> dict:
    """match_id -> ganador canonico (solo partidos ya cerrados)."""
    idx_partidos = {r["id"]: r for r in partidos.to_dict("records")}
    salida = {}
    for r in resultados.to_dict("records"):
        canon = _normaliza_ganador(r.get("ganador_oficial"), idx_partidos.get(r["match_id"]))
        if canon is None and r.get("marcador_local") is not None and r.get("marcador_visitante") is not None:
            canon = ganador_por_marcador(int(r["marcador_local"]), int(r["marcador_visitante"]))
        if canon:
            salida[r["match_id"]] = canon
    return salida


def calcular_ranking(
    usuarios: pd.DataFrame,
    predicciones: pd.DataFrame,
    partidos: pd.DataFrame,
    resultados: pd.DataFrame,
    sb_picks: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Tabla general: aciertos de temporada regular + bonus de Super Bowl."""
    # Red de seguridad: el admin nunca entra al ranking, aunque llegue sin filtrar.
    usuarios, predicciones, sb_picks = (
        sin_admin(usuarios), sin_admin(predicciones), sin_admin(sb_picks)
    )
    ganadores = mapa_ganadores(partidos, resultados)

    # Jornada de cada partido: define cuanto vale acertarlo.
    semana_de = {r["id"]: r["semana"] for r in partidos.to_dict("records")}

    puntos: dict[str, int] = defaultdict(int)     # suma ponderada por ronda
    aciertos: dict[str, int] = defaultdict(int)   # conteo simple, para efectividad
    jugados: dict[str, int] = defaultdict(int)
    for p in predicciones.to_dict("records"):
        real = ganadores.get(p["match_id"])
        if real is None:
            continue
        jugados[p["email"]] += 1
        if p["prediction"] == real:
            aciertos[p["email"]] += 1
            puntos[p["email"]] += puntos_de_semana(semana_de.get(p["match_id"], 1))

    campeon_real = resolver_equipo(config.get("actual_champion") or "")
    sub_real = resolver_equipo(config.get("actual_subcampeon") or "")
    bonus: dict[str, int] = defaultdict(int)
    for s in sb_picks.to_dict("records"):
        if campeon_real and resolver_equipo(s.get("campeon") or "") == campeon_real:
            bonus[s["email"]] += PUNTOS_CAMPEON
        if sub_real and resolver_equipo(s.get("subcampeon") or "") == sub_real:
            bonus[s["email"]] += PUNTOS_SUBCAMPEON

    # Universo de participantes: registrados + cualquiera con actividad
    emails = set(usuarios["email"].tolist()) | set(predicciones["email"].tolist()) | set(sb_picks["email"].tolist())
    nombres = dict(zip(usuarios["email"], usuarios["username"]))
    imagenes = (
        dict(zip(usuarios["email"], usuarios["avatar_url"]))
        if "avatar_url" in usuarios.columns else {}
    )

    filas = []
    total_picks = len(predicciones)
    for em in emails:
        pts_partidos = puntos.get(em, 0)
        n_aciertos = aciertos.get(em, 0)
        pts_sb = bonus.get(em, 0)
        cerrados = jugados.get(em, 0)
        nombre_part = nombres.get(em) or em.split("@")[0]
        filas.append(
            {
                "email": em,
                "Avatar": avatares.avatar_de(nombre_part, imagenes.get(em)),
                "Participante": nombre_part,
                "Aciertos": n_aciertos,
                "Puntos partidos": pts_partidos,
                "Calificados": cerrados,
                "Efectividad": round(100 * n_aciertos / cerrados, 1) if cerrados else 0.0,
                "Super Bowl": pts_sb,
                "Total": pts_partidos + pts_sb,
                "Picks": int((predicciones["email"] == em).sum()) if total_picks else 0,
            }
        )
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df = df.sort_values(["Total", "Efectividad", "Participante"], ascending=[False, False, True])
    df.insert(0, "Pos", range(1, len(df) + 1))
    return df.reset_index(drop=True)


def calcular_standings(partidos: pd.DataFrame, resultados: pd.DataFrame) -> pd.DataFrame:
    """
    Records W-L-T por equipo, derivados de `results`.

    SOLO cuenta temporada regular (semanas 1-18), igual que la tabla oficial de
    la NFL: los triunfos de playoffs no suman al record. Sin este filtro, al
    cargar la postemporada apareceria un 13-5 en una tabla que como maximo
    puede llegar a 17 juegos.
    """
    tabla = {
        abrev: dict(abrev=abrev, G=0, P=0, E=0, PF=0, PC=0, conf=i["conf"], div=i["div"])
        for abrev, i in NFL_TEAMS.items()
    }
    regulares = partidos[partidos["semana"] <= 18] if not partidos.empty else partidos
    idx = {r["id"]: r for r in regulares.to_dict("records")}

    for r in resultados.to_dict("records"):
        partido = idx.get(r["match_id"])
        if not partido:
            continue
        local = resolver_equipo(partido.get("equipo_local", ""))
        visita = resolver_equipo(partido.get("equipo_visitante", ""))
        if not local or not visita:
            continue
        ml, mv = r.get("marcador_local"), r.get("marcador_visitante")
        if ml is None or mv is None:
            continue
        ml, mv = int(ml), int(mv)
        tabla[local]["PF"] += ml; tabla[local]["PC"] += mv
        tabla[visita]["PF"] += mv; tabla[visita]["PC"] += ml
        if ml > mv:
            tabla[local]["G"] += 1; tabla[visita]["P"] += 1
        elif mv > ml:
            tabla[visita]["G"] += 1; tabla[local]["P"] += 1
        else:
            tabla[local]["E"] += 1; tabla[visita]["E"] += 1

    df = pd.DataFrame(tabla.values())
    jj = df["G"] + df["P"] + df["E"]
    df["JJ"] = jj
    df["PCT"] = ((df["G"] + 0.5 * df["E"]) / jj.replace(0, pd.NA)).fillna(0.0).round(3)
    df["DIF"] = df["PF"] - df["PC"]
    df["Equipo"] = df["abrev"].map(lambda a: f"{NFL_TEAMS[a]['ciudad']} {NFL_TEAMS[a]['apodo']}")
    df["Logo"] = df["abrev"].map(lambda a: LOGO_BASE.format(a.lower()))
    return df


# -----------------------------------------------------------------------------
#  9. COMPONENTES DE UI REUTILIZABLES
# -----------------------------------------------------------------------------
def bloque_equipo(nombre: str, subtitulo: str = "", alinear_derecha: bool = False) -> str:
    """
    HTML de logo + nombre. Los logos SIEMPRE son <img>, nunca emojis.

    El `onerror` degrada al PNG original del CDN si el redimensionador falla, y
    de ahi a un recuadro con la abreviatura del equipo. Asi la tarjeta nunca
    queda con el icono de imagen rota aunque el CDN deje de responder.
    """
    direccion = "row-reverse" if alinear_derecha else "row"
    alineado = "right" if alinear_derecha else "left"
    abrev = resolver_equipo(nombre)
    respaldo = LOGO_ORIGINAL.format(abrev.lower()) if abrev else LOGO_NFL
    sigla = abrev or "NFL"
    fallback = (
        "this.onerror=null;this.src='%s';"
        "this.onerror=function(){this.outerHTML='<div class=\\'logo-fallback\\'>%s</div>';};"
        % (respaldo, sigla)
    )
    return f"""
    <div class="team-row" style="flex-direction:{direccion};">
        <img src="{logo_url(nombre)}" alt="{nombre_display(nombre)}" onerror="{fallback}">
        <div style="text-align:{alineado};">
            <div class="team-name">{nombre_display(nombre)}</div>
            <div class="team-sub">{subtitulo}</div>
        </div>
    </div>
    """


def barra_votos(etiqueta: str, votos: int, total: int, color: str) -> str:
    pct = (votos / total * 100) if total else 0
    return f"""
    <div class="vote-label"><span>{etiqueta}</span><span>{votos} ({pct:.0f}%)</span></div>
    <div class="vote-track"><div class="vote-fill" style="width:{pct:.1f}%;background:{color};"></div></div>
    """


def panel_descansos(descansan: list[str]) -> None:
    """Franja con los logotipos de los equipos que tienen bye esta semana."""
    if not descansan:
        return
    tarjetas = "".join(
        f'<div class="bye-team">'
        f'<img src="{LOGO_BASE.format(a.lower())}" alt="{nombre_display(a)}">'
        f"<span>{nombre_display(a)}</span></div>"
        for a in descansan
    )
    st.markdown(
        f"""
        <div class="bye-box">
            <div class="bye-head">Descansan esta semana &bull; {len(descansan)} equipos</div>
            <div class="bye-list">{tarjetas}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def clase_posicion(pos: int) -> str:
    return {1: "r1", 2: "r2", 3: "r3"}.get(int(pos), "rn")


def panel_mi_posicion(email: str, ranking: pd.DataFrame) -> None:
    """Resumen del lugar que ocupa el usuario, al pie de sus picks."""
    st.divider()
    st.markdown("#### Mi posicion en el ranking global")

    if ranking.empty:
        st.caption("El ranking se activara cuando se capturen los primeros resultados.")
        return

    mio = ranking[ranking["email"] == email]
    if mio.empty:
        st.caption("Registra tus pronosticos para aparecer en el ranking.")
        return

    fila = mio.iloc[0]
    pos, total = int(fila["Pos"]), int(fila["Total"])
    lider = ranking.iloc[0]
    dif_lider = int(lider["Total"]) - total

    st.markdown(
        f"""
        <div class="match-card" style="display:flex;align-items:center;gap:18px;">
            <img src="{html_escape(str(fila['Avatar']), quote=True)}" class="avatar-fila" alt="">
            <div class="rank-chip {clase_posicion(pos)}" style="width:52px;height:52px;font-size:1.25rem;">
                {pos}
            </div>
            <div style="flex:1;">
                <div class="team-name">{html_escape(str(fila['Participante']))}</div>
                <div class="team-sub">
                    Lugar {pos} de {len(ranking)} &bull; {total} puntos &bull;
                    {fila['Efectividad']}% de efectividad
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Posicion", f"#{pos}", delta=None)
    c2.metric("Puntos totales", total)
    c3.metric("Aciertos", int(fila["Aciertos"]), help="Partidos acertados")
    if pos == 1:
        escolta = ranking.iloc[1] if len(ranking) > 1 else None
        ventaja = total - int(escolta["Total"]) if escolta is not None else 0
        c4.metric("Ventaja", f"+{ventaja}", help="Puntos sobre el segundo lugar")
    else:
        c4.metric("Diferencia con el lider", f"-{dif_lider}")

    # A quien tiene justo arriba: da contexto util cuando la tabla es larga
    if pos > 1:
        arriba = ranking.iloc[pos - 2]
        brecha = int(arriba["Total"]) - total
        if brecha == 0:
            st.caption(
                f"Estas empatado en puntos con **{arriba['Participante']}**, "
                "que aparece antes por mejor efectividad."
            )
        else:
            st.caption(
                f"A **{brecha}** punto{'s' if brecha != 1 else ''} de alcanzar a "
                f"**{arriba['Participante']}** (#{int(arriba['Pos'])}) &bull; "
                f"lider: **{lider['Participante']}** con {int(lider['Total'])}."
            )
    else:
        st.caption("Vas al frente de la tabla.")


def encabezado(usuario: dict | None = None) -> None:
    """
    Cabecera de la app. Con sesion iniciada saluda al participante por su
    nombre; en la pantalla de acceso muestra la descripcion del torneo.
    """
    st.markdown(CSS, unsafe_allow_html=True)

    if usuario:
        nombre = (usuario.get("username") or usuario.get("email", "").split("@")[0]).strip()
        subtitulo = f"Bienvenido <strong>{html_escape(nombre)}</strong>"
    else:
        subtitulo = "Pronosticos Moneyline &bull; Temporada Regular, Playoffs y Super Bowl LX"

    st.markdown(
        f"""
        <div class="nfl-hero">
            <img src="https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png" alt="NFL">
            <div>
                <h1>Quiniela NFL 2026</h1>
                <p>{subtitulo}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def tabla_logos(df: pd.DataFrame, columnas: dict, altura: int | None = None) -> None:
    """Renderiza un DataFrame con columna de logotipo usando column_config."""
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=altura,
        column_config=columnas,
    )


# -----------------------------------------------------------------------------
# 10. PANTALLA DE AUTENTICACION
#     Las credenciales viven en Supabase Auth; `users` guarda el perfil publico.
#     Todos los correos se normalizan con .lower() al registrar y al consultar.
# -----------------------------------------------------------------------------
def pantalla_auth() -> None:
    encabezado()
    st.markdown('<div class="auth-wrap">', unsafe_allow_html=True)
    tab_login, tab_registro = st.tabs(["Iniciar sesion", "Crear cuenta"])
    sb = get_supabase()

    # --- Login ---
    with tab_login:
        with st.form("form_login", clear_on_submit=False):
            email = st.text_input("Correo electronico", placeholder="tucorreo@dominio.com")
            password = st.text_input("Contrasena", type="password")
            enviar = st.form_submit_button("Entrar", use_container_width=True, type="primary")
        if enviar:
            email = (email or "").lower().strip()
            if not email or not password:
                st.warning("Captura tu correo y contrasena.")
            else:
                try:
                    sesion = sb.auth.sign_in_with_password({"email": email, "password": password})
                    perfil = obtener_perfil(email)
                    if perfil is None:
                        # Auto-reparacion: existe en Auth pero no tenia perfil publico
                        sb.table("users").insert(
                            {"username": email.split("@")[0], "email": email}
                        ).execute()
                        perfil = obtener_perfil(email)
                    refresh = getattr(getattr(sesion, "session", None), "refresh_token", None)
                    guardar_cookies(email, refresh)
                    st.session_state["usuario"] = perfil
                    invalidar_cache()
                    st.success("Bienvenido de vuelta.")
                    st.rerun()
                except Exception as exc:
                    # Mensaje generico a proposito: distinguir "no existe" de
                    # "contrasena incorrecta" permitiria averiguar que correos
                    # estan registrados probandolos uno por uno.
                    detalle = str(exc).lower()
                    if "email not confirmed" in detalle:
                        st.warning(
                            "Tu cuenta aun no esta confirmada. Revisa el correo "
                            "de verificacion que te enviamos (mira tambien la "
                            "carpeta de spam)."
                        )
                    else:
                        st.error("Correo o contrasena incorrectos.")

    # --- Registro ---
    with tab_registro:
        with st.form("form_registro", clear_on_submit=False):
            nuevo_user = st.text_input("Nombre de usuario", placeholder="Como te veran en el ranking")
            nuevo_email = st.text_input("Correo electronico", key="reg_mail")
            nueva_pass = st.text_input("Contrasena (min. 6 caracteres)", type="password", key="reg_pass")
            confirmar = st.text_input("Confirmar contrasena", type="password")
            crear = st.form_submit_button("Registrarme", use_container_width=True, type="primary")
        if crear:
            nuevo_email = (nuevo_email or "").lower().strip()   # regla: siempre minusculas
            nuevo_user = (nuevo_user or "").strip()
            if not nuevo_user or not nuevo_email or not nueva_pass:
                st.warning("Completa todos los campos.")
            elif nueva_pass != confirmar:
                st.warning("Las contrasenas no coinciden.")
            elif len(nueva_pass) < 6:
                st.warning("La contrasena debe tener al menos 6 caracteres.")
            elif obtener_perfil(nuevo_email):
                st.error("Ese correo ya esta registrado. Inicia sesion.")
            else:
                try:
                    sb.auth.sign_up({"email": nuevo_email, "password": nueva_pass})
                    sb.table("users").insert(
                        {"username": nuevo_user, "email": nuevo_email}
                    ).execute()
                    invalidar_cache()
                    st.success(
                        "Cuenta creada. Si tu proyecto exige confirmacion por correo, "
                        "revisa tu bandeja antes de entrar."
                    )
                except Exception as exc:
                    st.error(f"No fue posible registrar la cuenta: {exc}")

    st.markdown("</div>", unsafe_allow_html=True)


def panel_avatar(usuario: dict) -> None:
    """
    Configuracion del avatar, en tres niveles de esfuerzo creciente.

    El orden importa: quien no quiera complicarse nunca ve un selector de
    archivos, y quien quiera personalizarse tiene el camino abierto.
    """
    email = usuario["email"].lower()
    nombre = usuario.get("username") or email.split("@")[0]
    actual = usuario.get("avatar_url")

    c_img, c_txt = st.columns([1, 2])
    with c_img:
        st.image(avatares.avatar_de(nombre, actual), width=68)
    with c_txt:
        if not actual:
            st.caption("Avatar automatico con tus iniciales.")
        elif avatares.es_avatar_de_equipo(actual):
            st.caption(f"Logo de {nombre_display(avatares.es_avatar_de_equipo(actual))}.")
        else:
            st.caption("Foto personalizada.")

    with st.expander("Cambiar mi imagen"):
        # --- Nivel 2: logo de equipo (un clic, sin archivos) ---
        st.markdown("**Elegir el logo de un equipo**")
        abrevs = sorted(NFL_TEAMS.keys(), key=nombre_display)
        actual_equipo = avatares.es_avatar_de_equipo(actual)
        equipo = st.selectbox(
            "Equipo",
            abrevs,
            index=abrevs.index(actual_equipo) if actual_equipo in abrevs else 0,
            format_func=nombre_display,
            label_visibility="collapsed",
        )
        ce1, ce2 = st.columns([1, 3])
        with ce1:
            st.image(logo_url(equipo), width=44)
        with ce2:
            if st.button("Usar este logo", use_container_width=True):
                guardar_avatar_url(email, avatares.avatar_de_equipo(equipo))
                st.session_state["usuario"] = obtener_perfil(email)
                st.toast("Avatar actualizado.")
                st.rerun()

        st.divider()

        # --- Nivel 3: foto propia ---
        st.markdown("**O subir una foto**")
        st.caption(
            "Toma cualquier foto de tu galeria. La app la recorta y la reduce "
            "sola: no necesitas prepararla de ninguna forma."
        )
        archivo = st.file_uploader(
            "Foto",
            type=avatares.FORMATOS_ACEPTADOS,
            label_visibility="collapsed",
            key="subir_avatar",
        )
        if archivo is not None:
            datos = archivo.getvalue()
            if len(datos) > avatares.MAX_MB_ENTRADA * 1024 * 1024:
                st.error(
                    f"La imagen pesa {avatares.peso_legible(len(datos))} y el limite "
                    f"es {avatares.MAX_MB_ENTRADA} MB. Elige otra foto."
                )
            else:
                try:
                    vista = avatares.procesar_imagen(datos)
                except Exception as exc:
                    st.error(
                        "No se pudo leer esa imagen. Prueba con una foto en "
                        f"formato JPG o PNG. ({exc})"
                    )
                    vista = None

                if vista:
                    cv1, cv2 = st.columns([1, 2])
                    with cv1:
                        st.image(vista, width=88)
                    with cv2:
                        st.caption(
                            f"{avatares.peso_legible(len(datos))} "
                            f"→ {avatares.peso_legible(len(vista))}"
                        )
                    if st.button("Guardar esta foto", type="primary",
                                 use_container_width=True):
                        uid = auth_uid()
                        if not uid:
                            st.error(
                                "No se pudo verificar tu sesion. Cierra sesion, "
                                "vuelve a entrar e intentalo de nuevo."
                            )
                        else:
                            try:
                                url = avatares.subir_avatar(get_supabase(), uid, datos)
                                guardar_avatar_url(email, url)
                                st.session_state["usuario"] = obtener_perfil(email)
                                st.toast("Foto guardada.")
                                st.rerun()
                            except Exception as exc:
                                detalle = str(exc)
                                if "Bucket not found" in detalle:
                                    st.error(
                                        "Falta crear el almacenamiento de avatares. "
                                        "Ejecuta `sql/05_avatares.sql` en Supabase."
                                    )
                                else:
                                    st.error(f"No se pudo subir la foto: {detalle}")

        # --- Volver al avatar automatico ---
        if actual:
            st.divider()
            if st.button("Quitar imagen y usar mis iniciales", use_container_width=True):
                uid = auth_uid()
                if uid:
                    avatares.borrar_avatar(get_supabase(), uid)
                guardar_avatar_url(email, None)
                st.session_state["usuario"] = obtener_perfil(email)
                st.toast("Avatar restablecido.")
                st.rerun()


def barra_lateral(usuario: dict) -> None:
    with st.sidebar:
        st.markdown(
            f"### {html_escape(str(usuario.get('username', 'Participante')))}\n"
            f"<span style='opacity:.6;font-size:.85rem'>"
            f"{html_escape(str(usuario.get('email','')))}</span>",
            unsafe_allow_html=True,
        )
        panel_avatar(usuario)
        if es_admin(usuario.get("email")):
            st.caption("Sesion con privilegios de administrador")
        st.divider()

        # --- Zona horaria: rige TODA la conversion de fechas de la app ---
        st.markdown("**Zona horaria**")
        todas = sorted(available_timezones())
        opciones = ZONAS_SUGERIDAS + [z for z in todas if z not in ZONAS_SUGERIDAS]
        actual = st.session_state.get("tz", "America/Mexico_City")
        st.selectbox(
            "Selecciona tu zona",
            opciones,
            index=opciones.index(actual) if actual in opciones else 0,
            key="tz",
            label_visibility="collapsed",
        )
        ahora = datetime.now(tz_usuario())
        st.caption(f"Hora local: {ahora.strftime('%d/%m/%Y %I:%M %p')}")

        st.divider()
        if st.button("Actualizar datos", use_container_width=True):
            invalidar_cache()
            st.rerun()
        if st.button("Cerrar sesion", use_container_width=True):
            try:
                get_supabase().auth.sign_out()
            except Exception:
                pass
            limpiar_cookies()
            st.session_state.pop("usuario", None)
            st.rerun()


# -----------------------------------------------------------------------------
# 11. PESTANA: MIS PICKS
# -----------------------------------------------------------------------------
def tab_picks(usuario: dict, partidos: pd.DataFrame, predicciones: pd.DataFrame,
              resultados: pd.DataFrame, usuarios: pd.DataFrame,
              ranking: pd.DataFrame) -> None:
    st.subheader("Mis Picks")

    if partidos.empty:
        st.info("Aun no hay partidos cargados en la tabla `matches`.")
        return

    email = usuario["email"].lower()
    arbitro = es_admin(email)          # el admin observa, no vota
    nombres = dict(zip(usuarios["email"], usuarios["username"]))
    semanas_disponibles = sorted(partidos["semana"].unique().tolist())

    if arbitro:
        st.info(
            "Estas en modo arbitro: la cuenta administradora no participa en la "
            "quiniela, asi que no puede emitir pronosticos ni figura en el ranking. "
            "Puedes revisar los partidos y lo que voto la comunidad."
        )

    # --- Selector de semana (default: la semana en curso) ---
    pendientes = partidos[~partidos["fecha_hora"].isna()]
    proximos = pendientes[pendientes["fecha_hora"] > datetime.now(timezone.utc)]
    semana_default = int(proximos["semana"].min()) if not proximos.empty else int(semanas_disponibles[-1])

    col_sel, col_kpi1, col_kpi2, col_kpi3 = st.columns([2.2, 1, 1, 1])
    with col_sel:
        semana = st.selectbox(
            "Jornada",
            semanas_disponibles,
            index=semanas_disponibles.index(semana_default) if semana_default in semanas_disponibles else 0,
            format_func=etiqueta_semana,
        )

    de_semana = partidos[partidos["semana"] == semana].sort_values("fecha_hora")
    ids_semana = set(de_semana["id"].tolist())
    mis_picks = predicciones[predicciones["email"] == email]
    mis_picks_semana = mis_picks[mis_picks["match_id"].isin(ids_semana)]
    mapa_mis_picks = dict(zip(mis_picks["match_id"], mis_picks["prediction"]))

    ganadores = mapa_ganadores(partidos, resultados)
    aciertos_semana = sum(
        1 for mid in ids_semana
        if mid in ganadores and mapa_mis_picks.get(mid) == ganadores[mid]
    )

    col_kpi1.metric("Partidos", len(de_semana))
    if arbitro:
        col_kpi2.metric("Participantes", len(usuarios))
        col_kpi3.metric("Picks de la jornada", int(predicciones["match_id"].isin(ids_semana).sum()))
    else:
        col_kpi2.metric("Mis picks", f"{len(mis_picks_semana)}/{len(de_semana)}")
        col_kpi3.metric("Aciertos", aciertos_semana)

    # Equipos con bye: se muestran con su logotipo, nunca con emoji.
    panel_descansos(equipos_en_descanso(partidos, semana))

    # --- Conteo de votos de la comunidad por partido ---
    de_semana_preds = predicciones[predicciones["match_id"].isin(ids_semana)]
    votantes: dict = defaultdict(lambda: defaultdict(list))
    for p in de_semana_preds.to_dict("records"):
        etiqueta = nombres.get(p["email"]) or p["email"].split("@")[0]
        votantes[p["match_id"]][p["prediction"]].append(etiqueta)

    idx_result = {r["match_id"]: r for r in resultados.to_dict("records")}

    st.markdown("---")

    for partido in de_semana.to_dict("records"):
        mid = partido["id"]
        local, visita = partido["equipo_local"], partido["equipo_visitante"]
        bloqueado = esta_bloqueado(partido)
        resultado = idx_result.get(mid)
        ganador = ganadores.get(mid)
        mi_pick = mapa_mis_picks.get(mid)

        if ganador:
            estado = '<span class="badge badge-final">FINAL</span>'
        elif bloqueado:
            estado = '<span class="badge badge-locked">CERRADO</span>'
        else:
            estado = '<span class="badge badge-open">ABIERTO</span>'

        with st.container(border=True):
            st.markdown(
                f'<div class="match-meta"><span>{formato_fecha(partido["fecha_hora"])}</span>{estado}</div>',
                unsafe_allow_html=True,
            )

            c_loc, c_vs, c_vis = st.columns([4, 1.4, 4])
            sub_loc = "Local" if not resultado else f"Local &bull; {resultado.get('marcador_local', '-')}"
            sub_vis = "Visitante" if not resultado else f"Visitante &bull; {resultado.get('marcador_visitante', '-')}"
            with c_loc:
                st.markdown(bloque_equipo(local, sub_loc), unsafe_allow_html=True)
            with c_vs:
                if resultado:
                    st.markdown(
                        f'<div class="vs-pill">{resultado.get("marcador_local","-")}'
                        f' &nbsp;-&nbsp; {resultado.get("marcador_visitante","-")}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown('<div class="vs-pill">VS</div>', unsafe_allow_html=True)
            with c_vis:
                st.markdown(bloque_equipo(visita, sub_vis, alinear_derecha=True), unsafe_allow_html=True)

            # --- Formulario de voto ---
            opciones = [PICK_LOCAL, PICK_VISITANTE, PICK_EMPATE]
            etiquetas = {
                PICK_LOCAL: nombre_corto(local),
                PICK_VISITANTE: nombre_corto(visita),
                PICK_EMPATE: "Empate",
            }
            if arbitro:
                if ganador:
                    st.caption(f"Ganador oficial: **{etiquetas.get(ganador, ganador)}**")
            elif bloqueado:
                if mi_pick:
                    icono = ""
                    if ganador:
                        icono = " (acierto +1)" if mi_pick == ganador else " (sin puntos)"
                    st.info(f"Tu pick: **{etiquetas.get(mi_pick, mi_pick)}**{icono}")
                else:
                    st.warning("Voto cerrado: no registraste pronostico para este partido.")
            else:
                seleccion = st.radio(
                    "Tu pronostico",
                    opciones,
                    index=opciones.index(mi_pick) if mi_pick in opciones else None,
                    format_func=lambda o: etiquetas[o],
                    horizontal=True,
                    key=f"pick_{mid}",
                )
                if seleccion and seleccion != mi_pick:
                    guardar_pick(email, mid, seleccion)
                    st.toast(f"Pick guardado: {etiquetas[seleccion]}")
                    # Sin st.rerun(): se actualizan los conteos en memoria para
                    # que las barras reflejen el voto de inmediato. Un rerun aqui
                    # devolveria la vista a la primera seccion en cada pick.
                    mi_etiqueta = nombres.get(email) or email.split("@")[0]
                    if mi_pick and mi_etiqueta in votantes[mid][mi_pick]:
                        votantes[mid][mi_pick].remove(mi_etiqueta)
                    votantes[mid][seleccion].append(mi_etiqueta)
                    mapa_mis_picks[mid] = seleccion
                    mi_pick = seleccion

            # --- Estadisticas de la comunidad ---
            conteo = votantes.get(mid, {})
            total_votos = sum(len(v) for v in conteo.values())
            b1, b2, b3 = st.columns(3)
            for col, clave, color in (
                (b1, PICK_LOCAL, color_equipo(local)),
                (b2, PICK_EMPATE, "#6B7280"),
                (b3, PICK_VISITANTE, color_equipo(visita)),
            ):
                lista = conteo.get(clave, [])
                with col:
                    st.markdown(
                        barra_votos(etiquetas[clave], len(lista), total_votos, color),
                        unsafe_allow_html=True,
                    )

            with st.expander(f"Quien voto que ({total_votos} votos)"):
                if total_votos == 0:
                    st.caption("Sin votos registrados todavia.")
                else:
                    e1, e2, e3 = st.columns(3)
                    for col, clave in ((e1, PICK_LOCAL), (e2, PICK_EMPATE), (e3, PICK_VISITANTE)):
                        with col:
                            lista = sorted(conteo.get(clave, []), key=str.lower)
                            st.markdown(f"**{etiquetas[clave]}** ({len(lista)})")
                            if lista:
                                st.markdown("\n".join(f"- {n}" for n in lista))
                            else:
                                st.caption("Nadie")

    # --- Pie: lugar del usuario en el ranking global ---
    # El arbitro no compite, asi que ve el podio en lugar de una posicion propia.
    if arbitro:
        st.divider()
        st.markdown("#### Lideres del ranking")
        if ranking.empty:
            st.caption("Aun no hay participantes con puntos.")
        else:
            for _, f in ranking.head(3).iterrows():
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">'
                    f'<img src="{html_escape(str(f["Avatar"]), quote=True)}" '
                    f'class="avatar-fila" alt="">'
                    f'<div class="rank-chip {clase_posicion(f["Pos"])}">{int(f["Pos"])}</div>'
                    f'<span class="team-name">{html_escape(str(f["Participante"]))}</span>'
                    f'<span class="team-sub">{int(f["Total"])} puntos</span></div>',
                    unsafe_allow_html=True,
                )
    else:
        panel_mi_posicion(email, ranking)


# -----------------------------------------------------------------------------
# 12. PESTANA: POSICIONES NFL
# -----------------------------------------------------------------------------
def tab_posiciones(partidos: pd.DataFrame, resultados: pd.DataFrame) -> None:
    st.subheader("Posiciones NFL")
    st.caption(
        "Records calculados en vivo a partir de los marcadores capturados en `results`. "
        "PCT considera los empates como medio triunfo."
    )

    standings = calcular_standings(partidos, resultados)
    if standings["JJ"].sum() == 0:
        st.info("Todavia no hay marcadores cargados: las tablas se llenaran conforme avance la temporada.")

    col_afc, col_nfc = st.columns(2)
    for col, conf in ((col_afc, "AFC"), (col_nfc, "NFC")):
        color = COLOR_CONFERENCIA[conf]
        with col:
            st.markdown(
                f'<div class="conf-head">'
                f'<span class="conf-sigla" style="background:{color};">{conf}</span>'
                f'<span class="conf-linea" style="background:{color};"></span>'
                f"</div>",
                unsafe_allow_html=True,
            )
            for division in DIVISIONES:
                st.markdown(
                    f'<div class="div-title" style="border-left-color:{color};">'
                    f"{conf} {division}</div>",
                    unsafe_allow_html=True,
                )
                sub = standings[(standings["conf"] == conf) & (standings["div"] == division)]
                sub = sub.sort_values(["PCT", "DIF", "PF"], ascending=False)
                vista = sub[["Logo", "Equipo", "G", "P", "E", "PCT", "PF", "PC", "DIF"]]
                tabla_logos(
                    vista,
                    columnas={
                        "Logo": st.column_config.ImageColumn("", width="small"),
                        "Equipo": st.column_config.TextColumn("Equipo", width="medium"),
                        "G": st.column_config.NumberColumn("G", help="Ganados", width="small"),
                        "P": st.column_config.NumberColumn("P", help="Perdidos", width="small"),
                        "E": st.column_config.NumberColumn("E", help="Empates", width="small"),
                        "PCT": st.column_config.NumberColumn("PCT", format="%.3f", width="small"),
                        "PF": st.column_config.NumberColumn("PF", help="Puntos a favor", width="small"),
                        "PC": st.column_config.NumberColumn("PC", help="Puntos en contra", width="small"),
                        "DIF": st.column_config.NumberColumn("DIF", help="Diferencial", width="small"),
                    },
                    altura=178,
                )

    with st.expander("Ver conferencia completa (seeding)"):
        c1, c2 = st.columns(2)
        for col, conf in ((c1, "AFC"), (c2, "NFC")):
            with col:
                st.markdown(
                    f'<div class="conf-head">'
                    f'<span class="conf-sigla" style="background:{COLOR_CONFERENCIA[conf]};'
                    f'font-size:1.3rem;padding:4px 12px;">{conf}</span>'
                    f'<span class="conf-linea" style="background:{COLOR_CONFERENCIA[conf]};"></span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                sub = standings[standings["conf"] == conf].sort_values(
                    ["PCT", "DIF", "PF"], ascending=False
                )
                sub = sub[["Logo", "Equipo", "div", "G", "P", "E", "PCT", "DIF"]]
                tabla_logos(
                    sub,
                    columnas={
                        "Logo": st.column_config.ImageColumn("", width="small"),
                        "div": st.column_config.TextColumn("Division", width="small"),
                        "PCT": st.column_config.NumberColumn("PCT", format="%.3f"),
                    },
                    altura=600,
                )


# -----------------------------------------------------------------------------
# 13. PESTANA: SUPER BOWL
# -----------------------------------------------------------------------------
def formulario_super_bowl(email: str, actual: dict, temporada_cerrada: bool) -> None:
    """Selector de campeon y subcampeon. Solo para participantes."""
    st.markdown("#### Mi pronostico")

    abrev_previo = resolver_equipo(actual.get("campeon", "")) or ""
    conf_previa = NFL_TEAMS.get(abrev_previo, {}).get("conf", "AFC")
    conf_campeon = st.radio(
        "Conferencia del campeon",
        CONFERENCIAS,
        horizontal=True,
        index=CONFERENCIAS.index(conf_previa),
        disabled=temporada_cerrada,
    )
    conf_sub = "NFC" if conf_campeon == "AFC" else "AFC"

    ops_campeon = sorted(equipos_por_conferencia(conf_campeon), key=nombre_display)
    ops_sub = sorted(equipos_por_conferencia(conf_sub), key=nombre_display)

    prev_camp = abrev_previo or ops_campeon[0]
    prev_sub = resolver_equipo(actual.get("subcampeon", "")) or ops_sub[0]

    campeon = st.selectbox(
        f"Campeon ({conf_campeon})",
        ops_campeon,
        index=ops_campeon.index(prev_camp) if prev_camp in ops_campeon else 0,
        format_func=nombre_display,
        disabled=temporada_cerrada,
    )
    subcampeon = st.selectbox(
        f"Subcampeon ({conf_sub})",
        ops_sub,
        index=ops_sub.index(prev_sub) if prev_sub in ops_sub else 0,
        format_func=nombre_display,
        disabled=temporada_cerrada,
    )
    p1, p2 = st.columns(2)
    with p1:
        st.markdown(bloque_equipo(campeon, "Mi campeon"), unsafe_allow_html=True)
    with p2:
        st.markdown(bloque_equipo(subcampeon, "Mi subcampeon"), unsafe_allow_html=True)

    if st.button(
        "Guardar pronostico", type="primary", use_container_width=True,
        disabled=temporada_cerrada,
    ):
        guardar_super_bowl_pick(
            email, nombre_display(campeon), nombre_display(subcampeon)
        )
        st.success("Pronostico de Super Bowl guardado.")
        st.rerun()

    if temporada_cerrada:
        st.info(
            "Los pronosticos de Super Bowl estan cerrados: la temporada ya comenzo."
        )


def tab_super_bowl(usuario: dict, sb_picks: pd.DataFrame, usuarios: pd.DataFrame,
                   config: dict, partidos: pd.DataFrame) -> None:
    st.subheader("Super Bowl LX")
    st.caption(
        f"Campeon acertado = {PUNTOS_CAMPEON} puntos &bull; Subcampeon acertado = "
        f"{PUNTOS_SUBCAMPEON} puntos. El campeon debe ser de una conferencia y el "
        "subcampeon de la otra. Los pronosticos se cierran con el primer kickoff "
        "de la temporada."
    )

    email = usuario["email"].lower()
    arbitro = es_admin(email)
    nombres = dict(zip(usuarios["email"], usuarios["username"]))
    mio = sb_picks[sb_picks["email"] == email]
    actual = mio.to_dict("records")[0] if not mio.empty else {}

    campeon_oficial = config.get("actual_champion")
    sub_oficial = config.get("actual_subcampeon")
    ya_arranco = super_bowl_cerrado(partidos)
    temporada_cerrada = bool(campeon_oficial) or ya_arranco

    # Aviso de la fecha limite mientras siga abierto
    arranque = inicio_temporada(partidos)
    if not temporada_cerrada and arranque is not None:
        st.warning(
            f"Tienes hasta el primer kickoff de la temporada para definir tu "
            f"pronostico: **{formato_fecha(arranque)}**. Despues ya no podras cambiarlo."
        )

    if temporada_cerrada:
        st.success(
            f"Resultado oficial declarado: campeon **{nombre_display(campeon_oficial)}**"
            + (f", subcampeon **{nombre_display(sub_oficial)}**." if sub_oficial else ".")
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(bloque_equipo(campeon_oficial, "Campeon"), unsafe_allow_html=True)
        with c2:
            if sub_oficial:
                st.markdown(bloque_equipo(sub_oficial, "Subcampeon"), unsafe_allow_html=True)

    st.markdown("---")

    # El admin arbitra: ve las estadisticas de la comunidad pero no pronostica.
    if arbitro:
        st.info(
            "Modo arbitro: la cuenta administradora no pronostica el Super Bowl. "
            "El campeon y subcampeon oficiales se declaran desde el Panel Admin."
        )
        col_stats = st.container()
    else:
        col_form, col_stats = st.columns([1, 1.15])
        with col_form:
            formulario_super_bowl(email, actual, temporada_cerrada)

    # --- Estadisticas de la comunidad ---
    with col_stats:
        st.markdown("#### Quien voto por quien")
        if sb_picks.empty:
            st.caption("Nadie ha registrado su pronostico de Super Bowl.")
            return

        def agrupar(columna: str) -> dict[str, list[str]]:
            grupos: dict[str, list[str]] = defaultdict(list)
            for fila in sb_picks.to_dict("records"):
                valor = fila.get(columna)
                if not valor:
                    continue
                clave = resolver_equipo(valor) or str(valor)
                grupos[clave].append(nombres.get(fila["email"]) or fila["email"].split("@")[0])
            return grupos

        for titulo, columna in (("Campeon", "campeon"), ("Subcampeon", "subcampeon")):
            grupos = agrupar(columna)
            total = sum(len(v) for v in grupos.values())
            st.markdown(f"**{titulo}** &bull; {total} votos", unsafe_allow_html=True)
            for clave, gente in sorted(grupos.items(), key=lambda kv: -len(kv[1])):
                c_logo, c_barra = st.columns([1, 7])
                with c_logo:
                    st.image(logo_url(clave), width=34)
                with c_barra:
                    st.markdown(
                        barra_votos(nombre_display(clave), len(gente), total, color_equipo(clave)),
                        unsafe_allow_html=True,
                    )
                    with st.expander(f"Ver {len(gente)} votante(s)"):
                        st.markdown("\n".join(f"- {n}" for n in sorted(gente, key=str.lower)))
            st.markdown("---")


# -----------------------------------------------------------------------------
# 14. PESTANA: RANKING GLOBAL
# -----------------------------------------------------------------------------
def tab_ranking(usuario: dict, ranking: pd.DataFrame) -> None:
    st.subheader("Ranking Global")

    if ranking.empty:
        st.info("El ranking aparecera cuando existan participantes y resultados cargados.")
        return

    email = usuario["email"].lower()
    mio = ranking[ranking["email"] == email]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Participantes", len(ranking))
    if es_admin(email):
        k2.metric("Lider", ranking.iloc[0]["Participante"])
        k3.metric("Puntos del lider", int(ranking.iloc[0]["Total"]))
        k4.metric("Picks totales", int(ranking["Picks"].sum()))
        st.caption("La cuenta administradora arbitra y no aparece en la tabla.")
    elif not mio.empty:
        fila = mio.iloc[0]
        k2.metric("Mi posicion", f"#{int(fila['Pos'])}")
        k3.metric("Mis puntos", int(fila["Total"]))
        k4.metric("Mi efectividad", f"{fila['Efectividad']}%")

    # --- Podio ---
    top = ranking.head(3)
    if len(top) >= 3:
        st.markdown("#### Podio")
        cols = st.columns(3)
        for col, (_, fila) in zip(cols, top.iterrows()):
            with col:
                clase = {1: "r1", 2: "r2", 3: "r3"}[int(fila["Pos"])]
                st.markdown(
                    f"""
                    <div class="match-card" style="text-align:center;">
                        <img src="{html_escape(str(fila['Avatar']), quote=True)}" class="avatar-podio" alt="">
                        <div class="rank-chip {clase}" style="margin:-22px auto 8px;">{int(fila['Pos'])}</div>
                        <div class="team-name">{html_escape(str(fila['Participante']))}</div>
                        <div class="team-sub">{int(fila['Total'])} puntos &bull; {fila['Efectividad']}%</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.markdown("#### Tabla general")
    vista = ranking[
        ["Pos", "Avatar", "Participante", "Picks", "Calificados", "Aciertos",
         "Puntos partidos", "Super Bowl", "Total", "Efectividad"]
    ].copy()
    st.dataframe(
        vista,
        use_container_width=True,
        hide_index=True,
        height=min(720, 42 * (len(vista) + 1) + 8),
        column_config={
            "Pos": st.column_config.NumberColumn("#", width="small"),
            "Avatar": st.column_config.ImageColumn("", width="small"),
            "Picks": st.column_config.NumberColumn("Picks", help="Pronosticos registrados"),
            "Calificados": st.column_config.NumberColumn("Cerrados", help="Partidos ya con resultado"),
            "Aciertos": st.column_config.NumberColumn("Aciertos", help="Partidos acertados"),
            "Puntos partidos": st.column_config.NumberColumn(
                "Pts partidos",
                help="1 pt en temporada regular; 2 Comodines, 3 Divisional, "
                     "4 Final de Conferencia, 5 Super Bowl",
            ),
            "Super Bowl": st.column_config.NumberColumn("Bonus SB", help="10 campeon / 5 subcampeon"),
            "Total": st.column_config.ProgressColumn(
                "Total",
                format="%d",
                min_value=0,
                max_value=int(max(1, ranking["Total"].max())),
            ),
            "Efectividad": st.column_config.NumberColumn("Efectividad", format="%.1f%%"),
        },
    )

    st.download_button(
        "Descargar ranking (CSV)",
        # Sin la columna de imagen: en un CSV solo seria una URL larga inutil.
        vista.drop(columns=["Avatar"]).to_csv(index=False).encode("utf-8-sig"),
        file_name="ranking_quiniela_nfl_2026.csv",
        mime="text/csv",
    )

    with st.expander("Como se otorgan los puntos"):
        st.markdown(
            f"""
            **Partidos** &mdash; se acierta eligiendo al ganador directo
            (Moneyline, sin spread). El empate cuenta como opcion valida.

            | Jornada | Puntos por acierto |
            |---|---|
            | Semanas 1 a 18 | {PUNTOS_ACIERTO_REGULAR} |
            | Comodines | {PUNTOS_POR_RONDA[19]} |
            | Divisional | {PUNTOS_POR_RONDA[20]} |
            | Final de Conferencia | {PUNTOS_POR_RONDA[21]} |
            | Super Bowl | {PUNTOS_POR_RONDA[22]} |

            Las rondas finales valen mas para que la quiniela siga viva en enero
            en vez de quedar decidida en noviembre.

            **Super Bowl** &mdash; aparte del partido, el pronostico de
            pretemporada otorga **{PUNTOS_CAMPEON} puntos** por acertar al campeon
            y **{PUNTOS_SUBCAMPEON}** por el subcampeon. Se define antes del primer
            kickoff y despues ya no puede cambiarse.

            **Efectividad** &mdash; porcentaje de aciertos sobre partidos ya
            cerrados. No pondera por ronda: sirve para comparar puntería entre
            participantes que hayan jugado distinta cantidad de partidos.
            """
        )


# -----------------------------------------------------------------------------
# 15. PESTANA: PANEL ADMIN
#     Solo visible si el correo de la sesion coincide con secrets["ADMIN_EMAIL"].
# -----------------------------------------------------------------------------
def panel_sincronizacion(partidos: pd.DataFrame, resultados: pd.DataFrame) -> None:
    """
    Trae marcadores finales desde ESPN. Comparte motor con el cron de GitHub
    Actions, asi que lo que se pruebe aqui es exactamente lo que corre solo.
    """
    st.caption(
        "Descarga los marcadores de partidos ya finalizados y actualiza `results`. "
        "Los resultados que hayas capturado o corregido a mano quedan protegidos: "
        "la sincronizacion nunca los sobrescribe."
    )

    if "editado_manual" not in resultados.columns and not resultados.empty:
        st.error(
            "Falta la columna `editado_manual` en la tabla `results`. "
            "Ejecuta `sql/03_sincronizacion.sql` en el SQL Editor antes de sincronizar."
        )
        return

    semanas = sorted(partidos["semana"].unique().tolist())
    alcance = st.radio(
        "Que sincronizar",
        ["Jornadas recientes", "Una jornada", "Un partido", "Toda la temporada"],
        horizontal=True,
        key="sync_alcance",
    )

    elegidas = None
    solo_ids = None

    if alcance == "Jornadas recientes":
        st.caption(
            "Automatico: busca las jornadas que tuvieron partidos en los ultimos "
            "4 dias y revisa solo esas. Es lo que ejecuta el cron de GitHub."
        )
    elif alcance == "Una jornada":
        elegidas = [
            st.selectbox("Jornada", semanas, format_func=etiqueta_semana, key="sync_semana")
        ]
        st.caption("Revisa todos los partidos de la jornada que elijas, sin importar la fecha.")
    elif alcance == "Un partido":
        c1, c2 = st.columns([1, 2])
        with c1:
            semana_p = st.selectbox(
                "Jornada", semanas, format_func=etiqueta_semana, key="sync_semana_partido"
            )
        de_semana = partidos[partidos["semana"] == semana_p].sort_values("fecha_hora")
        opciones = de_semana["id"].tolist()
        rotulos = {
            r["id"]: f"{nombre_corto(r['equipo_local'])} vs {nombre_corto(r['equipo_visitante'])}"
            for r in de_semana.to_dict("records")
        }
        with c2:
            if opciones:
                elegido = st.selectbox(
                    "Partido", opciones,
                    format_func=lambda i: rotulos.get(i, str(i)),
                    key="sync_partido",
                )
                solo_ids = [elegido]
                elegidas = [semana_p]
            else:
                st.caption("Esta jornada no tiene partidos cargados.")
        st.caption("Solo se escribe ese partido; los demas de la jornada no se tocan.")
    else:
        elegidas = semanas
        st.caption("Barre las 18 jornadas mas los playoffs. Tarda mas, pero no omite nada.")

    simular = st.checkbox(
        "Solo simular (no escribe nada)", value=False,
        help="Util para ver que cambiaria antes de aplicarlo.",
    )

    if st.button("Sincronizar desde ESPN", type="primary", use_container_width=True):
        with st.spinner("Consultando ESPN..."):
            try:
                from sincronizador import sincronizar
                resumen = sincronizar(
                    get_supabase(), semanas=elegidas, simular=simular, solo_ids=solo_ids
                )
            except Exception as exc:
                st.error(f"La sincronizacion fallo: {exc}")
                return

        if not simular:
            invalidar_cache()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Nuevos", len(resumen["nuevos"]))
        m2.metric("Actualizados", len(resumen["actualizados"]))
        m3.metric("Sin cambio", resumen["sin_cambio"])
        m4.metric("Protegidos", len(resumen["protegidos"]))

        if simular:
            st.info("Simulacion: no se escribio nada en la base de datos.")

        if resumen["semanas"]:
            st.caption(
                "Jornadas revisadas: "
                + ", ".join(etiqueta_semana(s) for s in resumen["semanas"])
            )
        else:
            st.info(
                "No hay jornadas con partidos en los ultimos 4 dias. "
                "Elige una jornada concreta si quieres forzar la revision."
            )

        for titulo, clave, tipo in (
            ("Resultados nuevos", "nuevos", "success"),
            ("Marcadores actualizados", "actualizados", "warning"),
            ("Protegidos por edicion manual", "protegidos", "info"),
            ("Errores", "errores", "error"),
        ):
            if resumen[clave]:
                with st.expander(f"{titulo} ({len(resumen[clave])})", expanded=tipo == "error"):
                    for linea in resumen[clave]:
                        st.markdown(f"- {linea}")

        if not simular and (resumen["nuevos"] or resumen["actualizados"]):
            st.success("Ranking y posiciones actualizados.")

    # --- Estado de las capturas ---
    st.divider()
    st.markdown("##### Origen de los marcadores capturados")
    if resultados.empty:
        st.caption("Todavia no hay resultados en la base.")
        return

    manuales = int(resultados.get("editado_manual", pd.Series(dtype=bool)).fillna(False).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Total con resultado", len(resultados))
    c2.metric("Desde ESPN", len(resultados) - manuales)
    c3.metric("Editados a mano", manuales)
    if manuales:
        st.caption(
            "Los marcadores editados a mano no se actualizan solos. Si quieres "
            "devolverle el control a ESPN, vuelve a guardarlos desde la pestana "
            "Marcadores y luego ejecuta: "
            "`update public.results set editado_manual = false where match_id = ...`"
        )


def tab_admin(partidos: pd.DataFrame, resultados: pd.DataFrame,
              predicciones: pd.DataFrame, config: dict) -> None:
    st.subheader("Panel de Administracion")
    st.caption("Captura de marcadores oficiales, control de bloqueos y cierre de temporada.")

    if partidos.empty:
        st.warning("No hay partidos en la tabla `matches`.")
        return

    # Mismo motivo que en la navegacion principal: con st.tabs, cada st.rerun()
    # (guardar un marcador, mover un bloqueo) devolvia el panel a "Sincronizar".
    SECCIONES = ["Sincronizar", "Marcadores", "Bloqueos", "Cierre de temporada",
                 "Avatares", "Diagnostico"]
    if st.session_state.get("nav_admin") not in SECCIONES:
        st.session_state["nav_admin"] = SECCIONES[0]
    sub = st.segmented_control(
        "Seccion admin", SECCIONES, key="nav_admin", label_visibility="collapsed"
    )
    if sub is None:
        sub = st.session_state["nav_admin"] = SECCIONES[0]
    st.divider()

    idx_result = {r["match_id"]: r for r in resultados.to_dict("records")}

    # --- Sincronizacion automatica desde ESPN ---------------------------------
    if sub == "Sincronizar":
        panel_sincronizacion(partidos, resultados)

    # --- Captura de marcadores -----------------------------------------------
    if sub == "Marcadores":
        semanas = sorted(partidos["semana"].unique().tolist())
        semana = st.selectbox(
            "Jornada a capturar", semanas, format_func=etiqueta_semana, key="admin_semana"
        )
        de_semana = partidos[partidos["semana"] == semana].sort_values("fecha_hora")
        solo_pendientes = st.checkbox("Mostrar solo partidos sin resultado", value=False)

        for partido in de_semana.to_dict("records"):
            mid = partido["id"]
            previo = idx_result.get(mid)
            if solo_pendientes and previo:
                continue

            with st.container(border=True):
                st.markdown(
                    f'<div class="match-meta"><span>{formato_fecha(partido["fecha_hora"])}</span>'
                    f'<span>ID {mid}</span></div>',
                    unsafe_allow_html=True,
                )
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 3, 1.6])
                with c1:
                    st.markdown(bloque_equipo(partido["equipo_local"], "Local"), unsafe_allow_html=True)
                with c2:
                    ml = st.number_input(
                        "Local", min_value=0, max_value=120, step=1,
                        value=int(previo["marcador_local"]) if previo and previo.get("marcador_local") is not None else 0,
                        key=f"ml_{mid}", label_visibility="collapsed",
                    )
                with c3:
                    mv = st.number_input(
                        "Visita", min_value=0, max_value=120, step=1,
                        value=int(previo["marcador_visitante"]) if previo and previo.get("marcador_visitante") is not None else 0,
                        key=f"mv_{mid}", label_visibility="collapsed",
                    )
                with c4:
                    st.markdown(
                        bloque_equipo(partido["equipo_visitante"], "Visitante", alinear_derecha=True),
                        unsafe_allow_html=True,
                    )
                with c5:
                    sugerido = ganador_por_marcador(ml, mv)
                    st.caption(
                        {
                            PICK_LOCAL: nombre_corto(partido["equipo_local"]),
                            PICK_VISITANTE: nombre_corto(partido["equipo_visitante"]),
                            PICK_EMPATE: "Empate",
                        }[sugerido]
                    )
                    if st.button("Guardar", key=f"save_{mid}", use_container_width=True, type="primary"):
                        guardar_resultado(mid, ml, mv, sugerido)
                        st.success(f"Resultado del partido {mid} registrado.")
                        st.rerun()

                if previo:
                    canon = _normaliza_ganador(previo.get("ganador_oficial"), partido)
                    etiqueta = {
                        PICK_LOCAL: nombre_display(partido["equipo_local"]),
                        PICK_VISITANTE: nombre_display(partido["equipo_visitante"]),
                        PICK_EMPATE: "Empate",
                    }.get(canon, str(previo.get("ganador_oficial")))
                    st.caption(
                        f"Registrado: {previo.get('marcador_local')} - "
                        f"{previo.get('marcador_visitante')} &bull; Ganador oficial: {etiqueta}"
                    )

    # --- Bloqueos manuales ----------------------------------------------------
    if sub == "Bloqueos":
        st.caption(
            "El voto se cierra automaticamente al llegar la hora de inicio. "
            "Aqui puedes forzar el bloqueo o reabrir un partido."
        )
        semana_b = st.selectbox(
            "Jornada", sorted(partidos["semana"].unique().tolist()),
            format_func=etiqueta_semana, key="admin_bloq_semana",
        )
        de_semana_b = partidos[partidos["semana"] == semana_b].sort_values("fecha_hora")

        cb1, cb2 = st.columns(2)
        if cb1.button("Bloquear toda la jornada", use_container_width=True):
            for mid in de_semana_b["id"]:
                set_bloqueo_partido(mid, True)
            st.success("Jornada bloqueada.")
            st.rerun()
        if cb2.button("Reabrir toda la jornada", use_container_width=True):
            for mid in de_semana_b["id"]:
                set_bloqueo_partido(mid, False)
            st.success("Jornada reabierta.")
            st.rerun()

        for partido in de_semana_b.to_dict("records"):
            mid = partido["id"]
            c1, c2, c3 = st.columns([5, 3, 2])
            c1.markdown(
                f"**{nombre_display(partido['equipo_local'])}** vs "
                f"**{nombre_display(partido['equipo_visitante'])}**"
            )
            c2.caption(formato_fecha(partido["fecha_hora"]))
            nuevo = c3.toggle(
                "Bloqueado", value=bool(partido["bloqueado"]), key=f"tg_{mid}",
                label_visibility="collapsed",
            )
            if nuevo != bool(partido["bloqueado"]):
                set_bloqueo_partido(mid, nuevo)
                st.rerun()

    # --- Cierre de temporada / Super Bowl -------------------------------------
    if sub == "Cierre de temporada":
        st.caption(
            f"Al declarar al campeon oficial se otorgan {PUNTOS_CAMPEON} puntos a quienes "
            f"lo acertaron y {PUNTOS_SUBCAMPEON} a quienes acertaron el subcampeon."
        )
        abrevs = sorted(NFL_TEAMS.keys(), key=nombre_display)
        actual_camp = resolver_equipo(config.get("actual_champion") or "")
        actual_sub = resolver_equipo(config.get("actual_subcampeon") or "")

        c1, c2 = st.columns(2)
        with c1:
            campeon = st.selectbox(
                "Campeon oficial", ["(sin declarar)"] + abrevs,
                index=(abrevs.index(actual_camp) + 1) if actual_camp in abrevs else 0,
                format_func=lambda a: a if a == "(sin declarar)" else nombre_display(a),
            )
            if campeon != "(sin declarar)":
                st.markdown(bloque_equipo(campeon, "Campeon"), unsafe_allow_html=True)
        with c2:
            sub = st.selectbox(
                "Subcampeon oficial", ["(sin declarar)"] + abrevs,
                index=(abrevs.index(actual_sub) + 1) if actual_sub in abrevs else 0,
                format_func=lambda a: a if a == "(sin declarar)" else nombre_display(a),
            )
            if sub != "(sin declarar)":
                st.markdown(bloque_equipo(sub, "Subcampeon"), unsafe_allow_html=True)

        if campeon != "(sin declarar)" and sub != "(sin declarar)":
            if NFL_TEAMS[campeon]["conf"] == NFL_TEAMS[sub]["conf"]:
                st.error("Campeon y subcampeon deben pertenecer a conferencias distintas.")

        if st.button("Publicar resultado oficial", type="primary"):
            guardar_configuracion(
                nombre_display(campeon) if campeon != "(sin declarar)" else None,
                nombre_display(sub) if sub != "(sin declarar)" else None,
            )
            st.success("Configuracion del torneo actualizada. El ranking ya refleja el bonus.")
            st.rerun()

    # --- Diagnostico ----------------------------------------------------------
    if sub == "Avatares":
        st.caption(
            "Retira la imagen de cualquier participante si resulta inapropiada. "
            "Su avatar vuelve a ser el generado con sus iniciales; no se borra "
            "su cuenta ni sus pronosticos."
        )
        usuarios_admin = sin_admin(cargar_usuarios())
        con_imagen = (
            usuarios_admin[usuarios_admin["avatar_url"].notna()]
            if "avatar_url" in usuarios_admin.columns else pd.DataFrame()
        )
        if con_imagen.empty:
            st.info("Ningun participante ha configurado una imagen propia.")
        else:
            for u in con_imagen.to_dict("records"):
                c1, c2, c3 = st.columns([1, 5, 2])
                with c1:
                    st.image(avatares.avatar_de(u["username"], u["avatar_url"]), width=48)
                with c2:
                    tipo = (
                        f"Logo de {nombre_display(avatares.es_avatar_de_equipo(u['avatar_url']))}"
                        if avatares.es_avatar_de_equipo(u["avatar_url"])
                        else "Foto subida"
                    )
                    st.markdown(f"**{u['username']}**")
                    st.caption(f"{u['email']} &bull; {tipo}")
                with c3:
                    if st.button("Quitar imagen", key=f"delav_{u['id']}",
                                 use_container_width=True):
                        guardar_avatar_url(u["email"], None)
                        st.success(f"Imagen de {u['username']} retirada.")
                        st.rerun()
            st.caption(
                "Nota: el archivo permanece en el bucket hasta que su dueno suba "
                "otro. Para borrarlo del almacenamiento, usa Storage en el "
                "dashboard de Supabase."
            )

    if sub == "Diagnostico":
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Partidos", len(partidos))
        c2.metric("Regular", int((partidos["semana"] <= 18).sum()))
        c3.metric("Con resultado", len(resultados))
        c4.metric("Picks totales", len(predicciones))

        desconocidos = sorted(
            {
                str(v)
                for col in ("equipo_local", "equipo_visitante")
                for v in partidos[col].tolist()
                if resolver_equipo(v) is None
            }
        )
        if desconocidos:
            st.error(
                "Equipos que no pudieron mapearse a un logotipo oficial "
                "(revisa la escritura en `matches`):"
            )
            st.code("\n".join(desconocidos))
        else:
            st.success("Los 32 equipos de la base de datos se mapean correctamente a su logotipo.")

        st.markdown("**Cobertura de la temporada regular**")
        cobertura = (
            partidos[partidos["semana"] <= 18]
            .groupby("semana")
            .size()
            .rename("Juegos")
            .reset_index()
        )
        st.dataframe(cobertura, use_container_width=True, hide_index=True, height=300)


# -----------------------------------------------------------------------------
# 16. ORQUESTADOR PRINCIPAL
# -----------------------------------------------------------------------------
def main() -> None:
    st.session_state.setdefault("tz", "America/Mexico_City")

    # Cookies primero: si hay sesion previa, se restaura sin pedir credenciales.
    restaurar_sesion()

    usuario = st.session_state.get("usuario")
    if not usuario:
        pantalla_auth()
        return

    encabezado(usuario)
    barra_lateral(usuario)

    # Carga unica por render; todas las consultas van con .limit(10000)
    partidos = cargar_partidos()
    resultados = cargar_resultados()
    config = cargar_configuracion()

    # El administrador arbitra y no compite: se le excluye de todo lo que
    # alimenta el ranking y las estadisticas de la comunidad. Los datos crudos
    # (sin filtrar) solo se usan en el Panel Admin, para diagnostico.
    predicciones_todas = cargar_predicciones()
    predicciones = sin_admin(predicciones_todas)
    usuarios = sin_admin(cargar_usuarios())
    sb_picks = sin_admin(cargar_super_bowl())

    ranking = calcular_ranking(usuarios, predicciones, partidos, resultados, sb_picks, config)

    # Los emojis solo se usan como iconografia de navegacion, NUNCA para equipos.
    #
    # Se usa segmented_control y no st.tabs a proposito: st.tabs vuelve siempre
    # a la primera pestana cuando el script hace st.rerun() (al guardar un pick,
    # un marcador o un avatar), lo que hacia "saltar" la vista. Este control
    # guarda su seleccion en session_state y sobrevive a cualquier rerun.
    titulos = ["🏈 Mis Picks", "🏆 Posiciones NFL", "💍 Super Bowl", "📊 Ranking Global"]
    if es_admin(usuario.get("email")):
        titulos.append("⚙️ Panel Admin")

    if st.session_state.get("nav") not in titulos:
        st.session_state["nav"] = titulos[0]

    seccion = st.segmented_control(
        "Seccion", titulos, key="nav", label_visibility="collapsed"
    )
    # El control permite deseleccionar: sin esto la pantalla quedaria en blanco.
    if seccion is None:
        seccion = st.session_state["nav"] = titulos[0]

    st.markdown("")

    if seccion == titulos[0]:
        tab_picks(usuario, partidos, predicciones, resultados, usuarios, ranking)
    elif seccion == titulos[1]:
        tab_posiciones(partidos, resultados)
    elif seccion == titulos[2]:
        tab_super_bowl(usuario, sb_picks, usuarios, config, partidos)
    elif seccion == titulos[3]:
        tab_ranking(usuario, ranking)
    elif seccion == "⚙️ Panel Admin" and es_admin(usuario.get("email")):
        tab_admin(partidos, resultados, predicciones_todas, config)


if __name__ == "__main__":
    main()
