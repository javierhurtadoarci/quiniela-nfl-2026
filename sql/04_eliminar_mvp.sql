-- =============================================================================
--  ELIMINAR EL PRONOSTICO DE MVP
-- =============================================================================
--  OPCIONAL. La aplicacion ya no lee ni escribe la columna `mvp`, asi que
--  puedes dejarla en la base sin ningun efecto. Ejecuta esto solo si prefieres
--  limpiar el esquema.
--
--  ATENCION: es irreversible. Si algun participante ya habia guardado un MVP,
--  ese dato se pierde. Revisa primero que haya que perder con el SELECT de la
--  seccion 1 antes de correr el DROP.
-- =============================================================================

-- -----------------------------------------------------------------------------
--  1. Que se va a perder (ejecuta esto PRIMERO, por separado)
-- -----------------------------------------------------------------------------
select email, campeon, subcampeon, mvp
from public.super_bowl_predictions
where mvp is not null and btrim(mvp) <> '';


-- -----------------------------------------------------------------------------
--  2. Eliminar la columna (solo si el SELECT anterior no devolvio nada
--     que te importe conservar)
-- -----------------------------------------------------------------------------
alter table public.super_bowl_predictions
    drop column if exists mvp;


-- -----------------------------------------------------------------------------
--  3. Verificacion
-- -----------------------------------------------------------------------------
select column_name, data_type
from information_schema.columns
where table_schema = 'public' and table_name = 'super_bowl_predictions'
order by ordinal_position;
