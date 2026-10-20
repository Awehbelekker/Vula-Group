/**
 * VulaProjectWorkspace.jsx — "Claude Projects" for Vula. Pick a project → a scoped
 * workspace: named chat threads (left), project-aware chat (center), and a context rail
 * (right): editable brief, finances snapshot, recent docs, and to-dos (sync to ClickUp).
 */
import VulaProjectBoard from "./VulaProjectBoard";
import { useProjectWorkspace } from "./construction/useProjectWorkspace";
import ProjectWorkspaceV2 from "./construction/ProjectWorkspaceV2";

const C = { bg: "var(--bg)", surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)", text: "var(--text)", muted: "var(--muted)", alt: "var(--surface-alt)" };

function VulaProjectWorkspaceV1({ tenantId }) {
  const { projects, project, setProject, threads, threadId, setThreadId, messages, input, setInput, busy, brief, setBrief, briefDirty, setBriefDirty, tasks, newTask, setNewTask, mode, setMode, scroller, newThread, send, saveBrief, addTask, toggleTask } = useProjectWorkspace({ tenantId });

  return (
    <div style={{ display: "flex", height: "calc(100vh - 60px)", background: C.bg }}>
      {/* Threads rail */}
      <div style={{ width: 220, borderRight: `1px solid ${C.border}`, background: C.surface, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: 12, borderBottom: `1px solid ${C.border}` }}>
          <select value={project} onChange={(e) => setProject(e.target.value)} style={{ width: "100%", padding: "7px 8px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, fontWeight: 600 }}>
            {projects.length === 0 && <option>No projects yet</option>}
            {projects.map((p) => <option key={p} value={p}>{p.replace(/_/g, " ")}</option>)}
          </select>
          <button onClick={newThread} style={{ width: "100%", marginTop: 8, padding: "7px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}>＋ New chat</button>
        </div>
        <div style={{ overflowY: "auto", flex: 1 }}>
          {threads.map((t) => (
            <div key={t.id} onClick={() => setThreadId(t.id)} style={{ padding: "10px 12px", cursor: "pointer", fontSize: 12.5, borderBottom: `1px solid ${C.alt}`, background: threadId === t.id ? C.alt : "transparent", color: C.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              💬 {t.title || "New chat"}
            </div>
          ))}
        </div>
      </div>

      {/* Chat / Board */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ display: "flex", gap: 4, padding: "8px 12px", borderBottom: `1px solid ${C.border}`, background: C.surface }}>
          <button onClick={() => setMode("chat")} style={{ padding: "5px 12px", background: mode === "chat" ? C.alt : "transparent", border: "none", borderRadius: 7, fontSize: 12.5, fontWeight: 600, cursor: "pointer", color: mode === "chat" ? C.text : C.muted }}>💬 Chat</button>
          <button onClick={() => setMode("board")} style={{ padding: "5px 12px", background: mode === "board" ? C.alt : "transparent", border: "none", borderRadius: 7, fontSize: 12.5, fontWeight: 600, cursor: "pointer", color: mode === "board" ? C.text : C.muted }}>🗂 Board</button>
        </div>

        {mode === "board" ? (
          project && <VulaProjectBoard tenantId={tenantId} project={project} />
        ) : (
          <>
            <div ref={scroller} style={{ flex: 1, overflowY: "auto", padding: 20 }}>
              {messages.length === 0 && <div style={{ color: C.muted, fontSize: 13, textAlign: "center", marginTop: 40 }}>Ask anything about <b>{project.replace(/_/g, " ")}</b> — its documents, budget, who owes what, or what's outstanding.</div>}
              {messages.map((m, i) => (
                <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", marginBottom: 10 }}>
                  <div style={{ maxWidth: "76%", padding: "10px 13px", borderRadius: 12, fontSize: 13.5, lineHeight: 1.5, whiteSpace: "pre-wrap",
                    background: m.role === "user" ? C.green : C.surface, color: m.role === "user" ? "#fff" : C.text, border: m.role === "user" ? "none" : `1px solid ${C.border}` }}>
                    {m.content}
                  </div>
                </div>
              ))}
              {busy && <div style={{ color: C.muted, fontSize: 12 }}>Vula is thinking…</div>}
            </div>
            <div style={{ padding: 14, borderTop: `1px solid ${C.border}`, background: C.surface, display: "flex", gap: 8 }}>
              <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} placeholder={`Message ${project.replace(/_/g, " ")}…`} style={{ flex: 1, padding: "10px 12px", border: `1px solid ${C.border}`, borderRadius: 10, fontSize: 13.5 }} />
              <button onClick={send} disabled={busy} style={{ padding: "10px 18px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 10, fontWeight: 600, cursor: "pointer" }}>Send</button>
            </div>
          </>
        )}
      </div>

      {/* Context rail — hidden in board mode, which wants the full width */}
      {mode === "chat" && (
        <div style={{ width: 290, borderLeft: `1px solid ${C.border}`, background: C.surface, overflowY: "auto", padding: 14 }}>
          <div style={{ fontSize: 11, color: C.muted, textTransform: "uppercase", marginBottom: 5 }}>Project brief</div>
          <textarea value={brief} onChange={(e) => { setBrief(e.target.value); setBriefDirty(true); }} placeholder="Custom instructions for this project — scope, client preferences, key facts…"
            style={{ width: "100%", minHeight: 80, padding: 9, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 12.5, resize: "vertical", boxSizing: "border-box" }} />
          {briefDirty && <button onClick={saveBrief} style={{ marginTop: 6, padding: "5px 12px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 6, fontSize: 12, cursor: "pointer" }}>Save brief</button>}

          <div style={{ fontSize: 11, color: C.muted, textTransform: "uppercase", margin: "18px 0 6px" }}>To-dos</div>
          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <input value={newTask} onChange={(e) => setNewTask(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTask()} placeholder="Add a to-do…" style={{ flex: 1, padding: "6px 8px", border: `1px solid ${C.border}`, borderRadius: 7, fontSize: 12.5 }} />
            <button onClick={addTask} style={{ padding: "6px 10px", background: C.green, color: "var(--on-accent)", border: "none", borderRadius: 7, fontSize: 12, cursor: "pointer" }}>+</button>
          </div>
          {tasks.map((t) => (
            <div key={t.id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 0", fontSize: 12.5 }}>
              <input type="checkbox" checked={t.status === "done"} onChange={() => toggleTask(t)} />
              <span style={{ flex: 1, textDecoration: t.status === "done" ? "line-through" : "none", color: t.status === "done" ? C.muted : C.text }}>{t.title}</span>
              {t.clickup_task_id && <span title="Synced to ClickUp" style={{ fontSize: 10, color: "#7B68EE" }}>CU</span>}
            </div>
          ))}
          <p style={{ fontSize: 10.5, color: "var(--faint)", marginTop: 18, textAlign: "center" }}>This chat is scoped to {project.replace(/_/g, " ")}</p>
        </div>
      )}
    </div>
  );
}

export default function VulaProjectWorkspace({ v2 = false, ...props }) {
  return v2 ? <ProjectWorkspaceV2 {...props} /> : <VulaProjectWorkspaceV1 {...props} />;
}
