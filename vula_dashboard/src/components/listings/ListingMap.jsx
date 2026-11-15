/**
 * ListingMap.jsx — maps for the Listings screen (2026-11-10). Same approach as the delivery-area
 * map in settings/useSettings.js: Leaflet loaded lazily on first use, OpenStreetMap tiles, and
 * the address lookup through Vula's own /admin/geo/geocode endpoint.
 *
 *   <ListingsMap>  every listing with a location, as pins; a pin opens that listing.
 *   <PinPicker>    in the listing form: click the map (or "Find from address") to set the pin.
 */
import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { formatMoney } from "../../lib/money";
import { Button } from "../ui/index.jsx";

const CAPE_TOWN = { lat: -33.93, lng: 18.47 };
const hasPin = (p) => p.latitude != null && p.longitude != null;

function useLeaflet(ref, init) {
  const mapRef = useRef(null);
  useEffect(() => {
    let dead = false;
    import("leaflet").then(async (Lmod) => {
      if (dead || !ref.current || mapRef.current) return;
      await import("leaflet/dist/leaflet.css");
      const L = Lmod.default || Lmod;
      const map = L.map(ref.current, { scrollWheelZoom: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors", maxZoom: 19,
      }).addTo(map);
      mapRef.current = { map, L, layers: [] };
      init(mapRef.current);
    }).catch(() => {});
    return () => { dead = true; if (mapRef.current) { mapRef.current.map.remove(); mapRef.current = null; } };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  return mapRef;
}

const pinStyle = (status) => ({
  radius: 9, weight: 2, color: "#ffffff", // brand-colour: white ring so pins read on any tile
  fillColor: status === "under_offer" ? "var(--warn)" : status === "sold" || status === "let" ? "var(--muted)" : "var(--accent)",
  fillOpacity: 1,
});

export function ListingsMap({ listings, onOpen }) {
  const div = useRef(null);
  const [ready, setReady] = useState(false);
  const m = useLeaflet(div, () => setReady(true));
  const pinned = listings.filter(hasPin);

  useEffect(() => {
    const s = m.current;
    if (!ready || !s) return;
    s.layers.forEach((l) => l.remove());
    s.layers = pinned.map((p) => {
      const price = p.price_on_request || p.price_cents == null ? "Price on request"
        : formatMoney(p.price_cents, { cents: "never" }) + (p.listing_type === "rent" ? " pm" : "");
      const marker = s.L.circleMarker([p.latitude, p.longitude], pinStyle(p.status)).addTo(s.map);
      marker.bindTooltip(`${p.title} · ${price}`, { direction: "top", offset: [0, -8] });
      marker.on("click", () => onOpen(p));
      return marker;
    });
    if (pinned.length === 1) s.map.setView([pinned[0].latitude, pinned[0].longitude], 14);
    else if (pinned.length > 1) s.map.fitBounds(s.L.latLngBounds(pinned.map((p) => [p.latitude, p.longitude])), { padding: [40, 40], maxZoom: 15 });
    else s.map.setView([CAPE_TOWN.lat, CAPE_TOWN.lng], 10);
  }, [ready, listings]);  // eslint-disable-line react-hooks/exhaustive-deps

  const missing = listings.length - pinned.length;
  return (
    <div className="vls-mapwrap">
      <div ref={div} className="vls-map" role="region" aria-label="Map of listings" />
      <p className="vls-muted vls-map-note">
        {pinned.length === 0 ? "No listings have a location yet — open one and place its pin."
          : missing > 0 ? `${missing} listing${missing === 1 ? " has" : "s have"} no location yet — open ${missing === 1 ? "it" : "them"} to place a pin.`
          : "Tap a pin to open the listing."}
      </p>
    </div>
  );
}

export function PinPicker({ tenantId, value, onChange, addressText }) {
  const div = useRef(null);
  const [msg, setMsg] = useState("");
  const [finding, setFinding] = useState(false);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const m = useLeaflet(div, (s) => {
    const start = value || CAPE_TOWN;
    s.map.setView([start.lat, start.lng], value ? 15 : 10);
    s.marker = value ? s.L.circleMarker([value.lat, value.lng], pinStyle("active")).addTo(s.map) : null;
    s.map.on("click", (e) => onChangeRef.current({ lat: e.latlng.lat, lng: e.latlng.lng }));
  });

  useEffect(() => {
    const s = m.current;
    if (!s) return;
    if (!value) { if (s.marker) { s.marker.remove(); s.marker = null; } return; }
    if (s.marker) s.marker.setLatLng([value.lat, value.lng]);
    else s.marker = s.L.circleMarker([value.lat, value.lng], pinStyle("active")).addTo(s.map);
  }, [value]);  // eslint-disable-line react-hooks/exhaustive-deps

  const find = async () => {
    if (!addressText.trim()) { setMsg("Add the address or suburb first."); return; }
    setFinding(true); setMsg("");
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/geo/geocode`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: addressText }),
      });
      const d = await r.json();
      if (d.lat != null) { onChange({ lat: d.lat, lng: d.lng }); m.current?.map.setView([d.lat, d.lng], 15); setMsg("Found — drag the map and click to fine-tune."); }
      else setMsg(d.detail || "Not found — try street, suburb and town, or click the map.");
    } catch { setMsg("Couldn't search just now — click the map instead."); }
    setFinding(false);
  };

  return (
    <div className="vls-pick">
      <div ref={div} className="vls-map vls-map--small" role="region" aria-label="Click to place the listing's pin" />
      <div className="vls-row">
        <Button size="sm" variant="secondary" Icon={MapPin} onClick={find} disabled={finding}>{finding ? "Finding…" : "Find from address"}</Button>
        {value && <Button size="sm" variant="secondary" onClick={() => onChange(null)}>Remove pin</Button>}
        <span className="vls-muted">{msg || (value ? "Click the map to move the pin." : "Click the map to place the pin.")}</span>
      </div>
    </div>
  );
}
