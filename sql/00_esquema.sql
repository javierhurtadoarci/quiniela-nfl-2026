-- =============================================================================
--  QUINIELA NFL 2026 - ESQUEMA DE TABLAS
-- =============================================================================
--  Ejecutar ANTES de 01_politicas_rls.sql
--  Supabase -> SQL Editor -> New query -> pegar -> Run
--
--  Si ya creaste las tablas desde el Table Editor, revisa que los tipos
--  coincidan (sobre todo `fecha_hora`, ver nota critica abajo).
-- =============================================================================


-- -----------------------------------------------------------------------------
--  1. users - perfil publico
--     Las contrasenas NO viven aqui: las gestiona Supabase Auth (auth.users).
--     Esta tabla solo guarda el nombre visible en el ranking.
-- -----------------------------------------------------------------------------
create table if not exists public.users (
    id          bigint generated always as identity primary key,
    username    text        not null,
    email       text        not null,
    creado_en   timestamptz not null default now()
);

-- El correo se normaliza a minusculas desde la app, pero lo garantizamos aqui.
create unique index if not exists users_email_uidx
    on public.users (lower(email));


-- -----------------------------------------------------------------------------
--  2. matches - calendario (272 juegos regulares + 13 de playoffs)
--
--  NOTA CRITICA sobre `fecha_hora`:
--  Debe ser `timestamptz`, NUNCA `timestamp` a secas. La app convierte la hora
--  a la zona horaria de cada usuario; si el tipo no lleva zona, Postgres
--  devuelve la hora "desnuda" y todos los kickoffs aparecen corridos varias
--  horas, con lo que el bloqueo automatico de picks se dispara mal.
--  Guarda siempre el kickoff en UTC.
--
--  semana: 1-18 temporada regular
--          19 Comodines | 20 Divisional | 21 Final de Conferencia | 22 Super Bowl
-- -----------------------------------------------------------------------------
create table if not exists public.matches (
    id                bigint generated always as identity primary key,
    semana            smallint    not null check (semana between 1 and 22),
    equipo_local      text        not null,
    equipo_visitante  text        not null,
    fecha_hora        timestamptz not null,
    bloqueado         boolean     not null default false
);

create index if not exists matches_semana_idx on public.matches (semana, fecha_hora);


-- -----------------------------------------------------------------------------
--  3. predictions - picks semanales
--     prediction admite exactamente: LOCAL | VISITANTE
--     El empate no se pronostica (es rarisimo en la NFL); si un partido termina
--     empatado ningun pick coincide y nadie suma puntos en ese partido.
-- -----------------------------------------------------------------------------
create table if not exists public.predictions (
    id          bigint generated always as identity primary key,
    email       text        not null,
    match_id    bigint      not null references public.matches (id) on delete cascade,
    prediction  text        not null check (prediction in ('LOCAL','VISITANTE')),
    creado_en   timestamptz not null default now()
);

-- Un solo pick por usuario y partido: evita duplicados por doble clic
-- o por tener la app abierta en dos pestanas.
create unique index if not exists predictions_email_match_uidx
    on public.predictions (lower(email), match_id);


-- -----------------------------------------------------------------------------
--  4. results - marcadores oficiales
--     Aqui EMPATE si es valido: la NFL permite empates y el marcador oficial
--     tiene que poder registrarlos, aunque nadie los pueda pronosticar.
-- -----------------------------------------------------------------------------
create table if not exists public.results (
    match_id           bigint primary key references public.matches (id) on delete cascade,
    marcador_local     smallint not null check (marcador_local     >= 0),
    marcador_visitante smallint not null check (marcador_visitante >= 0),
    ganador_oficial    text     not null check (ganador_oficial in ('LOCAL','VISITANTE','EMPATE')),
    actualizado_en     timestamptz not null default now()
);


-- -----------------------------------------------------------------------------
--  5. Verificacion
-- -----------------------------------------------------------------------------
--  Hubo dos tablas mas, `super_bowl_predictions` y `tournament_settings`, que
--  sostenian el pronostico de pretemporada (campeon y subcampeon) y el bonus de
--  10 y 5 puntos. Ese pronostico se elimino: la quiniela se decide solo con los
--  aciertos partido a partido. El Super Bowl sigue jugandose como el partido de
--  la jornada 22 y es el que mas vale, pero se acierta igual que cualquier otro.
--  Para retirarlas de una base ya creada, usa `08_eliminar_super_bowl.sql`.
-- -----------------------------------------------------------------------------
select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name in ('users','matches','predictions','results')
order by table_name, ordinal_position;
