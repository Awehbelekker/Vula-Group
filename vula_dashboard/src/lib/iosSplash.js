/**
 * iosSplash.js — the iPhone's launch screen for the installed app (2026-10-27, PWA pass 6). iOS
 * shows a startup image while a home-screen web app opens (a blank white screen without one), and
 * only one made for that exact screen: so one link per iPhone size, light and dark, each drawn by
 * the API with the business's icon and name (vula_mind brand_icon.render_splash). iOS reads these
 * when Vula is added to the home screen; other browsers ignore them.
 */
// CSS width × height @ pixel ratio, portrait — must match brand_icon.SPLASH_SIZES.
export const IPHONES = [
  [440, 956, 3], [430, 932, 3], [402, 874, 3], [393, 852, 3], [428, 926, 3], [390, 844, 3],
  [375, 812, 3], [414, 896, 3], [414, 896, 2], [414, 736, 3], [375, 667, 2], [320, 568, 2],
];

export function setLaunchScreens(api, tenantId) {
  document.querySelectorAll('link[rel="apple-touch-startup-image"]').forEach((l) => l.remove());
  if (!tenantId) return;
  for (const [w, h, r] of IPHONES) {
    for (const dark of [false, true]) {
      const l = document.createElement("link");
      l.rel = "apple-touch-startup-image";
      l.media = `(device-width: ${w}px) and (device-height: ${h}px) and (-webkit-device-pixel-ratio: ${r}) `
        + `and (orientation: portrait) and (prefers-color-scheme: ${dark ? "dark" : "light"})`;
      l.href = `${api}/v1/commerce/${tenantId}/app-splash/${w * r}x${h * r}.png${dark ? "?dark=true" : ""}`;
      document.head.appendChild(l);
    }
  }
}
