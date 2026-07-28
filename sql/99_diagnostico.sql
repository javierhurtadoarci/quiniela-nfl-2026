-- =============================================================================
--  DIAGNOSTICO RAPIDO - que existe realmente en la base
-- =============================================================================
--  Pegar en el SQL Editor y ejecutar. No modifica nada.
-- =============================================================================

-- 1. Tablas que existen en el schema public
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;

-- Si el resultado sale VACIO -> el esquema no se creo: ejecuta 00_esquema.sql.
-- Si aparecen las 6 tablas -> el problema fue otro (ver notas abajo).
