# Vula Privacy Policy (draft, November 2026)

> **DRAFT for review by the owner and a legal adviser — not yet published.**
> It replaces the live policy at `#/privacy` and the deletion page at `#/data-deletion`
> (`vula_dashboard/src/components/VulaPrivacy.jsx`, last updated 3 June 2026). Nothing here is live
> until the owner signs it off and the page is updated.
>
> **Placeholders to fill before publishing**
> - [Company legal name] — the live page says "Vula Group (Pty) Ltd". Confirm the exact registered name.
> - [Registration number]
> - [Physical address] — the live page says only "Cape Town, South Africa".
> - [Postal address]
> - [Information Officer name, email, phone] — must also be registered with the Information Regulator.
> - [Deputy Information Officer] — if one is appointed.
> - [Effective date]
> - Every item marked **[confirm]** or **[verify current contact]** in the text. They are listed again at the end.
>
> **How this draft was checked.** Each statement about the system comes from
> `docs/client-data-protection.md` (checked against the live system on 17 Nov 2026), the audits of
> 13 and 17 Nov 2026, `CLAUDE.md`, `vula_mind/config.py`, `vula_mind/vula/retention.py` and
> `vula_mind/vula/privacy_data.py`. Where those sources are silent, the text says **[confirm]**.

---

**Effective date:** [Effective date]

[Company legal name] ("Vula", "we", "us"), registration number [Registration number], runs Vula: an
AI business assistant that small businesses use on WhatsApp and in a web dashboard. This policy
explains what personal information we hold, why, where it is kept, who helps us process it, how long
we keep it, and what your rights are under the Protection of Personal Information Act 4 of 2013
(POPIA).

This policy is for two groups of people:

- **Businesses that use Vula**, and their owners and staff.
- **The customers, clients and contacts of those businesses**, who message a business on WhatsApp or
  deal with it in other ways that Vula supports.

## 1. Who is responsible for your information

POPIA treats two roles differently. Which one Vula has depends on whose information it is.

| Whose information | Who decides why and how it is used (responsible party) | Vula's role |
|---|---|---|
| A business's own account with Vula: its owners' and staff's names, numbers, emails, logins, plan and billing | Vula | **Responsible party** |
| A business's customers, clients, suppliers and contacts: their messages, orders, invoices, documents and records | **The business** | **Operator.** We process it on the business's behalf and on its instructions |

**If you are a customer of a business that uses Vula:** that business is responsible for your
information. Vula runs the system the business uses. Questions or requests about your information
should go to the business first. You can also use the WhatsApp words in section 9 directly, or
contact us. We will help the business answer you.

**If you are a business using Vula:** you are the responsible party for your customers'
information. You must have a lawful reason to process it and must tell your customers how you use it.
This policy explains what Vula does as your operator. [confirm: a written operator agreement under
POPIA s21 with each business — does one exist in the signup terms?]

## 2. Information we collect

### About businesses using Vula and their team
- Names, phone numbers and email addresses of owners and staff.
- Login details. Logins are handled by our database provider (section 6). If a person turns on
  two-step sign-in, a link to their authenticator app is stored as well.
- Each team member's role and which parts of the dashboard they can use.
- Plan, billing and AI usage records (for example the monthly AI allowance and top-ups).
- WhatsApp Business Account details needed to send and receive messages: the account ID, phone
  number ID, display name and an access token.
- Passwords and keys for accounts the business connects (for example a mailbox, Google, Microsoft,
  ClickUp, Shopify, Xero or a payment provider). These are encrypted before they are stored.
- Enquiries made to Vula through our website (website leads).

### About a business's customers and contacts (Vula as operator)
- WhatsApp phone number and display name.
- Messages sent to the business and the replies, including voice notes, photos and documents.
- Orders, quotes, invoices, payments, bookings and other business records.
- Contact details kept in the business's contact list.
- A short note about past conversations with a client, if the business uses this feature (section 4).
- Where relevant to the business: property enquiries, what a buyer is looking for, and listings sent
  to them.

### From connected mailboxes
If a business connects a mailbox, Vula reads new mail in it. It builds a contact list and files work
attachments (such as invoices and quotes) into the business's documents. It skips bulk and "no-reply"
mail. **It never reads folders whose names show they are personal**, such as Personal, Private,
Medical, Health or Family.

### Documents and files
- Documents a business uploads or sends (quotes, invoices, statements, plans). Their text is made
  searchable for that business only.
