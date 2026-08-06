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
#  13. PESTANA: RANKING GLOBAL
#  14. PESTANA: PANEL ADMIN
#  15. ORQUESTADOR PRINCIPAL
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
# El pronostico de pretemporada (campeon y subcampeon) se elimino: la quiniela
# se decide solo con los aciertos partido a partido. El Super Bowl como PARTIDO
# sigue existiendo y es el que mas vale, en PUNTOS_POR_RONDA[22].
PUNTOS_ACIERTO_REGULAR = 1     # 1 punto por ganador directo (Moneyline)

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
    # LX se jugo en febrero de 2026 y cerro la temporada 2025; el de esta
    # temporada, que termina en febrero de 2027, es el LXI.
    22: "Super Bowl LXI",
}

# Valores canonicos almacenados en predictions.prediction y results.ganador_oficial
PICK_LOCAL = "LOCAL"
PICK_VISITANTE = "VISITANTE"
# EMPATE ya no es una opcion de la quiniela: el participante solo elige equipo.
# Se conserva porque la NFL si permite empates y `results.ganador_oficial` tiene
# que poder registrarlos; en ese caso simplemente nadie suma puntos.
PICK_EMPATE = "EMPATE"

# Lo unico que el participante puede votar.
OPCIONES_PICK = [PICK_LOCAL, PICK_VISITANTE]

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
    resolver_equipo,
    logo_url,
    nombre_display,
    nombre_corto,
    color_equipo,
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
#     La sesion sobrevive a las recargas para que nadie tenga que teclear su
#     contrasena cada vez que abre la app.
#
#     REGLA DE SEGURIDAD: lo unico que se guarda en el navegador es el refresh
#     token que emite Supabase Auth, y la identidad se toma SIEMPRE de la sesion
#     que Supabase valida al canjearlo. El correo no se guarda ni se lee de
#     ninguna cookie: una cookie la escribe quien quiera desde su navegador, asi
#     que confiar en un correo guardado ahi equivaldria a dejar entrar como
#     cualquier participante -el admin incluido- con solo editarla.
# -----------------------------------------------------------------------------
COOKIE_TOKEN = "nfl26_refresh"
COOKIE_EMAIL_LEGADO = "nfl26_email"   # de versiones anteriores: solo se borra
DIAS_SESION = 30

# Operaciones de cookie a la espera de ejecutarse. Ver `recordar_sesion`.
TOKEN_PENDIENTE = "_refresh_pendiente"
BORRADO_PENDIENTE = "_cookie_a_borrar"


def get_cookie_manager() -> stx.CookieManager:
    """Una instancia por sesion, por el mismo motivo que `get_supabase`."""
    if "cookie_mgr" not in st.session_state:
        st.session_state["cookie_mgr"] = stx.CookieManager(key="nfl26_cookies")
    return st.session_state["cookie_mgr"]


def _url_app() -> str:
    """URL con la que el navegador abrio la app. Vacia fuera de un run."""
    try:
        return str(st.context.url or "")
    except Exception:
        return ""


def _token_guardado() -> str | None:
    """
    Refresh token que mando el navegador, si hay alguno.

    Se lee de `st.context.cookies` -las cookies que viajan en la propia peticion-
    y no del componente: el componente necesita un ida y vuelta con el cliente y
    en el primer run todavia no ha contestado, con lo que la app mostraba el
    login antes de enterarse de que habia sesion guardada. El componente queda
    como respaldo por si esa via no estuviera disponible.
    """
    try:
        valor = st.context.cookies.get(COOKIE_TOKEN)
        if valor:
            return str(valor)
    except Exception:
        pass
    cookies = get_cookie_manager().get_all(key="get_all_cookies") or {}
    valor = cookies.get(COOKIE_TOKEN)
    return str(valor) if valor else None


