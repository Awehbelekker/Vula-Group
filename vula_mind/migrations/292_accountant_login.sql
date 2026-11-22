-- 292_accountant_login.sql — the accountant's login (19 Nov 2026, Phase 3 of Xero-quality books).
--
-- Ian: an accountant needs to see a business's books and documents without being able to change
-- anything, without taking a paid seat, and with every view and download on record. A login row may
-- now be 'accountant' (vula/api/tenant_auth.py: never full access, read-only, deny by default in the
-- tenant guard). The accountant's team row carries when their access ends and, for later, whether
-- they may post adjusting journals. Idempotent.

alter table vula_tenant_users drop constraint if exists vula_tenant_users_role_check;
alter table vula_tenant_users add constraint vula_tenant_users_role_check
    check (role in ('owner', 'staff', 'master', 'accountant'));

alter table vula_team_members add column if not exists expires_on date;
alter table vula_team_members add column if not exists can_adjust boolean not null default false;
