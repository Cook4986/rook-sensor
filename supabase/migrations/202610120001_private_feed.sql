-- Pilot: raw image bytes and receipt commit in the same Postgres transaction.
-- No bucket, public URL, client write grants or browser service credential.
begin;
create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;
create schema if not exists rook_private;
revoke all on schema rook_private from public, anon, authenticated;

create table public.rook_owners (
  singleton boolean primary key default true check(singleton),
  user_id uuid not null unique references auth.users(id)
);
create table public.rook_devices (
  device_id text primary key check(device_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'),
  owner_id uuid not null references public.rook_owners(user_id),
  event_count integer not null default 0,
  used_bytes bigint not null default 0,
  last_received_at timestamptz
);
create table rook_private.device_tokens (
  token_hash text primary key check(token_hash ~ '^[a-f0-9]{64}$'),
  device_id text not null references public.rook_devices(device_id),
  revoked_at timestamptz
);
create table rook_private.models (
  device_id text not null references public.rook_devices(device_id),
  artifact_sha256 text not null check(artifact_sha256 ~ '^[a-f0-9]{64}$'),
  classes_sha256 text not null check(classes_sha256 ~ '^[a-f0-9]{64}$'),
  manifest jsonb not null,
  primary key(device_id,artifact_sha256,classes_sha256)
);
create table public.rook_events (
  device_id text not null references public.rook_devices(device_id),
  event_id uuid not null,
  owner_id uuid not null references public.rook_owners(user_id),
  event_sha256 text not null,
  event_json text not null,
  observation jsonb not null,
  captured_at timestamptz not null,
  received_at timestamptz not null default clock_timestamp(),
  primary key(device_id,event_id)
);
create index rook_events_feed on public.rook_events(owner_id,received_at desc,event_id);
create table public.rook_images (
  device_id text not null,
  event_id uuid not null,
  owner_id uuid not null references public.rook_owners(user_id),
  content bytea not null check(octet_length(content) between 1 and 2097152),
  primary key(device_id,event_id),
  foreign key(device_id,event_id) references public.rook_events(device_id,event_id) on delete cascade
);
alter table public.rook_owners enable row level security;
alter table public.rook_devices enable row level security;
alter table public.rook_events enable row level security;
alter table public.rook_images enable row level security;
create policy owner_identity on public.rook_owners for select to authenticated using(user_id=(select auth.uid()));
create policy owner_devices on public.rook_devices for select to authenticated using(owner_id=(select auth.uid()));
create policy owner_events on public.rook_events for select to authenticated using(owner_id=(select auth.uid()));
create policy owner_images on public.rook_images for select to authenticated using(owner_id=(select auth.uid()));
revoke all on public.rook_owners,public.rook_devices,public.rook_events,public.rook_images from anon,authenticated;
grant select on public.rook_owners,public.rook_devices,public.rook_events,public.rook_images to authenticated;
grant all on public.rook_owners,public.rook_devices,public.rook_events,public.rook_images to service_role;

create function public.rook_authenticate_device(p_token_hash text) returns text
language sql security definer set search_path='' as $$
  select device_id from rook_private.device_tokens where token_hash=p_token_hash and revoked_at is null;
$$;

create function public.rook_ingest(p_token_hash text,p_event_json text,p_image bytea)
returns jsonb language plpgsql security definer set search_path='' as $$
declare
  token rook_private.device_tokens%rowtype;
  dev public.rook_devices%rowtype;
  previous public.rook_events%rowtype;
  e jsonb; model jsonb; box jsonb; eid uuid; event_hash text; received timestamptz;
  size bigint;
begin
  -- Revocation and final commit serialize on this row, after HTTP validation.
  select * into token from rook_private.device_tokens where token_hash=p_token_hash for share;
  if not found or token.revoked_at is not null then raise exception using errcode='P0401',message='unauthorized'; end if;
  if octet_length(p_event_json)>65536 then raise exception using errcode='P0422',message='event too large'; end if;
  e:=p_event_json::jsonb; eid:=(e->>'event_id')::uuid;
  if e->>'device_id' is distinct from token.device_id then raise exception using errcode='P0403',message='device mismatch'; end if;
  select * into dev from public.rook_devices where device_id=token.device_id for update;
  event_hash:=encode(extensions.digest(convert_to(p_event_json,'UTF8'),'sha256'),'hex');
  select * into previous from public.rook_events where device_id=token.device_id and event_id=eid;
  if found then
    if previous.event_sha256<>event_hash then raise exception using errcode='P0409',message='identity conflict'; end if;
    -- Even replays must bind the attachment; never accept corrupted retry bytes.
    if (e->'image'='null'::jsonb and p_image is not null) or
       (e->'image'<>'null'::jsonb and (p_image is null or encode(extensions.digest(p_image,'sha256'),'hex') is distinct from e#>>'{image,sha256}'))
    then raise exception using errcode='P0422',message='image mismatch'; end if;
    received:=previous.received_at;
  else
    select manifest into model from rook_private.models where device_id=token.device_id
      and artifact_sha256=e#>>'{model,artifact_sha256}' and classes_sha256=e#>>'{model,classes_sha256}';
    if not found or model->'model' is distinct from e->'model' then raise exception using errcode='P0422',message='unregistered model'; end if;
    for box in select * from jsonb_array_elements(e->'detections') loop
      if model->'class_names'->>((box->>'class_id')::integer) is distinct from box->>'label' then
        raise exception using errcode='P0422',message='vocabulary mismatch';
      end if;
    end loop;
    if e->'image'='null'::jsonb then
      if p_image is not null then raise exception using errcode='P0422',message='unexpected image'; end if;
    elsif p_image is null or octet_length(p_image)<>(e#>>'{image,byte_length}')::integer
       or encode(extensions.digest(p_image,'sha256'),'hex') is distinct from e#>>'{image,sha256}' then
      raise exception using errcode='P0422',message='image mismatch';
    end if;
    size:=octet_length(p_event_json)+coalesce(octet_length(p_image),0);
    if dev.event_count>=1000 or dev.used_bytes+size>268435456 then
      raise exception using errcode='P0429',message='pilot storage quota';
    end if;
    received:=clock_timestamp();
    insert into public.rook_events values(token.device_id,eid,dev.owner_id,event_hash,p_event_json,e,(e->>'captured_at')::timestamptz,received);
    if p_image is not null then insert into public.rook_images values(token.device_id,eid,dev.owner_id,p_image); end if;
    update public.rook_devices set event_count=event_count+1,used_bytes=used_bytes+size,last_received_at=received where device_id=token.device_id;
  end if;
  return jsonb_build_object('schema_version',1,'device_id',token.device_id,'event_id',eid,
    'event_sha256',event_hash,'received_at',to_char(received at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
end;
$$;
revoke all on function public.rook_authenticate_device(text),public.rook_ingest(text,text,bytea) from public,anon,authenticated;
grant execute on function public.rook_authenticate_device(text),public.rook_ingest(text,text,bytea) to service_role;
commit;