- Building plans uploaded for measuring quantities (take-off).
- Files a team member attaches in the dashboard chat. These are read, not filed, unless the person
  chooses to keep them.

### Technical information
- Error reports, sign-in requests and records that stop a message being handled twice.
- A record of each AI request: which model answered and, if it went to a cloud model, why. These
  records carry type labels, not the content of the message.

### Sensitive messages
If a message suggests someone may harm themselves, Vula replies with South African crisis helpline
numbers and tells one person at the business that someone on the line may need them. The business
gets the number, never the message. The log keeps no content.

## 3. How we use information

- To answer questions from the business's own records and documents.
- To take orders, send quotes and invoices, record payments and keep the books, when the business
  asks.
- To send messages the business asks for: order updates, reminders and (with the rules in section 9)
  marketing.
- To draft documents, estimates and reports for the business.
- To research prices and suppliers on the web when a team member asks (section 5).
- To improve answers for that business over time: for example, answers the business has approved
  are reused for similar questions. **We do not use any business's information to train AI models.**
- To run, secure and fix the service, and to bill for it.

We do not sell personal information. We do not use WhatsApp data for advertising.

## 4. What Vula remembers, and who can see it

- **Client notes.** If the business turns this on, Vula keeps one short, dated note per client about
  what was said in earlier conversations. It is written after a conversation goes quiet and shown to
  the assistant on the client's next message. It is never used as the source of an amount: orders,
  invoices and amounts are always read from the records. The note is deleted with the conversation
  (section 8) or when the client sends DELETE.
- **A staff member's own chats with Vula** are private to them. Colleagues, the owner and the Vula
  team's own screens do not show them.
- **Private notes.** A staff member can ask Vula to remember something about themselves. It is saved
  only when they ask, encrypted, and shown only to the assistant answering that person. They can see
  and delete it at any time. It is deleted with their data and when they leave the team.
- **Searching earlier chats.** When a staff member asks "what did we decide about…", Vula searches
  only that person's own chats with Vula, never a customer's or a colleague's.
- **Research notes.** When Vula researches something for a staff member, a clean, cited answer is kept
  so it can be reused by the team. It is shared with colleagues only if the person did not keep it
  private and it was written without their own chat or private notes in front of it. Colleagues never
  see the question as it was asked. Notes are reused for 30 days (prices, stock, suppliers) or 6
  months (other topics).
- **Chat attachments** are kept for 24 hours for the person who attached them, then deleted.
- **Client links.** A link a business shares with a client opens only that client's project. Links
  expire after 180 days and the business can switch them off.
- **Job costs** (supplier prices and pay rates) are visible to the business's owners and managers only.
- **Shared with other Vula businesses.** When a business confirms a build-up (how much material and
  labour goes into one unit of building work), the quantities are shared with other Vula businesses.
  No names, documents, prices or notes go with them.

## 5. How AI is used

- Vula answers from the business's own records and documents first.
- Most AI work runs on Vula's own server in South Africa. On 17 Nov 2026, about 64% of requests in the
  previous 24 hours ran there. This share changes from day to day.
- Some work goes to cloud AI models: when Vula's own server is busy or can't be reached, when its
  answer is not reliable enough, for reading scanned documents, and for some complex tasks. Every
  cloud request is recorded with the reason.
- **Every cloud AI request asks the provider to keep nothing and train on nothing (zero data
  retention).** Vula checks every 6 hours that each cloud model it uses is still available on these
  terms.
- **Voice notes** are transcribed on Vula's own server in South Africa first. If that server can't be
  reached, a cloud transcription service is used instead (section 6).
- **Web searches.** Before a search leaves Vula, it removes email addresses, phone, account and ID
  numbers, rand amounts and names written with a title (such as Mr or Mrs). Street addresses and names
  without a title can still get through.
- **Money is worked out by the system, not by AI.** Amounts are stored as exact cents and calculated
  in code. An amount read from a document is checked against the document's text. If it can't be
  found, it is not booked until a person checks it.
- **A person stays in control.** Before Vula sends a payment link, messages customers or changes
  records, it shows what it will do and waits for a yes. It then checks the change was made before
  saying it is done. [confirm with legal adviser: whether any Vula feature is an automated decision
  under POPIA s71]

## 6. Who helps us (sub-processors) and where your information is kept

We use these service providers to run Vula. Each receives only what it needs for its part of the
service.

