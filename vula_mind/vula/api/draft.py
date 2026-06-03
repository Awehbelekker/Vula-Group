"""
vula/api/draft.py

Vula Draft — AI-powered document generator for construction and business.

Generates professional, formatted documents from a brief:
  - Fee proposals (architectural, QS, engineering)
  - Project programmes (Gantt-style text)
  - Scope of works
  - Letters of appointment
  - Site meeting minutes templates
  - Contractor tender invitations

Flow:
  1. Receive brief + document type + tenant_id
  2. Retrieve tenant's own KB context (fee schedules, past proposals, rates)
  3. Retrieve shared construction KB context (SA standards, SACAP fees, JBCC)
  4. Generate structured document via DeepSeek R1
  5. Return markdown + optional PDF-ready HTML

Endpoints:
  POST /v1/draft/generate       — generate any document type
  GET  /v1/draft/types          — list available document types
  GET  /v1/draft/history/{tenant_id}  — past drafts for a tenant
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["draft"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_auth(api_key: str | None = Security(_api_key_header)) -> None:
    import secrets as _s
    if settings.api_key and (not api_key or not _s.compare_digest(api_key, settings.api_key)):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# ── Document type definitions ──────────────────────────────────────────────────

DOCUMENT_TYPES: Dict[str, Dict[str, Any]] = {
    "fee_proposal": {
        "label": "Fee Proposal",
        "description": "Professional fee proposal for architectural or consulting services",
        "system_prompt": """You are a senior South African architect drafting a professional fee proposal.
Use SACAP fee guidelines and the client's own fee schedule where available.
Format as a formal business document with:
- Company letterhead reference (company name, SACAP number)
- Client details
- Project description and scope
- Fee breakdown per SACAP stage (Inception → Construction Admin)
- Payment terms (30 days)
- Validity period
- Signature block
All amounts in ZAR. Professional, concise, no filler text.""",
        "output_sections": ["header", "project_description", "scope_of_services",
                            "fee_schedule", "payment_terms", "exclusions", "signature"],
    },
    "scope_of_works": {
        "label": "Scope of Works",
        "description": "Detailed scope of works document for contractors or subcontractors",
        "system_prompt": """You are a South African quantity surveyor drafting a scope of works.
Be specific, measurable, and reference SA standards (SANS, ASAQS, JBCC) where relevant.
Include: general conditions, specific work items, quality standards, programme expectations.
Use numbered clauses. Professional construction language.""",
        "output_sections": ["project_info", "general_conditions", "specific_works",
                            "quality_requirements", "programme", "payment_provisions"],
    },
    "appointment_letter": {
        "label": "Letter of Appointment",
        "description": "Professional letter appointing a contractor or consultant",
        "system_prompt": """You are a South African architect writing a formal letter of appointment.
Reference the relevant form of contract (JBCC, NEC, PROCSA as appropriate).
Include: appointed party, scope, contract sum, commencement date, contract document list.
Formal but clear language. SA legal conventions.""",
        "output_sections": ["letterhead", "appointment_details", "contract_terms",
                            "contract_documents", "acceptance_block"],
    },
    "site_meeting_minutes": {
        "label": "Site Meeting Minutes",
        "description": "Structured site meeting minutes template with action items",
        "system_prompt": """You are a South African architect preparing site meeting minutes.
Use standard SA construction meeting format.
Sections: Attendance, Previous minutes, Contractor's report, Employer's instructions,
Quality/safety issues, Programme status, Financial matters, Action items (owner + deadline).
Clear, numbered items. All instructions to be formally recorded.""",
        "output_sections": ["header", "attendance", "previous_minutes", "progress_report",
                            "instructions", "programme", "financial", "actions", "next_meeting"],
    },
    "tender_invitation": {
        "label": "Tender Invitation",
        "description": "Formal tender invitation letter for contractors",
        "system_prompt": """You are a South African architect preparing a tender invitation.
Reference CIDB grade requirements where relevant. Include pricing document list,
tender validity, NHBRC requirements, BEE requirements if applicable.
Formal SA procurement language.""",
        "output_sections": ["project_info", "tender_requirements", "tender_documents",
                            "pricing_instructions", "evaluation_criteria", "submission_details"],
    },
    "project_programme": {
        "label": "Project Programme",
        "description": "Construction programme with milestones and critical path items",
        "system_prompt": """You are a South African architect preparing a construction programme.
Create a realistic text-based programme with weeks/months as columns.
Key milestones: design stages, municipal submission, tender, construction phases, practical completion.
Reference typical SA timeframes. Flag critical path items.""",
        "output_sections": ["project_info", "design_programme", "tender_programme",
                            "construction_programme", "milestones", "assumptions"],
    },
    "payment_certificate": {
        "label": "Payment Certificate",
        "description": "Interim payment certificate for construction progress claims",
        "system_prompt": """You are a South African architect issuing an interim payment certificate.