def _email_de_sesion(respuesta) -> str | None:
    """
    Correo confirmado por Supabase a partir del token, nunca por el navegador.

    Se buscan las dos formas en que la libreria lo expone segun la version
    (`.user` y `.session.user`) para no depender de una en concreto.
    """
    candidatos = (
        getattr(respuesta, "user", None),
        getattr(getattr(respuesta, "session", None), "user", None),
    )
    for objeto in candidatos:
        correo = getattr(objeto, "email", None)
        if correo:
            return str(correo).lower().strip()
    return None


def recordar_sesion(refresh_token: str | None) -> None:
    """
    Agenda la escritura de la cookie para el arranque del siguiente run.

    NO la escribe aqui a proposito. `cm.set` no guarda nada en el acto: renderiza
    un componente cuyo JavaScript escribe la cookie en el navegador. Si el script
    termina antes de que ese ida y vuelta se complete -y un `st.rerun()` justo
    despues lo aborta- la cookie no llega a existir nunca. Era exactamente lo que
    pasaba al iniciar sesion: se pedia guardar la cookie y una linea despues se
    hacia rerun, asi que la sesion no sobrevivia a cerrar el navegador.

    Dejando el token aqui, `sincronizar_cookies()` lo guarda al principio de un
    run que si se renderiza entero.
    """
    if refresh_token:
        st.session_state[TOKEN_PENDIENTE] = refresh_token


def olvidar_sesion() -> None:
    """
    Agenda el borrado de la cookie, por el mismo motivo que `recordar_sesion`.

    El cierre de sesion tenia el mismo defecto que el login: pedia borrar la
    cookie y hacia rerun acto seguido, con lo que el borrado tampoco llegaba al
    navegador. Se notaba menos porque `sign_out()` revoca el token del lado de
    Supabase, pero dejaba la sesion dependiendo de que esa llamada no fallara.
    """
    st.session_state.pop(TOKEN_PENDIENTE, None)
    st.session_state[BORRADO_PENDIENTE] = True


def sincronizar_cookies() -> None:
    """
    Unico punto del que salen escrituras y borrados de cookie en toda la app.

    Concentrarlo aqui garantiza dos cosas: que la operacion ocurra dentro de un
    run que se renderiza entero -si no, el componente no alcanza a hablar con el
    navegador y no pasa nada-, y que nunca coincidan dos llamadas en el mismo
    run, que en Streamlit seria un error de clave de widget duplicada.
    """
    if st.session_state.pop(BORRADO_PENDIENTE, False):
        cm = get_cookie_manager()
        for nombre, k in ((COOKIE_TOKEN, "del_token"), (COOKIE_EMAIL_LEGADO, "del_email")):
            try:
                cm.delete(nombre, key=k)
            except Exception:
                pass
        return

    token = st.session_state.pop(TOKEN_PENDIENTE, None)
    if not token:
        return
    get_cookie_manager().set(
        COOKIE_TOKEN,
        token,
        expires_at=datetime.now() + timedelta(days=DIAS_SESION),
        # Solo en https. Forzarlo siempre haria que el navegador descartara la
        # cookie en un `http://` de desarrollo que no sea localhost.
        secure=_url_app().lower().startswith("https://"),
        # 'lax' y no 'strict': con strict el navegador no manda la cookie cuando
        # se llega a la app desde un enlace externo, que es justo como la abre
        # la mayoria. Sigue sin viajar en peticiones cross-site que escriban.
        same_site="lax",
        key="set_token",
    )


def restaurar_sesion() -> None:
    """
    Reconstruye la sesion canjeando el refresh token guardado.

    Sin token no hay restauracion: no existe ninguna ruta que acepte una
    identidad tomada del navegador sin que Supabase la valide antes.
    """
    if st.session_state.get("usuario"):
        return
    token = _token_guardado()
    if not token:
        return

    sb = get_supabase()
    try:
        sesion = sb.auth.refresh_session(token)
    except Exception:
        olvidar_sesion()           # token vencido, revocado o manipulado
        return

    email = _email_de_sesion(sesion)
    if not email:
        olvidar_sesion()
        return

    # Supabase rota el refresh token en cada canje: si no se guarda el nuevo, la
    # siguiente recarga presentaria uno ya consumido y la sesion se perderia.
    nuevo = getattr(getattr(sesion, "session", None), "refresh_token", None)
    if nuevo and nuevo != token:
        recordar_sesion(nuevo)

    perfil = obtener_perfil(email)
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


