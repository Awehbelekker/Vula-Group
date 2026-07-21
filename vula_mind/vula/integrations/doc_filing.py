"""
vula/integrations/doc_filing.py — Smart document filing.

When a tenant WhatsApps a doc/photo, Vula reads + classifies it (whatsapp.py
`_analyze_document`) and ingests it into the KB. This module adds the *filing*
layer: match the document to a project, store a durable copy + a queryable record
in `vula_filed_documents`, and — when the project maps to a ClickUp list — attach the
file into ClickUp (one "📎 Project Documents" task per list).

Project taxonomy: the tenant's ClickUp lists first, field-ops `project_id`s as
fallback. No confident match → the caller asks the user on WhatsApp.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Tokens too generic to identify a project.
_STOPWORDS = {
    "team", "space", "list", "phase", "with", "clickup", "started", "get",
    "the", "and", "branch", "project", "documents", "document", "hub", "site",
    "first", "second", "fix", "work", "stage", "stages", "general",
}
_MIN_TOKEN_LEN = 4
_GENERIC_SEGMENTS = {"team space", "space", "list", "get started with clickup"}


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _tokens(text: str) -> set[str]:
    """Distinctive lowercase tokens (≥4 chars, no stopwords) from free text."""
    raw = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in raw if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS and not t.isdigit()}


def _project_label(list_name: str) -> str:
    """Derive a human project label from a (possibly nested) ClickUp list name,
    e.g. 'Team Space / HPC_Bokaap / Phase 1 — Site' → 'HPC_Bokaap'."""
    segs = [s.strip() for s in str(list_name).split("/") if s.strip()]
    segs = [s for s in segs if s.lower() not in _GENERIC_SEGMENTS]
    non_phase = [s for s in segs if not s.lower().startswith("phase")]
    if non_phase:
        return non_phase[0]
    return segs[0] if segs else str(list_name)


def _clickup_candidates(tenant_id: str) -> list[tuple[str, str]]:
    """(list_id, list_name) for the tenant's ClickUp lists, or []."""
    try:
        from vula.clickup.credentials import get_tenant_clickup_creds
        creds = get_tenant_clickup_creds(tenant_id)
        if not creds:
            return []
        lids = creds.get("list_ids") or {}
        return [(k, v) for k, v in lids.items() if k != "default" and v]
    except Exception:
        return []


def _field_projects(tenant_id: str) -> list[str]:
    """Distinct field-ops project_ids for the tenant, or []."""
    try:
        res = (_client().table("vula_field_tasks").select("project_id")
               .eq("tenant_id", tenant_id).limit(500).execute())
        return sorted({r["project_id"] for r in (res.data or []) if r.get("project_id")})
    except Exception:
        return []


