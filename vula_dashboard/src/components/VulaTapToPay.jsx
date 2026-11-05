/**
 * VulaTapToPay.jsx — one-screen setup for Tap to Pay (NFC tag / QR → WhatsApp → PayFast → slip).
 * Four steps, top to bottom: connect PayFast → pick who gets paid + their share and get their tag →
 * run a R5 test payment → go live. Nothing here needs a developer: the server keeps the on/off
 * switch, and "Go live" stays locked until a real test payment has been confirmed.
 */
import TapToPayV2 from "./taptopay/TapToPayV2";

export default function VulaTapToPay({ ...props }) {
  return <TapToPayV2 {...props} />;
}

