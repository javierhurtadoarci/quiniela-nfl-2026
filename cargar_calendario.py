#!/usr/bin/env python
# =============================================================================
#  CARGA DEL CALENDARIO NFL 2026 -> tabla `matches`
# =============================================================================
#  Descarga el calendario oficial desde la API publica de ESPN y genera:
#     - calendario_nfl_2026.csv     (respaldo legible / importable a mano)
#     - sql/02_calendario.sql       (INSERTs listos para el SQL Editor)
#     - opcionalmente lo sube directo a Supabase con --subir
#
#  USO
#  ---------------------------------------------------------------------------
#    python cargar_calendario.py                 # solo genera CSV + SQL
#    python cargar_calendario.py --playoffs      # incluye comodines..Super Bowl
#    python cargar_calendario.py --subir         # ademas inserta en Supabase
#
#  CUANDO CARGAR LOS PLAYOFFS
#  ---------------------------------------------------------------------------
#  Al terminar la semana 18 y antes de los comodines. Hasta ese momento ESPN
#  publica esas rondas con ambos equipos como 'TBD' y el script las descarta:
#
#    python cargar_calendario.py --solo-playoffs --subir --simular   # en seco
#    python cargar_calendario.py --solo-playoffs --subir             # de verdad
#
#  CLAVE NECESARIA PARA --subir
#  ---------------------------------------------------------------------------
#  La tabla `matches` solo la escribe el administrador (politica `matches_admin`),
#  asi que hace falta la service_role en .streamlit/secrets.toml:
#
#    SUPABASE_SERVICE_KEY = "..."     # Supabase -> Settings -> API Keys
#
#  Ese archivo esta en .gitignore. NUNCA pongas esa clave en el panel de
#  secretos de Streamlit Cloud: se salta todas las politicas de seguridad.
#
#  No requiere instalar nada: usa solo la libreria estandar (urllib, csv,
#  tomllib). El modo --subir si necesita el paquete `supabase`.
#
#  Las horas se guardan SIEMPRE en UTC (ESPN las entrega asi). La app las
#  convierte despues a la zona horaria de cada usuario.
# =============================================================================

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
import time
import unicodedata
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).parent
TEMPORADA = 2026

API = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?dates={anio}&seasontype={tipo}&week={semana}&limit=100"
)

# ESPN: seasontype 2 = temporada regular (semanas 1-18)
#       seasontype 3 = postemporada. Su semana 4 es el Pro Bowl y se descarta.
SEMANAS_REGULARES = range(1, 19)
MAPA_PLAYOFFS = {1: 19, 2: 20, 3: 21, 5: 22}

JUEGOS_ESPERADOS_REGULAR = 272

# Los 32 equipos, para validar que el `displayName` de ESPN sea reconocible
# por el diccionario de logotipos de app.py.
EQUIPOS_VALIDOS = {
    "arizona cardinals", "atlanta falcons", "baltimore ravens", "buffalo bills",
    "carolina panthers", "chicago bears", "cincinnati bengals", "cleveland browns",
    "dallas cowboys", "denver broncos", "detroit lions", "green bay packers",
    "houston texans", "indianapolis colts", "jacksonville jaguars", "kansas city chiefs",
    "las vegas raiders", "los angeles chargers", "los angeles rams", "miami dolphins",
    "minnesota vikings", "new england patriots", "new orleans saints", "new york giants",
    "new york jets", "philadelphia eagles", "pittsburgh steelers", "san francisco 49ers",
    "seattle seahawks", "tampa bay buccaneers", "tennessee titans", "washington commanders",
}


def normaliza(texto: str) -> str:
    txt = unicodedata.normalize("NFKD", str(texto))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", "", txt.lower().strip())


# -----------------------------------------------------------------------------
#  Descarga
# -----------------------------------------------------------------------------
def pedir_json(url: str, intentos: int = 3) -> dict:
    for intento in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quiniela-nfl/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if intento == intentos:
                raise RuntimeError(f"Fallo la peticion a ESPN tras {intentos} intentos: {exc}")
            time.sleep(2 * intento)
    return {}