def match_project(tenant_id: str, text: str) -> Optional[dict]:
    """Match free text (caption + summary + extracted fields) to a project.

    Returns {project, clickup_list_id, confidence, ambiguous} or None when nothing matches at
    all. ClickUp lists are tried first; field-ops projects, then the canonical project register,
    are the fallback. `confidence` is numeric (1.0 = a strong multi-token ClickUp match, 0.6 = a
    single-token/field-ops match, 0.5 = the canonical-register fallback) — kept comparable
    across tiers instead of the old opaque "high"/"medium" strings, and directly storable in
    vula_filed_documents.match_confidence (migration 102).

    `ambiguous=True` (project=None) means multiple equally-plausible DIFFERENT projects matched
    within a tier and neither won clearly — modeled on bank_rec.py's _match_by_amount, which
    already treats an unresolved tie as "leave for review" rather than silently guessing. The
    old code picked an arbitrary winner on a tie (`best is None or cand > best`); this is the
    fix for that.
    """
    if not (text or "").strip():
        return None
    text_tokens = _tokens(text)

    if text_tokens:
        # ── ClickUp lists — highest-priority tier, can also attach the file into the list ──
        scored = []  # [(score, label, list_id)]
        for lid, lname in _clickup_candidates(tenant_id):
            overlap = _tokens(lname) & text_tokens
            if overlap:
                scored.append((len(overlap), _project_label(lname), lid))
        if scored:
            best_score = max(s for s, _, _ in scored)
            top = [c for c in scored if c[0] == best_score]
            distinct_labels = {label for _, label, _ in top}
            if len(distinct_labels) > 1:
                return {"project": None, "clickup_list_id": None, "confidence": float(best_score),
                       "ambiguous": True, "candidates": sorted(distinct_labels)}
            # One winning label (possibly several phase-sub-lists under it) — deterministic pick.
            _, label, lid = min(top, key=lambda c: (len(c[1]), c[2]))
            return {"project": label, "clickup_list_id": lid,
                    "confidence": 1.0 if best_score >= 2 else 0.6, "ambiguous": False}

        # ── Fallback: field-ops project ids (no ClickUp list to attach to) ──
        field_matches = sorted({pid for pid in _field_projects(tenant_id) if _tokens(pid) & text_tokens})
        if len(field_matches) > 1:
            return {"project": None, "clickup_list_id": None, "confidence": 0.6,
                   "ambiguous": True, "candidates": field_matches}
        if field_matches:
            return {"project": field_matches[0], "clickup_list_id": None,
                    "confidence": 0.6, "ambiguous": False}

    # ── Fallback: the canonical project register (vula_projects + prior expense/invoice
    # projects — see vula.commerce.expenses.known_projects), matched with the same loose
    # substring/token rules used when allocating an EXPENSE to a project. This tier exists
    # because _tokens()'s >=4-char filter (tuned for noisy ClickUp list-name overlap) makes
    # a short real project name/acronym like "HPC" unmatchable above, even though the exact
    # same reply already resolves correctly for expenses — a document reply of "HPC" must
    # resolve the same project a claim reply of "HPC" does. expenses.match_project() already
    # does its own best-single-match resolution, so no separate ambiguity check here.
    try:
        from vula.commerce import expenses as _expenses
        canonical = _expenses.match_project(tenant_id, text)
        if canonical:
            return {"project": canonical, "clickup_list_id": None, "confidence": 0.5, "ambiguous": False}
    except Exception as exc:
        logger.debug("canonical project register fallback skipped: %s", exc)
    return None


# Non-party structural signals only — a bank account number is the strongest: unique to a
# beneficiary, immune to name spelling. Party-name signals (supplier/vendor/payee/
# counterparty/beneficiary/client) are resolved separately below via party.resolve_party_name,
# which centralises the alias list this used to hand-roll here.
_ACCOUNT_SIGNAL_KEYS = (("account_number", "account"), ("account", "account"),
                        ("beneficiary_account", "account"), ("reference", "reference"))


def _signals_from(fields: dict) -> list:
    """Extract (signal_type, normalised_value) learning signals from a doc's fields."""
    from vula.commerce.party import resolve_party_name

    out, seen = [], set()
    for key, stype in _ACCOUNT_SIGNAL_KEYS:
        v = (fields or {}).get(key)
        if isinstance(v, str):
            val = v.strip().lower()
            if len(val) >= 3 and val not in seen:
                seen.add(val)
                out.append((stype, val))
    # 'payer' excluded — usually the tenant's own name (who paid), not who sent the document,
    # and would pollute learned rules with a signal that matches almost every doc.
    party = resolve_party_name(fields, exclude=("payer",))
    if party:
        val = party.lower()
        if len(val) >= 3 and val not in seen:
            out.append(("supplier", val))
    return out


def learn_filing_rule(tenant_id: str, fields: dict, project: str) -> int:
    """Remember that a doc with these signals belongs to `project`."""
    if not project or not fields:
        return 0
    n = 0
    for stype, val in _signals_from(fields):
        try:
            existing = (_client().table("vula_filing_rules").select("id,hits")
                        .eq("tenant_id", tenant_id).eq("signal", val).eq("project", project)
                        .limit(1).execute().data or [])
            if existing:
                _client().table("vula_filing_rules").update(
                    {"hits": (existing[0].get("hits") or 0) + 1, "last_used": "now()"}
                ).eq("id", existing[0]["id"]).execute()
            else:
                _client().table("vula_filing_rules").insert(
                    {"tenant_id": tenant_id, "signal": val, "signal_type": stype,
                     "project": project}).execute()
            n += 1
        except Exception as exc:
            logger.debug("learn_filing_rule skipped (run migration 026?): %s", exc)
    return n


