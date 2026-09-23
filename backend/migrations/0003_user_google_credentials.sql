-- Per-user Google/Gmail credentials: replaces the single shared
-- backend/.data/google_token.json, where the second user to connect Gmail
-- overwrote the first and every user's /gmail/candidates then read that one
-- inbox regardless of who asked.
--
-- Applied by hand via the Supabase Dashboard's SQL Editor (Project ->
-- SQL Editor -> paste -> Run), the same workflow 0001 documents -- direct
-- psycopg2 connections from this machine can't resolve
-- db.<project-ref>.supabase.co (needs IPv6), and a one-time manual apply is
-- the standard, accepted Supabase workflow for a project this size. Nothing
-- applies this file automatically, and app.google_auth deliberately does not
-- swallow database errors (only an undecodable credential counts as "not
-- connected"), so until this has been run by hand /auth/google/status and
-- /gmail/candidates fail loudly -- PostgREST 42P01, undefined table,
-- surfacing as a 500 -- rather than quietly reporting Gmail as
-- disconnected. Run it before deploying the code that expects it.

create table if not exists user_google_credentials (
    -- One row per user, not one per connection: reconnecting Gmail (or
    -- persisting a refreshed access token) replaces the stored credential,
    -- so app.google_auth.save_credentials upserts on this key. Being the
    -- primary key also means no separate user_id index is needed here,
    -- unlike user_facts in 0001 -- the primary key already provides one.
    user_id uuid primary key references auth.users(id) on delete cascade,
    -- Exactly the dict google.oauth2.credentials.Credentials.to_json()
    -- produces (token, refresh_token, token_uri, client_id, client_secret,
    -- scopes, expiry) -- the same payload that used to be the entire
    -- contents of backend/.data/google_token.json, now per user.
    credentials jsonb not null,
    created_at timestamptz not null default now(),
    -- Written explicitly on every upsert by save_credentials: `default
    -- now()` only fires on insert, and a reconnect or a refreshed access
    -- token is an update of an existing row.
    updated_at timestamptz not null default now()
);

comment on column user_google_credentials.credentials is
    'Serialized Google OAuth credential including a long-lived refresh token -- effectively standing read access to that user''s Gmail. Supabase encrypts the underlying disk and backups, so this is protected at rest at the storage layer, but the column itself is not separately encrypted. Encrypting it at the column level (pgsodium/Vault, or app-side envelope encryption keyed outside the database) is a deliberate follow-up, not an oversight: it is what would keep a leaked dump or an over-broad service_role query from handing over live mailbox access.';

-- Defense in depth, same reasoning as 0001: the backend only ever reaches
-- this table with the service_role key (which bypasses RLS), since the iOS
-- client talks to our backend rather than directly to Supabase's data API.
-- The real authorization check is the .eq("user_id", ...) filter in
-- app.google_auth. Enabling RLS with a correct policy now costs nothing and
-- is the right default if the client ever does read this table directly --
-- which matters more here than for user_facts, since a wrong row is
-- someone else's mailbox.
alter table user_google_credentials enable row level security;

create policy "Users can manage their own Google credentials"
    on user_google_credentials
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