| Provider | What it does for Vula | Where |
|---|---|---|
| **Supabase** | Main database (orders, invoices, contacts, conversations), logins, some file storage | Frankfurt, Germany |
| **Railway** | Runs the Vula application; stores uploaded files | Amsterdam, Netherlands |
| **Qdrant Cloud** | Searchable text of each business's documents (knowledge base) | **Currently São Paulo, Brazil. Being moved to Frankfurt, Germany.** |
| **Vercel** | Hosts the dashboard and the online shops (storefronts) | Server functions in Frankfurt, Germany. [confirm: where static pages are served from] |
| **Meta (WhatsApp Business Platform)** | Delivers WhatsApp messages | [confirm: Meta entity and location for the WhatsApp Cloud API] |
| **OpenRouter** | Routes cloud AI requests, under zero data retention | United States |
| **Anthropic (Claude)**, via OpenRouter | Cloud AI model, under zero data retention | United States [confirm: hosting region] |
| **Hosts of Meta's Llama models**, via OpenRouter | Cloud AI model for high-volume, lower-cost work, under zero data retention | [confirm: which hosts, e.g. SambaNova, and where] |
| **Serper** | Web search for research and market prices. Searches are cleaned first (section 5) | United States |
| **Groq** and **OpenAI** | Voice-note transcription, only as a fallback when Vula's own server can't be reached | United States [confirm: which are switched on in production, and their retention terms] |
| **Cloudflare** | Secure, access-controlled connection to Vula's own AI server in South Africa | [confirm: location] |
| **Yoco** | Card payments, when a customer pays a business | South Africa [confirm] |
| **PayFast** | Payment links | South Africa [confirm]. Currently switched off. [confirm before publishing] |
| **Resend** | Sends emails, such as a data export | [confirm: location]. Email notifications are currently switched off by design. [confirm whether to list it] |

**Vula's own AI server** is in South Africa. It is controlled by Vula and reached only through the
access-controlled connection above.

**Accounts a business connects itself.** A business can connect its own Google Drive, Microsoft
(OneDrive, Outlook, Dynamics 365), ClickUp, Shopify, WooCommerce, Xero or mailbox. Vula then reads
from (and, where the business allows, writes to) those accounts on the business's behalf. Those
providers hold the business's information under the business's own agreement with them. They do not
receive anything from Vula unless the business connects them.

[confirm: if error monitoring (Sentry, EU region) is switched on, add it to this table.]

### Sending information outside South Africa (POPIA section 72)
Some providers above are outside South Africa. We send information to them only on these bases:

- **Germany and the Netherlands (European Union).** These countries are bound by the EU's General Data
  Protection Regulation, a law that gives protection substantially similar to POPIA.
- **United States providers.** Each works under a contract with Vula (its terms of service and data
  processing terms) [confirm: a data processing agreement is in place with each]. Cloud AI requests
  also go with a zero data retention requirement, so the provider is asked not to store or train on
  them.
- **Brazil (Qdrant Cloud, until the move to Frankfurt is finished).** Under Qdrant's contract terms
  [confirm: data processing agreement and the basis for this transfer with the legal adviser].

[legal adviser: confirm these bases and whether POPIA s72(1)(b)–(e) should also be relied on.]

## 7. How we keep information safe

- **Each business's information is kept apart.** Every record is tagged with its business, and the
  database enforces this on every table (row-level security). Each business's documents are searched
  only for that business.
- **Access by role.** Team members see only the sections the business gives them. Removing someone
  from the team also removes their login.
- **Two-step sign-in** is available to every login: a 6-digit code from an app on their phone as well
  as their password. Once it is on, Vula refuses any session that didn't give the code.
- **Encryption.** Information is encrypted in transit and at rest. Saved passwords and keys are
  encrypted again with a separate key that can be changed. No passwords or secret keys are kept in the
  dashboard, the website or the app.
- **Checked messages.** Every message from WhatsApp, Yoco, PayFast, Shopify, WooCommerce or ClickUp is
  checked for its digital signature before Vula acts on it.
- **Backups.** The database is backed up daily and the last 7 days are kept. Each business's knowledge
  base is copied daily and the last 7 copies are kept.
- **Testing.** Thousands of automated tests run on every change, including checks that one business
  can't read another's records. The system was audited on 9, 13 and 17 November 2026.

