# Off the Hook — WhatsApp Broadcast
# Week of 30 May 2026 — Fresh Fish Specials

## SEND TO: All opted-in customers (WhatsApp broadcast list)
## NUMBER: +27 73 781 5979
## TIME: Monday 7:00am SAST

---

## Message (copy exactly — WhatsApp approved template format)

🐟 *This week at Off the Hook!*

Fresh fish has landed — here's what's available this week:

*FRESH FISH THIS WEEK* (per kg)
• Yellowfin Tuna Steaks — *R290/kg*
• Hake Fillets — *R160/kg*
• Hake Centre Cuts — *R220/kg*
• Kingklip Fillets — *R240/kg*
• Kingklip Centre Cuts (skinless) — *R290/kg*
• Jacopever Fillets — *R75/kg*
• Atlantic Mackerel Fillets — *R80/kg*
• Whole Octopus (unclean) — *R95/kg*

🐓 *Pasture-raised chicken also available*
GMO-free · No hormones · No brine
Fresh & frozen options in stock.

🦐 *Frozen seafood — always in stock*
Calamari, Prawns, Mussels, Salmon & more.

📦 All items sold by weight.
🚚 Cape Town delivery available.

_Reply or WhatsApp to order: 073 781 5979_
_E-mail: info@offthehook.capetown_

---

## n8n Workflow Trigger (automated version)

Send this via the n8n "Weekly Specials Broadcast" workflow:
- Workflow ID: oth-05
- Trigger: Cron — Monday 07:00 SAST
- Template: weekly_specials
- Parameters:
  - {{1}} = the fish list text above
  - {{2}} = https://offthehook.co.za/shop?category=fresh_fish

---

## Quick-send WhatsApp link (for manual one-tap sending)

https://wa.me/27737815979?text=Hi%2C+I%27d+like+to+order+from+this+week%27s+fresh+fish+list

---

## Notes for next week

- Update this file every Monday with the week's available fish
- The is_weekly_special flag in Supabase should be updated to reflect current stock
- Jacopever and Mackerel are budget-friendly options — always highlight the price
- Kingklip Centre Cuts are premium — lead with it for high-value orders
