# How Vula protects your business data

*Last checked: 13 November 2026, against the live system.*

Vula runs your business on WhatsApp and a web dashboard: orders, invoices, documents, projects and customer conversations. This page sets out how that information is kept separate, private and accurate. Every statement here was checked against the running system and its code on the date above. Where something is still being finished, we say so.

## 1. Your data is kept apart from every other business

- Every record carries your business's identity. Every database table enforces that at the database level (row-level security), on all 227 tables.
- Your documents and knowledge sit in a collection of their own, searched only for your business.
- Each business has its own WhatsApp number, branding and connected accounts.
- Automated tests run on every change to the system. They check that one business can't read or change another's records.

## 2. Only the right people see the right things

- Your team members get access only to the sections you give them. Removing someone from your team also removes their sign-in.
- A staff member's own conversations with Vula, and their private notes, are visible to them only. A bank account marked private to one person stays out of the business's books and searches.
- Links you share with a client open only that client's project. They are long random codes that expire after 180 days, and you can switch them off at any time. Documents that mention another of your projects are left out of what the client sees and downloads.

## 3. Where your data is kept

| What | Where |
|---|---|
| Your records (orders, invoices, contacts, conversations) | Germany (Frankfurt), on Supabase |
| The Vula application and uploaded files | Netherlands (Amsterdam), on Railway |
| Your documents' searchable text (knowledge base) | Qdrant Cloud: **being moved from Brazil (São Paulo) to Germany (Frankfurt)** |
| Vula's own AI server | South Africa |
| WhatsApp messages | Meta (WhatsApp Business Platform) |

Countries in the European Union protect personal information to a standard at least equal to POPIA. That is the basis on which South African law allows information to be stored there.

## 4. How AI uses your information

- Vula answers from your own records and documents first. Most of its AI work runs on its own server in South Africa; in the last 24 hours before this check, about 70% of requests ran there.
- Some work goes to cloud AI models: when the local server is busy or unavailable, for reading scanned documents, and for some complex tasks. Every one of those requests is recorded with the reason it went to the cloud.
- Every cloud request asks the AI providers to keep nothing and train on nothing (zero data retention).
- Vula does not use your business's information to train AI models.

## 5. Your money figures are worked out by the system, not guessed by AI

- Amounts are stored as exact whole cents and calculated by the system itself. The AI never does the arithmetic.
- Each amount read from an invoice or statement is checked against the text of the document. If a figure can't be found in the document, it is not booked; it waits for you to check it.
- Payments from Yoco and PayFast are only accepted when their digital signature is valid, the amount covers what is owed, and the event is recent.
- A payment can't settle an invoice or order raised after it. A bank line that already paid something can't pay something else.
- Before Vula sends a payment link, messages your customers or changes your records, it shows you what it will do and waits for your yes. It then checks the change in the database before telling you it's done.

## 6. Passwords and keys

- Saved passwords and keys are encrypted before they are stored, with a separate encryption key that can be changed. This covers mailbox passwords, payment-provider keys and connected accounts such as Google, Microsoft, ClickUp, Shopify and Xero.
- No passwords or secret keys are kept in the dashboard, the website or the app.
- Every message Vula receives from WhatsApp, Yoco, PayFast, Shopify, WooCommerce or ClickUp is checked for its digital signature before Vula acts on it.

## 7. Your customers' rights under POPIA

- A customer can send **STOP** to stop marketing messages. This covers broadcasts, reminders, automations and property listings. **START** turns them back on.
- A customer can send **DELETE** to remove their conversations and records with your business. They can send **EXPORT** to get a copy of them.
- Old conversations are removed automatically:
  - conversations: 2 years after the last message;
  - message logs: 1 year;
  - unconverted leads: 1 year;
  - error reports: 90 days;
  - sign-in codes: 30 days.
- Accounting records are kept for 5 years, as tax law requires.
- When you connect a mailbox, Vula never reads folders named as personal, such as Personal, Private, Medical or Family.

## 8. Backups

- The database is backed up every day, and the last 7 days are kept.
- Every business's knowledge base is copied every day, and the last 7 copies are kept. The most recent copy, on 13 November, succeeded for every business.
- Uploaded files are kept on permanent storage.

## 9. How it's checked

- More than 8,000 automated tests run on every change, along with checks that every table is protected and that no secrets are in the apps. Nothing reaches the live system until they pass.
- The whole system was audited before launch on 9 and 13 November 2026. Every serious problem found in the code was fixed and tested.

## Still being finished

- Moving the knowledge base to Frankfurt (see section 3).
- Two-step sign-in for dashboard logins.
- A published PAIA manual and a named Information Officer.
- Round-the-clock error alerts to the Vula team.

## Questions

Information Officer: **[name, email, phone]**

For a copy of what Vula holds about you or your customers, or to have it removed, contact the person above, or send EXPORT or DELETE on WhatsApp.