def extraer_semana(anio: int, tipo: int, semana_espn: int, semana_destino: int) -> list[dict]:
    """Devuelve los partidos de una semana, ya normalizados."""
    datos = pedir_json(API.format(anio=anio, tipo=tipo, semana=semana_espn))
    partidos = []

    for evento in datos.get("events", []):
        competencias = evento.get("competitions") or []
        if not competencias:
            continue
        competidores = competencias[0].get("competitors") or []

        local = visitante = None
        for c in competidores:
            nombre = (c.get("team") or {}).get("displayName")
            if c.get("homeAway") == "home":
                local = nombre
            elif c.get("homeAway") == "away":
                visitante = nombre

        fecha = evento.get("date")  # ISO 8601 en UTC, p.ej. 2026-09-11T00:20Z
        if not (local and visitante and fecha):
            print(f"  [aviso] Evento incompleto omitido: {evento.get('name', '?')}")
            continue

        partidos.append(
            {
                "semana": semana_destino,
                "equipo_local": local,
                "equipo_visitante": visitante,
                "fecha_hora": fecha.replace("Z", "+00:00"),
                "bloqueado": False,
            }
        )
    return partidos


def sin_definir(partido: dict) -> bool:
    """Partido de playoffs cuyos participantes aun no se conocen."""
    return "TBD" in (partido["equipo_local"], partido["equipo_visitante"])


def descargar(incluir_playoffs: bool, solo_playoffs: bool = False) -> list[dict]:
    todos: list[dict] = []

    if not solo_playoffs:
        print(f"Descargando temporada regular {TEMPORADA} desde ESPN...")
        for semana in SEMANAS_REGULARES:
            juegos = extraer_semana(TEMPORADA, 2, semana, semana)
            print(f"  Semana {semana:>2}: {len(juegos)} juegos")
            todos.extend(juegos)
            time.sleep(0.35)  # cortesia con la API

    if incluir_playoffs:
        print("Descargando playoffs...")
        omitidos = 0
        for semana_espn, semana_destino in MAPA_PLAYOFFS.items():
            juegos = extraer_semana(TEMPORADA, 3, semana_espn, semana_destino)
            definidos = [j for j in juegos if not sin_definir(j)]
            omitidos += len(juegos) - len(definidos)
            print(f"  Ronda {semana_destino}: {len(definidos)} juegos definidos")
            todos.extend(definidos)
            time.sleep(0.35)
        if omitidos:
            print(
                f"  Se omitieron {omitidos} partidos con equipos 'TBD'. Es lo esperado\n"
                "  antes de que concluya la semana 18: vuelve a correr el script con\n"
                "  --playoffs cuando se definan los clasificados."
            )

    todos.sort(key=lambda p: (p["semana"], p["fecha_hora"]))
    return todos