def lookup_learned_project(tenant_id: str, fields: dict) -> Optional[dict]:
    """If a learned rule matches this doc's signals, return its project (high confidence)."""
    sigs = [v for _, v in _signals_from(fields)]
    if not sigs:
        return None
    try:
        rows = (_client().table("vula_filing_rules").select("project,signal,hits")
                .eq("tenant_id", tenant_id).in_("signal", sigs)
                .order("hits", desc=True).limit(1).execute().data or [])
    except Exception:
        return None
    if rows:
        # A previously-confirmed learned signal is definitionally not ambiguous — a human
        # already resolved this exact case once. Numeric confidence matches match_project()'s
        # convention (1.0 = strong match) so both are directly comparable/storable.
        return {"project": rows[0]["project"], "clickup_list_id": None,
                "confidence": 1.0, "ambiguous": False, "learned": rows[0]["signal"]}
    return None


def project_examples(tenant_id: str, n: int = 3) -> list[str]:
    """A few project labels to prompt the user with when asking which project."""
    labels: list[str] = []
    for _, lname in _clickup_candidates(tenant_id):
        lab = _project_label(lname)
        if lab not in labels:
            labels.append(lab)
        if len(labels) >= n:
            break
    if not labels:
        labels = _field_projects(tenant_id)[:n]
    return labels


def _canonical_list_for_project(tenant_id: str, project: str) -> Optional[str]:
    """One stable ClickUp list per project, so a project's docs always file to the
    same place even when it spans many phase lists. Deterministic (sorted by name)."""
    cands = [(lid, name) for lid, name in _clickup_candidates(tenant_id)
             if _project_label(name) == project]
    if not cands:
        return None
    cands.sort(key=lambda x: str(x[1]))
    return cands[0][0]


def _existing_project_task(tenant_id: str, project: str) -> Optional[tuple]:
    """(list_id, task_id) of an already-created '📎 Project Documents' task for this
    project, from our own records — so we never create a duplicate. Else None."""
    if not project:
        return None
    try:
        res = (_client().table("vula_filed_documents")
               .select("clickup_list_id,clickup_task_id")
               .eq("tenant_id", tenant_id).eq("project", project)
               .not_.is_("clickup_task_id", "null")
               .order("created_at", desc=True).limit(1).execute())
        rows = res.data or []
    except Exception:
        return None
    if rows:
        return rows[0].get("clickup_list_id"), rows[0].get("clickup_task_id")
    return None


def _existing_filed_doc(tenant_id: str, project: str, filename: str,
                        content_hash: Optional[str] = None) -> Optional[dict]:
    """Newest already-filed row with the same tenant+project+filename, or None.

    content_hash, when given, narrows the match to the SAME bytes (migration 092) — a generic,
    reused filename (e.g. a bank's "Payment Notification.pdf") is not a reliable proxy for "same
    file", so without this a second distinct document sharing that name would be silently
    treated as a re-file of the first and never get its own row."""
    if not project or not filename:
        return None
    try:
        q = (_client().table("vula_filed_documents").select("*")
             .eq("tenant_id", tenant_id).eq("project", project)
             .eq("filename", filename).eq("status", "filed"))
        q = q.eq("content_hash", content_hash) if content_hash else q.is_("content_hash", "null")
        res = q.order("created_at", desc=True).limit(1).execute()
        rows = res.data or []
    except Exception:
        return None
    return rows[0] if rows else None


async def attach_into_project(tenant_id: str, project: Optional[str],
                              candidate_list_id: Optional[str], filename: str,
                              data: bytes, content_type: str) -> dict:
    """Attach a file to a project's single '📎 Project Documents' task, reusing an
    existing task (from our records) or a canonical list before creating anything.
    Returns {clickup_list_id, clickup_task_id} (values may be None on failure)."""
    from vula.clickup import service as clickup_service

    list_id, task_id = candidate_list_id, None
    if project:
        reuse = _existing_project_task(tenant_id, project)
        if reuse:
            list_id, task_id = reuse[0] or candidate_list_id, reuse[1]
        else:
            list_id = _canonical_list_for_project(tenant_id, project) or candidate_list_id

    if not list_id:
        return {"clickup_list_id": None, "clickup_task_id": None}
    try:
        res = await clickup_service.attach_file_to_list(
            tenant_id, list_id, filename, data, content_type, task_id=task_id)
        return {"clickup_list_id": list_id, "clickup_task_id": res.get("task_id")}
    except Exception as exc:
        logger.warning("ClickUp attach into project '%s' failed: %s", project, exc)
        return {"clickup_list_id": list_id, "clickup_task_id": None}


