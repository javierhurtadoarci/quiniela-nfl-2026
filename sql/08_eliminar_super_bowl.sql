-- =============================================================================
--  ELIMINAR EL PRONOSTICO DE SUPER BOWL
-- =============================================================================
--  La quiniela se decide solo con los aciertos partido a partido. Desaparece la
--  apuesta de pretemporada (campeon y subcampeon) y con ella el bonus de 10 y 5
--  puntos, que podia dar la vuelta a la tabla sin acertar un solo partido.
--
--  EL PARTIDO DE SUPER BOWL NO SE TOCA. Sigue en el calendario como la jornada
--  22 y es el que mas vale (5 puntos), pero se acierta como cualquier otro:
--  eligiendo al ganador. Su marcador se captura en Panel Admin -> Marcadores.
--
--  ATENCION: es irreversible. Si algun participante ya habia guardado su
--  pronostico, ese dato se pierde. Corre PRIMERO la seccion 1 para ver que hay.
--
--  La app ya no lee ninguna de estas dos tablas, asi que puedes dejarlas en la
--  base sin ningun efecto. Esto es solo para no cargar con esquema muerto.
-- =============================================================================


-- -----------------------------------------------------------------------------
--  1. Que se va a perder (ejecuta esto PRIMERO, por separado)
-- -----------------------------------------------------------------------------
select email, campeon, subcampeon, creado_en
from public.super_bowl_predictions
order by creado_en;

select * from public.tournament_settings;


-- -----------------------------------------------------------------------------
--  2. Como quedaria el ranking sin el bonus
--
--     Util antes de borrar: si alguien lidera solo gracias a los 10 puntos del
--     campeon, aqui se ve. Cuenta los aciertos ya calificados de cada quien,
--     que es exactamente lo que la app usa ahora para ordenar la tabla.
-- -----------------------------------------------------------------------------
select p.email,
       count(*) filter (
           where (r.ganador_oficial = 'LOCAL'     and p.prediction = 'LOCAL')
              or (r.ganador_oficial = 'VISITANTE' and p.prediction = 'VISITANTE')
       ) as aciertos,
       count(*) as partidos_calificados
from public.predictions p
join public.results r on r.match_id = p.match_id
group by p.email
order by aciertos desc, p.email;


-- -----------------------------------------------------------------------------
--  3. Eliminar
--     Las politicas RLS y los indices asociados se van con la tabla; no hace
--     falta borrarlos uno por uno.
-- -----------------------------------------------------------------------------
drop table if exists public.super_bowl_predictions;
drop table if exists public.tournament_settings;

-- Solo la usaban las politicas de la tabla que acaba de desaparecer.
drop function if exists public.super_bowl_abierto();


-- -----------------------------------------------------------------------------
--  4. Verificacion
--     Espera cuatro tablas: users, matches, predictions y results.
-- -----------------------------------------------------------------------------
select table_name
from information_schema.tables
where table_schema = 'public' and table_type = 'BASE TABLE'
order by table_name;

-- Y que no quede rastro de las funciones ni politicas del pronostico
select proname as funcion
from pg_proc
where pronamespace = 'public'::regnamespace
  and proname in ('partido_abierto', 'super_bowl_abierto', 'es_admin', 'mi_email')
order by proname;