Follow JBCC certificate format. Include: contract sum, previous payments, this claim,
retention calculation (5% until practical completion), net certificate amount.
All amounts in ZAR with VAT shown separately.""",
        "output_sections": ["certificate_header", "contract_details", "claim_breakdown",
                            "retention", "certificate_amount", "certification_statement"],
    },
}


# ── Request / Response models ──────────────────────────────────────────────────

class DraftRequest(BaseModel):
    tenant_id: str
    document_type: str
    brief: str = Field(..., min_length=10, max_length=4000,
                       description="Plain-language description of what to draft")
    project_name: Optional[str] = None
    client_name: Optional[str] = None
    project_value: Optional[float] = None
    extra_context: Optional[str] = None
    output_format: Literal["markdown", "html", "plain"] = "markdown"

    @field_validator("document_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in DOCUMENT_TYPES:
            raise ValueError(f"Unknown document type '{v}'. Valid: {list(DOCUMENT_TYPES)}")
        return v

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", v):
            raise ValueError("Invalid tenant_id")
        return v


class DraftResponse(BaseModel):
    draft_id: str
    document_type: str
    document_label: str
    content: str
    format: str
    word_count: int
    generated_at: str
    tenant_id: str
    model_used: str
    sources_used: int


# ── Draft history store ────────────────────────────────────────────────────────

class DraftStore:
    """SQLite log of generated drafts per tenant."""

    def __init__(self) -> None:
        self._db = settings.data_dir / "drafts.db"
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id     TEXT PRIMARY KEY,
                    tenant_id    TEXT NOT NULL,
                    doc_type     TEXT NOT NULL,
                    brief        TEXT NOT NULL,
                    content      TEXT NOT NULL,
                    word_count   INTEGER DEFAULT 0,
                    model        TEXT,
                    sources      INTEGER DEFAULT 0,
                    created_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_tenant ON drafts(tenant_id)")
            conn.commit()

    def save(self, tenant_id: str, draft_id: str, doc_type: str, brief: str,
             content: str, word_count: int, model: str, sources: int) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO drafts (draft_id,tenant_id,doc_type,brief,content,word_count,model,sources) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (draft_id, tenant_id, doc_type, brief[:500], content, word_count, model, sources),
            )
            conn.commit()

    def list_tenant(self, tenant_id: str, limit: int = 20) -> List[dict]:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                "SELECT draft_id,doc_type,brief,word_count,created_at FROM drafts "
                "WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [{"draft_id": r[0], "doc_type": r[1], "brief": r[2][:100],
                 "word_count": r[3], "created_at": r[4]} for r in rows]

    def get(self, draft_id: str) -> Optional[dict]:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE draft_id=?", (draft_id,)
            ).fetchone()
        if not row:
            return None
        return {"draft_id": row[0], "tenant_id": row[1], "doc_type": row[2],
                "brief": row[3], "content": row[4], "word_count": row[5],
                "model": row[6], "sources": row[7], "created_at": row[8]}


_store = DraftStore()


# ── Core generation logic ──────────────────────────────────────────────────────

async def _retrieve_context(tenant_id: str, brief: str, doc_type: str,
                            extra_context: Optional[str]) -> tuple[str, int]:
    """Pull relevant context from tenant KB + shared training KB."""
    from vula.ingestion.pipeline import VulaIngestionPipeline
    from vula.training.content import TRAINING_TENANT_ID

    sections: List[str] = []
    sources = 0

    # 1. Tenant-specific context (fee schedules, past proposals, project history)
    try:
        tenant_pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        tenant_chunks = await tenant_pipeline.query(brief, top_k=6)
        if tenant_chunks:
            tenant_context = "\n\n".join(
                f"[{c.get('filename','doc')}]: {c['text'][:600]}" for c in tenant_chunks
            )
            sections.append(f"## Your Business Knowledge\n{tenant_context}")
            sources += len(tenant_chunks)
    except Exception as exc:
        logger.warning("Tenant KB retrieval failed for %s: %s", tenant_id, exc)

    # 2. Shared construction KB (SA standards, SACAP fees, rates)
    try:
        training_pipeline = VulaIngestionPipeline(tenant_id=TRAINING_TENANT_ID)
        training_query = f"{doc_type} {brief}"
        training_chunks = await training_pipeline.query(training_query, top_k=4)
        if training_chunks:
            training_context = "\n\n".join(
                f"[{c.get('filename','standard')}]: {c['text'][:400]}" for c in training_chunks
            )
            sections.append(f"## SA Construction Standards & Rates\n{training_context}")
            sources += len(training_chunks)
    except Exception as exc:
        logger.warning("Training KB retrieval failed: %s", exc)

    # 3. Extra context from caller (e.g., client details, project specs)
    if extra_context:
        sections.append(f"## Additional Project Information\n{extra_context}")

    return "\n\n---\n\n".join(sections), sources


async def _generate_document(
    doc_config: Dict[str, Any],
    brief: str,
    context: str,
    project_name: Optional[str],
    client_name: Optional[str],
    project_value: Optional[float],
    output_format: str,
) -> tuple[str, str]:
    """Call LLM to generate the document. Returns (content, model_used)."""
    system_prompt = doc_config["system_prompt"]
    sections = doc_config["output_sections"]

    project_info = ""
    if project_name:
        project_info += f"\nProject Name: {project_name}"
    if client_name:
        project_info += f"\nClient: {client_name}"
    if project_value:
        project_info += f"\nConstruction Value: R{project_value:,.0f}"

    user_prompt = f"""Generate a professional {doc_config['label']}.

