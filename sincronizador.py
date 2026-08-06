# =============================================================================
#  SINCRONIZADOR DE RESULTADOS - ESPN -> tabla `results`
# =============================================================================
#  Un solo motor con dos disparadores:
#    1. El boton "Sincronizar desde ESPN" del Panel Admin (usa la sesion del
#       administrador, autorizada por RLS).
#    2. El cron de GitHub Actions (usa la service_role key).
#
#  REGLA CENTRAL: nunca se pisa un marcador con `editado_manual = true`.
#  Si el admin corrigio una fila a mano, esa correccion gana siempre sobre lo
#  que diga ESPN. Es el unico modo de que una mala sincronizacion no destruya
#  en silencio una correccion deliberada.
#
#  Solo se escriben partidos FINALIZADOS. Un marcador en vivo cambiaria el
#  ranking a media tarde y confundiria a los participantes.
#
#  USO EN LINEA DE COMANDOS
#  ---------------------------------------------------------------------------
#    python sincronizador.py                 # semanas con partidos recientes
#    python sincronizador.py --semana 5      # una semana concreta
#    python sincronizador.py --todas         # barrido completo 1-22
#    python sincronizador.py --simular       # muestra cambios sin escribir
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from equipos import resolver_equipo

TEMPORADA = 2026
LIMITE = 10000

API = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?dates={anio}&seasontype={tipo}&week={semana}&limit=100"
)

# ESPN: seasontype 2 = regular; 3 = postemporada (su semana 4 es el Pro Bowl).
MAPA_PLAYOFFS = {1: 19, 2: 20, 3: 21, 5: 22}
SEMANA_A_ESPN = {s: (2, s) for s in range(1, 19)}
SEMANA_A_ESPN.update({destino: (3, origen) for origen, destino in MAPA_PLAYOFFS.items()})

PICK_LOCAL, PICK_VISITANTE, PICK_EMPATE = "LOCAL", "VISITANTE", "EMPATE"


# -----------------------------------------------------------------------------
#  Descarga desde ESPN
# -----------------------------------------------------------------------------
# Las tres cabeceras son necesarias para que ESPN no responda 403; el porque
# esta documentado en `cargar_calendario.py`, con las pruebas. NO QUITAR
# ninguna. Se duplica aqui en lugar de compartirlo para que este script siga
# siendo autonomo: el cron de GitHub Actions solo instala `supabase`.
CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _pedir_json(url: str, intentos: int = 3) -> dict:
    for intento in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers=CABECERAS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if intento == intentos:
                raise RuntimeError(f"ESPN no respondio tras {intentos} intentos: {exc}")
            time.sleep(2 * intento)
    return {}


def marcadores_de_semana(semana: int) -> dict[tuple[str, str], dict]:
    """
    Marcadores FINALES de una jornada.
    Devuelve {(abrev_local, abrev_visitante): {marcador_local, marcador_visitante}}.
    Los partidos en curso o no jugados se ignoran.
    """
    if semana not in SEMANA_A_ESPN:
        return {}
    tipo, semana_espn = SEMANA_A_ESPN[semana]
    datos = _pedir_json(API.format(anio=TEMPORADA, tipo=tipo, semana=semana_espn))

    salida: dict[tuple[str, str], dict] = {}
    for evento in datos.get("events", []):
        competencias = evento.get("competitions") or []
        if not competencias:
            continue
        comp = competencias[0]

        # Solo partidos terminados: STATUS_FINAL (incluye tiempo extra).
        estado = ((comp.get("status") or {}).get("type") or {})
        if not estado.get("completed"):
            continue

        local = visitante = None
        for c in comp.get("competitors") or []:
            abrev = resolver_equipo((c.get("team") or {}).get("displayName", ""))
            try:
                puntos = int(c.get("score"))
            except (TypeError, ValueError):
                puntos = None
            if abrev is None or puntos is None:
                continue
            if c.get("homeAway") == "home":
                local = (abrev, puntos)
            elif c.get("homeAway") == "away":
                visitante = (abrev, puntos)

        if local and visitante:
            salida[(local[0], visitante[0])] = {
                "marcador_local": local[1],
                "marcador_visitante": visitante[1],
            }
    return salida


