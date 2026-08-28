"""
vula/training/business_content.py

Shared general South African small-business operations knowledge base.
Ingested into tenant_id="business_basics" (collection vula_business_basics) — analogous to
vula/training/content.py's construction/QS corpus, but scoped to general SME operations that
apply across every vertical (food, retail, trades, health, services), not just construction.

Kept as a SEPARATE collection from vula_training (not merged in) so architecture_planning.py/
standards_lookup.py's construction-only retrieval stays undiluted, and this corpus stays free
of construction-specific noise for a retail/food/health tenant's general question.
core/skills/commerce_admin.py's lookup_business_info tool falls back here when a tenant's own
KB has nothing relevant.

Covers: VAT/tax basics, bookkeeping, invoicing/debt collection, pricing, BCEA/HR basics, CCMA
process, POPIA basics, customer service, marketing fundamentals, business planning, business
registration. Deliberately NOT encyclopedic — ~13 focused documents matching the construction
corpus's scale, covering the highest-value general SME topics, not attempting to be a full
legal/tax reference. Real, specific SA facts throughout (rates, thresholds, deadlines) rather
than generic filler — same discipline as the construction corpus.
"""
from __future__ import annotations

from typing import List

from vula.training.content import TrainingDocument

BUSINESS_TRAINING_TENANT_ID = "business_basics"

