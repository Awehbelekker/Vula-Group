-- 167_wa_prompt_state.sql — durable, cross-worker backing for the "did we just ask this phone
-- number a yes/no follow-up" pattern (_purpose_prompted_at / _signature_prompted_at in
-- vula/api/whatsapp.py). Both are module-level in-memory dicts — fine on a single process, but
-- Railway already runs WEB_CONCURRENCY=2 (two uvicorn workers per replica) TODAY, the same root
-- cause already fixed once for inbound-message dedup (migration 071/vula_wa_msg_dedup): the
-- prompt and the reply landing on different workers means the reply is treated as a fresh,
-- unprompted message instead of the answer being waited for.
--
-- Same shape as vula_wa_msg_dedup: a shared, phone-keyed infra table with no tenant concept of
-- its own (a phone can be prompted before role/tenant is even resolved), service-role only.

create table if not exists vula_wa_prompt_state (
    phone       text not null,
    kind        text not null check (kind in ('purpose', 'signature')),
    prompted_at timestamptz not null default now(),
    primary key (phone, kind)
);

create index if not exists idx_wa_prompt_state_prompted_at on vula_wa_prompt_state (prompted_at);

alter table vula_wa_prompt_state enable row level security;
-- Intentionally no policy: global phone-keyed prompt-state ledger, service-role only, same
-- rationale as vula_wa_msg_dedup/vula_scheduler_lock (migration 115).
