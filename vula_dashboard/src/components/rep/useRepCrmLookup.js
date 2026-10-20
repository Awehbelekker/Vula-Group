/**
 * useRepCrmLookup.js — Dynamics 365 search and connection (shared by VulaRepCrmLookup /
 * VulaDynamics365Connect and RepCrmLookupV2). Moved verbatim out of those two files.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { VULA_API } from "../../lib/authFetch";

export const KINDS = [["contact", "Contacts"], ["account", "Accounts"], ["opportunity", "Opportunities"]];

export function useRepCrmLookup(tenantId) {
  const [connected, setConnected] = useState(null);
  const [kind, setKind] = useState("contact");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [error, setError] = useState("");

  const checkStatus = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/dynamics365/status/${tenantId}`);
    const d = await r.json().catch(() => ({}));
    setConnected(d.status === "connected");
  }, [tenantId]);
  useEffect(() => { checkStatus(); }, [checkStatus]);

  const search = async () => {
    setError("");
    const params = new URLSearchParams({ query, kind });
    const r = await fetch(`${VULA_API}/v1/dynamics365/${tenantId}/search?${params}`);
    const d = await r.json().catch(() => ({}));
    if (d.error) { setError(d.error); setResults([]); }
    else setResults(d.results || []);
  };

  return { connected, kind, setKind, query, setQuery, results, error, search };
}

export function useDynamics365Connect(tenantId) {
  const [status, setStatus] = useState(null);
  const [account, setAccount] = useState(null);
  const [orgUrl, setOrgUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const loadStatus = useCallback(async () => {
    if (!tenantId) return;
    try {
      const r = await fetch(`${VULA_API}/v1/dynamics365/status/${tenantId}`);
      const d = await r.json();
      setStatus(d.status); setAccount(d);
    } catch { setStatus("error"); }
  }, [tenantId]);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => {
    const onMsg = (e) => { if (e.data === "dynamics365-connected") loadStatus(); };
    const onFocus = () => loadStatus();
    window.addEventListener("message", onMsg); window.addEventListener("focus", onFocus);
    return () => { window.removeEventListener("message", onMsg); window.removeEventListener("focus", onFocus); if (pollRef.current) clearInterval(pollRef.current); };
  }, [loadStatus]);

  const handleConnect = useCallback(async () => {
    if (!orgUrl.trim()) { setError("Enter your Dynamics org URL first (e.g. https://yourorg.crm4.dynamics.com)."); return; }
    setLoading(true); setError(null);
    try {
      const r = await fetch(`${VULA_API}/v1/dynamics365/authorize-url?tenant_id=${encodeURIComponent(tenantId)}&org_url=${encodeURIComponent(orgUrl.trim())}`);
      const d = await r.json();
      if (!d.url) throw new Error(d.error || "Dynamics 365 not configured.");
      window.open(d.url, "d365-oauth", "width=520,height=680");
      let ticks = 0;
      pollRef.current = setInterval(() => { loadStatus(); if (++ticks > 30) clearInterval(pollRef.current); }, 3000);
    } catch (err) { setError(err.message); } finally { setLoading(false); }
  }, [tenantId, orgUrl, loadStatus]);

  return { status, account, orgUrl, setOrgUrl, loading, error, handleConnect, loadStatus };
}