async def file_document(
    tenant_id: str, *, filename: str, data: Optional[bytes], content_type: str,
    category: str = "", summary: str = "", fields: Optional[dict] = None,
    doc_id: str = "", source: str = "whatsapp", filed_by: str = "",
    project: Optional[str] = None, clickup_list_id: Optional[str] = None,
    status: str = "filed",
) -> dict:
    """Store a durable copy + a `vula_filed_documents` row, and (if a ClickUp list is
    given and we have bytes) attach the file into ClickUp. Returns the row dict
    plus `clickup_task_id` when attached.
    """
    # Content hash (migration 092) computed up front — used both for the skip-duplicate check
    # below and the row itself, so a second distinct file sharing a generic name never gets
    # mistaken for a re-file of the first.
    content_hash = hashlib.sha256(data).hexdigest() if data else None

    # Skip-duplicate: same file already filed under this project → update, don't re-attach.
    if status == "filed" and project:
        dup = _existing_filed_doc(tenant_id, project, filename, content_hash)
        if dup:
            try:
                _client().table("vula_filed_documents").update({
                    "category": category, "summary": summary, "fields": fields or {},
                    "doc_id": doc_id or dup.get("doc_id"),
                }).eq("id", dup["id"]).execute()
            except Exception as exc:
                logger.debug("Duplicate doc update skipped: %s", exc)
            dup["duplicate"] = True
            return dup

    file_url = None
    if data:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "document")
        object_path = f"{tenant_id}/{uuid.uuid4().hex}_{safe}"
        try:
            from vula.api.whatsapp import _upload_to_storage
            file_url = _upload_to_storage("documents", object_path, data, content_type)
        except Exception as exc:
            logger.warning("Document storage upload failed: %s", exc)

    clickup_task_id = None
    if clickup_list_id and data:
        att = await attach_into_project(tenant_id, project, clickup_list_id,
                                        filename, data, content_type)
        clickup_list_id = att.get("clickup_list_id") or clickup_list_id
        clickup_task_id = att.get("clickup_task_id")

    row = {
        "tenant_id": tenant_id, "project": project, "clickup_list_id": clickup_list_id,
        "clickup_task_id": clickup_task_id, "category": category, "summary": summary,
        "fields": fields or {}, "filename": filename, "file_url": file_url,
        "mime": content_type, "doc_id": doc_id, "source": source,
        "status": status, "filed_by": filed_by, "content_hash": content_hash,
    }
    try:
        # ignore_duplicates=True (Postgres ON CONFLICT DO NOTHING) against the unique
        # (tenant_id, source, filename, content_hash) index (migrations 081 + 092): with two
        # worker processes racing the same email-sync poll or WhatsApp attachment, whichever
        # insert lands first wins and the other is a silent no-op — never a second row for the
        # SAME bytes, and never clobbers an already-resolved row's project/status back to
        # unfiled. Different bytes under the same filename are a different row, correctly.
        ins = _client().table("vula_filed_documents").upsert(
            row, on_conflict="tenant_id,source,filename,content_hash", ignore_duplicates=True
        ).execute()
        if ins.data:
            row["id"] = ins.data[0].get("id")
        else:
            # Lost the race — fetch the row the winner created so the caller still gets an id.
            q = (_client().table("vula_filed_documents").select("*")
                 .eq("tenant_id", tenant_id).eq("source", source).eq("filename", filename))
            q = q.eq("content_hash", content_hash) if content_hash else q.is_("content_hash", "null")
            existing = q.limit(1).execute().data or []
            if existing:
                row = existing[0]
    except Exception as exc:
        # migration 092 not run yet — content_hash column/index don't exist. Retry against the
        # OLD (tenant_id, source, filename) constraint so filing keeps working in the meantime
        # (accepting the pre-092 generic-filename-collision limitation) rather than breaking
        # every document filing outright.
        logger.warning("vula_filed_documents insert retried without content_hash (run migration 092?): %s", exc)
        row.pop("content_hash", None)
        try:
            ins = _client().table("vula_filed_documents").upsert(
                row, on_conflict="tenant_id,source,filename", ignore_duplicates=True
            ).execute()
            if ins.data:
                row["id"] = ins.data[0].get("id")
            else:
                existing = (_client().table("vula_filed_documents").select("*")
                           .eq("tenant_id", tenant_id).eq("source", source)
                           .eq("filename", filename).limit(1).execute().data or [])
                if existing:
                    row = existing[0]
        except Exception as exc2:
            logger.warning("vula_filed_documents insert failed (run migration 015/081?): %s", exc2)
    return row