# -----------------------------------------------------------------------------
#  Validaciones
# -----------------------------------------------------------------------------
def validar(partidos: list[dict]) -> bool:
    """
    Valida solo la temporada regular. Los playoffs se omiten a proposito: hasta
    que termina la semana 18, ESPN publica esas rondas con ambos equipos como
    'TBD', asi que no hay nada que verificar ni que cargar todavia.
    """
    print("\n--- Validacion ---")
    ok = True

    regulares = [p for p in partidos if p["semana"] <= 18]
    playoffs = [p for p in partidos if p["semana"] > 18]

    print(f"Juegos de temporada regular: {len(regulares)} (esperados {JUEGOS_ESPERADOS_REGULAR})")
    if len(regulares) != JUEGOS_ESPERADOS_REGULAR:
        print("  [ALERTA] El total no cuadra. Revisa si ESPN ya publico el calendario completo.")
        ok = False

    # Todo equipo debe ser reconocible por el diccionario de logotipos
    desconocidos = sorted(
        {
            e
            for p in regulares
            for e in (p["equipo_local"], p["equipo_visitante"])
            if normaliza(e) not in EQUIPOS_VALIDOS
        }
    )
    if desconocidos:
        print("  [ALERTA] Equipos no reconocidos (no tendrian logotipo):")
        for e in desconocidos:
            print(f"    - {e}")
        ok = False
    else:
        print("Todos los equipos mapean a un logotipo oficial.")

    # En temporada regular cada equipo juega 17 partidos
    conteo = Counter()
    for p in regulares:
        conteo[p["equipo_local"]] += 1
        conteo[p["equipo_visitante"]] += 1
    raros = {e: n for e, n in conteo.items() if n != 17}
    if raros:
        print("  [ALERTA] Equipos que no tienen 17 juegos en temporada regular:")
        for e, n in sorted(raros.items()):
            print(f"    - {e}: {n}")
        ok = False
    else:
        print("Los 32 equipos tienen sus 17 juegos.")

    # Un mismo enfrentamiento no puede repetirse dentro de la misma semana
    duplicados = [
        clave for clave, n in Counter(
            (p["semana"], p["equipo_local"], p["equipo_visitante"]) for p in regulares
        ).items() if n > 1
    ]
    if duplicados:
        print(f"  [ALERTA] {len(duplicados)} enfrentamientos duplicados en la misma semana:")
        for semana, local, visita in duplicados[:10]:
            print(f"    - Semana {semana}: {local} vs {visita}")
        ok = False
    else:
        print("Sin enfrentamientos duplicados.")

    if playoffs:
        print(f"Playoffs incluidos: {len(playoffs)} partidos (se omite su validacion).")

    print("--- " + ("Validacion superada" if ok else "Revisa las alertas") + " ---\n")
    return ok


# -----------------------------------------------------------------------------
#  Salidas
# -----------------------------------------------------------------------------
def escribir_csv(partidos: list[dict], destino: Path) -> None:
    with destino.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["semana", "equipo_local", "equipo_visitante", "fecha_hora", "bloqueado"]
        )
        writer.writeheader()
        writer.writerows(partidos)
    print(f"CSV generado: {destino}")


