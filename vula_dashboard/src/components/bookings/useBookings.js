/**
 * useBookings.js — appointments' data and actions (2026-10-18, UX redesign Phase F). Shared by
 * VulaBookings.jsx and bookings/BookingsV2.jsx; moved out unchanged — same endpoints
 * (/v1/bookings/{tenant}, migration 050), same bodies, times in Africa/Johannesburg.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { isOnSADay, todaySA } from "../../lib/dates";

export const DAYNAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; // ISO 1..7
export const today = () => todaySA();
export function shift(d, n) { const x = new Date(d + "T12:00:00"); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); }
export function mondayOf(d) {
  const x = new Date(d + "T12:00:00");
  const dow = x.getDay() || 7;   // Sun=0 → 7
  x.setDate(x.getDate() - (dow - 1));
  return x.toISOString().slice(0, 10);
}
export const timeOf = (iso) => new Date(iso).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Johannesburg" });
export const onDay = (b, d) => isOnSADay(b.start_at, d);

/** POST a booking into a free slot (f.start is the slot's label, e.g. "09:30"). */
export function bookSlot(tenantId, date, f, services) {
  const svc = services.find(s => s.id === f.service_id);
  return fetch(`${VULA_API}/v1/bookings/${tenantId}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      start: `${date}T${f.start}`, service_id: f.service_id || null,
      service_name: svc?.name, customer_name: f.customer_name,
      customer_phone: f.customer_phone, notes: f.notes, channel: "dashboard",
    }),
  }).then(r => r.json()).catch(() => ({ error: "network" }));
}
export function saveService(tenantId, body) {
  return fetch(`${VULA_API}/v1/bookings/${tenantId}/services`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(r => r.json()).catch(() => ({ error: "network" }));
}
export function deleteService(tenantId, id) {
  return fetch(`${VULA_API}/v1/bookings/${tenantId}/services/${id}`, { method: "DELETE" });
}
export function saveRules(tenantId, s) {
  return fetch(`${VULA_API}/v1/bookings/${tenantId}/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      slot_minutes: +s.slot_minutes, workdays: s.workdays, open_time: s.open_time,
      close_time: s.close_time, buffer_min: +s.buffer_min, lead_hours: +s.lead_hours,
      horizon_days: +s.horizon_days,
    }),
  }).then(r => r.json()).catch(() => ({}));
}

export function useBookings(tenantId) {
  const [view, setView] = useState("day");     // day | week | services | settings
  const [date, setDate] = useState(today());
  const [bookings, setBookings] = useState([]);
  const [weekBookings, setWeekBookings] = useState([]);
  const [services, setServices] = useState([]);
  const [avail, setAvail] = useState(null);
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const loadDay = useCallback(async () => {
    if (!tenantId) return;
    const from = `${date}T00:00:00+02:00`;
    const [b, a] = await Promise.all([
      fetch(`${VULA_API}/v1/bookings/${tenantId}?from=${encodeURIComponent(from)}`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/bookings/${tenantId}/availability?date=${date}`).then(r => r.json()).catch(() => ({})),
    ]);
    setBookings((b.bookings || []).filter(x => onDay(x, date)));
    setAvail(a);
  }, [tenantId, date]);

  const weekStart = mondayOf(date);
  const loadWeek = useCallback(async () => {
    if (!tenantId) return;
    const from = `${weekStart}T00:00:00+02:00`;
    const b = await fetch(`${VULA_API}/v1/bookings/${tenantId}?from=${encodeURIComponent(from)}`).then(r => r.json()).catch(() => ({}));
    const weekEnd = shift(weekStart, 7);
    setWeekBookings((b.bookings || []).filter(x => (x.start_at || "").slice(0, 10) < weekEnd));
  }, [tenantId, weekStart]);

  const loadServices = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/bookings/${tenantId}/services?all=true`).then(r => r.json()).catch(() => ({}));
    setServices(d.services || []);
  }, [tenantId]);

  const loadSettings = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/bookings/${tenantId}/settings`).then(r => r.json()).catch(() => ({}));
    setSettings(d.settings || null);
  }, [tenantId]);

  useEffect(() => { loadDay(); }, [loadDay]);
  useEffect(() => { if (view === "week") loadWeek(); }, [view, loadWeek]);
  useEffect(() => { loadServices(); }, [loadServices]);
  useEffect(() => { if (view === "settings") loadSettings(); }, [view, loadSettings]);

  const setStatus = async (id, status) => {
    await fetch(`${VULA_API}/v1/bookings/${tenantId}/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    loadDay();
  };

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3000); };

  return {
    view, setView, date, setDate, bookings, weekBookings, weekStart, services, avail, settings, setSettings,
    busy, setBusy, msg, flash, loadDay, loadServices, setStatus,
  };
}
