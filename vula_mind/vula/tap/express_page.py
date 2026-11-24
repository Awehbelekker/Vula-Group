"""The express-pay page a tap opens (vula/tap/express.py). One small HTML page, no framework:
amount, tip buttons and Pay, sized for a phone held at a counter. The total shown while choosing
is worked out in the browser for display only; the server prices the payment again on Pay."""
from __future__ import annotations

from html import escape

from vula.tap.core import money as mo

_CSS = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:#f4f4f2;color:#1b1b1b}main{max-width:26rem;margin:0 auto;padding:1.25rem 1rem 2rem}
h1{font-size:1.15rem;margin:.25rem 0 0}.who{color:#555;margin:.2rem 0 1rem}.amt{font-size:2.6rem;
font-weight:700;margin:.25rem 0 1rem}.what{color:#555}.test{background:#fff4d6;border:1px solid #e8c766;
padding:.6rem .8rem;border-radius:.6rem;margin-bottom:1rem}.err{background:#fde8e8;border:1px solid #e9a3a3;
padding:.6rem .8rem;border-radius:.6rem;margin-bottom:1rem}.grid{display:grid;grid-template-columns:1fr 1fr;
gap:.6rem}.opt{display:block}.opt input{position:absolute;opacity:0}.opt span{display:flex;flex-direction:column;
justify-content:center;min-height:4.4rem;text-align:center;padding:.6rem .5rem;border:2px solid #d6d6d2;border-radius:.8rem;background:#fff;font-weight:600}
.opt small{display:block;font-weight:400;color:#666}.opt input:checked+span{border-color:#1b1b1b;
background:#1b1b1b;color:#fff}.opt input:checked+span small{color:#ddd}label.l{display:block;margin:1rem 0 .35rem;
font-weight:600}input.n{width:100%;font-size:1.3rem;padding:.75rem;border:2px solid #d6d6d2;border-radius:.8rem}
button{width:100%;margin-top:1.25rem;padding:1.05rem;font-size:1.15rem;font-weight:700;border:0;
border-radius:.9rem;background:#1b1b1b;color:#fff}button.alt{background:#fff;color:#1b1b1b;border:2px solid #1b1b1b}.foot{color:#666;font-size:.85rem;margin-top:1rem;
text-align:center}.foot a{color:#444}
"""

_JS = """
(function(){var f=document.getElementById('f');if(!f)return;var b=document.getElementById('pay');
var base=+f.dataset.bill||0;function cents(v){v=(v||'').replace(/[^0-9.,]/g,'').replace(',','.');
var n=parseFloat(v);return isNaN(n)?0:Math.round(n*100)}function rands(c){return 'R '+(c/100).toFixed(2)
.replace(/\\B(?=(\\d{3})+(?!\\d))/g,' ')}function upd(){var bill=base,tip=0,amt=f.elements['amount'];
if(amt&&amt.type!=='hidden'&&f.dataset.mode==='open')bill=cents(amt.value);var t=f.querySelector('input[name=tip]:checked');
if(f.dataset.mode==='quick'){var q=f.querySelector('input[name=amount]:checked');tip=q?(q.value==='other'?
cents(f.elements['amount_other'].value):+q.value):0;}else if(t){if(t.value==='custom')tip=cents(f.elements['custom_tip'].value);
else tip=Math.round(bill*(+t.value)/100);}var c=document.getElementById('custom');if(c)c.style.display=
(t&&t.value==='custom')?'block':'none';var o=document.getElementById('other');if(o){var q2=f.querySelector(
'input[name=amount]:checked');o.style.display=(q2&&q2.value==='other')?'block':'none'}var tot=bill+tip;
b.textContent=tot>0?'Pay '+rands(tot):'Pay'}f.addEventListener('input',upd);f.addEventListener('change',upd);
f.addEventListener('submit',function(){b.disabled=true;b.textContent='Opening secure payment\\u2026'});upd()})();
"""


def _n(nonce: str) -> str:
    """The CSP nonce attribute for an inline script (vula/tap/api.py sets the matching header)."""
    return f" nonce='{escape(nonce)}'" if nonce else ""


def _r(cents: int) -> str:
    return mo.format_rands(cents)


def _opt(name: str, value: str, label: str, small: str = "", checked: bool = False) -> str:
    return (f"<label class=opt><input type=radio name={name} value='{escape(value)}'"
            f"{' checked' if checked else ''}><span>{escape(label)}"
            f"{f'<small>{escape(small)}</small>' if small else ''}</span></label>")


def _tips(bill_cents: int, presets: list) -> str:
    opts = [_opt("tip", "0", "No tip", checked=True)]
    opts += [_opt("tip", str(p), f"{p}%", _r(mo.pct_tip(bill_cents, p * 100)) if bill_cents else "")
             for p in presets]
    opts.append(_opt("tip", "custom", "Other"))
    return (f"<label class=l>Add a tip?</label><div class=grid>{''.join(opts)}</div>"
            "<div id=custom style='display:none'><label class=l>Tip in rands</label>"
            "<input class=n name=custom_tip inputmode=decimal placeholder='e.g. 20'></div>")


_TABLE_CSS = """
.svc{margin-top:1.5rem;padding-top:1.1rem;border-top:1px solid #d6d6d2}.svc h2{font-size:1rem;margin:0 0 .6rem}
.svc form{margin:0}.svc button{margin-top:.55rem}.ok{background:#e6f4ea;border:1px solid #a9d3b6;padding:.6rem .8rem;
border-radius:.6rem;margin:.5rem 0}.wait{background:#fff4d6;border:1px solid #e8c766;padding:.6rem .8rem;border-radius:.6rem;margin:.5rem 0}
.lines{list-style:none;margin:.25rem 0 1rem;padding:0}.lines li{display:flex;justify-content:space-between;gap:.75rem;
padding:.35rem 0;border-bottom:1px solid #e4e4e0}.lines span:first-child{min-width:0;overflow-wrap:anywhere}
.mi{display:flex;gap:.75rem;align-items:center;padding:.7rem 0;border-bottom:1px solid #e4e4e0}.mi img{flex:none;width:3.4rem;
height:3.4rem;border-radius:.6rem;object-fit:cover;background:#e4e4e0}.mi .t{flex:1 1 auto;min-width:0;overflow-wrap:anywhere}
.mi small{display:block;color:#666}.mi input{flex:none;width:4.2rem;font-size:1.15rem;padding:.55rem .4rem;text-align:center;
border:2px solid #d6d6d2;border-radius:.7rem}h2.cat{font-size:1rem;margin:1.2rem 0 .1rem;color:#444}
textarea{width:100%;font:inherit;padding:.7rem;border:2px solid #d6d6d2;border-radius:.8rem;margin-top:.35rem}
a.btn{display:block;text-decoration:none}
"""

_SAID = {"waiter": "a waiter", "bill": "the bill", "machine": "the card machine"}


def _table(code: str, t: dict) -> str:
    """Service at the table (vula/tap/tableside.py): what this table has asked for, what it has
    ordered, and the buttons to ask."""
    if not t:
        return ""
    parts = []
    for c in t.get("calls") or []:
        what = _SAID.get(c["kind"], "someone")
        if c["status"] == "accepted":
            parts.append(f"<div class=ok>{escape(c.get('by') or 'Your waiter')} is on the way with {what}.</div>"
                         if c["kind"] != "waiter" else f"<div class=ok>{escape(c.get('by') or 'Your waiter')} is on the way.</div>")
        else:
            parts.append(f"<div class=wait>We've asked for {what}. Someone will be with you shortly.</div>")
    for o in t.get("orders") or []:
        if o["status"] == "pending":
            parts.append(f"<div class=wait>Order sent: {escape(o['summary'])}. Waiting for your waiter to confirm it.</div>")
        elif o["status"] == "confirmed":
            parts.append(f"<div class=ok>Order confirmed: {escape(o['summary'])}.</div>")
        else:
            parts.append(f"<div class=err>Your order ({escape(o['summary'])}) couldn't be taken"
                         f"{': ' + escape(o['reason']) if o.get('reason') else ''}. Please ask your waiter.</div>")
    asked = {c["kind"] for c in t.get("calls") or []}
    buttons = ""
    if t.get("orders_on"):
        buttons += f"<a class=btn href='/t/{escape(code)}/menu'><button type=button>See the menu and order</button></a>"
    if t.get("calls_on"):
        for kind, label in (("waiter", "Call a waiter"), ("bill", "Ask for the bill"), ("machine", "Bring the card machine")):
            if kind not in asked:
                buttons += (f"<form method=post action='/t/{escape(code)}/call'><input type=hidden name=kind value={kind}>"
                            f"<button type=submit class=alt>{label}</button></form>")
    head = f"<h2>{escape(t['label'])}</h2>" if t.get("label") else "<h2>Need something?</h2>"
    return f"<div class=svc>{head}{''.join(parts)}{buttons}</div>"


def _lines(items: list) -> str:
    if not items:
        return ""
    return "<ul class=lines>" + "".join(
        f"<li><span>{int(i['quantity'])} x {escape(str(i['description']))}</span><span>{_r(int(i['line_cents']))}</span></li>"
        for i in items) + "</ul>"


_MENU_JS = """
(function(){var f=document.getElementById('m');if(!f)return;var b=document.getElementById('send');
function rands(c){return 'R '+(c/100).toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g,' ')}
function upd(){var n=0,c=0;[].forEach.call(f.querySelectorAll('input[data-c]'),function(i){var q=parseInt(i.value,10)||0;
if(q<0){q=0;i.value=''}n+=q;c+=q*(+i.dataset.c)});b.disabled=n===0;b.textContent=n?('Send order \u00b7 '+rands(c)):'Choose something'}
f.addEventListener('input',upd);f.addEventListener('submit',function(){b.disabled=true;b.textContent='Sending\u2026'});upd()})();
"""


def menu(code: str, merchant: str, label: str, items: list, error: str = "", nonce: str = "") -> str:
    """The menu a table tag opens: every item with its price and a quantity box, a note, Send.
    The total on the button is for display; the server prices the order from its own records."""
    err = f"<div class=err>{escape(error)}</div>" if error else ""
    rows, cat = "", None
    for m in items:
        if m["category"] != cat:
            cat = m["category"]
            rows += f"<h2 class=cat>{escape(cat)}</h2>"
        img = f"<img src='{escape(m['image'])}' alt='' loading=lazy>" if m.get("image") else ""
        rows += (f"<label class=mi>{img}<span class=t><strong>{escape(m['name'])}</strong>"
                 f"<small>{_r(int(m['price_cents']))}{' · ' + escape(m['description']) if m.get('description') else ''}</small></span>"
                 f"<input type=number inputmode=numeric min=0 max=20 name='q_{escape(m['id'])}' data-c={int(m['price_cents'])} "
                 f"placeholder=0 aria-label='How many {escape(m['name'])}'></label>")
    body = (f"<h1>{escape(merchant)}</h1><p class=who>{escape(label)}</p>{err}"
            + (f"<form id=m method=post action='/t/{escape(code)}/order'>{rows}"
               "<label class=l>Anything to add? (optional)</label>"
               "<textarea name=note rows=2 maxlength=200 placeholder='No onions, extra ice'></textarea>"
               "<button id=send type=submit>Send order</button></form>"
               "<p class=foot>Your waiter confirms the order before it goes on your bill.</p>"
               if items else "<p>The menu isn't loaded yet. Please ask your waiter.</p>")
            + f"<p class=foot><a href='/t/{escape(code)}'>Back</a></p>")
    return (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Menu · {escape(merchant)}</title><style>{_CSS}{_TABLE_CSS}</style><main>{body}</main>"
            f"<script{_n(nonce)}>{_MENU_JS}</script></html>")


def render(code: str, v: dict, error: str = "", nonce: str = "") -> str:
    """The page for one view() result (kinds bill / quick_tip / open_amount / tabs / locked / no_bill)."""
    merchant, staff, kind = v.get("merchant") or "", v.get("staff") or "", v["kind"]
    head = (f"<h1>{escape(merchant)}</h1>"
            + (f"<p class=who>{'Tip for' if kind == 'quick_tip' else 'Served by'} {escape(staff)}</p>"
               if staff else "<p class=who></p>"))
    err = f"<div class=err>{escape(error)}</div>" if error else ""
    tab_links = ""
    if v.get("open_tab_url") or v.get("pay_tab_url"):
        tab_links = (
            (f"<a href='{escape(v['open_tab_url'])}'><button type=button>Open a tab</button></a>"
             if v.get("open_tab_url") else "")
            + (f"<a href='{escape(v['pay_tab_url'])}'><button type=button class=alt>Pay my tab</button></a>"
               if v.get("pay_tab_url") else "")
            + "<p class=foot>Tabs open in WhatsApp, under your number. Order as you go and pay when "
              "you leave.</p>")
    if kind == "tabs":
        body = f"{head}{err}<p>Run a tab: order as you go, pay when you leave.</p>{tab_links}"
    elif kind in ("locked", "no_bill"):
        msg = ("This bill is being paid on another phone. Ask staff to release it."
               if kind == "locked" else "There's nothing to pay on this tag right now. Ask staff to add your bill.")
        body = f"{head}{err}<p>{msg}</p>{tab_links}"
    else:
        hidden = f"<input type=hidden name=token value='{escape(v['token'])}'>"
        if kind == "bill":
            bill = int(v.get("bill_cents") or 0)
            test = ("<div class=test>Test payment. Nothing is booked to anyone.</div>" if v.get("is_test") else "")
            fields = (f"{test}<div class=what>{escape(v.get('description') or 'Your bill')}</div>"
                      f"<div class=amt>{_r(bill)}</div>{_lines(v.get('items') or [])}{_tips(bill, v['tip_presets'])}")
            mode, data_bill = "bill", bill
        elif kind == "quick_tip":
            opts = [_opt("amount", str(c), _r(c).replace(".00", ""), checked=(i == 1))
                    for i, c in enumerate(v["quick_presets"])]
            opts.append(_opt("amount", "other", "Other"))
            fields = (f"<label class=l>How much?</label><div class=grid>{''.join(opts)}</div>"
                      "<div id=other style='display:none'><label class=l>Amount in rands</label>"
                      "<input class=n name=amount_other inputmode=decimal placeholder='e.g. 15'></div>")
            mode, data_bill = "quick", 0
        else:                                             # open_amount
            fields = ("<label class=l>Amount to pay (rands)</label>"
                      "<input class=n name=amount inputmode=decimal required placeholder='e.g. 250'>"
                      f"{_tips(0, v['tip_presets'])}")
            mode, data_bill = "open", 0
        body = (f"{head}{err}<form id=f method=post action='/t/{escape(code)}/pay' data-mode={mode} "
                f"data-bill={data_bill}>{hidden}{fields}<button id=pay type=submit>Pay</button></form>"
                f"<p class=foot>Secure payment on {escape(v.get('pay_with') or 'PayFast')}.<br>"
                f"<a href='/t/{escape(code)}?via=whatsapp'>Rather pay in WhatsApp</a></p>")
    table = v.get("table")
    if table and kind == "no_bill":
        # a table with nothing to pay yet: the page is for ordering and calling, not an error
        body = f"{head}{err}<p>Nothing to pay yet.</p>{tab_links}"
    body += _table(code, table)
    return (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Pay {escape(merchant)}</title><style>{_CSS}{_TABLE_CSS if table else ''}</style><main>{body}</main>"
            f"<script{_n(nonce)}>{_JS}</script></html>")


_POLL_JS = """
(function(){var st=document.body.dataset.status,wa=document.body.dataset.wa,n=0,msg=document.getElementById('msg');
function tick(){n++;fetch(st,{credentials:'same-origin',cache:'no-store'}).then(function(r){return r.json()})
.then(function(d){if(d.paid&&d.receipt_url){location.replace(d.receipt_url+(wa?'?wa='+encodeURIComponent(wa):''));return}
if(d.paid){msg.textContent='Payment received. Thank you!';return}if(d.failed){msg.textContent=
"That payment didn't go through. You haven't been charged. Tap the tag to try again.";return}
if(n<60)setTimeout(tick,1500);else msg.textContent='Still confirming. Your slip will come on WhatsApp if you ask for it below.'})
.catch(function(){if(n<60)setTimeout(tick,2000)})}tick()})();
"""


def done(slip_url: str | None, status_url: str = "", nonce: str = "", pay_with: str = "PayFast") -> str:
    """Waits for PayFast's confirmation, then opens the animated printed slip (/r/<token>)."""
    slip = (f"<p><a href='{escape(slip_url)}'><button type=button>Get my slip on WhatsApp</button></a></p>"
            if slip_url else "<p>Your slip will arrive in WhatsApp as soon as the payment is confirmed.</p>")
    data = (f" data-status='{escape(status_url)}' data-wa='{escape(slip_url or '')}'" if status_url else "")
    return (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>Thank you</title>"
            f"<style>{_CSS}</style><body{data}><main><h1>Thank you</h1>"
            f"<p class=who id=msg>Confirming your payment with {escape(pay_with)}\u2026</p>"
            f"{slip}<p class=foot>Your slip opens here as soon as it is confirmed.</p></main>"
            f"{'<script' + _n(nonce) + '>' + _POLL_JS + '</script>' if status_url else ''}</html>")
