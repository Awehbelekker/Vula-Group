/**
 * options.js — sizes and colours as typed (24 Nov 2026), the pure helpers shared by the Add form
 * (loaded with the app) and OptionsBuilder / VariantsEditor (loaded when opened).
 */
export const MAX_OPTIONS = 3;
export const MAX_COMBOS = 100;

export function splitValues(text) {
  const seen = new Set();
  const out = [];
  for (const raw of String(text || "").split(",")) {
    const v = raw.trim().replace(/\s+/g, " ");
    if (v && !seen.has(v.toLowerCase())) { seen.add(v.toLowerCase()); out.push(v); }
  }
  return out;
}

/** [{name, values: "a, b"}] → [{name, values: [..]}] with empty rows dropped. */
export function toOptions(rows) {
  return (rows || [])
    .map((r) => ({ name: String(r.name || "").trim(), values: splitValues(r.values) }))
    .filter((r) => r.name || r.values.length);
}

export function countVersions(rows) {
  const opts = toOptions(rows).filter((o) => o.values.length);
  return opts.length ? opts.reduce((n, o) => n * o.values.length, 1) : 0;
}

/** Existing variants' values grouped under the product's option names, for editing. */
export function rowsFromVariants(names, variants) {
  const list = (names && names.length) ? names : [];
  return list.map((name) => {
    const vals = [];
    for (const v of variants || []) {
      if (v.archived) continue;
      const ov = v.option_values || {};
      const key = Object.keys(ov).find((k) => k.toLowerCase() === name.toLowerCase());
      const val = key ? String(ov[key]) : "";
      if (val && !vals.some((x) => x.toLowerCase() === val.toLowerCase())) vals.push(val);
    }
    return { name, values: vals.join(", ") };
  });
}

