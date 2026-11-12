# WhatsApp template: enquiry received

**Why it's needed.** Someone who fills in a business's website enquiry form gives their WhatsApp
number but hasn't messaged the business. Meta only allows free-form messages within 24 hours of
the person's *own* last message, so neither Vula nor the agent can reach them there first. An
approved template can. When the person replies to it, the normal 24-hour conversation opens and
the agent (or Vula) can carry on.

This is one template for **every** kind of business (estate agency, builder, salon…), so it's
approved once per WhatsApp Business Account and reused.

## Submit in Meta Business Manager → WhatsApp Manager → Message templates

| Field | Value |
|---|---|
| Name | `enquiry_received` |
| Category | **Utility** (it confirms something the person just did — not marketing) |
| Language | English (`en`) — add `af`, `zu`, `xh` copies later under the same name if wanted |
| Header | none |
| Footer | `Reply STOP to opt out` |
| Buttons | none |

**Body**

```
Hi {{1}}, thanks for your enquiry with {{2}} about {{3}}. An agent will be in touch shortly — you can reply here with any questions in the meantime.
```

**Sample values** (Meta asks for these when you submit):

| Variable | Meaning | Sample |
|---|---|---|
| `{{1}}` | first name | Thandi |
| `{{2}}` | business name | Oak & Stone Properties |
| `{{3}}` | what they asked about | 4 Bedroom family home with pool |

Why this wording tends to be approved as Utility: it names the person's own action ("your
enquiry"), contains no offer, price or promotion, and has no marketing call to action.

## Switch it on in Vula

1. Wait until the template shows **Active** in WhatsApp Manager (for every business number that
   should use it — templates belong to a WhatsApp Business Account).
2. Set `WHATSAPP_ENQUIRY_TEMPLATE=enquiry_received` (and `WHATSAPP_ENQUIRY_TEMPLATE_LANG=en` if
   different) on Railway, then `railway up` — env changes alone don't redeploy.

From then on, a website enquiry with a WhatsApp number gets the template straight away
(`vula/listings/service.py::_acknowledge`). Not sent when: the setting is empty, the enquiry came
in on WhatsApp (already a conversation), or no WhatsApp number was given. A refused send is
logged as an error and the enquiry is still stored and the agent still alerted.

**POPIA:** the person gave their number on the form to be contacted about this enquiry, so one
acknowledgement is in scope. Opt-outs ("STOP") are handled by Vula's existing suppression.
