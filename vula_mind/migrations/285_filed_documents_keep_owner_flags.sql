-- 285_filed_documents_keep_owner_flags.sql — the owner's choices on a document survive any later save.
--
-- 16 Nov 2026 (DIGG, Belladonna): skip invoice 1091 shows another client's site, so the owner kept it off
-- Rudi's client link (fields._hidden_from_client). A later save of the same document — code that reads a
-- document, changes one field and writes the whole `fields` back (asking which project, joining a second
-- page, a re-read) — wrote back a copy from before the owner's choice, and the invoice came back on the
-- client's download. Whatever the code path, a save that drops one of these keys now keeps it:
--
--   _hidden_from_client   kept off client links         _internal   an internal cost sheet
--   _show_on_client_link  shown although it names another project (false = no longer shown)
--   _note                 the owner's note on the document
--
-- To undo a choice, write the key with a new value (false), never by leaving it out.
-- Idempotent.

create or replace function vula_filed_documents_keep_owner_flags() returns trigger
language plpgsql as $$
declare
    k text;
begin
    if old.fields is null or new.fields is null then
        return new;
    end if;
    foreach k in array array['_hidden_from_client', '_show_on_client_link', '_internal', '_note'] loop
        if (old.fields ? k) and not (new.fields ? k) then
            new.fields := new.fields || jsonb_build_object(k, old.fields -> k);
        end if;
    end loop;
    return new;
end;
$$;

drop trigger if exists vula_filed_documents_keep_owner_flags on vula_filed_documents;
create trigger vula_filed_documents_keep_owner_flags
    before update of fields on vula_filed_documents
    for each row execute function vula_filed_documents_keep_owner_flags();
