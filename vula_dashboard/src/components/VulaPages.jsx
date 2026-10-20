/**
 * VulaPages.jsx — Puck drag-and-drop page builder, available to EVERY tenant from the dashboard.
 * Lists a tenant's pages, creates new ones, and edits them with the shared Puck config. Saves to
 * the tenant-scoped pages API. Published pages render at /pages/:tenant/:slug (public route) and,
 * for storefronts that ship the matching renderer (e.g. OTH), at the storefront's /p/:slug too.
 *
 * Editor depth (2026-07-17): draft-vs-publish, SEO fields, rename, duplicate, starter templates,
 * and live product blocks (window.__VULA_PAGE_TENANT injected for them).
 */
import { Puck } from "@measured/puck";
import "@measured/puck/puck.css";
import { config, VULA_PUCK_STYLES } from "../puck/config";
import { FONT_PAIRINGS } from "../theme/tokens";
import VulaImageUpload from "./VulaImageUpload";
import { ADDABLE_FEATURES, MOOD_PRESETS, PREVIEW_VIEWPORTS, STOREFRONT_ROOT_DOMAIN, TEMPLATES, norm, usePages } from "./pages/usePages";
import PagesV2 from "./pages/PagesV2";

const C = { surface: "var(--surface)", border: "var(--border)", text: "var(--text)", muted: "var(--muted)" };





const btn = (bg) => ({ padding: "8px 14px", border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, color: "#fff", background: bg, cursor: "pointer" });
const rowStyle = { textAlign: "left", padding: "10px 12px", border: `1px solid var(--border)`, borderRadius: 8, background: "var(--bg)", cursor: "pointer" };
const ghost = { padding: "8px 12px", border: `1px solid ${C.border}`, background: C.surface, color: C.text, borderRadius: 8, cursor: "pointer", fontSize: 13 };
// Rendered inside Puck's own headerActions override, alongside its built-in Publish button —
// deliberately plain/small so they read as part of that toolbar, not a second competing one.
const iconBtn = { padding: "6px 9px", border: "1px solid transparent", background: "transparent", borderRadius: 6, cursor: "pointer", fontSize: 14, lineHeight: 1 };
const iconBtnActive = { background: "rgba(44,85,69,0.1)", border: "1px solid var(--accent)" };
const menuItem = { textAlign: "left", padding: "8px 12px", border: "none", background: "transparent", borderRadius: 6, cursor: "pointer", fontSize: 13, color: C.text, whiteSpace: "nowrap" };