BRIEF:
{brief}
{project_info}

CONTEXT FROM KNOWLEDGE BASE:
{context if context else 'No specific context available — use SA construction best practices.'}

REQUIRED SECTIONS (in order):
{chr(10).join(f'  {i+1}. {s.replace("_", " ").title()}' for i, s in enumerate(sections))}

OUTPUT FORMAT:
- Use {output_format} formatting
- All monetary values in South African Rand (R)
- Dates in DD Month YYYY format
- Reference relevant SA standards (SACAP, JBCC, SANS, ASAQS) where appropriate
- Use [PLACEHOLDER] for any specific values not provided
- Be comprehensive but concise — no filler text

Generate the complete document now:"""

    model_used = "unknown"
    try:
        import litellm
        from core.llm_router import resolve_generation_route
        litellm.drop_params = True

        model, api_key, api_base = await resolve_generation_route()

        model_used = model

        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
            api_key=api_key,
            api_base=api_base,
        )
        content = response.choices[0].message.content or ""
        # Strip think tags from reasoning models
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return content, model_used

    except Exception as exc:
        logger.error("Draft generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Document generation failed: {exc}")


# ── API Endpoints ──────────────────────────────────────────────────────────────

@router.post("/draft/generate", response_model=DraftResponse,
             dependencies=[Depends(_require_auth)])
async def generate_draft(body: DraftRequest) -> DraftResponse:
    """Generate a professional document from a brief."""
    doc_config = DOCUMENT_TYPES[body.document_type]

    logger.info("Generating %s for tenant %s: %s",
                body.document_type, body.tenant_id, body.brief[:80])

    # Retrieve context from KBs
    context, sources = await _retrieve_context(
        body.tenant_id, body.brief, body.document_type, body.extra_context
    )

    # Generate document
    content, model_used = await _generate_document(
        doc_config=doc_config,
        brief=body.brief,
        context=context,
        project_name=body.project_name,
        client_name=body.client_name,
        project_value=body.project_value,
        output_format=body.output_format,
    )

    word_count = len(content.split())
    draft_id = hashlib.md5(
        f"{body.tenant_id}:{body.document_type}:{body.brief[:50]}:{datetime.utcnow().isoformat()}".encode()
    ).hexdigest()[:16]
    generated_at = datetime.utcnow().isoformat()

    # Persist
    _store.save(
        tenant_id=body.tenant_id,
        draft_id=draft_id,
        doc_type=body.document_type,
        brief=body.brief,
        content=content,
        word_count=word_count,
        model=model_used,
        sources=sources,
    )

    logger.info("Draft %s generated: %d words, %d KB sources, model=%s",
                draft_id, word_count, sources, model_used)

    return DraftResponse(
        draft_id=draft_id,
        document_type=body.document_type,
        document_label=doc_config["label"],
        content=content,
        format=body.output_format,
        word_count=word_count,
        generated_at=generated_at,
        tenant_id=body.tenant_id,
        model_used=model_used,
        sources_used=sources,
    )


@router.get("/draft/types", dependencies=[Depends(_require_auth)])
async def list_document_types() -> dict:
    """List all available document types."""
    return {
        "types": [
            {"id": k, "label": v["label"], "description": v["description"]}
            for k, v in DOCUMENT_TYPES.items()
        ]
    }


@router.get("/draft/history/{tenant_id}", dependencies=[Depends(_require_auth)])
async def draft_history(tenant_id: str, limit: int = 20) -> dict:
    """Return recent drafts for a tenant."""
    if not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", tenant_id):
        raise HTTPException(status_code=422, detail="Invalid tenant_id")
    return {"tenant_id": tenant_id, "drafts": _store.list_tenant(tenant_id, limit)}


@router.get("/draft/{draft_id}", dependencies=[Depends(_require_auth)])
async def get_draft(draft_id: str) -> dict:
    """Retrieve a previously generated draft by ID."""
    draft = _store.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found.")
    return draft
