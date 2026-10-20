/**
 * PagesV2.jsx — the redesigned Storefront › Pages (2026-10-20, UX redesign Phase F). Same calls as
 * VulaPages (pages/usePages.js). Around the Puck editor, whose canvas keeps rendering the tenant's
 * real site: the page list (reorder, open, and a Sheet of page actions), the homepage starter,
 * the live site, AI-draft (a Sheet), the editor's toolbar and SEO fields on tokens, version history
 * (a Sheet) and the refine chat. Nothing goes live until Publish; deleting, making a preview live
 * and restoring a version ask first, as before.
 */
import { useState } from "react";
import { Puck } from "@measured/puck";
import "@measured/puck/puck.css";
import { ArrowDown, ArrowLeft, ArrowUp, Copy, ExternalLink, Eye, EyeOff, History, Home, LayoutTemplate, MessageSquare,
  MoreHorizontal, Palette, Pause, Pencil, Plus, Rocket, Save, Search, Send, Sparkles, Trash2, X } from "lucide-react";
import { config, VULA_PUCK_STYLES } from "../../puck/config";
import { FONT_PAIRINGS } from "../../theme/tokens";
import { SA_TZ } from "../../lib/dates";
import VulaImageUpload from "../VulaImageUpload";
import { Button, Card, EmptyState, Field, IconButton, Input, Select, Sheet, Skeleton, StatusChip, Textarea } from "../ui/index.jsx";
import { ADDABLE_FEATURES, MOOD_PRESETS, PREVIEW_VIEWPORTS, STOREFRONT_ROOT_DOMAIN, TEMPLATES, norm, usePages } from "./usePages";
import "./pages.css";

const plain = (t) => String(t || "").replace(/\s*✓\s*/g, " ").replace(/\s+—\s+/g, ": ").trim();
const Status = ({ status }) => <StatusChip tone={status === "published" ? "ok" : "warn"}>{status === "published" ? "Live" : "Draft"}</StatusChip>;

export default function PagesV2({ tenantId }) {
  const x = usePages({ tenantId });
  return (
    <>
      {x.editing ? <Editor x={x} tenantId={tenantId} /> : <List x={x} tenantId={tenantId} />}
      <AiDraftSheet x={x} tenantId={tenantId} />
    </>
  );
}

