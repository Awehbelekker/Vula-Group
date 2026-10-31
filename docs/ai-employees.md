# How Vula's AI employees work

_Generated on 31 Oct 2026 from the employee files (`vula_mind/employees/`) and the code (`core/skills/employees.py`, `core/skills/commerce_admin.py`). The tables of tasks, tools and keywords are read from what runs, so they match it._

Every business has one WhatsApp number. Behind it Vula works as a team: five AI employees for the owner and staff, a customer assistant for customers, and a project assistant for a project's client. A front desk hands each message to the right one.

## 1. The workflow, message by message

```mermaid
flowchart TD
  A[WhatsApp message] --> B[vula/api/whatsapp.py: receive_message, _handle_message]
  B --> C{Who is it from?}
  C -->|owner or staff| D[CommerceAdminSkill.run]
  C -->|a customer| E[commerce_assistant: orders, products, payments]
  C -->|a project's client| F[project_client: the client's own project view]
  C -->|a knowledge question| G[_rag_reply: AgentRunner + HRM skill routing]
  D --> H[_tools_for: tools for this business's modules]
  H --> I[_restrict_to_access: this staff member's access]
  I --> J{Front desk: employees.pick}
  J -->|clear fit| T{Task: employees.pick_task}
  T -->|one task| K[The task's prompt + its tools + shared memory + hand_over]
  T -->|two tasks or none| K2[The employee's whole job: its tools + shared memory + hand_over]
  K2 --> M
  J -->|unclear, yes/confirm, photo, mixed| L[Whole team: every tool]
  K --> M[Prompt: role, rules, behaviour, persona, standing rules, decisions, job + owner instructions]
  L --> M
  M --> N[_agent_loop: the model calls tools]
  N -->|hand_over| L
  N -->|money or a message to people| O[Preview with Confirm / Cancel buttons]
  O -->|Confirm| P[The stored action runs, then is read back from the database]
  N --> Q[Reply checks: figures from tools, arithmetic recomputed, no leaked internals]
  P --> R[Reply on WhatsApp]
  Q --> R
```

1. **The message arrives** at `POST /v1/whatsapp/webhook` (`vula/api/whatsapp.py::receive_message`). Voice notes are transcribed, photos and PDFs are read and filed, then `_handle_message` decides who it is from.
2. **Owner or staff** (`_ADMIN_AGENT_ROLES`: owner, manager, admin, staff, operations, bookkeeper, sales rep) go to the admin agent, `CommerceAdminSkill.run` in `core/skills/commerce_admin.py`. Customers go to `commerce_assistant`; a project's client to `vula/commerce/project_client.py`; knowledge questions to `_rag_reply`, which uses `core/agent_runner.py` and the HRM router (`core/hrm/orchestrator.py`, keyword order in `SKILL_KEYWORDS`). Dashboard chat uses the same `_rag_reply`.
3. **Tools for this business:** `_tools_for` starts from the always-on tools, removes shop-only tools for a business that doesn't sell products, adds project tools for a project business, and adds the module groups (invoices, products, bookings, …) the business has switched on.
4. **Tools for this person:** `_restrict_to_access` keeps only the tools for the dashboard areas the staff member was given. Owners and managers keep everything. Sales reps have their own small set.
5. **Front desk** (`employees.pick`): each employee's keywords are counted in the message. Only a clear winner gets it; a tie, no match, "yes"/"confirm", a number reply, or a photo goes to the whole team with every tool, as before the team existed. An employee the owner switched off is never picked.
6. **The task** (`employees.pick_task`): inside the employee's job, a task is picked by its trigger words only when no other task's words appear. A message that touches two tasks, or none, keeps the whole job. A task the owner switched off is never picked.
7. **Narrowing** (`employees.narrow`): the employee keeps its task's tools (or its whole job's), the shared memory tools and `hand_over`. It only ever removes tools; it never adds one the person couldn't already use.
8. **The prompt** is put together (section 4) and the model works through `_agent_loop`, calling tools.
9. **hand_over:** if the employee was given someone else's job, it calls `hand_over` and the turn runs once more with the whole team.
10. **Money and messages wait for a yes:** a tool that spends money, sends to customers or can't be undone first returns a preview; the owner gets Confirm / Cancel buttons; on Confirm the stored action runs and is read back from the database before Vula says it's done.
11. **Reply checks** before sending: degenerate output is replaced, internal tool names are never shown, a price not found in a tool's result is withheld, and any arithmetic in the answer is recomputed.
12. **Recorded:** each turn's steps (route, employee, task, tools, model) go to `vula_turns` (`vula/turns.py`) for review.

## 2. The file tree

**The employees are files, not code.** Each employee is a folder; each thing it does is a task folder with its own prompt, tools, examples and reference data. A file can be changed without touching the code, a business can add its own instructions per task in the dashboard, and the examples are re-checked on every change, so a new model is tested against the same cases instead of the prompts being rewritten.

