/**
 * OptionsBuilder.jsx — sizes and colours for a product, typed once (24 Nov 2026 audit: the screen
 * added one variant at a time; 4 colours × 5 sizes was 20 rows typed by hand).
 *
 * Each option is a name ("Colour") and its values, typed comma-separated ("Red, Blue, Green").
 * The preview counts the versions this makes; the server makes them
 * (POST …/variants/generate, vula/commerce/product_variants.py) and refuses too many in words.
 * Used by the Add form (ProductsV2) and by the editor (VariantsEditor).
 */
import { Plus, X } from "lucide-react";
import { Button, IconButton, Input } from "../ui/index.jsx";

import { MAX_OPTIONS, MAX_COMBOS, countVersions, toOptions } from "./options";

export { MAX_OPTIONS, MAX_COMBOS, countVersions, toOptions };

const SUGGEST = ["Size", "Colour", "Material"];

export default function OptionsBuilder({ rows, onChange, disabled }) {
  const list = rows && rows.length ? rows : [{ name: "", values: "" }];
  const set = (i, k, v) => onChange(list.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const used = new Set(list.map((r) => (r.name || "").toLowerCase()));
  const next = SUGGEST.find((s) => !used.has(s.toLowerCase())) || "";
  const n = countVersions(list);
  const opts = toOptions(list).filter((o) => o.name && o.values.length);
  const sample = n && n <= 6
    ? opts.reduce((acc, o) => acc.flatMap((a) => o.values.map((v) => [...a, v])), [[]]).map((c) => c.join(" / "))
    : [];
  return (
    <div className="vpo">
      {list.map((r, i) => (
        <div key={i} className="vpo-row">
          <Input aria-label={`Option ${i + 1} name`} placeholder={i === 0 ? "Size" : (SUGGEST[i] || "Option")}
            value={r.name} disabled={disabled} onChange={(e) => set(i, "name", e.target.value)} className="vpo-name" />
          <Input aria-label={`${r.name || `Option ${i + 1}`} values (comma separated)`}
            placeholder={i === 0 ? "S, M, L, XL" : "Red, Blue, Green"} value={r.values} disabled={disabled}
            onChange={(e) => set(i, "values", e.target.value)} className="vpo-values" />
          <IconButton Icon={X} label={`Remove ${r.name || "this option"}`} disabled={disabled}
            onClick={() => onChange(list.filter((_, j) => j !== i))} />
        </div>
      ))}
      <div className="vpo-foot">
        {list.length < MAX_OPTIONS && (
          <Button type="button" size="sm" variant="secondary" Icon={Plus} disabled={disabled}
            onClick={() => onChange([...list, { name: next, values: "" }])}>Add an option</Button>
        )}
        <span className={`vpo-count${n > MAX_COMBOS ? " vpo-count--over" : ""}`} role="status">
          {n === 0 ? "Type the values separated by commas."
            : n > MAX_COMBOS ? `That makes ${n} versions. ${MAX_COMBOS} is the most one product can have.`
            : `This makes ${n} version${n === 1 ? "" : "s"}${sample.length ? `: ${sample.join(", ")}` : ""}.`}
        </span>
      </div>
    </div>
  );
}
