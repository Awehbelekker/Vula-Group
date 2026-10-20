/**
 * DraftV2.jsx — the redesigned Estimating › Draft (2026-10-20, UX redesign Phase F). Same call as
 * VulaDraft (estimating/useDraft.js): Vula drafts a construction document (fee proposal, scope of
 * works, appointment letter, minutes, tender invitation, programme, payment certificate) from a
 * brief and the business's own knowledge base, to copy into your letterhead.
 */
import { useState } from "react";
import { Copy, FileText, Sparkles } from "lucide-react";
import { Button, Card, EmptyState, Field, Input, Select, Textarea } from "../ui/index.jsx";
import { DOC_TYPES, useDraft } from "./useDraft";
import "./estimating.css";

export default function DraftV2({ tenantId }) {
  const x = useDraft({ tenantId });
  const [copied, setCopied] = useState(false);
  const copy = () => { x.copyToClipboard(); setCopied(true); setTimeout(() => setCopied(false), 2500); };
  const r = x.result;
  return (
    <section className="ves" aria-label="Draft a document">
      <p className="ves-sub">Draft a professional construction document from a short brief. Vula writes it from your own documents and knowledge base; you check it and put it on your letterhead.</p>
      <Card className="ves-card">
        <form className="ves-form" onSubmit={(e) => { e.preventDefault(); x.generate(); }}>
          <Field label="Document"><Select value={x.docType} onChange={(e) => x.setDocType(e.target.value)}>{DOC_TYPES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}</Select></Field>
          <Field label="Brief" hint="e.g. Fee proposal for a 450 m² office fit-out in Sea Point, full service from concept to construction admin">
            <Textarea rows={4} value={x.brief} onChange={(e) => x.setBrief(e.target.value)} />
          </Field>
          <div className="ves-grid">
            <Field label="Project" hint="Optional"><Input value={x.projectName} onChange={(e) => x.setProjectName(e.target.value)} /></Field>
            <Field label="Client" hint="Optional"><Input value={x.clientName} onChange={(e) => x.setClientName(e.target.value)} /></Field>
            <Field label="Project value (R)" hint="Optional"><Input type="number" min="0" inputMode="decimal" value={x.projectValue} onChange={(e) => x.setProjectValue(e.target.value)} /></Field>
          </div>
          <div><Button type="submit" Icon={Sparkles} disabled={x.loading || !x.brief.trim() || !x.tenantId}>{x.loading ? "Drafting…" : "Draft it"}</Button></div>
          {x.error && <p className="ves-error" role="alert">{x.error}</p>}
        </form>
      </Card>
      {r ? (
        <Card className="ves-card" role="group" aria-labelledby="ves-draft-h">
          <div className="ves-row">
            <span className="ves-grow"><h2 className="ves-h" id="ves-draft-h">{r.document_label}</h2>
              <span className="ves-muted">{r.word_count} words · {r.sources_used} {r.sources_used === 1 ? "source" : "sources"} from your knowledge base</span></span>
            <Button variant="secondary" Icon={Copy} onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
          </div>
          <pre className="ves-doc" tabIndex={0} aria-label="Drafted document">{r.content}</pre>
        </Card>
      ) : !x.loading && <EmptyState Icon={FileText} title="Nothing drafted yet">Pick a document, write a short brief, and Vula drafts it.</EmptyState>}
    </section>
  );
}
