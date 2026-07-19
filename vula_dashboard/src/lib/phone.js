/**
 * lib/phone.js — turns a customer-entered phone number into a WhatsApp-linkable international
 * number, without assuming South Africa. The old pattern (`.replace(/^0/, '27')`) rewrote ANY
 * leading 0 to South Africa's country code, silently mangling a non-SA number (a UK "07..."
 * becomes a fake "+2707...").
 *
 * Heuristic: only apply the local-SA-number assumption when the digits look like a bare SA local
 * number (leading 0, 10 digits total — e.g. 082 123 4567). Anything else (already has a country
 * code, or just doesn't match that shape) is passed through untouched, on the assumption the
 * number was already captured in international form.
 */
export function toWhatsAppNumber(phone) {
  const digits = (phone || '').replace(/[^\d]/g, '');
  if (digits.length === 10 && digits.startsWith('0')) {
    return '27' + digits.slice(1);
  }
  return digits;
}

export function whatsAppLink(phone) {
  return `https://wa.me/${toWhatsAppNumber(phone)}`;
}
