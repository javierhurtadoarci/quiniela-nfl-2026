-- =============================================================================
--  QUINIELA NFL 2026 - POLITICAS RLS
-- =============================================================================
--  Pegar completo en Supabase -> SQL Editor -> Run.
--
--  IMPORTANTE: cambia el correo de la linea de abajo por TU correo de admin
--  (el mismo que pongas en ADMIN_EMAIL de secrets.toml).
--
--  IMPORTANTE 2: despues de este archivo hay que ejecutar `07_seguridad.sql`,
--  que sustituye las politicas de `users` y `predictions` por versiones que
--  ademas validan la ventana de tiempo (no se puede votar ni ver los picks
--  ajenos de un partido ya empezado) y cierran la escritura anonima en `users`.
--  Las de aqui se conservan tal cual para no duplicar en dos archivos
--  definiciones que podrian quedar desincronizadas: son la base sobre la que 07
--  aplica el endurecimiento.
-- =============================================================================

-- -----------------------------------------------------------------------------
--  0. Funcion auxiliar: identifica al administrador por el correo de su JWT
-- -----------------------------------------------------------------------------
create or replace function public.es_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select lower(coalesce(auth.jwt() ->> 'email', '')) = lower('CAMBIA_ESTO@dominio.com');
$$;

-- Correo del usuario autenticado, normalizado a minusculas
create or replace function public.mi_email()
returns text
language sql
stable
as $$
  select lower(coalesce(auth.jwt() ->> 'email', ''));
$$;


-- -----------------------------------------------------------------------------
--  1. Activar RLS en todas las tablas
-- -----------------------------------------------------------------------------
alter table public.users                    enable row level security;
alter table public.matches                  enable row level security;
alter table public.predictions              enable row level security;
alter table public.results                  enable row level security;


-- -----------------------------------------------------------------------------
--  2. users - perfiles publicos
--     Todos los participantes se ven entre si (necesario para el ranking y para
--     mostrar "quien voto que"). Cada quien solo puede crear/editar su fila.
--
--     SUSTITUIDAS POR 07_seguridad.sql: alli el SELECT deja de estar abierto a
--     `anon` (exponia los correos de todos) y el INSERT deja de aceptar
--     escritura sin sesion; el perfil pasa a crearlo un trigger.
-- -----------------------------------------------------------------------------
drop policy if exists users_select   on public.users;
drop policy if exists users_insert   on public.users;
drop policy if exists users_update   on public.users;

create policy users_select on public.users
  for select to anon, authenticated
  using (true);

-- anon incluido: al registrarse con confirmacion de correo activada, el perfil
-- se inserta antes de que exista sesion.
create policy users_insert on public.users
  for insert to anon, authenticated
  with check (true);

create policy users_update on public.users
  for update to authenticated
  using (lower(email) = mi_email())
  with check (lower(email) = mi_email());


-- -----------------------------------------------------------------------------
--  3. matches - calendario (272 juegos + playoffs)
--     Lectura para todos; solo el admin modifica bloqueos u horarios.
-- -----------------------------------------------------------------------------
drop policy if exists matches_select on public.matches;
drop policy if exists matches_admin  on public.matches;

create policy matches_select on public.matches
  for select to anon, authenticated
  using (true);

create policy matches_admin on public.matches
  for all to authenticated
  using (es_admin())
  with check (es_admin());


-- -----------------------------------------------------------------------------
--  4. predictions - picks semanales
--     Lectura abierta (la app muestra quien voto por cada equipo).
--     Escritura restringida a las filas del propio usuario.
--
--     SUSTITUIDAS POR 07_seguridad.sql: estas comprueban de quien es el pick
--     pero no cuando se escribe, asi que permiten votar un partido ya jugado
--     llamando a la API directamente.
-- -----------------------------------------------------------------------------
drop policy if exists predictions_select on public.predictions;
drop policy if exists predictions_insert on public.predictions;
drop policy if exists predictions_update on public.predictions;
drop policy if exists predictions_delete on public.predictions;

create policy predictions_select on public.predictions
  for select to authenticated
  using (true);

create policy predictions_insert on public.predictions
  for insert to authenticated
  with check (lower(email) = mi_email());

create policy predictions_update on public.predictions
  for update to authenticated
  using (lower(email) = mi_email())
  with check (lower(email) = mi_email());

create policy predictions_delete on public.predictions
  for delete to authenticated
  using (lower(email) = mi_email() or es_admin());


-- -----------------------------------------------------------------------------
--  5. results - marcadores oficiales
--     Lectura para todos; captura exclusiva del admin.
-- -----------------------------------------------------------------------------
drop policy if exists results_select on public.results;
drop policy if exists results_admin  on public.results;

create policy results_select on public.results
  for select to anon, authenticated
  using (true);

create policy results_admin on public.results
  for all to authenticated
  using (es_admin())
  with check (es_admin());


-- -----------------------------------------------------------------------------
--  6. Integridad: un solo pick por usuario y partido
--     Evita duplicados si el usuario da doble clic o abre dos pestanas.
-- -----------------------------------------------------------------------------
create unique index if not exists predictions_email_match_uidx
  on public.predictions (lower(email), match_id);

create unique index if not exists users_email_uidx
  on public.users (lower(email));


-- -----------------------------------------------------------------------------
--  7. Verificacion
-- -----------------------------------------------------------------------------
select tablename,
       rowsecurity as rls_activo,
       (select count(*) from pg_policies p where p.tablename = t.tablename) as politicas
from pg_tables t
where schemaname = 'public'
  and tablename in ('users','matches','predictions','results')
order by tablename;
