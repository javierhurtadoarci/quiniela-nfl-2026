-- =============================================================================
--  QUITAR EL EMPATE DE LAS OPCIONES DE LA QUINIELA
-- =============================================================================
--  Los empates en la NFL son rarisimos, asi que el participante ya solo elige
--  entre los dos equipos. Este script deja la base alineada con la app.
--
--  OJO: solo cambia `predictions`. En `results` el EMPATE SIGUE SIENDO VALIDO,
--  porque un partido si puede terminar empatado y el marcador oficial tiene que
--  poder registrarlo. Cuando eso pasa, ningun pick coincide y nadie suma puntos.
--
--  Si tu base es nueva y ya corriste `00_esquema.sql` con esta version, no hace
--  falta ejecutar nada de aqui: la restriccion ya nace correcta.
-- =============================================================================

-- -----------------------------------------------------------------------------
--  1. Que picks de EMPATE hay guardados (ejecuta esto PRIMERO, por separado)
--     Si devuelve filas, el paso 2 las va a borrar: esos participantes se
--     quedan sin pronostico en esos partidos y tendran que volver a votar
--     (siempre que el partido no este bloqueado todavia).
-- -----------------------------------------------------------------------------
select p.email, p.match_id, m.semana, m.equipo_local, m.equipo_visitante
from public.predictions p
join public.matches m on m.id = p.match_id
where p.prediction = 'EMPATE'
order by m.semana, p.email;


-- -----------------------------------------------------------------------------
--  2. Borrar esos picks
--     Es obligatorio antes del paso 3: la nueva restriccion no puede crearse
--     mientras exista una sola fila con 'EMPATE'.
-- -----------------------------------------------------------------------------
delete from public.predictions
where prediction = 'EMPATE';


-- -----------------------------------------------------------------------------
--  3. Restringir prediction a LOCAL | VISITANTE
--     El nombre `predictions_prediction_check` es el que Postgres le puso a la
--     restriccion en linea de `00_esquema.sql`; lo reusamos para que el esquema
--     quede identico a una instalacion nueva.
-- -----------------------------------------------------------------------------
alter table public.predictions
    drop constraint if exists predictions_prediction_check;

alter table public.predictions
    add constraint predictions_prediction_check
    check (prediction in ('LOCAL','VISITANTE'));


-- -----------------------------------------------------------------------------
--  4. Verificacion
--     Espera ver la restriccion de `predictions` ya sin EMPATE y la de
--     `results` conservandolo.
-- -----------------------------------------------------------------------------
select rel.relname as tabla, con.conname as restriccion,
       pg_get_constraintdef(con.oid) as definicion
from pg_constraint con
join pg_class rel on rel.oid = con.conrelid
join pg_namespace nsp on nsp.oid = rel.relnamespace
where nsp.nspname = 'public'
  and rel.relname in ('predictions', 'results')
  and con.contype = 'c'
order by tabla, restriccion;
