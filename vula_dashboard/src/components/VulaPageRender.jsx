/**
 * VulaPageRender.jsx — public renderer for a tenant's Puck page. Mounted (pre-auth) on the hash
 * route #/page/:tenant/:slug, so EVERY tenant's published pages are viewable at a Vula URL even if
 * their storefront doesn't ship a renderer. Uses the same shared config as the editor → no drift.
 */
import { useState, useEffect } from "react";
import { Render } from "@measured/puck";
import "@measured/puck/puck.css";
import { config } from "../puck/config";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const centre = { padding: 48, fontFamily: "system-ui", textAlign: "center", color: "#666" };

export default function VulaPageRender({ tenant, slug }) {
  const [page, setPage] = useState(undefined);   // undefined = loading, null = not found

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenant}/pages/${slug}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setPage).catch(() => setPage(null));
  }, [tenant, slug]);

  if (page === undefined) return <div style={centre}>Loading…</div>;
  if (page === null) return <div style={centre}>Page not found.</div>;

  const d = page.puck_data || {};
  const data = { content: Array.isArray(d.content) ? d.content : [], root: { props: (d.root && (d.root.props || d.root)) || {} } };
  return (
    <div style={{ minHeight: "100vh", background: "#fff" }}>
      <Render config={config} data={data} />
    </div>
  );
}
