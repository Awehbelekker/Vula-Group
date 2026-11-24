/**
 * OptionPhotos.jsx — a photo per colour (24 Nov 2026). For one option (Colour by default), each
 * value picks one of the product's own photos; the server puts it on every version with that
 * value (POST …/variants/photo, https only, read back). The shop chat then shows the red shirt
 * when the red one is added to the basket. Upload photos to the gallery first.
 */
import { useMemo, useState } from "react";
import { Select, toast } from "../ui/index.jsx";
import { api } from "../../lib/api";

const COLOUR = /^(colou?r|kleur|shade)$/i;

export default function OptionPhotos({ tenantId, productId, optionNames, variants, gallery, onSaved }) {
  const names = (optionNames || []).filter(Boolean);
  const [option, setOption] = useState(() => names.find((n) => COLOUR.test(n)) || names[0] || "");
  const [busy, setBusy] = useState("");

  const values = useMemo(() => {
    const out = new Map();
    for (const v of variants || []) {
      if (v.archived) continue;
      const ov = v.option_values || {};
      const key = Object.keys(ov).find((k) => k.toLowerCase() === option.toLowerCase());
      if (!key) continue;
      const val = String(ov[key]);
      const low = val.toLowerCase();
      if (!out.has(low)) out.set(low, { value: val, photos: new Set() });
      if (v.image_url) out.get(low).photos.add(v.image_url);
    }
    return [...out.values()];
  }, [variants, option]);

  if (!names.length || !values.length) return null;

  async function choose(value, url) {
    setBusy(value);
    try {
      const d = await api(`/v1/commerce/${tenantId}/admin/products/${productId}/variants/photo`, {
        method: "POST", body: { option, value, image_url: url || null },
        fallback: "That photo didn't save. Try again.",
      });
      toast(url ? `Photo set on ${d.updated} version${d.updated === 1 ? "" : "s"}.` : "Photo removed.");
      onSaved?.();
    } catch (e) {
      toast(e.message, "danger");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="vpp">
      <div className="vpp-head">
        <span className="vpp-title">Photo for each</span>
        {names.length > 1 ? (
          <Select aria-label="Which option gets its own photo" value={option} onChange={(e) => setOption(e.target.value)}>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </Select>
        ) : <strong>{option}</strong>}
      </div>
      {!gallery.length && <p className="vpp-hint">Add photos to the gallery above first, then pick one for each {option.toLowerCase()}.</p>}
      {gallery.length > 0 && values.map(({ value, photos }) => {
        const current = photos.size === 1 ? [...photos][0] : "";
        return (
          <div key={value} className="vpp-row">
            <span className="vpp-value">{value}{photos.size > 1 && <span className="vpp-hint"> (mixed)</span>}</span>
            <div className="vpp-pics" role="radiogroup" aria-label={`Photo for ${value}`}>
              <button type="button" role="radio" aria-checked={!current && photos.size === 0}
                className={`vpp-pic vpp-none${!current && photos.size === 0 ? " is-on" : ""}`}
                disabled={busy === value} onClick={() => choose(value, null)}>None</button>
              {gallery.map((u, i) => (
                <button key={u} type="button" role="radio" aria-checked={current === u}
                  aria-label={`Photo ${i + 1} for ${value}`} className={`vpp-pic${current === u ? " is-on" : ""}`}
                  disabled={busy === value} onClick={() => choose(value, u)}>
                  <img src={u} alt="" loading="lazy" />
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
