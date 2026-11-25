"""
vula/api/product_guides.py — product guides a business sends its clients (vula/commerce/product_guides.py).

Dashboard (mounted at /v1/commerce, tenant-guarded):
    GET    /{tenant}/admin/product-guides                 the business's guides, with their links
    POST   /{tenant}/admin/product-guides                 {title, client_name, intro, categories,
                                                           product_ids, show_prices}
    PATCH  /{tenant}/admin/product-guides/{id}            the same fields, and `active`
    POST   /{tenant}/admin/product-guides/{id}/new-link   the old link stops working
    DELETE /{tenant}/admin/product-guides/{id}

Reading spec sheets out of a maker's catalogue PDF (vula/commerce/spec_import.py):
    POST   /{tenant}/admin/product-guides/spec-import             the PDF (multipart `file`) → {id}; read in the background
    GET    /{tenant}/admin/product-guides/spec-import             the latest reads
    GET    /{tenant}/admin/product-guides/spec-import/{id}        progress, then what was found
    POST   /{tenant}/admin/product-guides/spec-import/{id}/apply  {picks: [{key, product_id} | {key, create: {category}}]}

Public (no prefix): GET /guide/{token} — the guide page. White-labelled: the business's own
name, logo and colour; Vula only as the small credit at the foot, as on the shop page. Never
indexed, no referrer sent on. Every failure is the same 404.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["product-guides"])        # mounted at /v1/commerce
public = APIRouter(tags=["product-guides"])        # no prefix


def _who(request: Request) -> dict:
    from vula.api.commerce import _actor
    return {"email": _actor(request)}


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/{tenant_id}/admin/product-guides")
async def guides_list(tenant_id: str):
    from vula.commerce import product_guides as pg
    return {"guides": pg.list_guides(tenant_id)}


@router.post("/{tenant_id}/admin/product-guides")
async def guides_create(tenant_id: str, body: dict, request: Request):
    from vula.commerce import product_guides as pg
    try:
        return pg.create(tenant_id, body or {}, actor=_who(request))
    except pg.GuideError as exc:
        raise _bad(exc) from exc


@router.patch("/{tenant_id}/admin/product-guides/{guide_id}")
async def guides_update(tenant_id: str, guide_id: str, body: dict, request: Request):
    from vula.commerce import product_guides as pg
    try:
        return pg.update(tenant_id, guide_id, body or {}, actor=_who(request))
    except pg.GuideError as exc:
        raise _bad(exc) from exc


@router.post("/{tenant_id}/admin/product-guides/{guide_id}/new-link")
async def guides_new_link(tenant_id: str, guide_id: str, request: Request):
    from vula.commerce import product_guides as pg
    try:
        return pg.renew(tenant_id, guide_id, actor=_who(request))
    except pg.GuideError as exc:
        raise _bad(exc) from exc


@router.delete("/{tenant_id}/admin/product-guides/{guide_id}")
async def guides_delete(tenant_id: str, guide_id: str, request: Request):
    from vula.commerce import product_guides as pg
    try:
        pg.delete(tenant_id, guide_id, actor=_who(request))
    except pg.GuideError as exc:
        raise _bad(exc) from exc
    return {"deleted": guide_id}


# ── spec sheets from a catalogue PDF ──────────────────────────────────────────────────────────

_RUNNING: set = set()      # the background reads, kept referenced until they finish


@router.post("/{tenant_id}/admin/product-guides/spec-import")
async def spec_import_start(tenant_id: str, request: Request, file: UploadFile = File(...)):
    import asyncio
    from vula.commerce import spec_import as si
    data = await file.read()
    try:
        row = await asyncio.to_thread(si.start, tenant_id, data, file.filename or "catalogue.pdf", _who(request))
    except si.ImportError_ as exc:
        raise _bad(exc) from exc
    pages = row.pop("_pages")
    task = asyncio.create_task(si.run(tenant_id, row["id"], pages))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return {"id": row["id"], "status": row["status"], "pages": row["pages"], "file_name": row["file_name"]}


@router.get("/{tenant_id}/admin/product-guides/spec-import")
async def spec_import_list(tenant_id: str):
    from vula.commerce import spec_import as si
    return {"imports": si.latest(tenant_id)}


@router.get("/{tenant_id}/admin/product-guides/spec-import/{import_id}")
async def spec_import_get(tenant_id: str, import_id: str):
    from vula.commerce import spec_import as si
    try:
        return si.get(tenant_id, import_id)
    except si.ImportError_ as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{tenant_id}/admin/product-guides/spec-import/{import_id}/apply")
async def spec_import_apply(tenant_id: str, import_id: str, body: dict, request: Request):
    from vula.commerce import spec_import as si
    try:
        return await si.apply(tenant_id, import_id, (body or {}).get("picks") or [], actor=_who(request))
    except si.ImportError_ as exc:
        raise _bad(exc) from exc


# ── the page ──────────────────────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    import html
    return html.escape(str(s or ""), quote=True)


def _accent(raw: str) -> str:
    a = (raw or "").strip()
    if a and not a.startswith("#"):
        a = f"#{a}"
    return a if re.fullmatch(r"#[0-9a-fA-F]{6}", a) else "#2C5545"


def _ink_on(hex_colour: str) -> str:
    """White or near-black text on the business's colour, whichever reads (WCAG luminance)."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#FFFFFF" if (1.05 / (lum + 0.05)) >= 4.5 else "#1B1A17"


