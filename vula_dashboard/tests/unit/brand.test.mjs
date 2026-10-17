// deriveBrand (theme/tokens.js): a tenant's colour → the redesign's brand tokens, contrast-checked
// (2026-10-17, UX redesign B2). Run: npm run test:unit
import { test } from "node:test";
import assert from "node:assert/strict";
import { deriveBrand, contrastRatio } from "../../src/theme/tokens.js";

const SURFACE = "#FFFFFF";
const SURFACE_DM = "#1B1E1B";
const CASES = {
  "pale yellow": "#FFF3A0",
  "mid grey": "#808080",
  "near-black": "#111111",
  "Vula green": "#1F5A45",
};

for (const [name, hex] of Object.entries(CASES)) {
  test(`${name}: brand reads on the card and on-brand reads on the brand, both themes`, () => {
    const { light, dark } = deriveBrand(hex);
    assert.ok(contrastRatio(light.brand, SURFACE) >= 4.5, `light brand ${light.brand} on surface`);
    assert.ok(contrastRatio(light.on, light.brand) >= 4.5, `light on-brand ${light.on} on ${light.brand}`);
    assert.ok(contrastRatio(dark.brand, SURFACE_DM) >= 4.5, `dark brand ${dark.brand} on surface`);
    assert.ok(contrastRatio(dark.on, dark.brand) >= 4.5, `dark on-brand ${dark.on} on ${dark.brand}`);
    for (const v of [light.brand, light.strong, light.soft, light.on, dark.brand, dark.strong, dark.soft, dark.on]) {
      assert.match(v, /^#[0-9a-f]{6}$/i);
    }
  });
}

test("a colour that already passes is left alone in light mode (Vula green)", () => {
  assert.equal(deriveBrand("#1F5A45").light.brand.toLowerCase(), "#1f5a45");
});

test("a pale colour is darkened for light mode, a near-black lightened for dark mode", () => {
  assert.notEqual(deriveBrand("#FFF3A0").light.brand.toLowerCase(), "#fff3a0");
  assert.notEqual(deriveBrand("#111111").dark.brand.toLowerCase(), "#111111");
});

test("the soft tint is a light wash in light mode and a dark one in dark mode", () => {
  const { light, dark } = deriveBrand("#1F5A45");
  assert.ok(contrastRatio(light.soft, SURFACE) < 1.3);
  assert.ok(contrastRatio(dark.soft, SURFACE_DM) < 1.3);
});