def invalidar_cache() -> None:
    """Se llama tras cada escritura para que la UI refleje el dato nuevo."""
    for fn in (
        cargar_partidos, cargar_predicciones, cargar_resultados, cargar_usuarios,
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
def guardar_pick(email: str, match_id, prediccion: str) -> bool:
    """
    Inserta o actualiza el pick del usuario para un partido.

    Devuelve False si la base rechazo la escritura. Quien decide de verdad si el
    voto sigue abierto es la politica RLS `partido_abierto()`, no esta funcion:
    la interfaz solo dibuja el formulario mientras falta para el kickoff, pero
    cualquiera puede llamar a la API por su cuenta. Aqui se atrapa ese rechazo
    para que el caso limite -votar en el segundo exacto del arranque- muestre un
    aviso en vez de un error de Python.
    """
    sb = get_supabase()
    email = email.lower().strip()
    if es_admin(email):   # el arbitro no compite
        return False
    try:
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
    except Exception:
        return False
    invalidar_cache()
    return True


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


# -----------------------------------------------------------------------------
#  8. MOTOR DE PUNTUACION
#     Regular : 1 punto por acertar al ganador directo (Moneyline, sin spread).
#     Playoffs: la ronda define cuanto vale, hasta 5 por el Super Bowl.
#     Empates : no son votables. Si un partido termina empatado ningun pick
#               coincide con el resultado, asi que nadie suma en ese partido.
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
) -> pd.DataFrame:
    """
    Tabla general: se arma solo con los aciertos partido a partido, ponderados
    por ronda. No hay bonus de pretemporada; el Super Bowl pesa como partido.
    """
    # Red de seguridad: el admin nunca entra al ranking, aunque llegue sin filtrar.
    usuarios, predicciones = sin_admin(usuarios), sin_admin(predicciones)
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

    # Universo de participantes: registrados + cualquiera con actividad
    emails = set(usuarios["email"].tolist()) | set(predicciones["email"].tolist())
    nombres = dict(zip(usuarios["email"], usuarios["username"]))
    imagenes = (
        dict(zip(usuarios["email"], usuarios["avatar_url"]))
        if "avatar_url" in usuarios.columns else {}
    )

    # Los picks de partidos aun abiertos solo los ve su autor: la politica
    # `predictions_select` no entrega los ajenos hasta el cierre. Si la columna
    # "Picks" contara todo lo que hay en el dataframe, cada quien veria su
    # propia fila inflada frente a las demas. Contando unicamente partidos ya
    # cerrados, la tabla sale identica para todos.
    ids_cerrados = {
        r["id"] for r in partidos.to_dict("records") if esta_bloqueado(r)
    }
    picks_visibles = predicciones[predicciones["match_id"].isin(ids_cerrados)]

    filas = []
    for em in emails:
        pts_partidos = puntos.get(em, 0)
        n_aciertos = aciertos.get(em, 0)
        cerrados = jugados.get(em, 0)
        nombre_part = nombres.get(em) or em.split("@")[0]
        filas.append(
            {
                "email": em,
                "Avatar": avatares.avatar_de(nombre_part, imagenes.get(em)),
                "Participante": nombre_part,
                "Aciertos": n_aciertos,
                "Calificados": cerrados,
                "Efectividad": round(100 * n_aciertos / cerrados, 1) if cerrados else 0.0,
                "Total": pts_partidos,
                "Picks": int((picks_visibles["email"] == em).sum()),
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
        subtitulo = "Pronosticos Moneyline &bull; Temporada Regular, Playoffs y Super Bowl LXI"

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
                    # El correo de la sesion manda sobre el que se tecleo: es el
                    # que Supabase acaba de autenticar y el que evaluara RLS.
                    email = _email_de_sesion(sesion) or email
                    perfil = obtener_perfil(email)
                    if perfil is None:
                        # Auto-reparacion: existe en Auth pero no tenia perfil
                        # publico (cuentas anteriores al trigger de `07_seguridad`).
                        # Va autenticado, asi que la politica users_insert lo acepta.
                        sb.table("users").insert(
                            {"username": email.split("@")[0], "email": email}
                        ).execute()
                        perfil = obtener_perfil(email)
                    refresh = getattr(getattr(sesion, "session", None), "refresh_token", None)
                    # Agendado, no escrito: el st.rerun() de abajo abortaria el
                    # componente que guarda la cookie. Lo escribe `main` en el
                    # run siguiente, que si se renderiza completo.
                    recordar_sesion(refresh)
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
            nueva_pass = st.text_input("Contrasena (min. 8 caracteres)", type="password", key="reg_pass")
            confirmar = st.text_input("Confirmar contrasena", type="password")
            crear = st.form_submit_button("Registrarme", use_container_width=True, type="primary")
        if crear:
            nuevo_email = (nuevo_email or "").lower().strip()   # regla: siempre minusculas
            nuevo_user = (nuevo_user or "").strip()
            if not nuevo_user or not nuevo_email or not nueva_pass:
                st.warning("Completa todos los campos.")
            elif nueva_pass != confirmar:
                st.warning("Las contrasenas no coinciden.")
            elif len(nueva_pass) < 8:
                st.warning("La contrasena debe tener al menos 8 caracteres.")
            else:
                # No se consulta si el correo ya existe: responder "ese correo ya
                # esta registrado" permitiria averiguar quien juega probando
                # direcciones, justo lo que el login evita con su mensaje
                # generico. El indice unico de `users` y el propio Supabase Auth
                # impiden el duplicado, y el mensaje de abajo sirve para los dos
                # casos sin revelar cual ocurrio.
                try:
                    sb.auth.sign_up({
                        "email": nuevo_email,
                        "password": nueva_pass,
                        # El perfil publico lo crea el trigger `crear_perfil_usuario`
                        # con este dato. Insertarlo desde aqui exigiria escritura
                        # anonima en `users`, que la politica RLS ya no permite.
                        "options": {"data": {"username": nuevo_user}},
                    })
                    invalidar_cache()
                    st.success(
                        "Listo. Si el correo es valido y no estaba registrado, te "
                        "enviamos un enlace de confirmacion: revisa tu bandeja y "
                        "la carpeta de spam antes de iniciar sesion."
                    )
                except Exception:
                    st.error(
                        "No fue posible completar el registro. Revisa los datos e "
                        "intentalo de nuevo."
                    )

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
                except Exception:
                    st.error(
                        "No se pudo leer esa imagen. Prueba con una foto en "
                        "formato JPG o PNG."
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
                                # Solo se detalla el error de configuracion, que
                                # le sirve al admin para saber que le falta. El
                                # resto se responde en generico: el texto crudo
                                # de la excepcion describe la infraestructura.
                                if "Bucket not found" in str(exc):
                                    st.error(
                                        "Falta crear el almacenamiento de avatares. "
                                        "Ejecuta `sql/05_avatares.sql` en Supabase."
                                    )
                                else:
                                    st.error(
                                        "No se pudo subir la foto. Intentalo de "
                                        "nuevo en un momento."
                                    )

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
            olvidar_sesion()
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
        st.info("Todavia no hay partidos cargados. Avisale al administrador.")
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
            # Solo se vota equipo. EMPATE sigue en `etiquetas` porque el marcador
            # oficial si puede terminar empatado y hay que saber nombrarlo.
            opciones = OPCIONES_PICK
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
                    if ganador == PICK_EMPATE:
                        icono = " (el partido termino empatado: nadie suma)"
                    elif ganador:
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
                    if not guardar_pick(email, mid, seleccion):
                        st.warning(
                            "No se pudo registrar el pick: el partido ya esta "
                            "cerrado. Recarga la pagina para ver su estado."
                        )
                    else:
                        st.toast(f"Pick guardado: {etiquetas[seleccion]}")
                        # Se actualiza el estado en memoria en lugar de llamar a
                        # st.rerun(): un rerun aqui devolveria la vista a la
                        # primera seccion en cada pick.
                        #
                        # Ya no hace falta tocar `votantes`: con el partido
                        # abierto no se dibujan las barras de la comunidad, asi
                        # que no hay conteo que refrescar.
                        mapa_mis_picks[mid] = seleccion
                        mi_pick = seleccion

            # --- Estadisticas de la comunidad ---
            # Los votos ajenos se destapan al cerrarse el partido: verlos antes
            # permitiria copiar al lider o esperar al ultimo minuto para ir
            # contra la mayoria. La politica RLS `predictions_select` ya no los
            # entrega mientras siga abierto, asi que dibujar aqui las barras
            # solo mostraria el voto propio y daria un 100% enganoso.
            # El arbitro es la excepcion: no compite y necesita ver la jornada.
            if not (bloqueado or arbitro):
                st.caption(
                    "Los votos de los demas se revelan cuando el partido cierra."
                )
            else:
                conteo = votantes.get(mid, {})
                total_votos = sum(len(v) for v in conteo.values())
                # La columna de empate solo se dibuja si quedan picks historicos
                # de cuando esa opcion existia; de lo contrario son dos columnas.
                claves = list(OPCIONES_PICK)
                if conteo.get(PICK_EMPATE):
                    claves.insert(1, PICK_EMPATE)
                colores = {
                    PICK_LOCAL: color_equipo(local),
                    PICK_VISITANTE: color_equipo(visita),
                    PICK_EMPATE: "#6B7280",
                }
                for col, clave in zip(st.columns(len(claves)), claves):
                    lista = conteo.get(clave, [])
                    with col:
                        st.markdown(
                            barra_votos(etiquetas[clave], len(lista), total_votos, colores[clave]),
                            unsafe_allow_html=True,
                        )

                with st.expander(f"Quien voto que ({total_votos} votos)"):
                    if total_votos == 0:
                        st.caption("Sin votos registrados todavia.")
                    else:
                        for col, clave in zip(st.columns(len(claves)), claves):
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
        "Records calculados en vivo con los marcadores ya cargados en la app; "
        "los partidos sin resultado todavia no cuentan. PCT es el porcentaje de "
        "victorias, donde un empate vale medio triunfo."
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
# 13. PESTANA: RANKING GLOBAL
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
    # "Puntos partidos" y "Total" eran la misma cifra en cuanto desaparecio el
    # bonus de pretemporada, asi que se muestra una sola columna.
    vista = ranking[
        ["Pos", "Avatar", "Participante", "Picks", "Calificados", "Aciertos",
         "Total", "Efectividad"]
    ].copy()
    st.dataframe(
        vista,
        use_container_width=True,
        hide_index=True,
        height=min(720, 42 * (len(vista) + 1) + 8),
        column_config={
            "Pos": st.column_config.NumberColumn("#", width="small"),
            "Avatar": st.column_config.ImageColumn("", width="small"),
            "Picks": st.column_config.NumberColumn(
                "Picks",
                help="Pronosticos registrados en partidos ya cerrados. Los de "
                     "partidos abiertos no se muestran hasta el kickoff.",
            ),
            "Calificados": st.column_config.NumberColumn("Cerrados", help="Partidos ya con resultado"),
            "Aciertos": st.column_config.NumberColumn("Aciertos", help="Partidos acertados"),
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
            (Moneyline, sin spread). Solo se elige entre los dos equipos: si el
            partido termina empatado &mdash;algo excepcional&mdash; nadie suma.

            | Jornada | Puntos por acierto |
            |---|---|
            | Semanas 1 a 18 | {PUNTOS_ACIERTO_REGULAR} |
            | Comodines | {PUNTOS_POR_RONDA[19]} |
            | Divisional | {PUNTOS_POR_RONDA[20]} |
            | Final de Conferencia | {PUNTOS_POR_RONDA[21]} |
            | Super Bowl | {PUNTOS_POR_RONDA[22]} |

            Las rondas finales valen mas para que la quiniela siga viva en enero
            en vez de quedar decidida en noviembre. El Super Bowl es el partido
            que mas pesa, y se acierta como cualquier otro: eligiendo al ganador.

            **Efectividad** &mdash; porcentaje de aciertos sobre partidos ya
            cerrados. No pondera por ronda: sirve para comparar puntería entre
            participantes que hayan jugado distinta cantidad de partidos.
            """
        )


# -----------------------------------------------------------------------------
# 14. PESTANA: PANEL ADMIN
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
              predicciones: pd.DataFrame) -> None:
    st.subheader("Panel de Administracion")
    st.caption("Captura de marcadores oficiales, control de bloqueos y moderacion de avatares.")

    if partidos.empty:
        st.warning("No hay partidos en la tabla `matches`.")
        return

    # Mismo motivo que en la navegacion principal: con st.tabs, cada st.rerun()
    # (guardar un marcador, mover un bloqueo) devolvia el panel a "Sincronizar".
    SECCIONES = ["Sincronizar", "Marcadores", "Bloqueos", "Avatares", "Diagnostico"]
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

    # Ya no hay seccion de "Cierre de temporada": servia para declarar campeon y
    # subcampeon oficiales, que era lo unico que activaba el bonus. El Super Bowl
    # ahora se cierra capturando su marcador en "Marcadores", como cualquier otro
    # partido de la jornada 22.

    # --- Moderacion de avatares -----------------------------------------------
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
# 15. ORQUESTADOR PRINCIPAL
# -----------------------------------------------------------------------------
def main() -> None:
    st.session_state.setdefault("tz", "America/Mexico_City")

    # Cookies primero: si hay sesion previa, se restaura sin pedir credenciales.
    restaurar_sesion()

    # Y aqui se materializa lo que quedo agendado: la cookie del login, la del
    # token rotado o su borrado al cerrar sesion. Tiene que ir antes de
    # cualquier `return` de esta funcion, pero dentro de un run que llegue a
    # renderizarse entero.
    sincronizar_cookies()

    usuario = st.session_state.get("usuario")
    if not usuario:
        pantalla_auth()
        return

    encabezado(usuario)
    barra_lateral(usuario)

    # Carga unica por render; todas las consultas van con .limit(10000)
    partidos = cargar_partidos()
    resultados = cargar_resultados()

    # El administrador arbitra y no compite: se le excluye de todo lo que
    # alimenta el ranking y las estadisticas de la comunidad. Los datos crudos
    # (sin filtrar) solo se usan en el Panel Admin, para diagnostico.
    predicciones_todas = cargar_predicciones()
    predicciones = sin_admin(predicciones_todas)
    usuarios = sin_admin(cargar_usuarios())

    ranking = calcular_ranking(usuarios, predicciones, partidos, resultados)

    # Los emojis solo se usan como iconografia de navegacion, NUNCA para equipos.
    #
    # Se usa segmented_control y no st.tabs a proposito: st.tabs vuelve siempre
    # a la primera pestana cuando el script hace st.rerun() (al guardar un pick,
    # un marcador o un avatar), lo que hacia "saltar" la vista. Este control
    # guarda su seleccion en session_state y sobrevive a cualquier rerun.
    titulos = ["🏈 Mis Picks", "🏆 Posiciones NFL", "📊 Ranking Global"]
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
        tab_ranking(usuario, ranking)
    elif seccion == "⚙️ Panel Admin" and es_admin(usuario.get("email")):
        tab_admin(partidos, resultados, predicciones_todas)


if __name__ == "__main__":
    main()