function List({ x, tenantId }) {
  const [actionsFor, setActionsFor] = useState(null);
  const p = actionsFor;
  const act = (fn) => () => { setActionsFor(null); fn(); };
  return (
    <section className="vpg vpg-list-wrap" aria-label="Storefront pages">
      <div className="vpg-row">
        <p className="vpg-sub vpg-grow">Drag-and-drop pages, including live product grids. Publish and they're on your website straight away.</p>
        <Button Icon={Plus} onClick={() => x.setCreating(true)}>New page</Button>
      </div>

      {x.pages !== null && !x.pages.some((pg) => pg.slug === "home") && (
        <div className="vpg-banner" role="group" aria-labelledby="vpg-home">
          <Home size={22} aria-hidden="true" />
          <span className="vpg-grow"><b id="vpg-home">Customise your homepage</b>
            <span className="vpg-muted">Your site uses Vula's default homepage. Start from it here; nothing changes on your live site until you publish.</span></span>
          <Button Icon={Sparkles} onClick={() => x.openAiDraft("homepage")}>AI-draft my homepage</Button>
          <Button variant="secondary" Icon={LayoutTemplate} onClick={() => x.setEditing({ slug: "home", title: "Home", data: norm(x.homeSeed()), status: "draft", seo: {} })}>Start from a template</Button>
        </div>)}

      <div className="vpg-row">
        <Button variant="secondary" Icon={x.showLivePreview ? EyeOff : Eye} aria-expanded={x.showLivePreview} onClick={() => x.setShowLivePreview((v) => !v)}>
          {x.showLivePreview ? "Hide" : "Show"} your live site</Button>
        {x.storeUrl
          ? <a href={x.liveUrl} target="_blank" rel="noreferrer" className="vpg-row vpg-muted">Open in a new tab <ExternalLink size={14} aria-hidden="true" /></a>
          : <span className="vpg-muted">Live now at your free {tenantId}.{STOREFRONT_ROOT_DOMAIN} address; a custom domain can be added later.</span>}
      </div>
      {x.showLivePreview && <div className="vpg-frame"><iframe key={x.liveUrl} src={x.liveUrl} title="Your live site" /></div>}

      {x.pages === null ? <Card><Skeleton lines={3} /></Card>
        : x.pages.length === 0
          ? <EmptyState Icon={LayoutTemplate} title="No pages yet">Start with your homepage above, or make a new page from a template.</EmptyState>
          : <Card pad={4}>
              <ul className="vpg-pages" aria-label="Pages">
                {x.pages.map((pg, i) => {
                  const name = pg.title || pg.slug;
                  return (
                    <li key={pg.slug} className="vpg-page">
                      <span className="vpg-move">
                        <IconButton quiet Icon={ArrowUp} label={`Move ${name} up`} disabled={i === 0} onClick={() => x.move(i, -1)} />
                        <IconButton quiet Icon={ArrowDown} label={`Move ${name} down`} disabled={i === x.pages.length - 1} onClick={() => x.move(i, 1)} />
                      </span>
                      <button type="button" className="vpg-open" onClick={() => x.open(pg.slug, pg.title)}>
                        <b>{name}</b><span className="vpg-muted">/{pg.slug}</span><Status status={pg.status} />
                      </button>
                      <IconButton quiet Icon={MoreHorizontal} label={`More for ${name}`} onClick={() => setActionsFor(pg)} />
                    </li>);
                })}
              </ul>
            </Card>}

      <Sheet open={x.creating} onClose={() => x.setCreating(false)} title="New page"
        footer={<><Button variant="quiet" onClick={() => x.setCreating(false)}>Cancel</Button>
          <Button Icon={Plus} disabled={!x.newForm.title.trim()} onClick={x.createNew}>Create</Button></>}>
        <form className="vpg-form" onSubmit={(e) => { e.preventDefault(); x.createNew(); }}>
          <Field label="Page name" hint="e.g. Winter specials"><Input value={x.newForm.title} autoFocus onChange={(e) => x.setNewForm((f) => ({ ...f, title: e.target.value }))} /></Field>
          <Field label="Start from"><Select value={x.newForm.template} onChange={(e) => x.setNewForm((f) => ({ ...f, template: e.target.value }))}>
            {Object.entries(TEMPLATES).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}</Select></Field>
        </form>
      </Sheet>

      <Sheet open={!!p} onClose={() => setActionsFor(null)} title={p ? (p.title || p.slug) : ""}>
        {p && (
          <ul className="vpg-actions">
            <li><Button variant="quiet" Icon={Pencil} onClick={act(() => x.rename(p))}>Rename</Button></li>
            <li><Button variant="quiet" Icon={Copy} onClick={act(() => x.duplicate(p))}>Duplicate</Button></li>
            {!p.slug.endsWith("-preview") && <li><Button variant="quiet" Icon={Palette} onClick={act(() => x.tryNewTemplate(p))}>Try a new template</Button>
              <p className="vpg-muted" style={{ padding: "0 var(--space-3)" }}>Opens a private copy with a new design; your live page doesn't change until you make it live.</p></li>}
            <li><Button variant="quiet" Icon={Trash2} className="vpg-danger" onClick={act(() => x.del(p.slug, p.title))}>Delete</Button></li>
          </ul>)}
      </Sheet>
    </section>
  );
}

function AiDraftSheet({ x, tenantId }) {
  const close = () => !x.aiDraftBusy && x.setAiDraftTarget(null);
  return (
    <Sheet open={!!x.aiDraftTarget} onClose={close} title="AI-draft copy and design"
      footer={<><Button variant="quiet" disabled={x.aiDraftBusy} onClick={() => x.setAiDraftTarget(null)}>Cancel</Button>
        <Button Icon={Sparkles} disabled={x.aiDraftBusy} onClick={x.runAiDraft}>{x.aiDraftBusy ? "Drafting…" : "Draft it"}</Button></>}>
      <div className="vpg-form">
        <p className="vpg-muted">Optional: the more you give it, the more specific it gets. Nothing is saved until you review it, and your phone, email and address always come from your real settings.</p>
        <Field label="About your business" hint='e.g. "fresh fish shop, deliver in Cape Town, WhatsApp orders"'>
          <Textarea rows={2} value={x.aiDraftDescription} onChange={(e) => x.setAiDraftDescription(e.target.value)} />
        </Field>
        <div role="group" aria-labelledby="vpg-mood">
          <p className="vpg-muted" id="vpg-mood" style={{ marginBottom: "var(--space-2)" }}>Style (optional)</p>
          <div className="vpg-moods">
            {MOOD_PRESETS.map((m) => (
              <Button key={m.key} size="sm" variant={x.aiDraftMood === m.key ? "primary" : "secondary"} aria-pressed={x.aiDraftMood === m.key}
                onClick={() => x.setAiDraftMood((cur) => (cur === m.key ? "" : m.key))}>{m.label}</Button>))}
          </div>
        </div>
        <p className="vpg-muted">Or upload a photo or screenshot of a look you like; it's used instead of the styles above.</p>
        <VulaImageUpload tenantId={tenantId} maxFiles={1} existingUrls={x.aiDraftRefImageUrl ? [x.aiDraftRefImageUrl] : []}
          onUploaded={(urls) => x.setAiDraftRefImageUrl(urls[urls.length - 1] || "")} />
        {x.aiDraftErr && <p className="vpg-error" role="alert">{x.aiDraftErr}</p>}
      </div>
    </Sheet>
  );
}

function Editor({ x }) {
  const e = x.editing;
  const publicUrl = `${x.liveUrl.replace(/\/$/, "")}/p/${e.slug}`;   // the custom domain, or the free subdomain
  const isPreviewPage = e.slug.endsWith("-preview");
  const t = x.aiDraftTheme;

  const headerActions = ({ children }) => (
    <span className="vpg-tools">
      <IconButton quiet Icon={Sparkles} label="AI-draft this page" disabled={x.aiDraftBusy} onClick={() => x.openAiDraft("editor")} />
      <IconButton quiet Icon={MessageSquare} label="Refine with AI" aria-pressed={x.showRefine} onClick={() => x.setShowRefine((v) => !v)} />
      <IconButton quiet Icon={History} label="Version history" aria-pressed={x.showHistory} onClick={() => { x.setShowHistory((v) => !v); if (!x.showHistory) x.loadVersions(); }} />
      {x.storeUrl && <IconButton quiet Icon={Eye} label="Your live site" aria-pressed={x.showLivePreview} onClick={() => x.setShowLivePreview((v) => !v)} />}
      {isPreviewPage && <Button size="sm" Icon={Rocket} onClick={() => x.makeLive({ slug: e.slug, title: e.title })}>Make live at /{e.slug.replace(/-preview$/, "")}</Button>}
      {children}
    </span>
  );
  const setSeo = (k) => (ev) => x.setEditing((ed) => ({ ...ed, seo: { ...ed.seo, [k]: ev.target.value } }));
  const fields = ({ children, itemSelector }) => (
    <>
      {!itemSelector && (
        <div className="vpg-seo" role="group" aria-label="Page SEO">
          <b><Search size={14} aria-hidden="true" /> Page SEO</b>
          <Field label="Title" hint="Browser tab and Google"><Input value={e.seo?.title || ""} onChange={setSeo("title")} /></Field>
          <Field label="Description" hint="The search-result snippet"><Textarea rows={2} value={e.seo?.description || ""} onChange={setSeo("description")} /></Field>
          <Field label="Share image URL" hint="WhatsApp and social link previews"><Input type="url" value={e.seo?.image || ""} onChange={setSeo("image")} /></Field>
        </div>)}
      {children}
    </>
  );

  return (
    <section className="vpg" aria-label={`Editing ${e.title}`}>
      <div className="vpg-row">
        <Button variant="quiet" Icon={ArrowLeft} onClick={() => { x.setEditing(null); x.load(); }}>Pages</Button>
        <span className="vpg-grow" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2)" }}>
          <h2 className="vpg-h">{e.title}</h2><span className="vpg-muted">/{e.slug}</span><Status status={e.status} />
          {isPreviewPage && <span className="vpg-muted">Private preview, not live yet</span>}
        </span>
        {x.msg && <span className="vpg-ok" role="status">{plain(x.msg)}</span>}
        {e.status === "published" && <a href={publicUrl} target="_blank" rel="noreferrer" className="vpg-row">View live <ExternalLink size={14} aria-hidden="true" /></a>}
      </div>

      {t && (
        <div className="vpg-theme" role="group" aria-label="Suggested theme">
          <span className="vpg-swatch" style={{ background: t.accent_color }} aria-hidden="true" />
          <span className="vpg-grow">Suggested theme: {t.accent_color} · {(FONT_PAIRINGS[t.font_pairing] || {}).label || t.font_pairing}{t.styleNote ? ` ("${t.styleNote}")` : ""}</span>
          <Button size="sm" variant="secondary" disabled={x.applyingTheme} onClick={x.applyAiDraftTheme}>{x.applyingTheme ? "Applying…" : "Apply to brand kit"}</Button>
          <Button size="sm" variant="quiet" onClick={() => x.setAiDraftTheme(null)}>Dismiss</Button>
        </div>)}

      {x.showLivePreview && x.storeUrl && <div className="vpg-frame" style={{ height: 320 }}><iframe src={x.storeUrl} title="Your live site" /></div>}

      <div className="vpg-editor">
        <style>{VULA_PUCK_STYLES}{x.brandCss}</style>
        <Puck key={x.editorKey} config={config} data={e.data} onPublish={(data) => x.persist(data, "published")}
          onChange={(data) => { x.latestData.current = data; }} viewports={PREVIEW_VIEWPORTS} overrides={{ headerActions, fields }} />
      </div>
      <div className="vpg-row">
        <Button variant="secondary" Icon={Save} onClick={() => x.persist(x.latestData.current || e.data, "draft")}>Save draft</Button>
        {e.status === "published" && <Button variant="quiet" Icon={Pause} onClick={() => x.persist(x.latestData.current || e.data, "draft")}>Unpublish (back to draft)</Button>}
      </div>

      <Sheet open={x.showHistory} onClose={() => x.setShowHistory(false)} title="Version history">
        {x.versions === null ? <Skeleton lines={3} />
          : x.versions.length === 0 ? <p className="vpg-muted">No earlier versions yet. A snapshot is kept every time you publish over a page.</p>
            : <ul className="vpg-versions">
                {x.versions.map((v) => (
                  <li key={v.id}>
                    <span className="vpg-muted">{new Date(v.created_at).toLocaleString("en-ZA", { timeZone: SA_TZ, dateStyle: "medium", timeStyle: "short" })}</span>
                    <span>{v.title}</span>
                    <Button size="sm" variant="secondary" onClick={() => x.restoreVersion(v.id)}>Restore as draft</Button>
                  </li>))}
              </ul>}
      </Sheet>

      {x.showRefine && <Refine x={x} />}
    </section>
  );
}

