-- 190: other names a project is known by (2026-10-02, Ian: "Atlantis Foods is Atlantis Paarden
-- Eiland"). digg-demo had 192 documents filed under names that aren't on the project register —
-- "ATLANTIS FOODS" (110), ClickUp list names ("Sporty – Phase 2", "Team Space / Get Started…").
-- canonical_project() maps any alias to the registered name, so new filings, the document
-- clean-up and job costing all land on one project. Idempotent.

alter table vula_projects add column if not exists aliases text[] not null default '{}';
