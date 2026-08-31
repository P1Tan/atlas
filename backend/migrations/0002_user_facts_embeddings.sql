-- Enables pgvector and adds semantic search over user_facts (Milestone 6.5).
create extension if not exists vector;

alter table user_facts add column if not exists embedding vector(1536);

-- Cosine-distance top-K search, scoped to one user. SECURITY INVOKER (the
-- default) is fine here: this function is only ever called by the backend's
-- service_role client, which already bypasses Row Level Security regardless
-- of the function's security context -- query_user_id is the actual
-- authorization boundary, supplied by trusted backend code, never by the
-- caller directly (mirrors delete_fact's authorization model from 6.4a).
create or replace function match_user_facts(
    query_user_id uuid,
    query_embedding vector(1536),
    match_count int
)
returns table (id uuid, fact_text text, created_at timestamptz)
language sql
stable
as $$
    select id, fact_text, created_at
    from user_facts
    where user_id = query_user_id
      and embedding is not null
    order by embedding <=> query_embedding
    limit match_count;
$$;