def escribir_sql(partidos: list[dict], destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    lineas = [
        "-- =========================================================================",
        f"--  CALENDARIO NFL {TEMPORADA} - generado por cargar_calendario.py",
        f"--  {len(partidos)} partidos. Horas en UTC.",
        "-- =========================================================================",
        "",
        "--  Este archivo SOLO INSERTA. Usalo unicamente para la carga inicial:",
        "--  si `matches` ya tiene partidos, volver a ejecutarlo los duplicaria.",
        "--  Para agregar playoffs o corregir horarios mas adelante, usa:",
        "--      python cargar_calendario.py --solo-playoffs --subir",
        "--  que compara contra lo existente y nunca duplica ni borra.",
        "",
        "insert into public.matches (semana, equipo_local, equipo_visitante, fecha_hora, bloqueado) values",
    ]
    filas = [
        "  ({semana}, {local}, {visita}, '{fecha}'::timestamptz, false)".format(
            semana=p["semana"],
            local="'" + p["equipo_local"].replace("'", "''") + "'",
            visita="'" + p["equipo_visitante"].replace("'", "''") + "'",
            fecha=p["fecha_hora"],
        )
        for p in partidos
    ]
    lineas.append(",\n".join(filas) + ";")
    lineas.append("")
    lineas.append("select semana, count(*) as juegos from public.matches group by semana order by semana;")
    destino.write_text("\n".join(lineas), encoding="utf-8")
    print(f"SQL generado:  {destino}")


def _clave(semana, local: str, visitante: str) -> tuple:
    """Identidad de un partido, independiente de como se escriba el equipo."""
    from equipos import resolver_equipo
    return (int(semana), resolver_equipo(local), resolver_equipo(visitante))


def tipo_de_clave(key: str) -> str:
    """
    Distingue una clave `service_role` de una publica. Devuelve 'servicio',
    'publica' o 'desconocida'.

    Importa porque `matches` solo la puede escribir el admin autenticado
    (politica `matches_admin`). Con la clave publica se lee sin problema, pero
    todo insert o update lo rechaza RLS, y el error que devuelve Postgres no
    dice en ningun lado que el problema sea la clave.

    Se mira el formato nuevo por prefijo y el clasico decodificando el payload
    del JWT, que no requiere validar la firma ni instalar nada: solo se esta
    leyendo que rol dice traer.
    """
    key = (key or "").strip()
    if key.startswith("sb_secret_"):
        return "servicio"
    if key.startswith(("sb_publishable_", "sb_anon_")):
        return "publica"

    partes = key.split(".")
    if len(partes) == 3:
        try:
            relleno = "=" * (-len(partes[1]) % 4)
            datos = json.loads(base64.urlsafe_b64decode(partes[1] + relleno))
            rol = str(datos.get("role", "")).lower()
            if rol == "service_role":
                return "servicio"
            if rol in ("anon", "authenticated"):
                return "publica"
        except Exception:
            pass
    return "desconocida"


def subir_a_supabase(partidos: list[dict], simular: bool = False) -> None:
    """
    Carga idempotente: se puede ejecutar las veces que haga falta sin duplicar.

    Compara cada partido contra los que ya existen usando (semana, local,
    visitante). Los que ya estan NO se vuelven a insertar; si su horario cambio
    (la NFL reprograma partidos al Sunday Night con el flex scheduling), se
    actualiza solo la fecha.

    Nunca borra nada: los pronosticos ya registrados no corren riesgo.
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        sys.exit("Se requiere Python 3.11+ para leer secrets.toml.")
    try:
        from supabase import create_client
    except ModuleNotFoundError:
        sys.exit("Falta el paquete `supabase`. Instala con: pip install -r requirements.txt")

    ruta = RAIZ / ".streamlit" / "secrets.toml"
    if not ruta.exists():
        sys.exit(f"No existe {ruta}. Crea el archivo con tus claves antes de usar --subir.")

    secretos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    url = secretos.get("SUPABASE_URL")
    key = secretos.get("SUPABASE_SERVICE_KEY") or secretos.get("SUPABASE_KEY")
    if not url or not key:
        sys.exit("secrets.toml no tiene SUPABASE_URL / SUPABASE_KEY.")

    # Sin clave de servicio no hay escritura posible: mejor decirlo aqui, con
    # instrucciones, que dejar que falle mas adelante con un error de permisos
    # de Postgres que no explica nada.
    tipo = tipo_de_clave(key)
    if tipo == "publica" and not simular:
        sys.exit(
            "\nLa clave de secrets.toml es publica (anon) y la tabla `matches` solo\n"
            "la puede escribir el administrador: RLS va a rechazar la carga.\n"
            "\n"
            "Agrega a .streamlit/secrets.toml la clave service_role, que encuentras\n"
            "en Supabase -> Settings -> API Keys -> Reveal:\n"
            "\n"
            '    SUPABASE_SERVICE_KEY = "..."\n'
            "\n"
            "Ese archivo esta en .gitignore, asi que no sale de tu equipo. NUNCA la\n"
            "pongas en el panel de secretos de Streamlit Cloud: ahi vive la app\n"
            "publica y esa clave se salta todas las politicas de seguridad.\n"
            "\n"
            "Mientras tanto puedes ver que cambiaria agregando --simular."
        )
    if tipo == "desconocida" and not simular:
        print(
            "\nAviso: no se pudo identificar el tipo de clave. Si la carga falla con\n"
            "un error de permisos, es que no es la service_role."
        )

    cliente = create_client(url, key)

    previos = (
        cliente.table("matches")
        .select("id, semana, equipo_local, equipo_visitante, fecha_hora")
        .limit(10000)
        .execute()
    ).data or []
    indice = {_clave(p["semana"], p["equipo_local"], p["equipo_visitante"]): p for p in previos}
    print(f"\nLa tabla `matches` tiene {len(previos)} partidos registrados.")

    nuevos, reprogramados, iguales = [], [], 0
    for p in partidos:
        anterior = indice.get(_clave(p["semana"], p["equipo_local"], p["equipo_visitante"]))
        if anterior is None:
            nuevos.append(p)
            continue
        antes = str(anterior.get("fecha_hora") or "")[:16].replace(" ", "T")
        ahora = str(p["fecha_hora"])[:16]
        if antes != ahora:
            reprogramados.append((anterior["id"], anterior, p))
        else:
            iguales += 1

    print(f"  Ya existen y sin cambios : {iguales}")
    print(f"  Por insertar             : {len(nuevos)}")
    print(f"  Con horario reprogramado : {len(reprogramados)}")

    if reprogramados:
        print("\n  Cambios de horario detectados:")
        for _, ant, nue in reprogramados[:15]:
            print(f"    S{nue['semana']:>2} {nue['equipo_local']} vs {nue['equipo_visitante']}")
            print(f"        {str(ant.get('fecha_hora'))[:16]}  ->  {nue['fecha_hora'][:16]}")
        if len(reprogramados) > 15:
            print(f"    ... y {len(reprogramados) - 15} mas")

    if not nuevos and not reprogramados:
        print("\nNada que hacer: la base ya esta al dia.")
        return

    if simular:
        print("\n[SIMULACION] No se escribio nada.")
        return

    if nuevos:
        print(f"\nInsertando {len(nuevos)} partidos nuevos...")
        for i in range(0, len(nuevos), 100):
            cliente.table("matches").insert(nuevos[i : i + 100]).execute()
            print(f"  {min(i + 100, len(nuevos))}/{len(nuevos)}")

    if reprogramados:
        print(f"Actualizando {len(reprogramados)} horarios...")
        for match_id, _, nuevo in reprogramados:
            cliente.table("matches").update(
                {"fecha_hora": nuevo["fecha_hora"]}
            ).eq("id", match_id).execute()

    print("\nListo. Ningun partido fue duplicado ni eliminado.")


# -----------------------------------------------------------------------------
#  Entrada
# -----------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=f"Carga el calendario NFL {TEMPORADA} en `matches`.")
    ap.add_argument("--playoffs", action="store_true", help="incluir comodines hasta Super Bowl")
    ap.add_argument("--solo-playoffs", action="store_true",
                    help="descargar unicamente los playoffs (implica --playoffs)")
    ap.add_argument("--subir", action="store_true", help="insertar directo en Supabase")
    ap.add_argument("--simular", action="store_true",
                    help="con --subir: muestra los cambios sin escribir")
    ap.add_argument("--forzar", action="store_true", help="continuar aunque la validacion falle")
    args = ap.parse_args()

    solo_playoffs = args.solo_playoffs
    incluir_playoffs = args.playoffs or solo_playoffs

    partidos = descargar(incluir_playoffs=incluir_playoffs, solo_playoffs=solo_playoffs)
    if not partidos:
        if solo_playoffs:
            sys.exit(
                "ESPN todavia no publica los enfrentamientos de playoffs: siguen "
                "como 'TBD'. Vuelve a intentarlo cuando concluya la semana 18."
            )
        sys.exit("ESPN no devolvio partidos. Verifica tu conexion o intenta mas tarde.")

    # Con --solo-playoffs no tiene sentido validar los 272 juegos regulares.
    valido = True if solo_playoffs else validar(partidos)

    if not solo_playoffs:
        escribir_csv(partidos, RAIZ / f"calendario_nfl_{TEMPORADA}.csv")
        escribir_sql(partidos, RAIZ / "sql" / "02_calendario.sql")

    if args.subir:
        if not valido and not args.forzar:
            sys.exit("Validacion fallida. Revisa las alertas o usa --forzar para subir igual.")
        subir_a_supabase(partidos, simular=args.simular)
    else:
        print("\nSiguiente paso: ejecuta sql/02_calendario.sql en el SQL Editor de Supabase,")
        print("o vuelve a correr este script con --subir cuando tengas tus claves.")


if __name__ == "__main__":
    main()
