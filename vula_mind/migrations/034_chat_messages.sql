-- 034_chat_messages.sql — durable conversation history (was ephemeral SQLite on Railway,
-- wiped on every deploy). Per-tenant, per-phone; powers WhatsApp/chat memory + CRM history.
create table if not exists vula_chat_messages (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    phone      text not null default '',
    role       text not null check (role in ('user', 'assistant')),
    text       text not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_chat_messages_tp on vula_chat_messages (tenant_id, phone, created_at);
alter table vula_chat_messages enable row level security;
drop policy if exists "tenant_isolation" on vula_chat_messages;
create policy "tenant_isolation" on vula_chat_messages
    using (tenant_id = current_setting('app.tenant_id', true));
