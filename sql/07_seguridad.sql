-- =============================================================================
--  ENDURECIMIENTO DE SEGURIDAD
-- =============================================================================
--  Pegar completo en Supabase -> SQL Editor -> Run. Es idempotente: se puede
--  volver a ejecutar sin efectos secundarios.
--
--  QUE RESUELVE
--  ---------------------------------------------------------------------------
--  1. El cierre del voto al kickoff solo existia en la interfaz. Las politicas
--     anteriores comprobaban de quien era el pick, pero no CUANDO se escribia,
--     asi que cualquier participante podia llamar a la API de Supabase por su
--     cuenta y registrar o cambiar un pronostico con el partido ya terminado
--     -y `results` es de lectura abierta, o sea que ya sabia el marcador-.
--
--  2. Cualquiera podia insertar filas en `users` sin estar autenticado, con el
--     correo y el nombre que quisiera. Ahora el perfil lo crea un trigger.
--
--  3. La lista de correos de todos los participantes era legible sin sesion.
--
--  4. Los picks de los demas eran visibles con el partido aun abierto, con lo
--     que se podia copiar al lider o votar a lo seguro sabiendo a la mayoria.
--     Ahora se revelan al cerrarse el partido.
--
--  PRINCIPIO: la interfaz puede esconder un boton, pero solo la base de datos
--  puede impedir una escritura. Toda regla del juego se valida aqui.
-- =============================================================================


-- -----------------------------------------------------------------------------
--  1. Ventana de tiempo de un partido
--
--     `security definer` a proposito: esta funcion se evalua dentro de las
--     politicas RLS y no debe depender de que quien escribe pueda leer
--     `matches`. No filtra nada: devuelve un booleano.
-- -----------------------------------------------------------------------------
create or replace function public.partido_abierto(p_match_id bigint)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
           (select not m.bloqueado and m.fecha_hora > now()
              from public.matches m
             where m.id = p_match_id),
           false)   -- un partido inexistente nunca esta abierto
$$;

comment on function public.partido_abierto(bigint) is
  'Un partido admite picks solo si el admin no lo bloqueo y no ha llegado su kickoff.';


-- -----------------------------------------------------------------------------
--  2. predictions - el pick solo se toca con el partido abierto
--
--     El DELETE tambien lleva la restriccion, y no es un detalle menor: la
--     efectividad se calcula sobre los partidos ya cerrados que uno jugo, asi
--     que borrar un pick fallido despues del partido subiria el porcentaje.
--     El admin conserva el borrado para poder corregir incidencias.
--
--     El SELECT tambien cambia: los picks ajenos se revelan al cerrar el
--     partido, no antes. Mientras siga abierto, ver por quien voto el resto
--     permite copiar al que va lider o esperar al ultimo minuto para ir contra
--     la mayoria. Esconderlo solo en la pantalla no bastaba: la fila viajaba
--     igual al navegador y cualquiera podia consultarla desde la API.
-- -----------------------------------------------------------------------------
drop policy if exists predictions_select on public.predictions;
drop policy if exists predictions_insert on public.predictions;
drop policy if exists predictions_update on public.predictions;
drop policy if exists predictions_delete on public.predictions;

create policy predictions_select on public.predictions
  for select to authenticated
  using (
        lower(email) = mi_email()        -- el propio pick, siempre
     or not partido_abierto(match_id)    -- los ajenos, ya cerrado el partido
     or es_admin()                       -- el arbitro necesita ver la jornada
  );

create policy predictions_insert on public.predictions
  for insert to authenticated
  with check (lower(email) = mi_email() and partido_abierto(match_id));

create policy predictions_update on public.predictions
  for update to authenticated
  using       (lower(email) = mi_email() and partido_abierto(match_id))
  with check  (lower(email) = mi_email() and partido_abierto(match_id));

create policy predictions_delete on public.predictions
  for delete to authenticated
  using ((lower(email) = mi_email() and partido_abierto(match_id)) or es_admin());


-- -----------------------------------------------------------------------------
--  3. users - el perfil lo crea un trigger, no el navegador
--
--     La app ya no puede insertar el perfil al registrarse: con confirmacion
--     por correo activada no hay sesion todavia, y permitir esa escritura sin
--     autenticar es justo el agujero que se esta cerrando. El trigger corre del
--     lado del servidor cuando Supabase Auth da de alta la cuenta.
-- -----------------------------------------------------------------------------
create or replace function public.crear_perfil_usuario()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.users (username, email)
  select
      -- El nombre elegido en el formulario viaja en los metadatos del registro;
      -- si falta, se usa la parte local del correo.
      coalesce(nullif(btrim(new.raw_user_meta_data ->> 'username'), ''),
               split_part(new.email, '@', 1)),
      lower(new.email)
  where not exists (
      select 1 from public.users u where lower(u.email) = lower(new.email)
  );
  return new;
end;
$$;

drop trigger if exists crear_perfil_al_registrarse on auth.users;

create trigger crear_perfil_al_registrarse
  after insert on auth.users
  for each row execute function public.crear_perfil_usuario();


drop policy if exists users_select on public.users;
drop policy if exists users_insert on public.users;

-- Sin `anon`: la lista de participantes y sus correos exige sesion iniciada.
create policy users_select on public.users
  for select to authenticated
  using (true);

-- Queda solo como auto-reparacion de cuentas anteriores al trigger: el usuario
-- ya autenticado puede crear SU perfil, el de nadie mas.
create policy users_insert on public.users
  for insert to authenticated
  with check (lower(email) = mi_email());

-- users_update no cambia: la definio `05_avatares.sql` (propia fila o admin).


-- -----------------------------------------------------------------------------
--  4. Verificacion
-- -----------------------------------------------------------------------------

-- 4.1 Politicas vigentes. Espera ver `partido_abierto` en las cuatro de
--     predictions -select incluido, que es lo que oculta los picks ajenos hasta
--     el cierre- y ningun `{anon}` en las filas de users ni de predictions.
select tablename, policyname, cmd, roles,
       coalesce(qual, '-')        as using_expr,
       coalesce(with_check, '-')  as with_check_expr
from pg_policies
where schemaname = 'public'
  and tablename in ('users', 'predictions')
order by tablename, policyname;

-- 4.2 El trigger de alta de perfil quedo instalado
select tgname as trigger, tgrelid::regclass as tabla, tgenabled as habilitado
from pg_trigger
where tgname = 'crear_perfil_al_registrarse';

-- 4.3 Prueba de la ventana de tiempo: cuantos partidos siguen aceptando picks
select count(*) filter (where partido_abierto(id))       as partidos_abiertos,
       count(*) filter (where not partido_abierto(id))   as partidos_cerrados
from public.matches;
