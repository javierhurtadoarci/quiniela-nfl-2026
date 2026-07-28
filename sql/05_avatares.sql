-- =============================================================================
--  AVATARES DE PARTICIPANTES
-- =============================================================================
--  Pegar completo en Supabase -> SQL Editor -> Run.
--  Crea la columna, el bucket de Storage y sus politicas de acceso.
-- =============================================================================

-- -----------------------------------------------------------------------------
--  1. Columna en `users`
--     Guarda la URL final del avatar, venga de donde venga:
--       - logo NFL  -> https://a.espncdn.com/i/teamlogos/nfl/500/kc.png
--       - foto      -> https://TU-PROYECTO.supabase.co/storage/v1/.../uuid.webp
--       - NULL      -> la app genera un avatar con las iniciales
-- -----------------------------------------------------------------------------
alter table public.users
    add column if not exists avatar_url text;


-- -----------------------------------------------------------------------------
--  2. Bucket de Storage
--     public = true  -> las imagenes se sirven por URL directa y el navegador
--                       las cachea. Los nombres de archivo son UUIDs de Auth,
--                       no correos, asi que no se filtra informacion personal.
--     El limite de 2 MB es una red de seguridad del lado del servidor: la app
--     ya reduce cada foto a ~20 KB antes de subirla.
-- -----------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'avatares',
    'avatares',
    true,
    2097152,
    array['image/webp', 'image/jpeg', 'image/png']
)
on conflict (id) do update
   set public             = excluded.public,
       file_size_limit    = excluded.file_size_limit,
       allowed_mime_types = excluded.allowed_mime_types;


-- -----------------------------------------------------------------------------
--  3. Politicas del bucket
--     Cada participante solo puede escribir SU archivo: el nombre debe ser
--     exactamente su id de Supabase Auth. Sin esta restriccion, cualquiera
--     podria reemplazar el avatar de otro con la imagen que quisiera.
-- -----------------------------------------------------------------------------
drop policy if exists avatares_lectura  on storage.objects;
drop policy if exists avatares_subir    on storage.objects;
drop policy if exists avatares_cambiar  on storage.objects;
drop policy if exists avatares_borrar   on storage.objects;

-- Lectura publica: hace falta para que <img src> funcione sin autenticacion.
create policy avatares_lectura on storage.objects
    for select to anon, authenticated
    using (bucket_id = 'avatares');

create policy avatares_subir on storage.objects
    for insert to authenticated
    with check (
        bucket_id = 'avatares'
        and name = auth.uid()::text || '.webp'
    );

create policy avatares_cambiar on storage.objects
    for update to authenticated
    using (
        bucket_id = 'avatares'
        and name = auth.uid()::text || '.webp'
    );

-- El borrado tambien lo puede hacer el administrador, para retirar una imagen
-- inapropiada sin tener que entrar al dashboard de Supabase.
create policy avatares_borrar on storage.objects
    for delete to authenticated
    using (
        bucket_id = 'avatares'
        and (name = auth.uid()::text || '.webp' or es_admin())
    );


-- -----------------------------------------------------------------------------
--  4. El administrador puede limpiar el avatar de cualquier participante
--     (la politica users_update original solo dejaba editar la propia fila).
-- -----------------------------------------------------------------------------
drop policy if exists users_update on public.users;

create policy users_update on public.users
    for update to authenticated
    using (lower(email) = mi_email() or es_admin())
    with check (lower(email) = mi_email() or es_admin());


-- -----------------------------------------------------------------------------
--  5. Verificacion
-- -----------------------------------------------------------------------------
select 'columna' as objeto,
       column_name as nombre,
       data_type as detalle
  from information_schema.columns
 where table_schema = 'public' and table_name = 'users' and column_name = 'avatar_url'
union all
select 'bucket', id, 'publico=' || public::text
  from storage.buckets where id = 'avatares'
union all
select 'politica', policyname, cmd
  from pg_policies
 where tablename = 'objects' and policyname like 'avatares%';
