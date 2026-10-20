/**
 * TrainingV2.jsx — the redesigned Master HQ › Training KB (2026-10-20, UX redesign Phase F). Same
 * calls as VulaTraining (master/useTraining.js): the shared SA construction and SA business
 * knowledge bases every tenant's assistant falls back to: status, topics and seeding. A force
 * re-seed rebuilds the whole knowledge base, so it asks first.
 */
import { BookOpen, Database, RefreshCw } from "lucide-react";
import { Button, Card, EmptyState, PillTabs, Skeleton, StatusChip, confirmDialog } from "../ui/index.jsx";
import { KBS, useTraining } from "./useTraining";
import "./hq.css";

const TABS = Object.entries(KBS).map(([id, k]) => ({ id, label: k.label }));

export default function TrainingV2() {
  const x = useTraining();
  const k = KBS[x.kb];
  const force = async () => {
    if (await confirmDialog(`Rebuild the whole ${k.label.toLowerCase()} knowledge base from scratch? Every tenant's fallback answers come from it while it reseeds.`, { confirmLabel: "Force re-seed", danger: true })) x.triggerSeed(true);
  };
  return (
    <section className="vhq" aria-label="Training knowledge base">
      <PillTabs label="Knowledge base" tabs={TABS} active={x.kb} onChange={(id) => { x.setKb(id); x.setSeedMsg(null); }} />
      <p className="vhq-sub">{k.blurb}.</p>
      <Card className="vhq" role="group" aria-labelledby="vhq-kb-status" style={{ gap: "var(--space-3)" }}>
        <div className="vhq-row">
          <span className="vhq-grow"><h2 className="vhq-h" id="vhq-kb-status">
            {x.loading ? "Checking…" : x.isSeeded ? "Knowledge base is live" : "Not seeded yet"}
            {!x.loading && <StatusChip tone={x.isSeeded ? "ok" : "warn"}>{x.isSeeded ? "Live" : "Empty"}</StatusChip>}</h2>
            <span className="vhq-muted">{x.loading ? "" : x.isSeeded ? `${x.chunks.toLocaleString("en-ZA")} chunks · ${x.topics.length} topic documents` : `Seed it to fill the shared ${k.label.toLowerCase()} knowledge base.`}</span></span>
          <Button variant={x.isSeeded ? "secondary" : "primary"} Icon={Database} disabled={x.seeding || x.loading} onClick={() => x.triggerSeed(false)}>
            {x.seeding ? "Seeding…" : x.isSeeded ? "Re-seed" : "Seed now"}</Button>
          {x.isSeeded && <Button variant="quiet" disabled={x.seeding} onClick={force}>Force re-seed</Button>}
          <Button variant="quiet" Icon={RefreshCw} disabled={x.loading} onClick={x.loadStatus} aria-label="Refresh status">Refresh</Button>
        </div>
        {x.seedMsg && <p className="vhq-ok" role="status">{x.seedMsg}</p>}
      </Card>
      <Card className="vhq" role="group" aria-labelledby="vhq-kb-how" style={{ gap: "var(--space-2)" }}>
        <h2 className="vhq-h" id="vhq-kb-how">How the fallback works</h2>
        <ol className="vhq-steps">
          <li>A client asks a question on WhatsApp or in the dashboard.</li>
          <li>Vula searches the client's own documents first.</li>
          <li>With no good answer there, it falls back to this shared knowledge base.</li>
          <li>The client gets an answer about QS terms, contracts, materials, VAT, POPIA and the like.</li>
        </ol>
      </Card>
      <h2 className="vhq-overline">Topics ({x.topics.length})</h2>
      {x.loading ? <Card><Skeleton lines={4} /></Card>
        : x.topics.length === 0 ? <EmptyState Icon={BookOpen} title="No topics yet">Seed the knowledge base to load them.</EmptyState>
        : <Card pad={4}>
            <ul className="vhq-list" aria-label="Topics">
              {x.topics.map((d) => (
                <li key={d.filename} className="vhq-item vhq-row" style={{ padding: "var(--space-3)" }}>
                  <span className="vhq-grow"><b>{d.topic}</b><span className="vhq-muted">{d.filename} · {Math.round(d.chars / 1000)}k characters</span></span>
                  <StatusChip tone="ok">Seeded</StatusChip>
                </li>))}
            </ul>
          </Card>}
      <p className="vhq-muted">Add content in vula/training/content.py, then re-seed.</p>
    </section>
  );
}