If we become aware of a security compromise affecting your personal information, we will tell the
Information Regulator and the people affected as POPIA section 22 requires. For a business's customer
information, we will tell the business without delay so it can do the same. [confirm: incident
process and who notifies]

## 8. How long we keep information

Vula deletes information automatically when it is no longer needed. A daily job does this.

| Information | Kept for |
|---|---|
| Conversations, with their messages, ratings and the client note | 2 years after the **last** message |
| Message and question logs | 1 year |
| Website enquiries that did not become a customer | 1 year |
| Building plans uploaded for take-off | 90 days |
| Error reports | 90 days |
| WhatsApp sign-in requests | 30 days |
| Records that stop a message being handled twice | 30 days |
| Files attached to the dashboard chat | 1 day |
| Records of WhatsApp messages Vula sent (a preview and whether it was delivered) | 1 year |
| Customer questions passed to the business's team | 1 year |
| Dashboard chat history | 2 years |
| Emails waiting on a reply (preview and summary) | 1 year |
| Email contacts found in a connected mailbox | 2 years after the last email with them |
| Short extracts of sent emails used to learn the business's writing style | 1 year |
| AI routing records (which model was used and why; no message content) | 1 year |
| System health checks and answer-check reports | 180 days |
| Failed and processed delivery notices from other services | 90 days |
| Automation run log | 1 year |
| Research notes | Reused for 30 days or 6 months (section 4), deleted a month after that |
| Private notes | Until the person deletes them, asks for their data to be deleted, or leaves the team |
| **Accounting records** (invoices, quotes, payments, bank lines, expenses, payroll) | **At least 5 years**, as the Tax Administration Act (s29) requires |
| Emails a business sends a client from its project page | Kept like invoices, as a record of what was sent |
| Bookings, sales pipeline records and filed documents | Kept as the business's own records, until the business deletes them |
| Backups | 7 days |

Uploaded documents and files the business filed are the business's own records and are kept until it
deletes them, as above. Consent records (who agreed to or stopped messages) and audit trails are kept
for as long as the business uses Vula, so we can show what was agreed.

**When a business stops using Vula:** [confirm: what happens to the business's data and how long it
is kept. The 13 Nov audit notes there is no business-wide export or erase yet.]

## 9. Your rights and how to use them

Under POPIA you can:

- **ask what information is held about you**, and get a copy;
- **ask for it to be corrected** if it is wrong;
- **ask for it to be deleted**, unless the law requires it to be kept;
- **object** to it being used, including for direct marketing;
- **complain to the Information Regulator** (below).

### On WhatsApp (works on any business's Vula number)

| Send | What happens |
|---|---|
| **STOP** | No more marketing or reminder messages from that business, including broadcasts, automations and property listings. Repeat orders are paused. Nothing is deleted. |
| **START** | Turns those messages back on. |
| **DELETE** | Your conversations and records with that business are deleted straight away, and the reply tells you what was removed. |
| **EXPORT** | A copy of your conversations and your own orders and invoices with that business is emailed to the address on file. [confirm: this needs the email service switched on; until then the reply asks you to email us] |

Each word applies to the business whose number you sent it to. If you deal with more than one
business on Vula, send it to each.

**What DELETE does not remove, on purpose:**
- Orders, invoices and payments. A business must keep its financial records by law (section 8).
- Emails a business sent you from a project page. These are kept like invoices.
- Bookings and sales pipeline records, which are the business's own records.
- Your number on a **do-not-contact list**, so you are never sent marketing again even if you message
  later.

[confirm with owner: whether order updates and deposit requests should continue after STOP. Today
they are treated as service messages and continue.]

### By email
Email our Information Officer at [Information Officer name, email, phone], or hello@vula.co.za. Say
what you want and give the phone number or email address the business has for you. We may ask you to
confirm who you are before we act.

We will reply within [confirm: response time, e.g. 30 days]. If we can't act on a request (for
example because the law requires a record to be kept), we will tell you why.

### Complaints to the Information Regulator
If you are not happy with how we have handled your information, you can complain to the Information
Regulator of South Africa:

- Email: complaints.IR@justice.gov.za [verify current contact]
- Website: https://inforegulator.org.za [verify current contact]

## 10. WhatsApp Business Platform and connecting a business's account

Vula is a technology provider on the WhatsApp Business Platform (Meta). A business connects its own
WhatsApp Business Account to Vula through Meta's Embedded Signup. With the business's permission, we
store only what is needed to run its messaging: the account ID, phone number ID, display name and an
access token. We use these only to send and receive messages for that business and to receive its
messages.