def ganador_por_marcador(local: int, visitante: int) -> str:
    if local > visitante:
        return PICK_LOCAL
    if visitante > local:
        return PICK_VISITANTE
    return PICK_EMPATE


# -----------------------------------------------------------------------------
#  Motor de sincronizacion
# -----------------------------------------------------------------------------
def sincronizar(cliente, semanas: list[int] | None = None, simular: bool = False,
                solo_ids: list | None = None) -> dict:
    """
    Contrasta ESPN contra `results` y escribe lo que falte o haya cambiado.

    `cliente` es un Client de supabase-py ya autenticado (sesion admin en la app,
    o service_role en el cron).

    `solo_ids` limita la escritura a partidos concretos: se consulta la jornada
    completa a ESPN (es una sola peticion de todos modos) pero unicamente se
    tocan esos match_id. Sirve para corregir un partido puntual sin arriesgar
    el resto de la jornada.

    Devuelve un resumen para mostrar o registrar.
    """
    resumen = {
        "nuevos": [], "actualizados": [], "protegidos": [],
        "sin_cambio": 0, "semanas": [], "errores": [],
    }

    partidos = (
        cliente.table("matches")
        .select("id, semana, equipo_local, equipo_visitante, fecha_hora")
        .limit(LIMITE)
        .execute()
    ).data or []

    if solo_ids is not None:
        objetivo = {str(x) for x in solo_ids}
        partidos = [p for p in partidos if str(p["id"]) in objetivo]
        if semanas is None:
            semanas = sorted({int(p["semana"]) for p in partidos})

    if semanas is None:
        semanas = semanas_con_partidos_recientes(partidos)
    resumen["semanas"] = sorted(semanas)
    if not semanas:
        return resumen

    try:
        filas = (
            cliente.table("results")
            .select("match_id, marcador_local, marcador_visitante, editado_manual")
            .limit(LIMITE)
            .execute()
        ).data or []
    except Exception as exc:
        if "editado_manual" in str(exc):
            raise RuntimeError(
                "La tabla `results` no tiene la columna `editado_manual`. "
                "Ejecuta sql/03_sincronizacion.sql en el SQL Editor de Supabase "
                "antes de sincronizar: sin esa columna no hay forma de proteger "
                "los marcadores corregidos a mano."
            ) from exc
        raise

    existentes = {r["match_id"]: r for r in filas}

    for semana in sorted(semanas):
        try:
            finales = marcadores_de_semana(semana)
        except RuntimeError as exc:
            resumen["errores"].append(f"Semana {semana}: {exc}")
            continue
        if not finales:
            continue

        for partido in [p for p in partidos if int(p["semana"]) == int(semana)]:
            clave = (
                resolver_equipo(partido.get("equipo_local", "")),
                resolver_equipo(partido.get("equipo_visitante", "")),
            )
            dato = finales.get(clave)
            if not dato:
                continue

            ml, mv = dato["marcador_local"], dato["marcador_visitante"]
            etiqueta = f"S{semana} {partido['equipo_local']} {ml}-{mv} {partido['equipo_visitante']}"
            previo = existentes.get(partido["id"])

            if previo and previo.get("editado_manual"):
                # Correccion manual: es la fuente de verdad, se respeta.
                if (previo["marcador_local"], previo["marcador_visitante"]) != (ml, mv):
                    resumen["protegidos"].append(
                        f"{etiqueta} (en la base: {previo['marcador_local']}-"
                        f"{previo['marcador_visitante']}, editado a mano)"
                    )
                else:
                    resumen["sin_cambio"] += 1
                continue

            payload = {
                "match_id": partido["id"],
                "marcador_local": ml,
                "marcador_visitante": mv,
                "ganador_oficial": ganador_por_marcador(ml, mv),
                "editado_manual": False,
            }

            if previo is None:
                if not simular:
                    cliente.table("results").insert(payload).execute()
                resumen["nuevos"].append(etiqueta)
            elif (previo["marcador_local"], previo["marcador_visitante"]) != (ml, mv):
                if not simular:
                    cliente.table("results").update(payload).eq(
                        "match_id", partido["id"]
                    ).execute()
                resumen["actualizados"].append(
                    f"{etiqueta} (antes {previo['marcador_local']}-{previo['marcador_visitante']})"
                )
            else:
                resumen["sin_cambio"] += 1

    return resumen


