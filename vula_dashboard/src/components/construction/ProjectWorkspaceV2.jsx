/**
 * ProjectWorkspaceV2.jsx — the redesigned Operate › Workspace (2026-10-20, UX redesign Phase F).
 * Same calls as VulaProjectWorkspace (construction/useProjectWorkspace.js): pick a project for a
 * scoped workspace with named chat threads, a project-aware chat, the project brief (custom
 * instructions) and its to-dos (synced to ClickUp where connected). Board keeps the existing
 * freeform board. Three columns from 1200px; stacked below.
 */
import { MessageSquare, Plus, Send } from "lucide-react";
import { Button, Card, Field, Input, PillTabs, Select, StatusChip, Textarea } from "../ui/index.jsx";
import VulaProjectBoard from "../VulaProjectBoard";
import { useProjectWorkspace } from "./useProjectWorkspace";
import "./construction.css";

const nice = (p) => String(p || "").replace(/_/g, " ");
const MODES = [{ id: "chat", label: "Chat" }, { id: "board", label: "Board" }];

export default function ProjectWorkspaceV2({ tenantId }) {
  const x = useProjectWorkspace({ tenantId });
  const name = nice(x.project);
  return (
    <section className="vco" style={{ maxWidth: "none" }} aria-label="Project workspace">
      <div className="vcx-row">
        <span className="vcx-grow" style={{ flex: "1 1 240px" }}>
          <Field label="Project">
            <Select value={x.project} onChange={(e) => x.setProject(e.target.value)}>
              {x.projects.length === 0 && <option value="">No projects yet</option>}
              {x.projects.map((p) => <option key={p} value={p}>{nice(p)}</option>)}
            </Select>
          </Field>
        </span>
        <PillTabs label="Workspace view" tabs={MODES} active={x.mode} onChange={x.setMode} />
      </div>

      {x.mode === "board"
        ? (x.project && <VulaProjectBoard tenantId={tenantId} project={x.project} />)
        : (
          <div className="vcx-ws">
            <Card className="vcx-card" role="group" aria-labelledby="vcx-ws-threads">
              <div className="vcx-row"><h2 className="vcx-h" id="vcx-ws-threads" style={{ flex: 1 }}>Chats</h2>
                <Button size="sm" variant="secondary" Icon={Plus} disabled={!x.project} onClick={x.newThread}>New chat</Button></div>
              {x.threads.length === 0 ? <p className="vcx-muted">No chats yet. Ask something and one starts.</p> : (
                <ul className="vcx-threads">
                  {x.threads.map((t) => (
                    <li key={t.id}><button type="button" className="vcx-thread" aria-current={x.threadId === t.id ? "true" : undefined} onClick={() => x.setThreadId(t.id)}>
                      <MessageSquare size={16} aria-hidden="true" style={{ flex: "none" }} /><span>{t.title || "New chat"}</span></button></li>))}
                </ul>)}
            </Card>

            <Card className="vcx-card vcx-chat" role="group" aria-label={`Chat about ${name || "this project"}`}>
              <ol className="vcx-msgs" ref={x.scroller} aria-live="polite" aria-label="Conversation">
                {x.messages.length === 0 && <li className="vcx-muted" style={{ textAlign: "center", margin: "var(--space-6) auto" }}>Ask anything about <b>{name}</b>: its documents, budget, who owes what, or what's outstanding.</li>}
                {x.messages.map((m, i) => (
                  <li key={i} className={`vcx-msg vcx-msg--${m.role === "user" ? "me" : "vula"}`}><span className="vcx-sr">{m.role === "user" ? "You" : "Vula"}: </span>{m.content}</li>))}
                {x.busy && <li className="vcx-muted">Vula is thinking…</li>}
              </ol>
              <form className="vcx-compose" onSubmit={(e) => { e.preventDefault(); x.send(); }}>
                <Input value={x.input} onChange={(e) => x.setInput(e.target.value)} aria-label={`Message about ${name || "this project"}`} placeholder={`Ask about ${name}…`} disabled={!x.project} />
                <Button type="submit" Icon={Send} disabled={x.busy || !x.project || !x.input.trim()}>Send</Button>
              </form>
            </Card>

            <Card className="vcx-card" role="group" aria-labelledby="vcx-ws-brief">
              <h2 className="vcx-h" id="vcx-ws-brief">Project brief</h2>
              <Field label="Instructions for this project" hint="Scope, client preferences, key facts. Vula reads it in every chat here.">
                <Textarea rows={4} value={x.brief} onChange={(e) => { x.setBrief(e.target.value); x.setBriefDirty(true); }} />
              </Field>
              {x.briefDirty && <div><Button size="sm" onClick={x.saveBrief}>Save brief</Button></div>}
              <h2 className="vcx-h">To-dos</h2>
              <form className="vcx-row" onSubmit={(e) => { e.preventDefault(); x.addTask(); }}>
                <span className="vcx-grow" style={{ flex: "1 1 120px" }}><Input value={x.newTask} onChange={(e) => x.setNewTask(e.target.value)} aria-label="New to-do" placeholder="Add a to-do…" /></span>
                <Button type="submit" size="sm" variant="secondary" Icon={Plus} disabled={!x.newTask.trim()}>Add</Button>
              </form>
              <ul className="vcx-list" aria-label="To-dos">
                {x.tasks.map((t) => (
                  <li key={t.id}>
                    <label className={`vcx-todo${t.status === "done" ? " vcx-todo--done" : ""}`}>
                      <input type="checkbox" checked={t.status === "done"} onChange={() => x.toggleTask(t)} />
                      <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{t.title}</span>
                      {t.clickup_task_id && <StatusChip outline>ClickUp</StatusChip>}
                    </label>
                  </li>))}
              </ul>
            </Card>
          </div>)}
    </section>
  );
}