We do not use WhatsApp data for advertising and do not sell it. We share it only with the providers in
section 6. Our use follows Meta's Platform Terms and the WhatsApp Business Messaging Policy. A business
can disconnect at any time (Settings > WhatsApp). This revokes the token and stops all messaging at
once.

## 11. Marketing messages

Businesses on Vula can send broadcasts and reminders on WhatsApp. Anyone can stop them at any time by
sending STOP. Outside WhatsApp's 24-hour window, a business can only send messages using a template
Meta has approved. [legal adviser: confirm the consent basis for marketing to a business's customers
under POPIA s69 and how it is described here.]

## 12. Children

[confirm: Vula is aimed at businesses and does not knowingly collect children's information. A
business's customers may include children; state how this is handled.]

## 13. Changes to this policy

We will update this policy when the service changes. The date at the top shows when it last changed.
We will tell businesses using Vula about important changes. [confirm: how — email, dashboard notice]

## 14. Contact us

[Company legal name], registration number [Registration number]
Physical address: [Physical address]
Postal address: [Postal address]
Information Officer: [Information Officer name, email, phone]
Deputy Information Officer: [Deputy Information Officer]
General email: hello@vula.co.za

Our PAIA manual explains how to ask for records we hold: [link to the PAIA manual once published].

---

## Appendix: replacement text for the Data Deletion page (`#/data-deletion`)

The live page says "We process deletion requests within 30 days." That promise had no job behind it
(`vula_mind/vula/privacy_data.py`). DELETE on WhatsApp now works straight away. Proposed text:

> **Deleting your data**
>
> You can ask for your personal information to be deleted at any time, under POPIA.
>
> **On WhatsApp:** send **DELETE** to the business's Vula number. Your conversations and records with
> that business are deleted straight away, and the reply tells you what was removed. If something
> can't be removed at once, the reply says so, and we finish it by hand and confirm to you.
>
> **By email:** write to [Information Officer email] or hello@vula.co.za, subject "Data deletion
> request", with the phone number or email the business has for you. We will confirm within [confirm:
> response time].
>
> **What is deleted:** your conversations and message history, the business's note about your
> earlier conversations, open questions and escalations, your contact record, broadcasts and messages
> sent to you, property enquiries and listings sent to you, and anything you asked Vula to remember
> about you (for team members, also your research notes and chat attachments).
>
> **What is kept, and why:** orders, invoices and payments (a business must keep financial records for
> at least 5 years); emails a business sent you from a project page (kept like invoices); bookings and
> sales pipeline records (the business's own records); and your number on a do-not-contact list, so
> you are never sent marketing again. Backups are overwritten within 7 days.
>
> **Businesses:** to remove your WhatsApp Business Account connection, disconnect in Settings >
> WhatsApp. This revokes the access token at once. To close your whole account, email us. [confirm:
> account closure process]

---

## Review list: everything to fill or confirm

**Placeholders:** [Company legal name], [Registration number], [Physical address], [Postal address],
[Information Officer name, email, phone], [Deputy Information Officer], [Effective date], link to the
PAIA manual.

**[confirm] items:**
1. Operator agreement (POPIA s21) with each business in the signup terms.
2. Whether any feature is an automated decision under POPIA s71.
3. Vercel: where static pages are served from.
4. Meta: contracting entity and location for the WhatsApp Cloud API.
5. Anthropic: hosting region.
6. Llama hosts: which providers and where.
7. Groq and OpenAI: whether switched on in production, and their retention terms.
8. Cloudflare: location.
9. Yoco and PayFast: country; PayFast currently off.
10. Resend: location, and whether to list it while switched off.
11. Sentry: add if switched on.
12. A data processing agreement with each US provider and with Qdrant; the legal basis for the Brazil
    transfer.
13. Which POPIA s72 bases to rely on (legal adviser).
14. Incident and breach notification process (s22).
15. (Done 18 Nov 2026: retention periods for outbound messages, escalations, telemetry, email tables; uploads are kept as business records.)
16. What happens to a business's data when it leaves Vula.
17. EXPORT needs the email service switched on.
18. Whether order updates and deposits continue after STOP.
19. Response time for emailed requests.
20. Consent basis for marketing (POPIA s69).
21. Children.
22. How businesses are told about policy changes.

**[verify current contact]:** the Information Regulator's complaints email and website.