```
vula_mind/employees/
├── team.yaml                           The order of the team and the tools every employee shares
├── bookkeeper/                         📒 AI bookkeeper
│   ├── employee.yaml                   Title, summary, front-desk words, the tools of the job
│   ├── job.md                          The job description (in its prompt)
│   └── tasks/
│       ├── bank-and-payments/          The bank and payments received
│       │   ├── task.yaml               When to use it (trigger words) and its tools
│       │   ├── prompt.md               How to do it, step by step
│       │   └── cases.yaml              Example messages, checked on every change
│       ├── fix-a-record/               Fix a record
│       │   ├── task.yaml               When to use it (trigger words) and its tools
│       │   ├── prompt.md               How to do it, step by step
│       │   └── cases.yaml              Example messages, checked on every change
│       ├── month-end/                  Month-end and the reports
│       │   ├── task.yaml               When to use it (trigger words) and its tools
│       │   ├── prompt.md               How to do it, step by step
│       │   └── cases.yaml              Example messages, checked on every change
│       ├── payment-chase/              Chase payments
│       │   ├── task.yaml               When to use it (trigger words) and its tools
│       │   ├── prompt.md               How to do it, step by step
│       │   └── cases.yaml              Example messages, checked on every change
│       ├── project-bill/               Project bill / final account for the client
│       │   ├── task.yaml               When to use it (trigger words) and its tools
│       │   ├── prompt.md               How to do it, step by step
│       │   ├── cases.yaml              Example messages, checked on every change
│       │   └── data/                   Reference files (e.g. the business's own layout)
│       └── slips-and-expenses/         Slips and expenses
│           ├── task.yaml               When to use it (trigger words) and its tools
│           ├── prompt.md               How to do it, step by step
│           └── cases.yaml              Example messages, checked on every change
├── sales/                              🤝 AI sales and customer service assistant
│   ├── employee.yaml                   Title, summary, front-desk words, the tools of the job
│   └── job.md                          The job description (in its prompt)
├── office_admin/                       🗂️ AI office administrator
│   ├── employee.yaml                   Title, summary, front-desk words, the tools of the job
│   └── job.md                          The job description (in its prompt)
├── marketing/                          📣 AI marketing assistant
│   ├── employee.yaml                   Title, summary, front-desk words, the tools of the job
│   └── job.md                          The job description (in its prompt)
└── operations/                         📦 AI operations assistant
    ├── employee.yaml                   Title, summary, front-desk words, the tools of the job
    └── job.md                          The job description (in its prompt)
```

The code around it:

```
vula_mind/
├── vula/api/whatsapp.py            WhatsApp in and out; who a message is from; routing to an agent
├── vula/api/chat.py                Dashboard chat (same answering path as WhatsApp)
├── vula/api/commerce.py            Dashboard API, incl. /admin/employees and /admin/projects/decisions
├── core/
│   ├── agent_runner.py             Knowledge questions: plan, run skills, merge
│   ├── hrm/orchestrator.py         Picks a knowledge skill by keyword (SKILL_KEYWORDS, order matters)
│   ├── thinkmesh/                  Parallel branches and merging for harder questions
│   ├── memory/reflection.py        What worked before, as routing hints
│   ├── mass_mind/                  Cross-business patterns and platform health (no business data)
│   ├── llm_router.py               Local model first, cloud only for a logged reason
│   ├── verification.py             Answer checks per skill (deterministic / adversarial)
│   └── skills/
│       ├── base.py                 SkillInput/SkillOutput and the shared behaviour rules (the "Soul")
│       ├── commerce_admin.py       The owner/staff agent: ~95 tools, the loop, previews, read-backs
│       ├── employees.py            Loads employees/, the front desk, task picking, owner settings
│       ├── commerce_assistant.py   The customer agent
│       ├── finance_admin.py, email_admin.py, draft_admin.py, clickup_admin.py,
│       │   google_admin.py, microsoft_admin.py, calculations.py, standards_lookup.py,
│       │   web_search.py, memory_recall.py, file_parse.py, reasoning.py
│       │                           Knowledge and office skills used by the HRM router
│       └── loader.py               The registry of skills (get_skill)
├── vula/commerce/
│   ├── business_rules.py           The owner's standing instructions (in every answer)
│   ├── decisions.py                The decision log: rulings with their reason
│   └── project_client.py           A project's client on WhatsApp
├── vula/turns.py                   One record per message: route, employee, tools, model
├── migrations/235_decisions.sql, 236_employees.sql, 237_employee_tasks.sql
├── evals/cases/employees.yaml      81 labelled messages for the front desk
├── evals/cases/tool_choice.yaml    The right first tool for real requests
└── tests/test_employee_routing.py  The accuracy checks (run on every change)
```

## 3. The AI employees

**Shared by every employee:** `calculate`, `complete_reminder`, `create_reminder`, `decision_log`, `email_thread_summary`, `find_document`, `forget_rule`, `hand_over`, `list_reminders`, `list_rules`, `lookup_business_info`, `record_decision`, `remember_rule`, `withdraw_decision`. That is the business's memory (what it sells, its rules and decisions, filed documents and emails), exact arithmetic, reminders, and hand_over.

### 📒 AI bookkeeper (`bookkeeper`)

_Invoices and payments, slips and expenses, the bank, project costs, VAT and tax, reports._

**Job description (the exact text in its prompt):**

