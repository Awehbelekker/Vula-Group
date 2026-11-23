/**
 * useProjectWorkspace.js — a project-scoped workspace (shared by VulaProjectWorkspace and
 * ProjectWorkspaceV2): the project's chat threads and messages, its brief and its to-dos.
 * Moved verbatim out of VulaProjectWorkspace.jsx.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { VULA_API } from "../../lib/authFetch";
import { api, tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

export const P = (t, p) => `${VULA_API}/v1/projects/${t}/${p}`;
// The same paths without the host, for lib/api (which adds it). 23 Nov 2026 audit: nothing here
// checked the server's answer — a refused brief showed as saved, a failed to-do cleared the box.
const PP = (t, p) => `/v1/projects/${t}/${p}`;

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
    tryApi(PP(tenantId, "labels")).then(({ ok, data: d, error }) => {
      if (!ok) { toast(`Your projects couldn't be loaded: ${error}`, "danger"); return; }
      setProjects(d.projects || []);
      if (!project && d.projects?.length) setProject(d.projects[0]);
    });
  }, [tenantId]);

  const loadProject = useCallback(async () => {
    if (!project) return;
    const [t, b, tk] = await Promise.all([
      tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/threads`)),
      tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/brief`)),
      tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/tasks`)),
    ]);
    // Only what loaded replaces what's on screen (a failed brief must not look empty and be saved over).
    if (t.ok) {
      setThreads(t.data.threads || []);
      setThreadId((t.data.threads || [])[0]?.id || null);
    }
    if (b.ok) { setBrief(b.data.brief || ""); setBriefDirty(false); }
    if (tk.ok) setTasks(tk.data.tasks || []);
    const failed = [t, b, tk].find((x) => !x.ok);
    if (failed) toast(`Part of this project couldn't be loaded: ${failed.error}`, "danger");
  }, [tenantId, project]);
  useEffect(() => { loadProject(); }, [loadProject]);

  const loadMessages = useCallback(async () => {
    if (!threadId) { setMessages([]); return; }
    const { ok, data: d, error } = await tryApi(PP(tenantId, `threads/${threadId}/messages`));
    if (!ok) { setMessages([]); toast(`This chat couldn't be loaded: ${error}`, "danger"); return; }
    setMessages(d.messages || []);
  }, [tenantId, threadId]);
  useEffect(() => { loadMessages(); }, [loadMessages]);
  useEffect(() => { scroller.current?.scrollTo(0, scroller.current.scrollHeight); }, [messages, busy]);

  const newThread = async () => {
    const { ok, data: d, error } = await tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/threads`), { method: "POST" });
    if (!ok) { toast(`A new chat couldn't be started: ${error}`, "danger"); return; }
    if (d.thread) { setThreads([d.thread, ...threads]); setThreadId(d.thread.id); setMessages([]); }
  };

  const send = async () => {
    if (!input.trim()) return;
    let tid = threadId;
    if (!tid) {
      const { ok, data: d, error } = await tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/threads`), { method: "POST" });
      if (!ok || !d.thread?.id) { toast(`Your message wasn't sent — a chat couldn't be started: ${error || "try again."}`, "danger"); return; }
      tid = d.thread?.id; setThreadId(tid); setThreads([d.thread, ...threads]);
    }
    const text = input; setInput("");
    const mine = { role: "user", content: text };
    setMessages((m) => [...m, mine]); setBusy(true);
    try {
      const d = await api(PP(tenantId, `threads/${tid}/messages`), {
        method: "POST", body: { project, message: text },
      });
      setMessages((m) => [...m, { role: "assistant", content: d.reply || "…" }]);
      loadProject();
    } catch (e) {
      // the question goes back in the box to send again
      setMessages((m) => m.filter((x) => x !== mine));
      setInput(text);
      toast(`Vula didn't answer that: ${e.message}`, "danger");
    }
    finally { setBusy(false); }
  };

  const saveBrief = async () => {
    const { ok, error } = await tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/brief`), {
      method: "PUT", body: { brief },
    });
    if (!ok) { toast(`The brief wasn't saved: ${error}`, "danger"); return; }
    setBriefDirty(false);
  };
  const addTask = async () => {
    if (!newTask.trim()) return;
    const { ok, data: d, error } = await tryApi(PP(tenantId, `p/${encodeURIComponent(project)}/tasks`), {
      method: "POST", body: { title: newTask },
    });
    if (!ok) { toast(`The to-do wasn't added: ${error}`, "danger"); return; }
    if (d.task) setTasks([d.task, ...tasks]); setNewTask("");
  };
  const toggleTask = async (t) => {
    const status = t.status === "done" ? "open" : "done";
    const { ok, error } = await tryApi(PP(tenantId, `tasks/${t.id}`), { method: "PATCH", body: { status } });
    if (!ok) { toast(`That to-do wasn't updated: ${error}`, "danger"); return; }
    setTasks(tasks.map((x) => x.id === t.id ? { ...x, status } : x));
  };
  return { projects, setProjects, project, setProject, threads, setThreads, threadId, setThreadId, messages, setMessages, input, setInput, busy, setBusy, brief, setBrief, briefDirty, setBriefDirty, tasks, setTasks, newTask, setNewTask, mode, setMode, scroller, loadProject, loadMessages, newThread, send, saveBrief, addTask, toggleTask };
}