function VulaPagesV1({ tenantId }) {
  const { pages, editing, setEditing, creating, setCreating, openMenuSlug, setOpenMenuSlug, newForm, setNewForm, showSeo, setShowSeo, showHistory, setShowHistory, versions, msg, storeUrl, showLivePreview, setShowLivePreview, latestData, aiDraftBusy, aiDraftErr, editorKey, aiDraftTarget, setAiDraftTarget, aiDraftDescription, setAiDraftDescription, aiDraftMood, setAiDraftMood, aiDraftRefImageUrl, setAiDraftRefImageUrl, aiDraftTheme, setAiDraftTheme, applyingTheme, showRefine, setShowRefine, refineMessages, refineInput, setRefineInput, refineSending, refineFoundFeatures, refineEndRef, brandCss, liveUrl, load, open, homeSeed, createNew, persist, rename, duplicate, del, tryNewTemplate, makeLive, move, loadVersions, restoreVersion, openAiDraft, runAiDraft, applyAiDraftTheme, sendRefine, addFeature } = usePages({ tenantId });

  // Shared across both the list view (homepage banner) and the editor (✨ icon) — one modal,
  // one flow, regardless of which entry point opened it.
  const aiDraftModal = aiDraftTarget && (
    <>
      <div onClick={() => !aiDraftBusy && setAiDraftTarget(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", zIndex: 1099 }} />
      <div style={{ position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)", width: 420, maxWidth: "92vw",
        background: "var(--surface)", borderRadius: 12, boxShadow: "0 12px 40px rgba(0,0,0,0.25)", zIndex: 1100, padding: 20 }}>
        <strong style={{ color: C.text, fontSize: 15 }}>✨ AI-draft copy &amp; design</strong>
        <p style={{ fontSize: 12, color: C.muted, margin: "4px 0 14px" }}>
          Optional — the more you give it, the more specific the result. Nothing is saved until you review it.
        </p>
        <textarea
          placeholder='Briefly describe your business (e.g. "fresh fish shop, deliver in Cape Town, WhatsApp orders")'
          value={aiDraftDescription} onChange={(e) => setAiDraftDescription(e.target.value)} rows={2}
          style={{ width: "100%", padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, fontFamily: "inherit", resize: "vertical", marginBottom: 12, boxSizing: "border-box" }}
        />
        <p style={{ fontSize: 11.5, fontWeight: 600, color: C.muted, textTransform: "uppercase", margin: "0 0 6px" }}>Style direction (optional)</p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
          {MOOD_PRESETS.map((m) => (
            <button key={m.key} onClick={() => setAiDraftMood((cur) => (cur === m.key ? "" : m.key))}
              style={{ ...ghost, padding: "6px 11px", fontSize: 12.5, ...(aiDraftMood === m.key ? iconBtnActive : {}) }}>
              {m.label}
            </button>
          ))}
        </div>
        <p style={{ fontSize: 11.5, color: C.muted, margin: "0 0 6px" }}>
          Or upload a photo/screenshot of a look you like — used instead of the presets above:
        </p>
        <VulaImageUpload tenantId={tenantId} maxFiles={1}
          existingUrls={aiDraftRefImageUrl ? [aiDraftRefImageUrl] : []}
          onUploaded={(urls) => setAiDraftRefImageUrl(urls[urls.length - 1] || "")} />
        {aiDraftErr && <p style={{ color: "var(--danger)", fontSize: 12, margin: "10px 0 0" }}>{aiDraftErr}</p>}
        <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={() => setAiDraftTarget(null)} disabled={aiDraftBusy} style={ghost}>Cancel</button>
          <button onClick={runAiDraft} disabled={aiDraftBusy} style={btn("var(--accent)")}>
            {aiDraftBusy ? "Drafting…" : "✨ Draft it"}
          </button>
        </div>
      </div>
    </>
  );

  const aiDraftThemeStrip = aiDraftTheme && (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", marginBottom: 8,
      background: "var(--accent-soft, rgba(44,85,69,0.08))", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12.5 }}>
      <span style={{ width: 18, height: 18, borderRadius: "50%", background: aiDraftTheme.accent_color, border: `1px solid ${C.border}`, flexShrink: 0 }} />
      <span style={{ color: C.text }}>
        Suggested theme: <strong>{aiDraftTheme.accent_color}</strong> · {(FONT_PAIRINGS[aiDraftTheme.font_pairing] || {}).label || aiDraftTheme.font_pairing}
        {aiDraftTheme.styleNote ? ` — "${aiDraftTheme.styleNote}"` : ""}
      </span>
      <button onClick={applyAiDraftTheme} disabled={applyingTheme} style={{ ...ghost, padding: "4px 10px", fontSize: 12, marginLeft: "auto" }}>
        {applyingTheme ? "Applying…" : "Apply to brand kit"}
      </button>
      <button onClick={() => setAiDraftTheme(null)} style={{ ...ghost, padding: "4px 9px", fontSize: 12 }}>Dismiss</button>
    </div>
  );

  if (editing) {
    const publicUrl = storeUrl
      ? `${storeUrl.replace(/\/$/, "")}/p/${editing.slug}`
      : `https://${tenantId}.${STOREFRONT_ROOT_DOMAIN}/p/${editing.slug}`;
    const isPreviewPage = editing.slug.endsWith("-preview");

    // Puck's own overrides API (confirmed present in the installed package — not a workaround)
    // — headerActions extends Puck's OWN toolbar (where its built-in Publish button already
    // lives) instead of stacking a second row of buttons above the canvas; fields extends its
    // own right-hand sidebar so Page SEO reads as part of the editor, not a separate toggle
    // panel that pushes the canvas down every time someone opens it.
    const headerActions = ({ children }) => (
      <>
        <button onClick={() => openAiDraft("editor")} disabled={aiDraftBusy} title="AI-draft real copy & design for this page" style={iconBtn}>{aiDraftBusy ? "⏳" : "✨"}</button>
        <button onClick={() => setShowRefine((v) => !v)} title="Refine with AI" style={{ ...iconBtn, ...(showRefine ? iconBtnActive : {}) }}>💬</button>
        <button onClick={() => setShowSeo((s) => !s)} title="Page SEO" style={{ ...iconBtn, ...(showSeo ? iconBtnActive : {}) }}>🔍</button>
        <button onClick={() => { setShowHistory((v) => !v); if (!showHistory) loadVersions(); }} title="Version history" style={{ ...iconBtn, ...(showHistory ? iconBtnActive : {}) }}>🕐</button>
        {storeUrl && (
          <button onClick={() => setShowLivePreview((v) => !v)} title="Current live site" style={{ ...iconBtn, ...(showLivePreview ? iconBtnActive : {}) }}>👁</button>
        )}
        {isPreviewPage && (
          <button onClick={() => makeLive({ slug: editing.slug, title: editing.title })} style={{ ...btn("var(--accent)"), fontSize: 12, padding: "7px 12px" }}>
            ✓ Make live at /{editing.slug.replace(/-preview$/, "")}
          </button>
        )}
        {editing.status === "published" && <a href={publicUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12.5, marginRight: 4 }}>View live ↗</a>}
        {children}
      </>
    );

    const fields = ({ children, itemSelector }) => (
      <>
        {!itemSelector && (
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}` }}>
            <p style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: C.muted, margin: "0 0 8px" }}>Page SEO</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <input placeholder="SEO title (browser tab / Google)" value={editing.seo?.title || ""}
                onChange={(e) => setEditing((ed) => ({ ...ed, seo: { ...ed.seo, title: e.target.value } }))}
                style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12.5 }} />
              <textarea placeholder="SEO description (search result snippet)" value={editing.seo?.description || ""}
                onChange={(e) => setEditing((ed) => ({ ...ed, seo: { ...ed.seo, description: e.target.value } }))}
                rows={2} style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12.5, fontFamily: "inherit", resize: "vertical" }} />
              <input placeholder="Share image URL (WhatsApp / social link preview)" value={editing.seo?.image || ""}
                onChange={(e) => setEditing((ed) => ({ ...ed, seo: { ...ed.seo, image: e.target.value } }))}
                style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12.5 }} />
            </div>
          </div>
        )}
        {children}
      </>
    );

    return (
      <div>
        {aiDraftModal}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", flexWrap: "wrap" }}>
          <button onClick={() => { setEditing(null); load(); }} style={btn("var(--muted)")}>← Pages</button>
          <strong style={{ color: C.text }}>{editing.title}</strong>
          <span style={{ color: C.muted, fontSize: 12 }}>/{editing.slug}</span>
          <span style={{ fontSize: 11, fontWeight: 600, borderRadius: 999, padding: "2px 9px",
            color: editing.status === "published" ? "var(--ok)" : "var(--warn)",
            background: editing.status === "published" ? "rgba(44,122,75,.12)" : "rgba(183,121,31,.12)" }}>
            {editing.status === "published" ? "Live" : "Draft"}
          </span>
          {msg && <span style={{ color: "var(--ok)", fontSize: 12 }}>{msg}</span>}
          {isPreviewPage && <span style={{ fontSize: 12, color: C.muted }}>— private preview, not live yet</span>}
        </div>

        {aiDraftThemeStrip}

        {showLivePreview && storeUrl && (
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", height: 320, marginBottom: 10, background: "var(--bg)" }}>
            <iframe src={storeUrl} title="Current live site" style={{ width: "100%", height: "100%", border: "none" }} />
          </div>
        )}

        <div style={{ height: "74vh", border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
          {/* Both get mirrored into Puck's canvas iframe (see brandCss comment above) — this is
              the only way animation/depth CSS and the tenant's real brand colors reach the
              live preview while editing. */}
          <style>{VULA_PUCK_STYLES}{brandCss}</style>
          <Puck key={editorKey} config={config} data={editing.data} onPublish={(data) => persist(data, "published")}
            onChange={(data) => { latestData.current = data; }}
            viewports={PREVIEW_VIEWPORTS} overrides={{ headerActions, fields }} />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button onClick={() => persist(latestData.current || editing.data, "draft")} style={ghost}>💾 Save draft</button>
          {editing.status === "published" && (
            <button onClick={() => persist(latestData.current || editing.data, "draft")} style={{ ...ghost, color: "var(--warn)" }}>⏸ Unpublish (back to draft)</button>
          )}
        </div>

        {/* Fixed-position drawer, not inline — history is looked at occasionally, not something
            that should ever displace the canvas the way a push-down panel did before. */}
        {showHistory && (
          <>
            <div onClick={() => setShowHistory(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.15)", zIndex: 999 }} />
            <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 340, background: "var(--surface)",
              borderLeft: `1px solid ${C.border}`, boxShadow: "-8px 0 28px rgba(0,0,0,0.12)", zIndex: 1000,
              padding: 16, overflowY: "auto" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <strong style={{ color: C.text }}>Version history</strong>
                <button onClick={() => setShowHistory(false)} style={{ ...ghost, padding: "4px 9px" }}>✕</button>
              </div>
              {versions === null ? <span style={{ fontSize: 12, color: C.muted }}>Loading…</span> :
                versions.length === 0 ? <span style={{ fontSize: 12, color: C.muted }}>No earlier versions yet — a snapshot is taken every time you publish over an existing page.</span> :
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {versions.map((v) => (
                      <div key={v.id} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5, paddingBottom: 10, borderBottom: `1px solid var(--surface-alt)` }}>
                        <span style={{ color: C.muted }}>{new Date(v.created_at).toLocaleString("en-ZA")}</span>
                        <span>{v.title}</span>
                        <button onClick={() => restoreVersion(v.id)} style={{ ...ghost, padding: "4px 10px", fontSize: 11, alignSelf: "flex-start" }}>Restore as draft</button>
                      </div>
                    ))}
                  </div>}
            </div>
          </>
        )}

        {/* Floating chat widget, bottom-right — separate from History's right-side drawer so the
            two never collide. Only mounted while open, so no cost when unused. */}
        {showRefine && (
          <div style={{ position: "fixed", bottom: 16, right: 16, width: 340, height: 420, background: "var(--surface)",
            border: `1px solid ${C.border}`, borderRadius: 12, boxShadow: "0 8px 28px rgba(0,0,0,0.18)",
            zIndex: 1000, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 14px", borderBottom: `1px solid ${C.border}` }}>
              <strong style={{ fontSize: 13, color: C.text }}>💬 Refine with AI</strong>
              <button onClick={() => setShowRefine(false)} style={{ ...ghost, padding: "4px 9px" }}>✕</button>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
              {refineMessages.length === 0 && (
                <p style={{ fontSize: 12, color: C.muted, margin: 0 }}>
                  Tell me what to change — e.g. "make the headline punchier" or "less corporate."
                  Or paste a URL of a site you like and I'll suggest real features to add.
                  Only what you ask for changes; nothing saves until you hit Save draft / Publish.
                </p>
              )}
              {refineMessages.map((m, i) => (
                <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
                  <div style={{ maxWidth: "85%", padding: "8px 12px", borderRadius: 12, fontSize: 13, lineHeight: 1.4,
                    whiteSpace: "pre-wrap", ...(m.role === "user"
                      ? { background: "var(--accent)", color: "#fff", borderBottomRightRadius: 3 }
                      : { background: "var(--bg)", color: C.text, border: `1px solid ${C.border}`, borderBottomLeftRadius: 3 }) }}>
                    {m.text}
                  </div>
                </div>
              ))}
              {refineFoundFeatures.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {refineFoundFeatures.map((f) => (
                    <button key={f} onClick={() => addFeature(f)} disabled={refineSending}
                      style={{ ...ghost, padding: "6px 11px", fontSize: 12 }}>
                      + Add {ADDABLE_FEATURES[f]}
                    </button>
                  ))}
                </div>
              )}
              {refineSending && (
                <div style={{ display: "flex", justifyContent: "flex-start" }}>
                  <div style={{ padding: "8px 12px", borderRadius: 12, fontSize: 13, background: "var(--bg)", border: `1px solid ${C.border}` }}>…</div>
                </div>
              )}
              <div ref={refineEndRef} />
            </div>
            <form onSubmit={(e) => { e.preventDefault(); sendRefine(); }} style={{ display: "flex", gap: 6, padding: 10, borderTop: `1px solid ${C.border}` }}>
              <input value={refineInput} onChange={(e) => setRefineInput(e.target.value)} disabled={refineSending}
                placeholder="What would you like to change?"
                style={{ flex: 1, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, boxSizing: "border-box" }} />
              <button type="submit" disabled={refineSending || !refineInput.trim()} style={btn("var(--accent)")}>Send</button>
            </form>
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
      {aiDraftModal}
      <div style={{ display: "flex", alignItems: "center" }}>
        <strong style={{ color: C.text }}>Storefront pages</strong>
        <button onClick={() => setCreating((c) => !c)} style={{ ...btn("var(--accent)"), marginLeft: "auto" }}>{creating ? "Close" : "+ New page"}</button>
      </div>
      <p style={{ fontSize: 12, color: C.muted, margin: "4px 0 12px" }}>
        Drag-and-drop pages, including live product grids — publish and they're on your website automatically.
      </p>

      {/* Editing "a new page" doesn't obviously connect to "the homepage I already have live" —
          this makes that connection explicit and one click, instead of requiring the tenant to
          know that naming a page exactly "Home" is what makes it take over the site's homepage. */}
      {pages !== null && !pages.some((p) => p.slug === "home") && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, padding: 12,
          background: "var(--accent-soft, rgba(44,85,69,0.08))", border: `1px solid ${C.border}`, borderRadius: 8 }}>
          <span style={{ fontSize: 20 }}>🏠</span>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13, color: C.text }}>Want to customize your homepage?</strong>
            <p style={{ fontSize: 12, color: C.muted, margin: "2px 0 0" }}>
              Your site is currently using Vula's default homepage design. Start from it below —
              nothing changes on your live site until you hit <strong>Publish</strong>.
            </p>
          </div>
          <button onClick={() => openAiDraft("homepage")} style={btn("var(--accent)")}>
            ✨ AI-draft my homepage
          </button>
          <button
            onClick={() => setEditing({ slug: "home", title: "Home", data: norm(homeSeed()), status: "draft", seo: {} })}
            style={ghost}
          >
            ✎ Start from template
          </button>
        </div>
      )}

      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: showLivePreview ? 8 : 0 }}>
          <button onClick={() => setShowLivePreview((v) => !v)} style={ghost}>
            {showLivePreview ? "▲ Hide" : "▼ Show"} current live site
          </button>
          {storeUrl && <a href={liveUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>Open in new tab ↗</a>}
          {!storeUrl && <span style={{ fontSize: 11, color: C.muted }}>(live now at your free {tenantId}.{STOREFRONT_ROOT_DOMAIN} address — a custom domain can be added later)</span>}
        </div>
        {showLivePreview && (
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", height: 480, background: "var(--bg)" }}>
            <iframe
              key={liveUrl}
              src={liveUrl}
              title="Current live site"
              style={{ width: "100%", height: "100%", border: "none" }}
              onError={(e) => { e.target.style.display = "none"; }}
            />
          </div>
        )}
      </div>

      {creating && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12, background: "var(--bg)", border: `1px solid ${C.border}`, borderRadius: 8, padding: 10 }}>
          <input placeholder="Page name (e.g. Winter Specials)" value={newForm.title} autoFocus
            onChange={(e) => setNewForm((f) => ({ ...f, title: e.target.value }))}
            onKeyDown={(e) => e.key === "Enter" && createNew()}
            style={{ flex: 1, minWidth: 180, padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13 }} />
          <select value={newForm.template} onChange={(e) => setNewForm((f) => ({ ...f, template: e.target.value }))}
            style={{ padding: "8px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13 }}>
            {Object.entries(TEMPLATES).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
          </select>
          <button onClick={createNew} style={btn("var(--accent)")}>Create</button>
        </div>
      )}

      {pages === null ? <p style={{ color: C.muted }}>Loading…</p> :
        pages.length === 0 ? <p style={{ color: C.muted }}>No pages yet — create your first one (try the "Customize homepage" button above, or a homepage template below).</p> :
          <div style={{ display: "grid", gap: 8 }}>
            {pages.map((p, i) => (
              <div key={p.slug} style={{ display: "flex", alignItems: "center", gap: 6, position: "relative" }}>
                <div style={{ display: "flex", flexDirection: "column" }}>
                  <button onClick={() => move(i, -1)} disabled={i === 0} title="Move up"
                    style={{ ...ghost, padding: "1px 8px", fontSize: 10, opacity: i === 0 ? 0.3 : 1, borderBottom: "none", borderRadius: "6px 6px 0 0" }}>▲</button>
                  <button onClick={() => move(i, 1)} disabled={i === pages.length - 1} title="Move down"
                    style={{ ...ghost, padding: "1px 8px", fontSize: 10, opacity: i === pages.length - 1 ? 0.3 : 1, borderRadius: "0 0 6px 6px" }}>▼</button>
                </div>
                <button onClick={() => open(p.slug, p.title)} style={{ ...rowStyle, flex: 1 }}>
                  <span style={{ fontWeight: 600, color: C.text }}>{p.title || p.slug}</span>
                  <span style={{ color: C.muted, fontSize: 12 }}>  /{p.slug}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, marginLeft: 8,
                    color: p.status === "published" ? "var(--ok)" : "var(--warn)" }}>
                    {p.status === "published" ? "● Live" : "○ Draft"}
                  </span>
                </button>
                {/* Rename/duplicate/try-template/delete are all occasional actions — collapsed into
                    one overflow menu instead of 4 separate always-visible icon buttons per row. */}
                <button onClick={() => setOpenMenuSlug((s) => s === p.slug ? null : p.slug)} title="More actions"
                  style={{ ...ghost, padding: "8px 10px" }}>⋯</button>
                {openMenuSlug === p.slug && (
                  <>
                    <div onClick={() => setOpenMenuSlug(null)} style={{ position: "fixed", inset: 0, zIndex: 10 }} />
                    <div style={{ position: "absolute", top: "100%", right: 0, marginTop: 4, zIndex: 11,
                      background: "var(--surface)", border: `1px solid ${C.border}`, borderRadius: 8, boxShadow: "0 4px 16px rgba(0,0,0,0.1)",
                      minWidth: 170, display: "flex", flexDirection: "column", padding: 4 }}>
                      <button onClick={() => { setOpenMenuSlug(null); rename(p); }} style={menuItem}>✏️ Rename</button>
                      <button onClick={() => { setOpenMenuSlug(null); duplicate(p); }} style={menuItem}>⧉ Duplicate</button>
                      {!p.slug.endsWith("-preview") && (
                        <button onClick={() => { setOpenMenuSlug(null); tryNewTemplate(p); }} style={menuItem} title="Preview this page with a new template — never affects the live version until you publish it">
                          🎨 Try new template
                        </button>
                      )}
                      <button onClick={() => { setOpenMenuSlug(null); del(p.slug, p.title); }} style={{ ...menuItem, color: "var(--danger)" }}>🗑 Delete</button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>}
    </div>
  );
}

export default function VulaPages({ v2 = false, ...props }) {
  return v2 ? <PagesV2 {...props} /> : <VulaPagesV1 {...props} />;
}