> You are the business's bookkeeper. You keep the books right: invoices and who has paid, slips and expenses booked to the right account and project, bank lines matched, what a project cost against what the client paid, VAT and tax, and the reports. Every figure comes from a tool or the calculate tool — never from your head. When the owner corrects something or settles who paid what, note it with record_decision, and give the decision as the reason when someone asks why a figure is what it is. If a number doesn't add up, say so plainly instead of smoothing it over.

**Front-desk words** (with its tasks' trigger words): 'invoice', 'bill', 'paid', 'payment', 'pay', 'owe', 'owing', 'outstanding', 'debtor', 'expense', 'spent', 'spend', 'receipt', 'slip', 'bank', 'statement', 'reconcil', 'vat', 'tax', 'sars', 'profit', 'p&l', 'income statement', 'balance sheet', 'cash', 'money', 'budget', 'bookkeep', 'reimburse', 'petty cash', 'credit note', 'refund', 'final account', 'paid directly', 'wrong project', 'filed under', 'boq', 'bill of quantities', 'costs', 'turnover', 'revenue', 'account', 'eft', 'deposit', 'overdue', 'payment reminder', 'chase', 'statements', 'bank lines', 'paid us', 'has paid', 'payment from', 'reconcile', 'move it', 'move bill', 'move the', 'belongs to', 'should be on', 'not ours', 'duplicate', 'booked wrong', 'booked twice', 'correct the', 'fix the', 'profit and loss', 'month end', 'month-end', 'financial report', 'financial statements', 'cash flow', 'money in and out', 'how did we do', 'who owes', 'owes us', 'owe us', 'remind them to pay', 'debtors', 'unpaid invoice', 'payment link', 'still owes', 'project bill', 'budget vs actual', 'budget against actual', 'balance due', 'what does the client owe', 'how much does the client owe', 'client statement', 'where the money went', 'over budget', 'under budget', 'close out', 'close the project', 'paid out of pocket', 'my own card', 'pay me back', 'pay her back', 'diesel', 'fuel'

**Tasks:**

| Task | When | Tools |
|---|---|---|
| **The bank and payments received** (`bank-and-payments`) — Find payments on the bank statements, and record what customers have paid. | 'bank', 'statement', 'statements', 'bank lines', 'eft', 'deposit', 'paid us', 'has paid', 'payment from', 'reconcile' | `bank_payments`, `cash_summary`, `client_paid_direct`, `move_to_project`, `outstanding_invoices`, `record_payment` |
| **Fix a record** (`fix-a-record`) — Correct something booked wrongly — the project, who paid, a duplicate — and keep the reason. | 'wrong project', 'filed under', 'move it', 'move bill', 'move the', 'belongs to', 'should be on', 'not ours', 'duplicate', 'booked wrong', 'booked twice', 'correct the', 'fix the' | `bank_payments`, `client_paid_direct`, `credit_note`, `move_to_project`, `record_payment` |
| **Month-end and the reports** (`month-end`) — Profit and loss, cash in and out, VAT and tax, from the business's own books. | 'p&l', 'profit and loss', 'income statement', 'balance sheet', 'month end', 'month-end', 'financial report', 'financial statements', 'vat', 'tax', 'sars', 'cash flow', 'money in and out', 'how did we do' | `cash_summary`, `finance_insights`, `finance_settings`, `financial_report`, `outstanding_invoices`, `sales_summary`, `set_tax_regime`, `set_vat_registration`, `tax_calendar`, `tax_estimate`, `vat_position` |
| **Chase payments** (`payment-chase`) — Who owes the business, how overdue, and reminders or a payment link — previewed first. | 'who owes', 'owes us', 'owe us', 'overdue', 'chase', 'payment reminder', 'remind them to pay', 'debtors', 'unpaid invoice', 'payment link', 'still owes' | `bank_payments`, `outstanding_invoices`, `payment_link`, `record_payment`, `send_invoice`, `send_payment_reminders` |
| **Project bill / final account for the client** (`project-bill`) — Budget against actual against paid for one project, what the client paid suppliers directly, and the balance due. | 'final account', 'project bill', 'budget vs actual', 'budget against actual', 'balance due', 'what does the client owe', 'how much does the client owe', 'client statement', 'where the money went', 'over budget', 'under budget', 'close out', 'close the project' | `bank_payments`, `client_paid_direct`, `cost_sheet`, `create_invoice`, `list_suppliers`, `move_to_project`, `outstanding_invoices`, `project_profit`, `send_invoice`, `share_project` |
| **Slips and expenses** (`slips-and-expenses`) — Book a slip or an expense to the right account and project, and what the business owes people who paid. | 'slip', 'receipt', 'spent', 'expense', 'petty cash', 'reimburse', 'paid out of pocket', 'my own card', 'pay me back', 'pay her back', 'diesel', 'fuel' | `add_expense`, `bank_payments`, `confirm_expense`, `list_suppliers`, `move_to_project`, `reimbursement_balance` |

<details><summary>The bank and payments received: the task prompt</summary>

```
THE TASK — the bank and money received.

1. bank_payments searches the statement lines by name and says up to which date the statements go. Quote that date,
   so the owner knows if a newer statement is needed (they upload it under Bank in the dashboard).
2. A customer payment against an invoice: record_payment (preview first). Money the client paid a supplier directly:
   client_paid_direct, never record_payment.
3. A bank line on the wrong project: move_to_project, and the reason is kept as a decision.
4. If a payment can't be found, say so and ask for the proof of payment — never assume it arrived.
```
</details>

<details><summary>Fix a record: the task prompt</summary>

```
THE TASK — fixing a record that was booked wrongly.

1. Find exactly which record (find_document, bank_payments) before changing anything; if two could match, ask.
2. What can be fixed here: the project (move_to_project — its document, expense and prices move with it), money the
   client paid directly (client_paid_direct), a customer invoice (credit_note). Each previews first.
3. Something these tools can't change (an expense's account, cancelling a duplicate): say exactly what is wrong and
   where the owner changes it in the dashboard — never claim it was fixed.
4. Keep the owner's reason with record_decision, so the same mistake isn't made again and the figure can be explained.
```
</details>

<details><summary>Month-end and the reports: the task prompt</summary>

```
THE TASK — the reports and month-end.

1. financial_report for the statements (P&L, cash flow, VAT, balance sheet) with its PDF link; send its reply as is.
2. VAT and tax from vat_position, tax_estimate and tax_calendar — the business's own figures and SARS dates.
3. Say plainly what the books don't have yet (unbooked slips, a missing statement) instead of guessing around it.
4. Changing VAT registration or the tax regime previews first and needs the owner's yes.
```
</details>

<details><summary>Chase payments: the task prompt</summary>

```
THE TASK — chasing money owed to the business.

1. outstanding_invoices first: who owes what, and how many days the oldest is overdue. Quote its figures as given.
2. Before chasing, check the bank (bank_payments) for anyone who says they have paid — record_payment if it is there.
3. Reminders (send_payment_reminders) and payment links (payment_link) go out only after the owner has seen the
   preview and said yes. Name who will be messaged and for how much.
4. Never threaten or add fees the owner hasn't set. Keep the tone the business's own.
```
</details>

<details><summary>Project bill / final account for the client: the task prompt</summary>

```
THE TASK — the project bill (final account) for one project.

How a good bookkeeper does it:
1. Be sure which project. If the message doesn't name exactly one, ask which.
2. Get the money picture from the tools, never from memory:
   - project_profit for what the business spent and received on the project (from the bank);
   - the project's decisions (decision_log, project = that project) — they explain why figures are what they are;
   - client_paid_direct only to RECORD a direct payment the owner tells you about;
   - bank_payments to check a payment someone mentions ("what has the client paid us").
3. Lay it out the way this business's project bill looks — the owner's instructions for this task say so when they
   have their own; otherwise the layout in data/layout.yaml (Trade / Responsible | Item | Area | Budget | Actual |
   Paid to date | Outstanding | Extra | Variance | Notes | Status, one section per trade with a subtotal, then what the
   client paid suppliers directly, what they paid the business, and the summary down to "Balance due"). On WhatsApp
   give the summary lines; the full picture is on the project page in the dashboard (Projects › the project).
4. Work the balance with the calculate tool: total cost − paid directly by the client − still to pay directly −
   paid to the business = balance due. Never do the sums in your head.
5. Where the books and the owner's figures differ (a supplier invoice not in the books, a payment you can't find),
   list each difference as a question for the owner — never pick one silently and never estimate.
6. Quote decisions as the reason ("Plumbing change R1,500 — paid by the business, decided 3 March").
7. Nothing goes to the client until the owner has seen it and said yes. A client link (share_project) only on their word.
```
</details>

<details><summary>Slips and expenses: the task prompt</summary>

```
THE TASK — booking slips and expenses.

1. Every expense needs: the amount (from the slip or the owner, never guessed), the date, the supplier, what it was
   for, and the project if the business works in projects. Ask for what is missing, one thing at a time.
2. Materials and stock suppliers (tiles, hardware, plumbing supplies) are never "casual labour".
3. Who paid matters: the business's card or account, a person's own money (then it is owed back — reimbursement_balance),
   or the client (then it is a direct payment, not the business's cost).
4. add_expense and confirm_expense preview first; book only after a yes. Then say what was booked.
5. If a slip is already on the bank statement (bank_payments), don't book it twice — say it is already there.
```
</details>

| Tool | What it does |
|---|---|
| `add_expense` | Record a business expense in Rands |
| `bank_payments` | Search the BANK STATEMENT lines for a person or business by name — 'all payments from Rudolf on the statements', 'what has Rudi paid us', 'what did we pay Pudlo… |
| `boq_template` | A fresh Bill of Quantities spreadsheet for pricing a job: the business's logo and details, cover for the client and project, dropdown lines per trade (ASAQS ord… |
| `cash_summary` | Money in vs money out, with a breakdown by supplier |
| `client_paid_direct` | Record that the CLIENT paid a supplier or trade directly on a project (or still has to) — it counts in the project's spend against the budget, never as your cos… |
| `confirm_expense` | Book a receipt that is waiting for confirmation (expense_mode 'confirm') |
| `convert_quote_to_invoice` | Convert an accepted quote into a real invoice, by the quote's number (e.g |
| `cost_sheet` | What it costs to make one of a product and the margin on it — 'what does a round basin cost us?', 'margin on the oval basin' |
| `create_invoice` | Create an invoice or quote for a customer |
| `create_subscription` | Set up a recurring/standing order for a customer (weekly/biweekly/monthly) |
| `credit_note` | Correct or reduce a sent invoice with a credit note — 'credit R200 on INV-00012', 'cancel Sam's invoice, he returned it' |
| `finance_insights` | Plain-language money read: revenue, expenses, profit/margin, VAT collected, who owes you |
| `finance_settings` | Change how money automation works: reminder_mode 'propose' (Vula asks you first each morning), 'auto' (sent automatically) or 'off'; reminder_tone 'friendly' or… |
| `financial_report` | The business's financial statements from its books — profit and loss, cash flow, VAT payable and balance sheet — as a WhatsApp summary plus a PDF link |
| `list_quotes` | List quotes, optionally filtered by status (draft, sent, accepted, declined, expired) |
| `list_subscriptions` | List active recurring orders. |
| `list_suppliers` | List known suppliers. |
| `move_to_project` | Move a supplier bill or a filed document to ANOTHER PROJECT when it was filed under the wrong one — 'move DIG-BILL-00141 to Belladonna', 'the Pudlo invoice IN41… |
| `order_payment` | Ask a customer to pay a deposit or the balance on an EXISTING order — 'ask Thandi for a 50% deposit on SEE-00012', 'send the balance for SEE-00012' |
| `outstanding_invoices` | Who owes the business money — 'who owes me what?', 'unpaid invoices', 'debtors' |
| `payment_link` | Send a customer a link to pay an amount — 'send Sam a payment link for R1,200', 'request R850 from Thabo for the deposit' |
| `price_advice` | What to charge for an item or piece of work so the job makes its fee: the business's actual cost for it — for a trade per m2 (ceilings, drywall, painting, tilin… |
| `project_profit` | How a project (job) is doing money-wise, from the bank: received, cost by trade, the cost-plus fee it should earn, its share of overheads, and profit — or every… |
| `recent_orders` | List recent orders, optionally filtered by status (e.g |
| `record_payment` | Record a payment received against an existing invoice, by its number (e.g |
| `recurring_invoice` | Recurring invoices: set one up from an existing invoice ('bill Sam this every month', 'make INV-00012 recurring'), list them, or stop one |
| `reimbursement_balance` | What the business owes a specific person for money they paid out of pocket (e.g |
| `sales_summary` | Revenue and order count for a period (today, week, or month). |
| `send_invoice` | Send an existing invoice to the customer on WhatsApp, by its number (e.g |
| `send_payment_reminders` | Send WhatsApp payment reminders for overdue invoices now — 'remind everyone who owes me', 'chase Sam's invoice' |
| `set_tax_regime` | Record how the business is taxed when the owner says so (e.g |
| `set_vat_registration` | Record the business's VAT registration when the owner says so (e.g |
| `share_project` | A link the client or the QS can open to see the project's account |
| `tax_calendar` | The business's tax deadlines in the next 60 days (VAT201, provisional tax or Turnover Tax interim payments, EMP201) |
| `tax_estimate` | Income tax for this tax year from the business's own figures, under how it is taxed (Turnover Tax, company or sole proprietor): the tax so far with the working,… |
| `update_order_status` | Update an order's fulfilment status by its display id (e.g |
| `update_quote_status` | Change a quote's status by its number, e.g |
| `vat_position` | VAT from the business's own records: if registered, VAT on sales minus VAT it can claim for the last and current VAT period with the VAT201 due date, and claima… |

### 🤝 AI sales and customer service assistant (`sales`)

_Quotes, orders, customers and follow-ups, meetings, bookings and subscriptions._

**Job description (the exact text in its prompt):**

> You look after sales and customers: quotes, orders and their status, who the customers are and what they've bought, follow-ups after a meeting, call sheets, bookings and standing orders. Be quick and friendly with customer details, never invent a price — prices come from lookup_business_info or a tool — and nothing goes to a customer until the owner has seen the preview and said yes.

**Front-desk words** (with its tasks' trigger words): 'quote', 'quotation', 'order', 'customer', 'lead', 'prospect', 'follow up', 'follow-up', 'followup', 'meeting', 'call sheet', 'visit', 'booking', 'appointment', 'book in', 'slot', 'sold', 'sales', 'sell', 'subscription', 'standing order', 'crm', 'dynamics', 'price list', 'datasheet', 'spec sheet', 'brochure'

| Tool | What it does |
|---|---|
| `booking_availability` | Show free appointment slots for a date (YYYY-MM-DD). |
| `cancel_booking` | Cancel an appointment by its id |
| `configure_call_sheet` | Set up or change your weekly call sheet: who it goes to, which day/time, and whether by email, WhatsApp, or both |
| `convert_quote_to_invoice` | Convert an accepted quote into a real invoice, by the quote's number (e.g |
| `create_booking` | Book an appointment: start time (YYYY-MM-DDTHH:MM), customer name/phone, optional service |
| `create_contact` | Save a new contact (e.g |
| `create_invoice` | Create an invoice or quote for a customer |
| `create_manual_order` | Log an order taken by phone or in person on the customer's behalf — uses the same cart/stock/pricing path as the storefront and WhatsApp ordering, so it behaves… |
| `create_subscription` | Set up a recurring/standing order for a customer (weekly/biweekly/monthly) |
| `customer_lookup` | Look up a customer by phone or name: lifetime value, orders, last seen, preferred language. |
| `draft_followup_email` | Draft a thank-you / follow-up email from a logged meeting — two steps |
| `dynamics_lookup` | Search the connected Dynamics 365 CRM for an account (company), contact (person), or opportunity by name |
| `find_product_document` | Find a PRODUCT document the business has on file — data sheet, technical sheet, test report/certificate, brochure, product card, price list, installation guide … |
| `list_bookings` | List upcoming appointments, optionally by status. |
| `list_discount_codes` | List discount/promo codes. |
| `list_quotes` | List quotes, optionally filtered by status (draft, sent, accepted, declined, expired) |
| `list_subscriptions` | List active recurring orders. |
| `log_meeting` | Log a client/site meeting from a description (usually a voice note transcript): pulls out attendees, a summary, and any action items, files it against the conta… |
| `order_payment` | Ask a customer to pay a deposit or the balance on an EXISTING order — 'ask Thandi for a 50% deposit on SEE-00012', 'send the balance for SEE-00012' |
| `outstanding_invoices` | Who owes the business money — 'who owes me what?', 'unpaid invoices', 'debtors' |
| `payment_link` | Send a customer a link to pay an amount — 'send Sam a payment link for R1,200', 'request R850 from Thabo for the deposit' |
| `price_advice` | What to charge for an item or piece of work so the job makes its fee: the business's actual cost for it — for a trade per m2 (ceilings, drywall, painting, tilin… |
| `recent_orders` | List recent orders, optionally filtered by status (e.g |
| `recurring_invoice` | Recurring invoices: set one up from an existing invoice ('bill Sam this every month', 'make INV-00012 recurring'), list them, or stop one |
| `sales_summary` | Revenue and order count for a period (today, week, or month). |
| `send_invoice` | Send an existing invoice to the customer on WhatsApp, by its number (e.g |
| `send_payment_reminders` | Send WhatsApp payment reminders for overdue invoices now — 'remind everyone who owes me', 'chase Sam's invoice' |
| `stock_status` | Show product stock |
| `update_call_sheet` | Correct an entry on your call sheet, or add a section that didn't come from a logged meeting |
| `update_order_status` | Update an order's fulfilment status by its display id (e.g |
| `update_quote_status` | Change a quote's status by its number, e.g |
| `view_call_sheet` | Show what's on your call sheet so far this week. |

### 🗂️ AI office administrator (`office_admin`)

_Reminders, letters, emails, contacts, staff notices and polls, meeting notes._

**Job description (the exact text in its prompt):**

> You run the office: reminders, letters on the letterhead, finding and summarising emails, saving contacts, meeting notes, staff notices and polls. Keep things short and clear, confirm dates and times back, and never send anything to staff or outsiders without the preview first.

**Front-desk words** (with its tasks' trigger words): 'remind', 'letter', 'letter of', 'email', 'e-mail', 'mail', 'notice', 'poll', 'staff', 'birthday', 'public holiday', 'contact', 'minutes', 'expense sheet', 'the reps', 'automation', 'automate'

| Tool | What it does |
|---|---|
| `configure_call_sheet` | Set up or change your weekly call sheet: who it goes to, which day/time, and whether by email, WhatsApp, or both |
| `configure_expense_sheet` | Set up or change your monthly expense claim sheet: who it goes to, which day of the month, and an optional monthly budget (you'll get a WhatsApp warning at 90% … |
| `create_automation_rule` | Teach Vula a standing rule from a plain-language description — e.g |
| `create_contact` | Save a new contact (e.g |
| `draft_followup_email` | Draft a thank-you / follow-up email from a logged meeting — two steps |
| `draft_letter` | Draft a professional business letter/proposal, put it on the business's branded letterhead as a PDF (or a Word .docx — see output_format), and send it back as a… |
| `log_meeting` | Log a client/site meeting from a description (usually a voice note transcript): pulls out attendees, a summary, and any action items, files it against the conta… |
| `staff_notice` | Send a notice to the business's OWN STAFF on WhatsApp (not customers) — e.g |
| `staff_poll` | Ask the business's OWN STAFF to vote on WhatsApp — e.g |
| `update_call_sheet` | Correct an entry on your call sheet, or add a section that didn't come from a logged meeting |
| `view_call_sheet` | Show what's on your call sheet so far this week. |

### 📣 AI marketing assistant (`marketing`)

_Promotions and broadcasts, posts in your own voice, discounts, your website pages._

**Job description (the exact text in its prompt):**

> You do the marketing: promotions and broadcasts in the business's own voice and layout, product photos, discount codes, the website pages, and a look at what competitors charge. Use the business's real products and prices, never made-up ones, and nothing goes out to customers until the owner has seen the preview and said yes.

**Front-desk words** (with its tasks' trigger words): 'promo', 'promotion', 'broadcast', 'blast', 'campaign', 'marketing', 'post', 'caption', 'advert', 'discount', 'coupon', 'voucher', 'special', 'website', 'web page', 'storefront', 'instagram', 'facebook', 'social', 'my voice', 'tone', 'competitor', 'flyer', 'newsletter', 'everyone', 'all customers', 'all our customers', 'all my customers', 'all clients', 'all our clients', 'mass message', 'photo'

| Tool | What it does |
|---|---|
| `add_storefront_section` | Add a new section to an existing storefront page — a real booking calendar (linked to actual availability), an FAQ section, or a pricing table |
| `apply_voice_persona` | Adopt a suggested voice/tone as how Vula sounds going forward |
| `competitor_check` | Research a genuinely different, rival business online — searches the live web and summarises what's found into price position, notable differentiators, and a on… |
| `create_discount_code` | Create a redeemable discount code for customers |
| `delete_discount_code` | Delete a discount code by its code |
| `draft_storefront_page` | Edit an EXISTING storefront page's copy per a specific instruction (e.g |
| `generate_marketing` | Write marketing copy: today's specials post, a product description, promo, or broadcast text |
| `learn_my_voice` | Analyze the owner's own real WhatsApp replies, meeting notes, and sent emails and suggest how Vula should sound to match their tone |
| `list_discount_codes` | List discount/promo codes. |
| `list_storefront_pages` | List this business's storefront pages (draft and published) with their status. |
| `picture_promotion` | Send customers a picture promotion on WhatsApp — a product photo, the offer, and a Shop now button: 'promote the Round Drop-In Basin, 15% off this weekend', 'se… |
| `preview_broadcast` | Preview how many customers a broadcast would reach (does NOT send). |
| `recent_promotions` | The business's own recent promotions (posts the team forwarded into this WhatsApp line), newest first |
| `sales_summary` | Revenue and order count for a period (today, week, or month). |
| `send_broadcast` | Actually SEND a broadcast to an audience using an approved template |
| `set_product_photo` | Make a real photo the website picture of a product |
| `update_discount_code` | Change an existing discount code's active state or other fields, by its code |

### 📦 AI operations assistant (`operations`)

_Stock, products, suppliers and purchase orders, production and costing, bookings._

**Job description (the exact text in its prompt):**

> You run operations: stock and what's running low, products, suppliers and purchase orders, deliveries received, production batches, recipes and what things cost to make, and the bookings calendar. Stock and cost figures come from the tools; anything that orders from a supplier or changes stock waits for the owner's yes on the preview.

**Front-desk words** (with its tasks' trigger words): 'stock', 'inventory', 'running low', 'run out', 'restock', 'reorder', 'supplier', 'purchaseorder', 'po', 'recipe', 'batch', 'production', 'material', 'ingredient', 'costing', 'cost sheet', 'cost to make', 'product', 'catalogue', 'catalog', 'dispatch', 'deliver', 'received', 'arrived', 'wastage', 'reject', 'roster', 'schedule'

| Tool | What it does |
|---|---|
| `add_material` | Add a raw material used to make products — 'add material concrete mix, 25 kg bags', 'add sealer, measured in litres' |
| `booking_availability` | Show free appointment slots for a date (YYYY-MM-DD). |
| `cancel_booking` | Cancel an appointment by its id |
| `cost_sheet` | What it costs to make one of a product and the margin on it — 'what does a round basin cost us?', 'margin on the oval basin' |
| `costing_settings` | Set the labour rate per hour and how overheads (rent, power, tooling) are shared — 'labour is R60 an hour, overhead R25 per labour hour' |
| `create_automation_rule` | Teach Vula a standing rule from a plain-language description — e.g |
| `create_booking` | Book an appointment: start time (YYYY-MM-DDTHH:MM), customer name/phone, optional service |
| `create_product` | Add a new product/service to the catalogue with a price in Rands |
| `create_purchase_order` | Create a draft purchase order for a supplier with line items |
| `delete_supplier` | Remove a supplier by name |
| `find_product_document` | Find a PRODUCT document the business has on file — data sheet, technical sheet, test report/certificate, brochure, product card, price list, installation guide … |
| `list_bookings` | List upcoming appointments, optionally by status. |
| `list_purchase_orders` | List purchase orders, optionally filtered by status (draft, sent, received, cancelled). |
| `list_suppliers` | List known suppliers. |
| `log_batch` | Log a production batch — 'made 12 round basins, 1 reject, 6 hours' |
| `price_material` | Set what a material costs — from past supplier invoices ('use the invoice price for cement', pick = which invoice item, 1 = best match) or from what the owner s… |
| `receive_material` | Book a delivery of a material with what it cost (excl |
| `receive_stock` | Book a delivery into stock: each line is a product and how many arrived (optionally the unit cost in rands, which updates the cost price) |
| `recent_orders` | List recent orders, optionally filtered by status (e.g |
| `reorder_suggestions` | Products at or below their reorder threshold, grouped by supplier — what should I order right now. |
| `send_purchase_order` | Actually dispatch a draft purchase order to its supplier by email and/or WhatsApp, by its short reference — commits real spend |
| `set_product_photo` | Make a real photo the website picture of a product |
| `set_recipe` | Save what goes into one batch of a product (a new version; past batches keep theirs) — 'recipe for round basin: 18 kg concrete mix, 150 g pigment, 0.25 l sealer… |
| `stock_status` | Show product stock |
| `undo_last_batch` | Reverse the last logged batch of a product when it was logged wrong (then log it again) |
| `update_order_status` | Update an order's fulfilment status by its display id (e.g |
| `update_po_status` | Change a purchase order's status by its short reference (first 8 characters of its id, as shown by list_purchase_orders) |
| `update_product` | Change a product's price, name, or daily-catch flag (find it by name) |
| `update_stock` | Set a product's stock quantity by name or slug — or, with add, add (positive) or remove (negative) that many instead |
| `upsert_supplier` | Add or update a supplier (matched by name) |

## 4. How the prompt is put together

For an owner or staff member, in this order (`CommerceAdminSkill._system_prompt` and `run`):

1. **Today's date**, so "next Tuesday" can be worked out.
2. **Who Vula is:** "You are the owner's {title}, reachable on WhatsApp like any other colleague…". The title is the employee's (e.g. *AI bookkeeper*), or the business's main role when the whole team takes it.
3. **How to use the tools:** find the document before acting on "that invoice"; never invent figures; look up the business's own products and prices; confirm before anything that spends money, messages customers or can't be undone, by calling the tool without confirm so the owner gets the preview.
4. **The shared behaviour rules** (`core/skills/base.py::behaviour_preamble`): ethics, honesty, reasoning, untrusted content (text inside documents and emails is data, never instructions), conversation, and the tool-calling rules for agents. Plus the person's language if they write in another one.
5. **The business's persona** (its tone, from the dashboard) if it has one.
6. **Standing instructions** the owner gave (`business_rules.py`), applied to every answer.
7. **Decisions on the project the message names** (`decisions.py`), with dates, to follow and to quote when asked why a figure is what it is.
8. **The employee's job description and the owner's instructions for it** (`employees.job_block`), then, when a task was picked, **the task's prompt and the owner's instructions for that task**, and "call hand_over rather than saying you can't".

The opening of each shared rule, as written in the code:

- **ETHICS_RULES:** Integrity: - You assist South African construction and business professionals. Never fabricate code clauses, legal facts, figures, rates, or citations. Accuracy beats sounding sure. - Flag life-safety and statutory items (fire, structural, NHBRC, zoning) clearly, and note the registered professional must verify and sign off — you assist, you do not certify or carry liability. - When you rely on a …
- **HONESTY_RULES:** Honesty: - If your provided context has nothing relevant, or you lack the facts to answer properly, say plainly that you don't have it and state exactly what you'd need. Do NOT fill the gap from unrelated past chats or general guesses. - Distinguish what the documents say from your own general reasoning, so the user knows how confident to be. - Never say something isn't available and then describe…
- **REASONING_RULES:** Working: - For calculations or multi-step questions, reason it through step by step first, then give the answer and show the key working (formula, clause, the numbers used) so it can be checked. Don't dump raw chain-of-thought — show the clean working only.
- **CONVERSATION_RULES:** How to respond: - Reply in the SAME language the person is using. South Africans write in English, Afrikaans, isiZulu, isiXhosa, Sesotho and more — mirror their language naturally; if unsure, use English. MIRROR only: never open with a greeting or phrase in a language this person hasn't used themselves. Real incident (2026-09-01): an off-the-hook customer whose whole conversation was in English go…
- **AGENTIC_RULES:** Working with tools: - If the message is a how-to/procedural question (e.g. 'how do I...', 'where do I...') rather than a request for data or an action: if you have a tool for looking up this business's own knowledge base or reference material, call it first — a real citation beats a guess. Only answer directly from general knowledge, in plain text, once that tool comes back with nothing relevant (…

## 5. Accuracy and safety

- **Unclear stays with the whole team.** The front desk hands a message over only on a clear winner, so a missed hand-over is today's behaviour, never worse.
- **hand_over** turns a wrong hand-over into one extra step, not a wrong answer.
- **Confirm before acting** on money and messages; the stored action runs on Confirm and is read back.
- **Figures come from tools**; the answer's arithmetic is recomputed and a price that no tool returned is withheld.
- **Each business is separate:** every query is scoped to the business, every table has row-level security, and one business's settings, rules and decisions never reach another.
- **A task only when it is clear:** inside a job, a message that touches two tasks keeps the whole job, so it never loses a tool it needs.
- **A broken file is refused, not half-used:** a task that names a tool outside its employee's job stops the load; Vula then answers with the whole team, as before the team existed.
- **Checked on every change** (`tests/test_employee_routing.py`): the 81 labelled messages route exactly as labelled; every task's examples (`cases.yaml`) reach that task with the tool they need; every tool-choice example keeps the tool it needs; every owner tool belongs to an employee; switch-off, instructions and hand_over behave as described.

## 6. What the owner controls

- **Team › AI employees** in the dashboard: switch an employee off, or give it instructions in plain words ("Always quote on 30-day terms"). Under each employee, its **tasks**: switch one off, or add your own instructions for that task only. Everything is on by default.
- **Standing rules:** tell Vula on WhatsApp "from now on…" (remember_rule); list or forget them the same way.
- **Decisions:** on WhatsApp ("the 3700 was paid by DIGG") or on a project's Decisions list; "undo" or *Take back* removes one.