function Refine({ x }) {
  return (
    <div className="vpg-refine" role="region" aria-label="Refine with AI">
      <div className="vpg-refine-head"><b className="vpg-grow">Refine with AI</b>
        <IconButton quiet Icon={X} label="Close refine" onClick={() => x.setShowRefine(false)} /></div>
      <ol className="vpg-thread" aria-live="polite" aria-label="Conversation">
        {x.refineMessages.length === 0 && <li className="vpg-muted">Tell me what to change, e.g. "make the headline punchier". Or paste the address of a site you like and I'll suggest features to add. Only what you ask for changes, and nothing saves until you save or publish.</li>}
        {x.refineMessages.map((m, i) => (
          <li key={i} className={`vpg-msg vpg-msg--${m.role === "user" ? "me" : "vula"}`}><span className="vpg-sr">{m.role === "user" ? "You" : "Vula"}: </span>{plain(m.text)}</li>))}
        {x.refineFoundFeatures.length > 0 && (
          <li className="vpg-row">{x.refineFoundFeatures.map((f) => (
            <Button key={f} size="sm" variant="secondary" Icon={Plus} disabled={x.refineSending} onClick={() => x.addFeature(f)}>Add {ADDABLE_FEATURES[f]}</Button>))}</li>)}
        {x.refineSending && <li className="vpg-msg vpg-msg--vula" aria-label="Vula is working">…</li>}
        <li ref={x.refineEndRef} aria-hidden="true" />
      </ol>
      <form className="vpg-compose" onSubmit={(ev) => { ev.preventDefault(); x.sendRefine(); }}>
        <Input value={x.refineInput} onChange={(ev) => x.setRefineInput(ev.target.value)} disabled={x.refineSending} aria-label="What would you like to change?" placeholder="What would you like to change?" />
        <Button type="submit" Icon={Send} disabled={x.refineSending || !x.refineInput.trim()}>Send</Button>
      </form>
    </div>
  );
}