BUSINESS_TRAINING_DOCUMENTS: List[TrainingDocument] = [

    # ── Tax ──────────────────────────────────────────────────────────────────
    TrainingDocument(
        filename="vat_basics.md",
        topic="VAT basics for SA small businesses",
        content="""# VAT Basics for South African Small Businesses

## Do I need to register?
VAT registration is COMPULSORY once your taxable turnover exceeds R1 million in any consecutive
12-month period. You may register VOLUNTARILY once turnover exceeds R50,000 in the past 12
months — many small businesses do this to reclaim input VAT on setup costs, even before it's
required.

## The standard rate
South Africa's standard VAT rate is 15%. A small number of items are zero-rated (0%) — basic
foodstuffs like maize meal, brown bread, milk, and vegetables, plus exports. A very short list
of goods/services is VAT-exempt entirely (e.g. residential rental, certain financial services)
— exempt is different from zero-rated: an exempt business can't claim input VAT back.

## Output VAT vs input VAT
- Output VAT: the 15% you charge customers on sales — you collect it on SARS's behalf.
- Input VAT: the 15% you paid on business purchases/expenses — you can claim this back.
- What you actually pay SARS = output VAT collected minus input VAT you can claim.
Keep every tax invoice you receive — SARS requires a valid tax invoice (supplier's VAT number,
date, description, VAT amount shown separately) to claim input VAT on it.

## Filing periods (VAT201 return)
Most small businesses file 2-monthly (Category A or B, alternating odd/even months). Businesses
with turnover under R1.5m may qualify for 6-monthly or annual categories in specific cases.
Returns and payment are due by the 25th of the month after the period ends (25th of the last
business day if filed via eFiling and paying electronically — check current SARS deadlines, they
shift slightly year to year).

## Common small-business VAT mistakes
- Charging VAT before you're actually registered (illegal — you need a VAT number first).
- Not issuing a proper tax invoice (missing VAT number, missing separate VAT amount).
- Forgetting input VAT can only be claimed with a valid tax invoice in hand — not just a
  receipt or statement.
""",
    ),
    TrainingDocument(
        filename="sars_tax_basics.md",
        topic="Income tax & provisional tax basics",
        content="""# Income Tax & Provisional Tax Basics for SA Small Businesses

## Sole proprietor vs company tax
A sole proprietor's business income is taxed as PERSONAL income, at individual marginal rates
(18%–45%, on a sliding scale). A registered company (Pty Ltd) pays a flat 27% corporate income
tax rate on profit, separate from the owner's personal tax — the owner is then taxed again
personally on any salary or dividend drawn from the company.

## Provisional tax
Anyone who earns income other than a standard salary (this includes almost every small-business
owner) is a PROVISIONAL TAXPAYER — you pay tax in advance, twice a year, rather than waiting for
one annual bill:
- 1st period: end of August — estimate half your year's tax liability.
- 2nd period: end of February — top up to your full estimated liability for the tax year.
- 3rd/"top-up" period (optional): end of September the following year — settle any shortfall
  voluntarily to avoid interest.
Underestimating your provisional tax significantly can trigger a SARS penalty — better to
estimate slightly high than badly low.

## Turnover Tax (an alternative for very small businesses)
Sole proprietors, partnerships, and companies with turnover under R1 million/year MAY elect
Turnover Tax instead of normal income tax + VAT — a simplified single tax calculated directly
on turnover (not profit), on a sliding scale starting at 0% up to a max around 3%. Trade-off:
you generally can't claim expense deductions or input VAT under this system, so it usually
suits a business with genuinely low expenses relative to revenue.

## Key deadlines to know
- Provisional tax: end of August, end of February.
- Annual income tax return (individuals/provisional taxpayers): typically January/February the
  following year via eFiling (SARS publishes the exact date each season).
- PAYE/UIF/SDL (if you employ staff): monthly, by the 7th of the following month via EMP201.
Always confirm exact current-year dates on the SARS website or with a real accountant/tax
practitioner — deadlines shift slightly year to year and this is general guidance, not
personalised tax advice.
""",
    ),

    # ── Bookkeeping & invoicing ──────────────────────────────────────────────
    TrainingDocument(
        filename="bookkeeping_basics.md",
        topic="Basic bookkeeping practices",
        content="""# Basic Bookkeeping Practices for a Small Business

## Cash basis vs accrual basis
- Cash basis: record income when you actually receive the money, expenses when you actually pay
  them. Simple, common for very small/sole-proprietor businesses.
- Accrual basis: record income when EARNED (invoice issued) and expenses when INCURRED
  (bill received), regardless of when cash actually moves. Required for VAT-registered
  businesses on the invoice basis, and gives a truer picture of profitability.

## Separate your business and personal finances
Open a dedicated business bank account from day one, even as a sole proprietor with no legal
requirement to. Mixing personal and business transactions is the single most common reason a
small business's books become unreconcilable, and it makes proving business expenses to SARS
much harder.

## What records SARS actually requires you to keep
- All sales/tax invoices issued.
- All purchase invoices/receipts for expenses claimed.
- Bank statements.
- A record of all VAT charged and claimed, if VAT-registered.
Keep records for at least 5 YEARS — SARS can request them for audit within that window.

## Reconciling your bank account
Reconcile (match your bookkeeping records against your actual bank statement) at least monthly.
This is the single fastest way to catch a missed invoice, a double payment, a bank fee you
didn't record, or a fraudulent transaction — the longer you leave it, the harder it is to
untangle.

## The three numbers every owner should check monthly
1. Cash in the bank right now.
2. Money owed TO you (accounts receivable) — and how overdue it is.
3. Money you owe (accounts payable) — and what's due soon.
A business can be "profitable on paper" and still run out of cash if these three aren't
watched.
""",
    ),
    TrainingDocument(
        filename="invoicing_and_debt_collection.md",
        topic="Invoicing requirements & chasing late payment",
        content="""# Invoicing Requirements & Chasing Late Payment

## What a valid SA tax invoice must show (if VAT-registered)
- The words "Tax Invoice".
- Supplier's name, address, and VAT registration number.
- Invoice date and a unique sequential invoice number.
- Description, quantity, and price of goods/services.
- The VAT amount, shown SEPARATELY from the price (not just included silently).
- For invoices over R5,000: the customer's name and address too.
A non-VAT-registered business can still issue a normal invoice — just without VAT fields, and
must never charge or show VAT it isn't registered to collect.

## Setting clear payment terms
State payment terms on every invoice explicitly (e.g. "Payment due within 7 days", "50% deposit,
balance on delivery"). Ambiguous or absent terms are the single biggest reason small businesses
struggle to collect on time — customers default to whatever's easiest for them when nothing was
agreed.

## Chasing late payment — an escalation ladder
1. A friendly reminder a few days before/on the due date.
2. A firmer follow-up once overdue (restate the amount, the original due date, ask for a
   payment date).
3. A final notice/letter of demand — states the amount, gives a firm deadline (commonly 7-10
   days), and states the next step if unpaid (e.g. handing over for collection, legal action).
4. Small Claims Court (claims up to R20,000, no lawyer needed, low-cost, relatively fast) or a
   debt collector/attorney's letter of demand for larger amounts.

## Reducing late payment in the first place
- Require a deposit upfront for larger or custom orders.
- Invoice IMMEDIATELY on delivery/completion, not days later — momentum matters.
- Offer more than one easy payment method (EFT, card, instant payment link).
- For repeat late-payers, consider requiring payment upfront on future orders.
""",
    ),
    TrainingDocument(
        filename="pricing_and_margins.md",
        topic="Pricing, markup vs margin",
        content="""# Pricing: Markup vs Margin, and Common Mistakes

## Markup vs margin — the difference that trips up most owners
- MARKUP = the amount you add to your cost, expressed as a % of COST.
  Example: cost R100, markup 50% → sell price = R150.
- MARGIN = your profit expressed as a % of the SELLING PRICE.
  Example: sell price R150, cost R100, profit R50 → margin = R50/R150 = 33.3%.
The same numbers give a 50% markup but only a 33.3% margin — this confusion is the single most
common small-business pricing mistake, and it usually means owners underestimate how much they
actually need to charge to hit a target profit margin.

## Formula to hit a target margin (not markup)
To achieve a target margin M (as a decimal, e.g. 0.30 for 30%):
   Selling price = Cost ÷ (1 − M)
Example: cost R100, target margin 30% → price = R100 ÷ 0.70 = R142.86 (NOT R130, which is what
a naive "add 30%" markup would wrongly give you).

## What to actually include in "cost" before pricing
Many small businesses only price against the direct product/material cost and forget:
- Packaging and delivery.
- Payment gateway/card fees (often 2-3.5% of the transaction).
- Wastage/spoilage (especially food).
- A fair allocation of overheads (rent, staff time, electricity) — not just the item itself.
- VAT, if registered — the price customers see needs to cover it, it isn't "extra" income.

## Common underpricing mistakes
- Copying a competitor's price without knowing their cost structure or margin.
- Never revisiting prices as supplier costs rise (margin quietly erodes over time).
- Pricing a custom/one-off job the same as a standard product, ignoring the extra time/risk.
- Discounting habitually "to be nice" without tracking the cumulative margin impact.
""",
    ),

    # ── Labour law & HR ──────────────────────────────────────────────────────
    TrainingDocument(
        filename="bcea_basics.md",
        topic="Basic Conditions of Employment Act essentials",
        content="""# BCEA Basics — Employee Entitlements Every SA Employer Must Know

## Who it covers
The Basic Conditions of Employment Act (BCEA) sets minimum standards for almost all employees in
South Africa, including part-time and casual workers, with only narrow exclusions. As soon as
you have even one employee, these minimums apply — they can't be contracted away to something
worse.

## Working hours
- Maximum ordinary hours: 45 hours/week (9 hours/day for a 5-day week, 8 hours/day for a 6-day
  week).
- Overtime: max 10 hours/week, paid at 1.5× the normal rate (or 2× for Sunday/public holiday
  work, unless the employee normally works Sundays, in which case it's 1.5×).
- A meal break of at least 60 minutes after 5 continuous hours of work (may be reduced to 30 min
  by agreement).

## Leave entitlements
- Annual leave: minimum 21 consecutive days (or 1 day per 17 days worked) per annual cycle, paid.
- Sick leave: 30 days paid sick leave over a 3-year cycle (in the first 6 months, it's 1 day per
  26 days worked).
- Family responsibility leave: 3 days paid per year (for employees who've worked 4+ months, at
  least 4 days/week) — for the birth/illness of a child, or death of close family.
- Maternity leave: 4 consecutive months, unpaid by the employer (claimable from UIF).

## Termination and notice
Minimum notice periods scale with length of service:
- Less than 6 months employed: 1 week's notice.
- 6 months to 1 year: 2 weeks' notice.
- 1 year or more: 4 weeks' notice.
Notice must be in writing (unless the employee can't read). Dismissal must be both procedurally
AND substantively fair — see ccma_process.md for what happens if it isn't.

## Payslips and record-keeping
Employers must issue a written payslip every payday, showing at minimum: employer/employee
details, pay period, hours worked, gross pay, all deductions itemised, and net pay.
""",
    ),
    TrainingDocument(
        filename="ccma_process.md",
        topic="CCMA dispute process",
        content="""# The CCMA Process — What Happens If a Dismissal or Labour Dispute Is Disputed

## What the CCMA is
The Commission for Conciliation, Mediation and Arbitration (CCMA) is a free, independent body
that resolves labour disputes — unfair dismissal, unfair labour practice, unfair discrimination,
and more. Either party (usually the employee) refers a dispute; it does NOT require a lawyer.

## Timeframes matter
An unfair dismissal dispute must be referred to the CCMA within 30 DAYS of the dismissal. Most
other unfair labour practice disputes: within 90 days. Missing the deadline can bar the claim
entirely unless "good cause" for the delay is shown.

## The two-stage process
1. **Conciliation** — a CCMA commissioner tries to help both sides reach a voluntary settlement,
   usually within 30 days of the referral. Most disputes that settle, settle here. If it doesn't
   settle, a certificate of non-resolution is issued.
2. **Arbitration** (or Labour Court, for some dispute types) — a commissioner hears both sides'
   evidence and makes a binding ruling. This is more formal — parties may bring evidence,
   witnesses, and legal representation is allowed in more complex matters.

## What employers should actually do to avoid losing at the CCMA
Dismissal must be both:
- SUBSTANTIVELY fair — a genuine, valid reason (misconduct, incapacity, or operational
  requirements/retrenchment).
- PROCEDURALLY fair — the employee was told the allegation clearly, given a real chance to
  respond (a disciplinary hearing for misconduct), and the outcome was communicated properly.
A dismissal that's substantively justified but procedurally botched (no hearing, no warning, no
chance to respond) can still be found unfair and result in reinstatement or compensation —
process matters as much as the underlying reason.

## When to get real legal/HR help
Any dismissal, retrenchment, or serious disciplinary matter is worth getting proper advice on
BEFORE acting — a labour consultant, attorney, or your industry body — since procedural mistakes
are the most common (and most avoidable) way employers lose CCMA cases.
""",
    ),
    TrainingDocument(
        filename="hiring_and_hr_basics.md",
        topic="Hiring, contracts, disciplinary process",
        content="""# Hiring & Basic HR Practice for a Small Business

## Written contracts are required
Every employee must be given written particulars of employment (effectively a contract) within
their first day, covering at minimum: job title/duties, start date, working hours, remuneration
and how/when it's paid, leave entitlement, and notice period. A verbal-only arrangement is a
real, common, and easily-avoidable compliance gap.

## Probation
Probation is a genuine trial period (commonly 1-3 months, occasionally up to 6 for senior
roles) — it must be reasonable in length for the role, and the employee must still be given a
fair opportunity to improve if performance is the issue, with proper feedback, before being let
go. Probation does NOT mean "no process required."

## Registering as an employer
As soon as you employ anyone, you generally need to register for:
- UIF (Unemployment Insurance Fund) — both employer and employee contribute 1% of pay each.
- COIDA (Compensation for Occupational Injuries and Diseases) — covers workplace injury claims;
  employer-funded, based on your industry's risk category and payroll.
- PAYE (if pay is above the tax threshold) — you withhold and pay over employee income tax
  monthly.

## A fair disciplinary process, in outline
1. Investigate the allegation before acting.
2. Notify the employee in writing of the specific allegation and the hearing date, with enough
   time to prepare.
3. Hold a hearing — let them respond, bring a co-worker/union rep if they wish.
4. A fair, proportionate outcome — a first offence rarely warrants dismissal unless it's
   serious (theft, violence, gross dishonesty).
5. Communicate the outcome and the right to refer to the CCMA if they disagree.

## Common small-business HR mistakes
- No written contract at all.
- Dismissing on the spot for a first, minor offence with no hearing.
- Inconsistent treatment — disciplining one employee harshly for something another was never
  pulled up on.
- Not keeping any written record of warnings/incidents (makes a fair process very hard to prove
  later).
""",
    ),

    # ── Compliance ────────────────────────────────────────────────────────────
    TrainingDocument(
        filename="popia_basics.md",
        topic="POPIA basics for customer data",
        content="""# POPIA Basics — What It Means for a Small Business's Customer Data

## What POPIA is
The Protection of Personal Information Act (POPIA, Act 4 of 2013) regulates how any organisation
(including a small business) collects, stores, uses, and shares people's personal information —
names, contact details, ID numbers, financial info, and more. It applies to any business
processing personal data of people in South Africa, regardless of the business's own size.

## The core principles that actually matter day-to-day
- **Lawful, minimal collection**: only collect personal information you genuinely need for a
  specific, stated purpose — not "just in case."
- **Consent**: get clear consent before using someone's information for something new (e.g.
  marketing messages) — and make it just as easy to withdraw consent as to give it.
- **Purpose limitation**: don't use data collected for one reason (e.g. fulfilling an order) for
  an unrelated purpose (e.g. reselling a customer list) without fresh consent.
- **Security**: take reasonable steps to keep personal information safe from loss, unauthorised
  access, or leaks — this applies just as much to a WhatsApp customer list or a spreadsheet as
  to a big database.
- **Retention**: don't keep personal information indefinitely once you no longer have a genuine
  reason to — have some plan for when/how old customer data is deleted.

## Marketing messages specifically
Unsolicited direct marketing (WhatsApp, SMS, email) generally requires PRIOR consent (opt-in),
not just an assumption that a past customer is fine with it. Every marketing message should make
opting out easy, and an opt-out request must actually be honoured going forward.

## What a data breach means for you
If personal information is compromised (e.g. a leaked customer database) and there's a real risk
of harm to the people affected, POPIA requires notifying the Information Regulator and the
affected individuals as soon as reasonably possible.

## Practical takeaway for a small business
This isn't only a "big company" law. Keep a customer list only as detailed as you actually need,
be able to explain why you're storing what you store, get real consent before marketing to
someone, and have a real (even if simple) way to delete a customer's data if they ask.
""",
    ),
    TrainingDocument(
        filename="business_registration_basics.md",
        topic="CIPC registration, sole prop vs Pty Ltd",
        content="""# Business Registration Basics — Sole Proprietor vs Pty Ltd

## Sole proprietorship
The simplest structure — no formal registration needed to start trading, the business and the
owner are legally the SAME person. Fast and cheap to start, but the owner has UNLIMITED personal
liability — if the business owes money or is sued, personal assets (car, house) are at risk.
All profit is taxed as the owner's personal income.

## Registering a Pty Ltd (private company)
Registered with the CIPC (Companies and Intellectual Property Commission), online, relatively
quick and inexpensive. Creates a SEPARATE LEGAL ENTITY from the owner(s) — the company owns its
own assets/debts, and shareholders' liability is generally limited to what they've invested
(not their personal assets). Requires: a unique company name (or use the registration number as
the name), at least one director, a registered address, and a Memorandum of Incorporation (MOI —
CIPC provides a standard one for simple companies).

## Why it matters beyond "sounding official"
- Liability protection: a Pty Ltd shields personal assets far better than a sole proprietorship.
- Tax: a company pays a flat 27% corporate rate; profit only becomes the owner's personal income
  (taxed again) once actually drawn as salary or dividend — this can be more or less
  tax-efficient depending on how much profit is reinvested vs drawn out.
- Credibility: many suppliers, landlords, and larger corporate customers prefer or require
  dealing with a registered company.
- Continuity: a company continues to exist independent of any one owner; a sole proprietorship
  legally ends when the owner does.

## Other common registrations to know about
- A business bank account (recommended even for a sole proprietorship, see
  bookkeeping_basics.md).
- Industry-specific licences/permits (e.g. a health certificate for food handling, a liquor
  licence, professional body registration) — these vary by industry and are separate from
  company registration itself.
- SARS registration for income tax (automatic-ish for individuals; a new company must register
  separately) and VAT once required (see vat_basics.md).
""",
    ),

    # ── Customer service & marketing ─────────────────────────────────────────
    TrainingDocument(
        filename="customer_service_basics.md",
        topic="Customer service best practices on WhatsApp",
        content="""# Customer Service Best Practices for a WhatsApp-Run Business

## Response time sets expectations fast
WhatsApp feels instant to customers, even more than email — a slow first reply (hours, not
minutes) is one of the most common reasons a customer gives up and buys elsewhere. Even a quick
"Thanks, checking on that now" holds their attention while a fuller answer is prepared.

## Handling a complaint well
1. Acknowledge it FIRST, without being defensive — "I'm sorry to hear that, let's sort it out."
2. Get the specifics (order number, what went wrong) before promising a fix.
3. Offer a concrete resolution, not just an apology — a refund, replacement, or clear next step.
4. Follow up once it's actually resolved, not just once it's promised.
A complaint handled well, fast, often creates a MORE loyal customer than one who never had a
problem at all — the recovery matters more than the initial mistake.

## Returns/refunds — set a clear policy and stick to it
Customers trust a business more when the return/refund policy is clear UPFRONT, not improvised
per complaint. A generous-but-clear policy (e.g. "not satisfied within 7 days, full refund")
often costs less in practice than an inconsistent one, because it removes friction and disputes.

## Tone on WhatsApp for South African small businesses
Warm, direct, and human reads better than overly formal corporate language — customers chose
WhatsApp because it feels personal. Mirror the customer's own language and tone where
appropriate (many SA customers switch fluidly between English and their home language), but
never sacrifice clarity for friendliness — a customer should always know exactly what happens
next.

## Setting availability expectations
If a business genuinely can't respond instantly outside certain hours, say so plainly (e.g. "We
reply between 8am-5pm weekdays") rather than leaving a customer wondering whether they've been
ignored.
""",
    ),
    TrainingDocument(
        filename="marketing_fundamentals.md",
        topic="Small-business marketing fundamentals",
        content="""# Marketing Fundamentals for a South African Small Business

## Positioning — know what you're actually selling
Before any marketing message, be able to answer clearly: who is this for, what problem does it
solve for them, and why choose this business over the alternative (price, quality, convenience,
service, trust)? A message that tries to appeal to everyone usually persuades no one — a
specific, honest positioning outperforms a generic one.

## WhatsApp and social media basics
- Consistency beats intensity — a steady weekly presence outperforms a burst of posts followed
  by silence.
- Show REAL product/work, not just stock imagery — genuine photos of actual products, real
  customers (with permission), or real work build more trust for a small business than polished
  generic content.
- A clear call to action matters — tell people exactly what to do next ("Message us to order",
  "Tap to book") rather than assuming it's obvious.

## Referral and word-of-mouth
Word-of-mouth is usually the highest-trust, lowest-cost channel a small business has. A simple,
genuine referral incentive (a discount for both the referrer and the new customer) can multiply
an existing happy customer base without new ad spend. Actively ASKING satisfied customers for a
referral or review works better than passively hoping it happens.

## Brand consistency
Using the same name, colours, tone, and logo everywhere (WhatsApp, signage, invoices, social
media) makes a business look more established and trustworthy than inconsistent branding does —
this costs nothing but attention to detail.

## Measuring what actually works
Track, even simply, where new customers actually heard about the business (ask directly — "how
did you find us?"). Many small businesses keep spending on a channel out of habit long after it
stopped being where new customers actually come from.
""",
    ),
    TrainingDocument(
        filename="business_plan_basics.md",
        topic="Simple business plan structure",
        content="""# A Simple Business Plan Structure

A business plan doesn't need to be a 40-page document to be useful — for most small businesses,
a clear 1-3 page plan covering the sections below is genuinely more useful than an elaborate one
that never gets referred to again.

## 1. Summary
One paragraph: what the business does, who it's for, and what makes it different. Write this
LAST, once the rest is clear.

## 2. The market
- Who is the target customer, specifically?
- How big is the realistic opportunity (don't just estimate the whole country's population —
  estimate the actual realistic customer base)?
- Who are the real competitors/alternatives, and what does this business do differently?

## 3. Operations
- What's actually involved in delivering the product/service day to day?
- What's needed to run it — stock, equipment, staff, a location?
- What could go wrong operationally, and what's the plan if it does (a key supplier disappears,
  a key staff member leaves)?

## 4. Marketing & sales
- How will customers actually find out the business exists?
- What's the realistic path from "hears about it" to "becomes a paying customer"?
- What's the pricing strategy, and why (see pricing_and_margins.md)?

## 5. Financial projections
Doesn't need to be complex — a simple monthly projection for the first 12 months covering:
- Expected revenue (be conservative, not optimistic, for a first plan).
- Fixed costs (rent, salaries, subscriptions) vs variable costs (materials, per-unit costs).
- The break-even point — the revenue level where the business stops losing money monthly.
- How much starting capital is actually needed to survive until break-even, INCLUDING a buffer
  for when reality runs behind plan (a common mistake is planning with zero margin for delay).

## Why bother with this at all
The real value of writing a plan isn't the document — it's being forced to think through the
market, operations, and numbers honestly before committing money and time, and having something
concrete to revisit and compare reality against later.
""",
    ),
]
