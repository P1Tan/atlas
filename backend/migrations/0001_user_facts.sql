-- Milestone 6.1: structured storage for long-term user facts.
--
-- Applied by hand via the Supabase Dashboard's SQL Editor (Project ->
-- SQL Editor -> paste -> Run) -- direct psycopg2 connections from this
-- machine can't resolve db.<project-ref>.supabase.co (needs IPv6), and a
-- one-time manual apply is the standard, accepted Supabase workflow for a
-- project this size. Keep future schema changes as new numbered files here
-- even though they're applied by hand, so the schema stays versioned and
-- reviewable.

create table if not exists user_facts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    fact_text text not null,
    created_at timestamptz not null default now()
);

create index if not exists user_facts_user_id_idx on user_facts (user_id);

-- Defense in depth: the backend currently only ever accesses this table
-- with the service_role key (which bypasses RLS), since the iOS client
-- talks to our backend, not directly to Supabase's data API. Enabling RLS
-- with a correct policy now costs nothing and is the right default if that
-- ever changes.
alter table user_facts enable row level security;

create policy "Users can manage their own facts"
    on user_facts
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