def semanas_con_partidos_recientes(partidos: list[dict], margen_dias: int = 4) -> list[int]:
    """
    Semanas cuyos partidos ya iniciaron dentro de los ultimos `margen_dias`.

    Evita barrer las 22 jornadas en cada ejecucion del cron: en un martes
    cualquiera solo interesa la semana que acaba de jugarse.
    """
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(days=margen_dias)
    semanas = set()
    for p in partidos:
        crudo = p.get("fecha_hora")
        if not crudo:
            continue
        try:
            fecha = datetime.fromisoformat(str(crudo).replace("Z", "+00:00"))
        except ValueError:
            continue
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        if desde <= fecha <= ahora:
            semanas.add(int(p["semana"]))
    return sorted(semanas)


def formatear_resumen(r: dict) -> str:
    lineas = []
    if r["semanas"]:
        lineas.append(f"Semanas revisadas: {', '.join(str(s) for s in r['semanas'])}")
    else:
        lineas.append("No hay jornadas con partidos recientes.")
    lineas.append(
        f"Nuevos: {len(r['nuevos'])} | Actualizados: {len(r['actualizados'])} | "
        f"Sin cambio: {r['sin_cambio']} | Protegidos: {len(r['protegidos'])}"
    )
    for titulo, clave in (("Nuevos", "nuevos"), ("Actualizados", "actualizados"),
                          ("Protegidos (edicion manual)", "protegidos"),
                          ("Errores", "errores")):
        if r[clave]:
            lineas.append(f"\n{titulo}:")
            lineas.extend(f"  - {x}" for x in r[clave])
    return "\n".join(lineas)


# -----------------------------------------------------------------------------
#  Entrada por linea de comandos (la usa GitHub Actions)
# -----------------------------------------------------------------------------
def cliente_desde_entorno():
    """
    Cliente con permisos de escritura.

    Prioriza SUPABASE_SERVICE_KEY (variables de entorno del cron); si no existe,
    cae a .streamlit/secrets.toml para pruebas locales.
    """
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")

    if not (url and key):
        ruta = Path(__file__).parent / ".streamlit" / "secrets.toml"
        if ruta.exists():
            import tomllib
            sec = tomllib.loads(ruta.read_text(encoding="utf-8"))
            url = url or sec.get("SUPABASE_URL")
            key = key or sec.get("SUPABASE_SERVICE_KEY") or sec.get("SUPABASE_KEY")

    if not (url and key):
        sys.exit(
            "Faltan credenciales. Define SUPABASE_URL y SUPABASE_SERVICE_KEY "
            "como variables de entorno, o llena .streamlit/secrets.toml."
        )
    return create_client(url, key)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sincroniza marcadores finales desde ESPN.")
    ap.add_argument("--semana", type=int, action="append",
                    help="jornada concreta (repetible: --semana 5 --semana 6)")
    ap.add_argument("--todas", action="store_true", help="barrer las 22 jornadas")
    ap.add_argument("--simular", action="store_true", help="no escribe, solo reporta")
    args = ap.parse_args()

    if args.todas:
        semanas = list(range(1, 23))
    elif args.semana:
        semanas = args.semana
    else:
        semanas = None   # deteccion automatica por fecha

    cliente = cliente_desde_entorno()
    try:
        resumen = sincronizar(cliente, semanas=semanas, simular=args.simular)
    except RuntimeError as exc:
        sys.exit(f"\n{exc}\n")

    if args.simular:
        print("[SIMULACION] No se escribio nada en la base.\n")
    print(formatear_resumen(resumen))

    if resumen["errores"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