async def resolve_pending_document(tenant_id: str, phone: str, text: str) -> Optional[dict]:
    """If `phone` has a recent pending_project document, treat `text` as the
    project answer: match it, update the row (project + ClickUp), re-download the
    stored file and attach it into ClickUp. Returns a result dict or None.

    Returns: None (no pending doc), {"unmatched": True, "filename": ...},
    or {"filed": True, "project": ..., "clickup": bool, "filename": ...}.
    """
    try:
        from datetime import datetime, timedelta, timezone
        # 30 minutes was too tight at real volume — most "which project?" prompts went
        # unanswered in time and the doc was permanently orphaned even when someone
        # replied hours later. 24h gives a realistic same-day/next-morning window.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        base = (_client().table("vula_filed_documents").select("*")
                .eq("tenant_id", tenant_id).eq("status", "pending_project")
                .gte("created_at", cutoff).order("created_at", desc=True))
        # Match the doc filed for this exact phone first; otherwise, if the replier is a
        # known team member, let them answer the tenant's most-recent pending doc.
        rows = base.eq("filed_by", phone).limit(1).execute().data or []
        if not rows:
            try:
                from vula.integrations.notify import team_member_for_phone
                if team_member_for_phone(tenant_id, phone):
                    rows = base.limit(1).execute().data or []
            except Exception:
                pass
    except Exception:
        return None
    if not rows:
        return None
    doc = rows[0]

    # "skip" → leave it unfiled (still stored + in KB).
    if text.strip().lower() in ("skip", "none", "no project", "unfiled", "leave it"):
        try:
            _client().table("vula_filed_documents").update({"status": "filed"}) \
                .eq("id", doc["id"]).execute()
        except Exception:
            pass
        return {"skipped": True, "filename": doc.get("filename")}

    match = match_project(tenant_id, text)
    # A match with no `project` (either no candidate at all, or an unresolved tie between
    # several plausible projects — match.get("ambiguous")) is treated identically: re-ask
    # rather than silently filing under a null project.
    if not match or not match.get("project"):
        # Don't hijack an unrelated question — only re-ask for short answers.
        if len(text) > 60 or text.strip().endswith("?"):
            return None
        result = {"unmatched": True, "filename": doc.get("filename")}
        if match and match.get("candidates"):
            result["candidates"] = match["candidates"]
        return result

    # Same file already filed under this project → drop the pending dup, point at it.
    existing = _existing_filed_doc(tenant_id, match["project"], doc.get("filename") or "",
                                   doc.get("content_hash"))
    if existing and existing.get("id") != doc["id"]:
        try:
            _client().table("vula_filed_documents").delete().eq("id", doc["id"]).execute()
        except Exception:
            pass
        return {"filed": True, "project": match["project"],
                "clickup": bool(existing.get("clickup_task_id")),
                "filename": doc.get("filename"), "duplicate": True}

    clickup_list_id, clickup_task_id = match.get("clickup_list_id"), None
    if match.get("clickup_list_id") and doc.get("file_url"):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as client:
                fb = await client.get(doc["file_url"])
                fb.raise_for_status()
                data = fb.content
            att = await attach_into_project(
                tenant_id, match["project"], match["clickup_list_id"],
                doc.get("filename") or "document", data,
                doc.get("mime") or "application/octet-stream")
            clickup_list_id = att.get("clickup_list_id") or clickup_list_id
            clickup_task_id = att.get("clickup_task_id")
        except Exception as exc:
            logger.warning("ClickUp attach (pending resolve) failed: %s", exc)

    try:
        _client().table("vula_filed_documents").update({
            "project": match["project"], "clickup_list_id": clickup_list_id,
            "clickup_task_id": clickup_task_id, "status": "filed",
        }).eq("id", doc["id"]).execute()
    except Exception as exc:
        logger.warning("Pending doc update failed: %s", exc)

    # Learn from this correction so similar docs auto-file next time.
    learned = learn_filing_rule(tenant_id, doc.get("fields") or {}, match["project"])

    # Post to the project finance ledger (reconciles payment<->invoice if it can).
    try:
        from vula.integrations.finances import post_finance_from_doc
        post_finance_from_doc(tenant_id, match["project"], doc.get("fields") or {},
                              doc.get("doc_id"), doc.get("filename") or "",
                              doc.get("summary") or "", doc.get("category") or "")
    except Exception as exc:
        logger.debug("finance post (resolve) skipped: %s", exc)

    return {"filed": True, "project": match["project"], "learned_signals": learned,
            "clickup": bool(clickup_task_id), "filename": doc.get("filename")}
