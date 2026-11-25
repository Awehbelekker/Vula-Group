/**
 * Placeholder and example wording that fits the business (24 Nov 2026 audit: every business saw
 * one fish shop's and one architect's examples). Made-up names only — never a real client's or
 * person's.
 *
 *   const ex = examplesFor(businessType)
 *   <Input placeholder={ex.product} />
 */

const NEUTRAL = {
  product: 'e.g. Gift voucher R250',
  productName: 'Gift voucher',
  category: 'e.g. Best sellers',
  special: 'This week only: 10% off everything in store',
  specialTitle: '⭐ This week\'s specials',
  broadcastBody: 'Hi {{1}}! This week\'s specials are in. Reply ORDER to order or ask us anything.',
  broadcastDetails: 'e.g. gift vouchers R250, 10% off this weekend, free delivery over R500',
  discount: 'e.g. 20% off this weekend',
  businessName: 'e.g. Sunrise Trading',
  website: 'e.g. www.yourbusiness.co.za',
  shopUrl: 'e.g. yourshop.myshopify.com',
  project: 'e.g. Main Road office fit-out',
  client: 'e.g. Sizwe Holdings',
  person: 'e.g. Thandi Mokoena',
  signature: 'e.g. Thandi Mokoena, Director',
  pageBrief: 'e.g. a friendly local business with fast delivery',
  heroTitle: 'Welcome',
  featuredTitle: 'Featured',
}

const FOOD = {
  ...NEUTRAL,
  product: 'e.g. Fresh hake fillets 1kg',
  productName: 'Fresh hake fillets',
  category: 'e.g. Fresh fish',
  special: 'Fresh yellowtail and snoek in today',
  specialTitle: '🐟 This week\'s specials',
  broadcastBody: 'Hi {{1}}! Fresh stock is in this week. Reply ORDER to order or ask us anything.',
  broadcastDetails: 'e.g. yellowtail R180, snoek R95, free delivery over R500',
  discount: 'e.g. 20% off crayfish this weekend',
  pageBrief: 'e.g. a fresh food shop with same-day delivery',
  heroTitle: 'Fresh, local, delivered',
  featuredTitle: "Today's picks",
}

const TRADES = {
  ...NEUTRAL,
  product: 'e.g. Ceiling board 6.4mm',
  productName: 'Ceiling board',
  category: 'e.g. Materials',
  pageBrief: 'e.g. a reliable building contractor',
}

const BY_TYPE = { food: FOOD, trades: TRADES }

export function examplesFor(businessType) {
  return BY_TYPE[businessType] || NEUTRAL
}

export const isFood = (businessType) => businessType === 'food'
