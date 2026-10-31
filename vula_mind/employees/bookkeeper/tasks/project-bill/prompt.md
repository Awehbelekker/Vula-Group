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
   When the owner wants the bill as a file (spreadsheet / PDF / "the final account to send"), call
   project_bill_file: it builds both from the books, checks the spreadsheet adds up to them, and returns private
   links for the owner. Send its reply as it is.
4. Work the balance with the calculate tool: total cost − paid directly by the client − still to pay directly −
   paid to the business = balance due. Never do the sums in your head.
5. Where the books and the owner's figures differ (a supplier invoice not in the books, a payment you can't find),
   list each difference as a question for the owner — never pick one silently and never estimate.
6. Quote decisions as the reason ("Plumbing change R1,500 — paid by the business, decided 3 March").
7. Nothing goes to the client until the owner has seen it and said yes. A client link (share_project) only on their word.