def _not_found() -> HTMLResponse:
    return HTMLResponse("<!doctype html><meta charset=utf-8><meta name=robots content=noindex>"
                        "<title>Not found</title><p style='font-family:system-ui;padding:40px;text-align:center'>"
                        "This guide isn't available. Ask whoever sent it for a new link.</p>",
                        status_code=404, headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


@public.get("/guide/{token}", response_class=HTMLResponse)
async def guide_page(token: str, request: Request) -> HTMLResponse:
    from vula.commerce import powered_by, product_guides as pg, service, shop_stats, web_basket
    guide = pg.by_token(token)
    if not guide:
        return _not_found()
    tenant_id = guide["tenant_id"]
    if not shop_stats.is_bot(request.headers.get("user-agent")):
        pg.record_view(guide)
    try:
        inv = await service.get_invoice_settings(tenant_id) or {}
    except Exception:  # noqa: BLE001
        inv = {}
    shop = (inv.get("trading_as") or inv.get("company_name") or tenant_id.replace("_", " ").replace("-", " ").title())
    accent = _accent(inv.get("accent_color") or "")
    logo = inv.get("logo_url") or ""
    logo = logo if isinstance(logo, str) and logo.startswith("https://") else ""
    number = web_basket.wa_number(tenant_id)
    data = pg.page_data(guide)
    data.update({"shop": shop, "wa": number})
    blob = json.dumps(data).replace("<", "\\u003c")
    page = (_PAGE.replace("{{TITLE}}", _esc(f"{data['title']} — {shop}"))
            .replace("{{ACCENT}}", accent).replace("{{ON_ACCENT}}", _ink_on(accent))
            .replace("{{SHOP}}", _esc(shop))
            .replace("{{LOGO}}", f'<img class="logo" src="{_esc(logo)}" alt="{_esc(shop)}">' if logo
                     else f'<span class="mono" aria-hidden="true">{_esc(shop[:1].upper())}</span>')
            .replace("{{PB_CSS}}", powered_by.pill_css()).replace("{{PB}}", powered_by.pill_html())
            .replace("{{DATA}}", blob))
    return HTMLResponse(page, headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store",
                                       "Referrer-Policy": "no-referrer"})


