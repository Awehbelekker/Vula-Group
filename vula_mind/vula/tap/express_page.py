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
border-radius:.9rem;background:#1b1b1b;color:#fff}.foot{color:#666;font-size:.85rem;margin-top:1rem;
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


def render(code: str, v: dict, error: str = "") -> str:
    """The page for one view() result (kinds bill / quick_tip / open_amount / locked / no_bill)."""
    merchant, staff, kind = v.get("merchant") or "", v.get("staff") or "", v["kind"]
    head = (f"<h1>{escape(merchant)}</h1>"
            + (f"<p class=who>{'Tip for' if kind == 'quick_tip' else 'Served by'} {escape(staff)}</p>"
               if staff else "<p class=who></p>"))
    err = f"<div class=err>{escape(error)}</div>" if error else ""
    if kind in ("locked", "no_bill"):
        msg = ("This bill is being paid on another phone. Ask staff to release it."
               if kind == "locked" else "There's nothing to pay on this tag right now. Ask staff to add your bill.")
        body = f"{head}{err}<p>{msg}</p>"
    else:
        hidden = f"<input type=hidden name=token value='{escape(v['token'])}'>"
        if kind == "bill":
            bill = int(v.get("bill_cents") or 0)
            test = ("<div class=test>Test payment. Nothing is booked to anyone.</div>" if v.get("is_test") else "")
            fields = (f"{test}<div class=what>{escape(v.get('description') or 'Your bill')}</div>"
                      f"<div class=amt>{_r(bill)}</div>{_tips(bill, v['tip_presets'])}")
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
                "<p class=foot>Secure payment on PayFast.<br>"
                f"<a href='/t/{escape(code)}?via=whatsapp'>Rather pay in WhatsApp</a></p>")
    return (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Pay {escape(merchant)}</title><style>{_CSS}</style><main>{body}</main>"
            f"<script>{_JS}</script></html>")


def done(slip_url: str | None) -> str:
    slip = (f"<p><a href='{escape(slip_url)}'><button type=button>Get my slip on WhatsApp</button></a></p>"
            if slip_url else "<p>Your slip will arrive in WhatsApp as soon as the payment is confirmed.</p>")
    return (f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>Thank you</title>"
            f"<style>{_CSS}</style><main><h1>Thank you</h1><p class=who>Your payment is being confirmed.</p>"
            f"{slip}<p class=foot>You can close this page.</p></main></html>")
