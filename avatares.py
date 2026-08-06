# =============================================================================
#  AVATARES DE PARTICIPANTES
# =============================================================================
#  Tres niveles, pensados para gente sin experiencia tecnica:
#
#    1. INICIALES  - automatico, nadie tiene que hacer nada. Se genera un SVG
#                    con las iniciales y un color derivado del nombre.
#    2. LOGO NFL   - un clic para elegir entre los 32 equipos. Sin archivos.
#    3. FOTO       - se sube desde la galeria del celular y la app la reduce
#                    de ~6 MB a ~20 KB sin que el usuario haga nada.
#
#  El nivel 3 es el unico que toca Supabase Storage; los otros dos no requieren
#  subir nada. La mayoria de los participantes se quedara en 1 o 2.
# =============================================================================

from __future__ import annotations

import base64
import hashlib
from io import BytesIO

from equipos import LOGO_BASE, LOGO_ORIGINAL, NFL_TEAMS, resolver_equipo

BUCKET = "avatares"

# Lado del avatar en pixeles. 256 se ve nitido en pantallas retina y pesa poco.
LADO = 256
CALIDAD_WEBP = 82

# Tope de entrada. Una foto de celular ronda 3-8 MB; 8 da margen suficiente
# sin permitir que alguien suba un archivo absurdo.
MAX_MB_ENTRADA = 8

FORMATOS_ACEPTADOS = ["png", "jpg", "jpeg", "webp", "heic", "heif", "bmp"]

# Paleta de fondos para las iniciales. Colores oscuros y saturados: el texto
# siempre va en blanco, asi que el contraste queda garantizado sin calcularlo.
PALETA = [
    "#0F4C81", "#8C1D40", "#155E3F", "#5B2C6F", "#8A4B08",
    "#1B4965", "#7B2D26", "#2D5F3F", "#4A3B76", "#96500F",
    "#134E5E", "#6B2737",
]


# -----------------------------------------------------------------------------
#  Nivel 1: avatar generado a partir del nombre
# -----------------------------------------------------------------------------
def _iniciales(nombre: str) -> str:
    """
    Hasta dos letras del nombre. Solo se conservan caracteres alfanumericos:
    el resultado se inserta en un SVG, y un '<' suelto -aunque no alcance para
    formar una etiqueta- dejaria el documento mal formado y la imagen no
    cargaria.
    """
    limpio = "".join(c for c in str(nombre or "") if c.isalnum() or c.isspace())
    partes = [p for p in limpio.strip().split() if p]
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def _color_de(texto: str) -> str:
    """Color estable: el mismo nombre siempre produce el mismo fondo."""
    h = hashlib.sha256(str(texto or "").encode("utf-8")).digest()
    return PALETA[h[0] % len(PALETA)]


def avatar_iniciales(nombre: str, lado: int = LADO) -> str:
    """
    Data URI de un SVG con las iniciales. No requiere archivos ni red, asi que
    funciona incluso antes de que el participante configure nada.
    """
    iniciales = _iniciales(nombre)
    color = _color_de(nombre)
    # Dos letras necesitan tipografia mas chica que una para no desbordar.
    tamano_fuente = int(lado * (0.38 if len(iniciales) > 1 else 0.46))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{lado}" height="{lado}" '
        f'viewBox="0 0 {lado} {lado}">'
        f'<rect width="{lado}" height="{lado}" rx="{lado // 2}" fill="{color}"/>'
        f'<text x="50%" y="50%" dy="0.35em" text-anchor="middle" fill="#FFFFFF" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif" '
        f'font-size="{tamano_fuente}" font-weight="700">{iniciales}</text>'
        f"</svg>"
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


# -----------------------------------------------------------------------------
#  Nivel 2: logotipo de un equipo NFL
# -----------------------------------------------------------------------------
def avatar_de_equipo(equipo: str) -> str | None:
    abrev = resolver_equipo(equipo)
    return LOGO_BASE.format(abrev.lower()) if abrev else None


def es_avatar_de_equipo(url: str | None) -> str | None:
    """
    Si la URL corresponde a un logo NFL, devuelve la abreviatura del equipo.

    Compara por la ruta del archivo y no por la URL completa: asi sigue
    reconociendo los avatares guardados antes de que los logos pasaran por el
    redimensionador del CDN.
    """
    if not url:
        return None
    texto = str(url)
    if "/teamlogos/nfl/" not in texto:
        return None
    for abrev in NFL_TEAMS:
        if f"/{abrev.lower()}.png" in texto:
            return abrev
    return None


