/**
 * useProjectWorkspace.js — a project-scoped workspace (shared by VulaProjectWorkspace and
 * ProjectWorkspaceV2): the project's chat threads and messages, its brief and its to-dos.
 * Moved verbatim out of VulaProjectWorkspace.jsx.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { VULA_API } from "../../lib/authFetch";

export const P = (t, p) => `${VULA_API}/v1/projects/${t}/${p}`;

export function useProjectWorkspace({ tenantId }) {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [threads, setThreads] = useState([]);
  const [threadId, setThreadId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [brief, setBrief] = useState("");
  const [briefDirty, setBriefDirty] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [newTask, setNewTask] = useState("");
  const [mode, setMode] = useState("chat"); // "chat" | "board"
  const scroller = useRef(null);

  useEffect(() => {
    fetch(P(tenantId, "labels")).then((r) => r.json()).then((d) => {
      setProjects(d.projects || []);
      if (!project && d.projects?.length) setProject(d.projects[0]);
    });
  }, [tenantId]);

  const loadProject = useCallback(async () => {
    if (!project) return;
    const [t, b, tk] = await Promise.all([
      fetch(P(tenantId, `p/${encodeURIComponent(project)}/threads`)).then((r) => r.json()),
      fetch(P(tenantId, `p/${encodeURIComponent(project)}/brief`)).then((r) => r.json()),
      fetch(P(tenantId, `p/${encodeURIComponent(project)}/tasks`)).then((r) => r.json()),
    ]);
    setThreads(t.threads || []);
    setThreadId((t.threads || [])[0]?.id || null);
    setBrief(b.brief || ""); setBriefDirty(false);
    setTasks(tk.tasks || []);
  }, [tenantId, project]);
  useEffect(() => { loadProject(); }, [loadProject]);

  const loadMessages = useCallback(async () => {
    if (!threadId) { setMessages([]); return; }
    const d = await fetch(P(tenantId, `threads/${threadId}/messages`)).then((r) => r.json());
    setMessages(d.messages || []);
  }, [tenantId, threadId]);
  useEffect(() => { loadMessages(); }, [loadMessages]);
  useEffect(() => { scroller.current?.scrollTo(0, scroller.current.scrollHeight); }, [messages, busy]);

  const newThread = async () => {
    const d = await fetch(P(tenantId, `p/${encodeURIComponent(project)}/threads`), { method: "POST" }).then((r) => r.json());
    if (d.thread) { setThreads([d.thread, ...threads]); setThreadId(d.thread.id); setMessages([]); }
  };

  const send = async () => {
    if (!input.trim()) return;
    let tid = threadId;
    if (!tid) {
      const d = await fetch(P(tenantId, `p/${encodeURIComponent(project)}/threads`), { method: "POST" }).then((r) => r.json());
      tid = d.thread?.id; setThreadId(tid); setThreads([d.thread, ...threads]);
    }
    const text = input; setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]); setBusy(true);
    try {
      const d = await fetch(P(tenantId, `threads/${tid}/messages`), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, message: text }),
      }).then((r) => r.json());
      setMessages((m) => [...m, { role: "assistant", content: d.reply || "…" }]);
      loadProject();
    } catch { setMessages((m) => [...m, { role: "assistant", content: "Something went wrong." }]); }
    finally { setBusy(false); }
  };

  const saveBrief = async () => {
    await fetch(P(tenantId, `p/${encodeURIComponent(project)}/brief`), {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ brief }),
    });
    setBriefDirty(false);
  };
  const addTask = async () => {
    if (!newTask.trim()) return;
    const d = await fetch(P(tenantId, `p/${encodeURIComponent(project)}/tasks`), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: newTask }),
    }).then((r) => r.json());
    if (d.task) setTasks([d.task, ...tasks]); setNewTask("");
  };
  const toggleTask = async (t) => {
    const status = t.status === "done" ? "open" : "done";
    await fetch(P(tenantId, `tasks/${t.id}`), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) });
    setTasks(tasks.map((x) => x.id === t.id ? { ...x, status } : x));
  };
  return { projects, setProjects, project, setProject, threads, setThreads, threadId, setThreadId, messages, setMessages, input, setInput, busy, setBusy, brief, setBrief, briefDirty, setBriefDirty, tasks, setTasks, newTask, setNewTask, mode, setMode, scroller, loadProject, loadMessages, newThread, send, saveBrief, addTask, toggleTask };
}
