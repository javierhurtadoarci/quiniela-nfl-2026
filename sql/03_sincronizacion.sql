-- =============================================================================
--  SINCRONIZACION AUTOMATICA DE RESULTADOS
-- =============================================================================
--  Pegar en Supabase -> SQL Editor -> Run.
--  Habilita que el sincronizador escriba marcadores sin pisar tus correcciones.
-- =============================================================================

-- -----------------------------------------------------------------------------
--  1. Marca de edicion manual
--     true  = un humano capturo o corrigio este marcador -> el sincronizador
--             NUNCA lo sobrescribe.
--     false = viene de ESPN -> se actualiza libremente si el dato cambia.
-- -----------------------------------------------------------------------------
alter table public.results
    add column if not exists editado_manual boolean not null default false;

-- Las filas que ya existian se capturaron a mano desde el Panel Admin,
-- asi que quedan protegidas.
update public.results
   set editado_manual = true
 where editado_manual is distinct from true;


-- -----------------------------------------------------------------------------
--  2. Rastro de la ultima sincronizacion (util para diagnosticar el cron)
-- -----------------------------------------------------------------------------
alter table public.results
    add column if not exists sincronizado_en timestamptz;


-- -----------------------------------------------------------------------------
--  3. Verificacion
-- -----------------------------------------------------------------------------
select column_name, data_type, column_default
from information_schema.columns
where table_schema = 'public' and table_name = 'results'
order by ordinal_position;