# -----------------------------------------------------------------------------
#  Nivel 3: foto propia
# -----------------------------------------------------------------------------
def procesar_imagen(datos: bytes, lado: int = LADO) -> bytes:
    """
    Convierte cualquier foto en un WebP cuadrado de `lado` px.

    Hace tres cosas que el usuario no deberia tener que saber:
      - corrige la orientacion EXIF (fotos de celular que salen acostadas),
      - recorta al centro para dejarla cuadrada sin deformar,
      - comprime a WebP, que a esta escala pesa ~20 KB.
    """
    from PIL import Image, ImageOps   # import diferido: solo hace falta al subir

    img = Image.open(BytesIO(datos))
    img = ImageOps.exif_transpose(img)      # respeta como se tomo la foto

    # Fondo blanco para PNG/WebP con transparencia; sin esto el canal alfa se
    # vuelve negro al convertir a RGB.
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fondo = Image.new("RGB", img.size, (255, 255, 255))
        fondo.paste(img, mask=img.split()[-1])
        img = fondo
    else:
        img = img.convert("RGB")

    img = ImageOps.fit(img, (lado, lado), method=Image.LANCZOS, centering=(0.5, 0.4))

    buffer = BytesIO()
    img.save(buffer, format="WEBP", quality=CALIDAD_WEBP, method=6)
    return buffer.getvalue()


def ruta_en_bucket(auth_uid: str) -> str:
    """
    Nombre del archivo dentro del bucket.

    Se usa el id de Supabase Auth, no el correo: el bucket es publico y sus
    nombres de archivo son visibles, asi que un `javier@gmail.com.webp` estaria
    filtrando el correo de cada participante. Ademas permite que la politica de
    Storage exija que cada quien solo escriba su propio archivo.
    """
    return f"{auth_uid}.webp"


def subir_avatar(cliente, auth_uid: str, datos: bytes) -> str:
    """Procesa, sube al bucket y devuelve la URL publica."""
    procesada = procesar_imagen(datos)
    ruta = ruta_en_bucket(auth_uid)

    cliente.storage.from_(BUCKET).upload(
        path=ruta,
        file=procesada,
        file_options={
            "content-type": "image/webp",
            "cache-control": "3600",
            "upsert": "true",     # reemplaza el avatar anterior
        },
    )
    url = cliente.storage.from_(BUCKET).get_public_url(ruta)

    # Sufijo anti-cache: sin esto el navegador sigue mostrando la foto vieja
    # porque la URL no cambio. El hash del contenido solo se mueve si la
    # imagen cambio de verdad.
    firma = hashlib.md5(procesada).hexdigest()[:8]
    return f"{url.split('?')[0]}?v={firma}"


def borrar_avatar(cliente, auth_uid: str) -> None:
    """Elimina el archivo del bucket. Silencioso si no existia."""
    try:
        cliente.storage.from_(BUCKET).remove([ruta_en_bucket(auth_uid)])
    except Exception:
        pass


# -----------------------------------------------------------------------------
#  Resolucion final
# -----------------------------------------------------------------------------
# Solo https y data URIs de formatos rasterizados. Se excluye `data:image/svg+xml`
# a proposito: un SVG es un documento y puede traer <script> dentro. Dentro de un
# <img> el navegador no lo ejecuta, pero basta con que alguien copie esa misma
# URL a otro contexto para que si corra, y la app no gana nada permitiendolo: los
# avatares generados son SVG en memoria, no valores guardados en la base.
ESQUEMAS_PERMITIDOS = (
    "https://",
    "data:image/webp;",
    "data:image/png;",
    "data:image/jpeg;",
)


def url_segura(valor: str | None) -> str | None:
    """
    Acepta la URL solo si apunta a una imagen por https o es un data URI.

    `users.avatar_url` la puede escribir cada participante en su propia fila a
    traves de la API de Supabase, sin pasar por la interfaz. Sin esta validacion
    alguien podria guardar ahi `javascript:...` o texto con comillas para
    romper el atributo src y ejecutar codigo en el navegador de los demas,
    ya que ese valor se renderiza en el ranking que todos ven.
    """
    if not valor:
        return None
    texto = str(valor).strip()
    if not texto or not texto.lower().startswith(ESQUEMAS_PERMITIDOS):
        return None
    # Ningun caracter que permita salir del atributo o inyectar etiquetas.
    if any(c in texto for c in ('"', "'", "<", ">", "\n", "\r", "\t", " ")):
        return None
    return texto


def avatar_de(nombre: str, avatar_url: str | None, lado: int = LADO) -> str:
    """
    Imagen a mostrar para un participante.

    Si no configuro nada -o si lo guardado no supera la validacion- cae en las
    iniciales generadas: nunca queda vacio ni se propaga un valor sospechoso.
    """
    segura = url_segura(avatar_url)
    return segura if segura else avatar_iniciales(nombre, lado)


def peso_legible(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.0f} KB"
    return f"{n_bytes / (1024 * 1024):.1f} MB"
