/**
 * layout.js — page padding that gives way on a phone (mobile pass, 24 Oct 2026).
 *
 * A screen's own side padding stacks on the shell's (the redesign pads its content 16px; the old
 * design's tenant wrapper 12px on a phone), which cost a 360px phone up to 80px of width. sidePad
 * keeps the full value from about 770px wide up, so computers and tablets look exactly as before,
 * and shrinks to nothing on a phone, where the shell's padding is the page gutter.
 */
export const sidePad = (max = 24) => `clamp(0px, 6vw - 22px, ${max}px)`;