# Vula's own design (not a copy of anyone's): the business's colour carries it; the rest is a
# calm paper ground, one sans face, figures aligned. Every product field is set with textContent.
_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow, noarchive"><meta name="referrer" content="no-referrer">
<meta name="theme-color" content="{{ACCENT}}">
<title>{{TITLE}}</title>
<style>
:root{--accent:{{ACCENT}};--on:{{ON_ACCENT}};--bg:#F6F4EF;--surface:#FFFFFF;--text:#1F1E1B;--muted:#6E6A62;--line:#E4DFD4;--soft:#EFEBE3;--ok:#2F6B45;--r:16px}
@media (prefers-color-scheme:dark){:root{--bg:#141412;--surface:#1F1F1C;--text:#ECE9E1;--muted:#A29E95;--line:#33322D;--soft:#292824;--ok:#7CC49A}}
*{box-sizing:border-box}html,body{margin:0}
body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);-webkit-text-size-adjust:100%;line-height:1.45}
.hero{background:var(--accent);color:var(--on);padding:22px 16px 26px}
.wrap{max-width:1120px;margin:0 auto}
.brand{display:flex;align-items:center;gap:12px}
.logo{height:40px;max-width:160px;object-fit:contain;background:#fff;border-radius:8px;padding:4px}
.mono{width:40px;height:40px;border-radius:10px;background:var(--on);color:var(--accent);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:19px}
.shop{font-weight:600;font-size:15px;opacity:.92}
h1{font-size:clamp(24px,5vw,34px);line-height:1.15;margin:18px 0 6px;letter-spacing:-.015em}
.for{font-size:14px;opacity:.85;margin:0}
.intro{font-size:15px;max-width:680px;margin:12px 0 0;opacity:.95;white-space:pre-line}
.tools{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px 10px}
.row{display:flex;gap:8px;align-items:center}
.search{flex:1;min-width:0;font:inherit;font-size:16px;padding:11px 14px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--text);outline:none}
.search:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
.ibtn{flex:none;height:44px;padding:0 14px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--text);font:inherit;font-size:14px;cursor:pointer}
.chips{display:flex;gap:8px;overflow-x:auto;padding:10px 0 2px;scrollbar-width:none}.chips::-webkit-scrollbar{display:none}
.chip{flex:none;border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;padding:7px 13px;font:inherit;font-size:14px;cursor:pointer}
.chip.on{background:var(--accent);border-color:var(--accent);color:var(--on)}
.count{font-size:13px;color:var(--muted);margin:8px 0 0}
main{padding:16px}
.cat{font-size:13px;font-weight:600;text-transform:none;color:var(--muted);margin:22px 0 10px;display:flex;gap:8px;align-items:baseline}
.cat:first-child{margin-top:4px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;display:flex;flex-direction:column;scroll-margin-top:140px}
.card.flash{box-shadow:0 0 0 3px var(--accent)}
.strip{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none;background:var(--soft)}.strip::-webkit-scrollbar{display:none}
.strip img{flex:none;width:100%;aspect-ratio:4/3;object-fit:cover;scroll-snap-align:center;display:block}
.body{padding:14px 16px 16px;display:flex;flex-direction:column;gap:10px;flex:1}
.head{display:flex;gap:10px;align-items:flex-start;justify-content:space-between}
h3{font-size:17px;margin:0;line-height:1.25}
.grade{flex:none;font-size:12px;font-weight:600;padding:4px 9px;border-radius:999px;background:color-mix(in srgb,var(--accent) 14%,var(--surface));color:var(--text);border:1px solid color-mix(in srgb,var(--accent) 35%,var(--line));max-width:55%;text-align:right}
.price{font-weight:650;font-variant-numeric:tabular-nums}.price s{color:var(--muted);font-weight:400;margin-left:6px}
.desc{font-size:14px;color:var(--muted);margin:0}
.ben{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:14px}
.ben li{display:flex;gap:8px}.ben li::before{content:"";flex:none;width:7px;height:7px;margin-top:7px;border-radius:50%;background:var(--accent)}
details{border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;font-size:14px;font-weight:600;list-style:none;display:flex;justify-content:space-between}
summary::-webkit-details-marker{display:none}summary::after{content:"+";color:var(--muted)}details[open] summary::after{content:"–"}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
td{padding:6px 0;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{color:var(--muted);width:42%;padding-right:10px}
tr:last-child td{border-bottom:0}
.stock{font-size:12.5px;color:var(--muted)}.stock.in{color:var(--ok)}
select{font:inherit;font-size:15px;width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--text)}
.acts{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:auto;padding-top:4px}
.btn{display:flex;align-items:center;justify-content:center;gap:6px;min-height:44px;padding:0 12px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--text);font:inherit;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;text-align:center}
.btn.wa{grid-column:1/-1;background:var(--accent);border-color:var(--accent);color:var(--on)}
.empty{color:var(--muted);text-align:center;padding:48px 0}
.toast{position:fixed;left:50%;bottom:calc(18px + env(safe-area-inset-bottom));transform:translateX(-50%);background:var(--text);color:var(--bg);padding:9px 16px;border-radius:999px;font-size:14px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:9}
.toast.show{opacity:.95}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media print{.tools,.acts,.vpb,.toast{display:none!important}.hero{background:none;color:#000;padding:0 0 12px}
 body{background:#fff;color:#000}.card{break-inside:avoid;border-color:#ccc}details>*{display:block}details summary::after{content:""}
 .grid{grid-template-columns:1fr 1fr}.strip img:not(:first-child){display:none}}
{{PB_CSS}}
</style></head><body>
<header class="hero"><div class="wrap">
  <div class="brand">{{LOGO}}<span class="shop">{{SHOP}}</span></div>
  <h1 id="t"></h1><p class="for" id="for"></p><p class="intro" id="intro"></p>
</div></header>
<div class="tools"><div class="wrap">
  <div class="row"><input class="search" id="q" type="search" placeholder="Search products or colours" autocomplete="off" aria-label="Search">
  <button class="ibtn" id="pdf" type="button">Save as PDF</button></div>
  <div class="chips" id="chips"></div><p class="count" id="count"></p>
</div></div>
<main><div class="wrap" id="list"></div></main>
{{PB}}
<div class="toast" id="toast" role="status"></div>
<script id="g" type="application/json">{{DATA}}</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('g').textContent),cat='',q='',pick={};
function el(t,a,txt){var e=document.createElement(t);if(a)for(var k in a)e.setAttribute(k,a[k]);if(txt!=null)e.textContent=txt;return e}
function R(c){return 'R'+(c/100).toLocaleString('en-ZA',{minimumFractionDigits:2,maximumFractionDigits:2})}
function toast(m){var t=document.getElementById('toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(function(){t.classList.remove('show')},1800)}
document.getElementById('t').textContent=D.title;
if(D.client)document.getElementById('for').textContent='Prepared for '+D.client;
if(D.intro)document.getElementById('intro').textContent=D.intro;
function hay(i){return [i.name,i.cat_label,i.grade,i.desc].concat(i.benefits,i.colours,i.rows.map(function(r){return r[0]+' '+r[1]})).join(' ').toLowerCase()}
D.items.forEach(function(i){i._h=hay(i)});
function match(i){if(cat&&i.cat!==cat)return false;var w=q.toLowerCase().split(/\s+/).filter(Boolean);return w.every(function(x){return i._h.indexOf(x)>=0})}
function msg(i,kind){var c=pick[i.id]||'';
  if(kind==='sample')return 'Hi '+D.shop+', please can I get a sample of '+i.name+(c?' in '+c:'')+'.\n(From your guide: '+D.title+')';
  return 'Hi '+D.shop+', please can you check stock for '+i.name+'.\nColour: '+c+'\nQuantity: \n(From your guide: '+D.title+')'}
function wa(i,kind){return 'https://wa.me/'+D.wa+'?text='+encodeURIComponent(msg(i,kind))}
function share(i){var url=location.href.split('#')[0]+'#p-'+i.id;
  if(navigator.share){navigator.share({title:i.name+' — '+D.shop,text:i.name+' from '+D.shop,url:url}).catch(function(){})}
  else if(navigator.clipboard){navigator.clipboard.writeText(url).then(function(){toast('Link copied')},function(){toast(url)})}else{toast(url)}}
function card(i){var c=el('article',{'class':'card',id:'p-'+i.id});
  if(i.imgs.length){var s=el('div',{'class':'strip'});i.imgs.forEach(function(u,n){s.appendChild(el('img',{src:u,alt:i.name+(i.imgs.length>1?' photo '+(n+1):''),loading:'lazy'}))});c.appendChild(s)}
  var b=el('div',{'class':'body'});c.appendChild(b);
  var h=el('div',{'class':'head'});h.appendChild(el('h3',null,i.name));if(i.grade)h.appendChild(el('span',{'class':'grade'},i.grade));b.appendChild(h);
  if(D.prices&&i.cents){var p=el('div',{'class':'price'},R(i.cents)+(i.unit?' / '+i.unit:''));if(i.was_cents)p.appendChild(el('s',null,R(i.was_cents)));b.appendChild(p)}
  if(i.desc)b.appendChild(el('p',{'class':'desc'},i.desc));
  if(i.benefits.length){var u=el('ul',{'class':'ben'});i.benefits.forEach(function(x){u.appendChild(el('li',null,x))});b.appendChild(u)}
  if(i.rows.length){var d=el('details');d.appendChild(el('summary',null,'Specifications'));var t=el('table');
    i.rows.forEach(function(r){var tr=el('tr');tr.appendChild(el('td',null,r[0]));tr.appendChild(el('td',null,r[1]));t.appendChild(tr)});
    d.appendChild(t);if(i.rows.length<=6)d.open=true;b.appendChild(d)}
  if(i.colours.length){var sel=el('select',{'aria-label':'Colour'});sel.appendChild(el('option',{value:''},'Choose a colour ('+i.colours.length+')'));
    i.colours.forEach(function(x){sel.appendChild(el('option',{value:x},x))});sel.value=pick[i.id]||'';
    sel.onchange=function(){pick[i.id]=sel.value};b.appendChild(sel)}
  b.appendChild(el('div',{'class':'stock'+(i.in_stock?' in':'')},i.in_stock?'In stock — check the quantity you need':'Ask about availability'));
  var a=el('div',{'class':'acts'});
  if(D.wa){var w=el('a',{'class':'btn wa',href:'#',rel:'noopener'},'Check stock on WhatsApp');w.onclick=function(e){e.preventDefault();window.open(wa(i,'stock'),'_blank')};a.appendChild(w);
    var sm=el('a',{'class':'btn',href:'#',rel:'noopener'},'Ask for a sample');sm.onclick=function(e){e.preventDefault();window.open(wa(i,'sample'),'_blank')};a.appendChild(sm)}
  var sh=el('button',{'class':'btn',type:'button'},'Share');sh.onclick=function(){share(i)};a.appendChild(sh);
  if(i.link){a.appendChild(el('a',{'class':'btn',href:i.link,target:'_blank',rel:'noopener noreferrer'},'Maker’s page'))}
  b.appendChild(a);return c}
function render(){var box=document.getElementById('list');box.innerHTML='';var shown=0,order=Object.keys(D.cats);
  order.forEach(function(k){var its=D.items.filter(function(i){return i.cat===k&&match(i)});if(!its.length)return;shown+=its.length;
    var hd=el('div',{'class':'cat'});hd.appendChild(el('span',null,D.cats[k]));hd.appendChild(el('span',null,'('+its.length+')'));box.appendChild(hd);
    var g=el('div',{'class':'grid'});its.forEach(function(i){g.appendChild(card(i))});box.appendChild(g)});
  if(!shown)box.appendChild(el('p',{'class':'empty'},D.items.length?'Nothing matches “'+q+'” — try fewer words.':'There are no products in this guide yet.'));
  document.getElementById('count').textContent=(q||cat)?shown+' of '+D.items.length+' products':D.items.length+' product'+(D.items.length===1?'':'s')+(order.length>1?' in '+order.length+' categories':'')}
function chips(){var box=document.getElementById('chips'),keys=Object.keys(D.cats);box.innerHTML='';if(keys.length<2){box.style.display='none';return}
  [['','All']].concat(keys.map(function(k){return [k,D.cats[k]]})).forEach(function(kv){var b=el('button',{'class':'chip'+(kv[0]===cat?' on':''),type:'button'},kv[1]);
    b.onclick=function(){cat=kv[0];chips();render()};box.appendChild(b)})}
document.getElementById('q').addEventListener('input',function(e){q=e.target.value.trim();if(q&&cat){cat='';chips()}render()});
document.getElementById('pdf').onclick=function(){document.querySelectorAll('details').forEach(function(d){d.open=true});window.print()};
chips();render();
var h=location.hash;if(h.indexOf('#p-')===0){var t=document.getElementById(h.slice(1));if(t){t.scrollIntoView({block:'start'});t.classList.add('flash');setTimeout(function(){t.classList.remove('flash')},1800)}}
})();
</script></body></html>"""
