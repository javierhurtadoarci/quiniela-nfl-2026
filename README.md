# Quiniela NFL 2026

Plataforma web de pronósticos *Pick'em* para la temporada 2026 de la NFL.
Cada participante elige al ganador directo de cada partido (Moneyline, sin
spread) y compite en un ranking global.

Construida con **Streamlit** + **Supabase** (PostgreSQL, Auth y Storage).

---

## Qué incluye

| Sección | Contenido |
|---|---|
| 🏈 Mis Picks | Los 272 partidos por jornada, con logos, hora local y bloqueo automático al kickoff. Al cerrarse cada partido se destapan las barras de votación de la comunidad y quién votó por cada equipo. |
| 🏆 Posiciones NFL | Récords reales W-L-T por conferencia y división, calculados desde los marcadores. |
| 📊 Ranking Global | Tabla general con avatares, podio y desglose de puntos. |
| ⚙️ Panel Admin | Sincronización con ESPN, captura de marcadores, bloqueos y moderación de avatares. |

### Puntuación

Todo se gana partido a partido: se acierta eligiendo al ganador directo. No hay
bonus de pretemporada ni apuestas paralelas.

| Jornada | Puntos por acierto |
|---|---|
| Semanas 1–18 | 1 |
| Comodines | 2 |
| Divisional | 3 |
| Final de Conferencia | 4 |
| Super Bowl | 5 |

Las rondas finales valen más para que la quiniela siga viva en enero en vez de
quedar decidida en noviembre. El Super Bowl es el partido que más pesa, y se
acierta como cualquier otro.

---

## Instalación

### 1. Base de datos

Ejecuta los archivos de [`sql/`](sql/) **en orden** desde el SQL Editor de
Supabase:

| Archivo | Qué hace |
|---|---|
| `00_esquema.sql` | Crea las 4 tablas |
| `02_calendario.sql` | Carga los 272 partidos |
| `01_politicas_rls.sql` | Políticas de seguridad — **edita la línea 17 con tu correo de admin** |
| `03_sincronizacion.sql` | Habilita la sincronización automática |
| `05_avatares.sql` | Columna de avatar y bucket de Storage |
| `07_seguridad.sql` | **Obligatorio.** Cierra el voto al kickoff del lado del servidor y crea el perfil por trigger |

`99_diagnostico.sql` es opcional. Los siguientes solo hacen falta en bases
creadas antes del cambio que describen; en una instalación nueva no aplican:

| Archivo | Para qué |
|---|---|
| `06_sin_empates.sql` | Quita el empate de las opciones de voto |
| `08_eliminar_super_bowl.sql` | Borra el pronóstico de pretemporada y su bonus |
| `04_eliminar_mvp.sql` | Obsoleto: el `08` ya borra esa tabla entera |

> **`07_seguridad.sql` va después de `01` y de `05`**, porque reemplaza políticas
> que ambos definen. Sin él, un participante puede llamar a la API de Supabase
> por su cuenta y registrar un pick con el partido ya terminado: el bloqueo al
> kickoff que ves en pantalla es solo la interfaz.

> **No vuelvas a ejecutar `02_calendario.sql`.** Solo inserta y es para la carga
> inicial. Para agregar playoffs o corregir horarios usa el script de Python.

En **Authentication → Providers → Email**, decide si dejas activada la
confirmación por correo. Con `07_seguridad.sql` da igual cuál elijas: el perfil
público lo crea un trigger al dar de alta la cuenta, ya no la app.

Además, en **Authentication → Policies / Rate limits** conviene activar:

- **Longitud mínima de contraseña: 8** (la app ya la exige, esto lo respalda en el servidor)
- **Protección contra contraseñas filtradas** (`Leaked password protection`)
- Los **límites de intentos** por hora, que acotan la fuerza bruta contra el login

### 2. Credenciales

Copia la plantilla y llénala con tus claves:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

```toml
SUPABASE_URL = "https://TU-PROYECTO.supabase.co"   # sin /rest/v1/ al final
SUPABASE_KEY = "TU_ANON_O_PUBLISHABLE_KEY"          # nunca la service_role
ADMIN_EMAIL  = "tucorreo@dominio.com"               # igual que en 01_politicas_rls.sql
```

### 3. Ejecutar

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Mantenimiento durante la temporada

### Resultados

El botón **Panel Admin → Sincronizar** trae los marcadores finales desde ESPN.
Se puede sincronizar por jornadas recientes, una jornada o un partido concreto.

Los marcadores capturados a mano quedan marcados como `editado_manual` y la
sincronización **nunca los sobrescribe**.

Para que corra solo, el workflow de GitHub Actions se ejecuta viernes, lunes y
martes de madrugada. Requiere dos secretos en el repositorio:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY` — la clave `service_role`, **solo aquí**, nunca en el código

### Calendario

```bash
# Ver cambios sin escribir (recomendado cada par de semanas por el flex scheduling)
python cargar_calendario.py --subir --simular

# Aplicarlos
python cargar_calendario.py --subir

# Cargar playoffs (a partir de enero, cuando ESPN los publique)
python cargar_calendario.py --solo-playoffs --subir
```

La carga es idempotente: compara contra lo existente, nunca duplica ni borra.

---

## Estructura

```
app.py                  Aplicación Streamlit
equipos.py              Catálogo de los 32 equipos y sus logotipos
avatares.py             Avatares: iniciales, logos de equipo y fotos
sincronizador.py        Motor de sincronización con ESPN
cargar_calendario.py    Carga y actualización del calendario
sql/                    Scripts de base de datos
.github/workflows/      Cron de sincronización
```

---

## Notas de seguridad

- Todas las tablas tienen **RLS activo**. La clave pública solo puede hacer lo
  que las políticas permiten.
- Solo el correo de `ADMIN_EMAIL` puede capturar marcadores, y esa restricción
  vive en la base de datos, no en la interfaz.
- **El cierre al kickoff se impone en la base**, no en la pantalla: un pick no
  se puede registrar, cambiar ni borrar con el partido ya empezado, y los picks
  de los demás no son legibles hasta que cierra.
- La sesión persiste guardando **solo el refresh token** de Supabase Auth. La
  identidad siempre se deriva de la sesión que Supabase valida, nunca de una
  cookie: el navegador no decide quién eres.
- Los nombres de usuario se escapan antes de renderizarse y las URLs de avatar
  se validan: solo `https://` o `data:image/` de formatos rasterizados.
- Cada avatar se guarda con el UUID de Supabase Auth, no con el correo, porque
  el bucket es público.
- La cuenta administradora no participa en la quiniela ni aparece en el ranking.

> Los participantes autenticados pueden leer la tabla `users`, que incluye los
> correos. Es aceptable en un grupo de conocidos; si vas a abrir la quiniela a
> desconocidos, conviene revisar esa política.

---

## Créditos

Calendario y marcadores desde la API pública de ESPN. Los logotipos son marcas
registradas de la NFL y sus equipos, usados aquí con fines identificativos en un
proyecto sin ánimo de lucro.
