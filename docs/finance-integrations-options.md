# Finance integrations — bank feeds and Stub (write-ups, no build)

Capabilities 5 and 8 of the finance back-office brief (6 Oct 2026). Both are research only;
nothing here is built, and no provider integration starts without Ian's approval.

## 5. Bank feed and auto-categorisation

### What Vula already has (built and wired)
| Piece | Where |
|---|---|
| FNB statement PDF — deterministic parser, LLM fallback for other banks | `vula/ingestion/fnb_statement.py`, bank-statement upload |
| Statement spreadsheet import (xlsx/csv, categories → accounts, projects, trades) | `vula/commerce/statement_sheet.py` |
| Auto-categorisation: learned rules first, AI second, owner-confirmed rules taught back | `vula/commerce/accounting.py` (`learn_category_rule`, `categorize_batch`) |
| Matching to invoices, supplier bills, POPs and orders; WhatsApp review of the unsure ones | `vula/commerce/bank_rec.py`, `vula/commerce/bank_review.py` |
| Every matched line posts to the ledger | `vula/commerce/ledger.py` |

So the "CSV import with auto-categorisation and invoice matching" step the brief calls safe is
already done. What's missing is a **live feed**: today a statement arrives when someone uploads or
emails it.

### Options for a live feed
| Option | What it gives | Trade-offs |
|---|---|---|
| **Stitch** (Financial Data API) | Account, balance and transaction data from the major SA banks through one GraphQL API, with the customer's consent; the same company also does Pay-by-Bank and payouts | Commercial contract and per-call/connection pricing (not public); the business owner has to link each account; consent expires and must be renewed; one more processor of POPIA personal information to list. Best coverage of the realistic options. |
| **Bank's own statement-sharing API (e.g. Capitec's secure statement sharing)** | Statements pushed from the bank with the customer's consent | One bank at a time; partner onboarding with each bank; not every bank offers one. Stub uses Capitec's — a signal it's obtainable for an SME product. |
| **Investec programmable banking** | Real-time transactions via an open API | Only Investec business clients — none of the current tenants |
| **Yoco** | Card settlements already reach Vula through the Yoco connection | Card takings only — not a bank feed |
| **Ozow / PayFast / Peach** | Payment collection | Not account data at all |
| **Scheduled email forwarding of statements** (bank → Vula mailbox) | Near-live with no contract — most SA banks can email a statement on a schedule, and Vula's mailbox sync already files and parses them | Daily/weekly, not real-time; depends on the bank's PDF layout (FNB parsed deterministically; others via the LLM fallback with its checks) |

### Recommendation
1. **Now, no contract:** set up the bank's scheduled statement email into the tenant's connected
   mailbox. It reuses everything above; the only work is a short guide per bank and, as samples
   arrive, a deterministic parser per bank layout (like FNB's).
2. **When volume justifies it:** a Stitch pilot for one tenant, behind a flag, after checking
   price, consent renewal and POPIA terms. Approval needed before any build.

## 8. Stub interoperability (research only)

**What Stub is.** A South African accounting and invoicing app for micro-entrepreneurs and
freelancers (stub.africa). It does invoices, quotes, credit notes, customer statements and
recurring invoices, takes online payments (4.5% + R2 per SA card on the base tier), and syncs
bank accounts on Pro (R189 a month at the time of writing). It has a Capitec statement-sharing
integration.

**Its API — "stub Connect"** (developers.stub.africa), as described in its public docs:
- REST; separate test (`test.connect.stub.africa`) and production (`connect.stub.africa`) hosts.
- Short-lived authentication tokens (one hour), fetched server-side.
- **Push** (`POST /api/push/sale` and similar): send sales, income and expenses *into* a
  business's Stub account.
- **Pull** — businesses linked to an email, insights, expenses and income — is **asynchronous**:
  results are delivered to a webhook URL you supply, and the webhook is mandatory.
- An embedded-accounting widget.

**Not confirmed from here.** This network can't reach Stub's site, and the public summaries found
don't mention these, so each needs checking with Stub before any decision:
- an MCP server;
- a plan restriction on the API;
- invoice-level pull — the docs list *income*, not invoices with line items and numbers.

**Could Vula read invoices from Stub for a tenant already on it?** Probably partly:
- **Likely feasible:** pulling a tenant's income/sales into Vula through a partner integration.
  Each pull arrives at a Vula webhook and maps to `commerce_invoices` as outbound,
  `source='stub'`, read-only on Vula's side, matched by number to avoid duplicates. The pull
  needs a tenant-scoped, signature-checked webhook endpoint, and the Stub tokens stored
  Fernet-encrypted like other gateway credentials.
- **Unclear:** whether invoice detail (customer, line items, VAT, due date, status) comes through,
  or only income totals. Without line items and status, Vula's debtors, reminders and VAT views
  can't run from Stub data.
- **Fits Stub's design better:** the other direction. Stub Connect is mainly push, so Vula could
  push the sales it records into the tenant's Stub books for a tenant who keeps Stub as their
  accounting system.

**Questions for Stub before anything is built**
1. Is there a partner programme, and what does it cost?
2. Can a partner pull invoices with line items, statuses and payments, or only income?
3. Is there an MCP server, and does it need a paid plan?
4. Webhook signing and retry rules.
5. POPIA: what Stub, as operator, allows a partner to do with its customers' data.

## Sources
- [stub — Introduction (developers.stub.africa)](https://developers.stub.africa/)
- [stub — Pull data](https://developers.stub.africa/api-documentation/pull-data)
- [stub — Push data](https://developers.stub.africa/api-documentation/push-data)
- [stub — Authentication tokens](https://developers.stub.africa/api-documentation/authentication-tokens)
- [stub — Pricing](https://stub.africa/pricing)
- [Accounting software made for entrepreneurs (SME South Africa)](https://smesouthafrica.co.za/accounting-software-made-for-entrepreneurs/)
- [Stitch — APIs (apis.io)](https://apis.io/providers/stitch-money/)
- [Stitch Direct Deposit (TechCabal)](https://techcabal.com/2022/11/23/stitch-direct-deposit/)
