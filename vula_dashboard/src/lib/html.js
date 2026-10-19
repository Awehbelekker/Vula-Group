/**
 * html.js — escape text for HTML we build by hand (2026-10-19). The print windows (delivery run
 * sheet, packing slip, customer statement) open about:blank, which shares the dashboard's origin,
 * so anything a customer typed — a name, an address, a note — must go in as text, never markup.
 */
const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export const escapeHtml = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ENTITIES[ch]);
