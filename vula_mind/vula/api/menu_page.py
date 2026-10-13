"""
vula/api/menu_page.py — the shop's public web page, linked from its WhatsApp menu.

WhatsApp's list message has no image per row (Meta platform limit), so this page carries the
photos. 2026-10-13 (SeedleMe, Ian): it was a read-only photo grid with full product blurbs and
no way to order. It is now a small shop: one card per range with its colours to pick (photo and
price follow the colour), a short description, quantity and "Add to basket", search and
categories, and a basket that goes back to WhatsApp ("Hi! I'd like to order basket K7Q2MX") —
vula/commerce/web_basket.py moves it into the customer's WhatsApp cart and checkout carries on
in the chat. Prices are always the server's; the page only says which products and how many.
"""
import html
import json
import re
import time
from collections import deque

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["menu_page"])

_PLACEHOLDER_IMG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='220'%3E"
    "%3Crect width='300' height='220' fill='%23DDD8CE'/%3E%3Ctext x='150' y='115' "
    "font-family='sans-serif' font-size='14' fill='%238A8680' text-anchor='middle'%3ENo photo yet"
    "%3C/text%3E%3C/svg%3E"
)

# A dot of colour next to each colour name (names the shop uses; anything else gets a neutral).
_SWATCH = {
    "black": "#1d1d1d", "charcoal": "#3f4144", "grey": "#9a9a98", "gray": "#9a9a98",
    "dark grey": "#5f6062", "light grey": "#c9c9c6", "nguni pattern grey": "#8d8d8a",
    "ivory": "#efe8d8", "white": "#f6f6f4", "off white": "#eeebe3", "cream": "#efe4c8",
    "sandstone": "#d6b98f", "beige": "#d9c7a7", "natural": "#cdbfa8", "stone": "#b9b1a3",
    "terracotta": "#c0623f", "burnt orange": "#c8662b", "orange": "#e07a2e", "rust": "#a6492a",
    "choc plum": "#5a3a3c", "brown": "#6b4a32", "red": "#b23a32", "pink": "#e7a7b0",
    "peach blossom": "#f1c2a6", "yellow": "#e7c341", "gold": "#c9a440", "green": "#4f7a4c",
    "olive": "#7a7a3a", "mint": "#a8d5ba", "teal": "#2f7f7f", "blue": "#3d6fb3",
    "bright blue": "#1f8fd6", "ocean blue": "#2b6c8f", "navy": "#24344d", "purple": "#6c4a8a",
    "slate": "#5c6670", "silver": "#c0c0c0", "copper": "#b87333",
}


def _esc(s: str) -> str:
    # Quotes too: _esc is also used inside src="..."/alt="..." attributes, where a product
    # name or image URL containing " could break out and inject markup (XSS on a public page).
    return html.escape(str(s or ""), quote=True)


def _fmt_price(cents) -> str:
    try:
        return f"R{int(cents) / 100:.2f}"
    except Exception:
        return ""


def short_description(text: str, limit: int = 150) -> str:
    """The first sentence or two, up to `limit` characters — the rest is behind "More"."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:stop + 1] if stop >= 60 else cut.rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def shop_data(products: list) -> list:
    """Products → one entry per range for the page: its options (colour, price, photo)."""
    from vula.commerce import product_families as pf
    out = []
    groups = []
    for r in pf.group(products):
        if r["colours"] or len(r["members"]) == 1:
            groups.append(r)
        else:                                  # same name, no colour to tell them apart: each its own card
            groups += [{**r, "id": f"{r['id']}{i}", "members": [m], "colours": []}
                       for i, m in enumerate(r["members"])]
    for r in groups:
        members = [c["product"] for c in r["colours"]] if r["colours"] else r["members"][:1]
        opts = []
        for p in members:
            colour = pf.colour_of(p.get("name") or "") if r["colours"] else None
            from vula.commerce.web_basket import sized
            imgs = [sized(u, 900) for u in [p.get("image_url")] + list(p.get("images") or [])
                    if isinstance(u, str) and u.startswith("http")]
            opts.append({"pid": str(p.get("id")), "colour": colour,
                         "swatch": _SWATCH.get((colour or "").lower(), "#b8b2a6"),
                         "name": p.get("name") or "Item",
                         "cents": int(p.get("price_cents") or 0),
                         "kg": str(p.get("sold_by") or "").lower() == "kg",
                         "img": sized(p.get("image_url"), 900) or "",
                         "imgs": list(dict.fromkeys(imgs))[:8]})
        lead = members[0]
        full = re.sub(r"\s+", " ", (lead.get("description") or lead.get("notes") or "")).strip()
        out.append({"id": r["id"], "title": r["title"] if r["colours"] else (lead.get("name") or r["title"]),
                    "cat": r.get("category") or "general", "short": short_description(full),
                    "full": full[:900], "opts": opts})
    return out


@router.get("/shop-ping", include_in_schema=False)
async def shop_ping(request: Request) -> JSONResponse:
    """Which shop this address serves — how Vula knows a shop's own domain is live."""
    from vula.commerce import web_basket
    return JSONResponse({"shop": web_basket.tenant_for_shop_host(request.headers.get("host") or "")},
                        headers={"Cache-Control": "no-store"})


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def shop_home(request: Request) -> HTMLResponse:
    """shop.<their domain>/ — the shop on its own address, so the customer sees the business's
    name in WhatsApp's browser, not Vula's (2026-10-13). Any other host has nothing here."""
    from fastapi import HTTPException
    from vula.commerce import web_basket
    tenant_id = web_basket.tenant_for_shop_host(request.headers.get("host") or "")
    if not tenant_id:
        raise HTTPException(status_code=404, detail="Not Found")
    return await photo_menu(tenant_id)


@router.get("/menu/{tenant_id}", response_class=HTMLResponse)
async def photo_menu(tenant_id: str) -> HTMLResponse:
    from vula.commerce import service as cs
    from vula.commerce import web_basket

    products = await web_basket.shop_products(tenant_id)
    try:
        inv = await cs.get_invoice_settings(tenant_id)
    except Exception:
        inv = None
    shop_name = _esc((inv or {}).get("trading_as") or (inv or {}).get("company_name") or tenant_id.replace("-", " ").title())
    # Brand-through (UI overhaul P4): the tenant's own accent + logo on their public page.
    accent = (inv or {}).get("accent_color") or "#2C5545"
    if not accent.startswith("#"):
        accent = f"#{accent}"
    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent):
        accent = "#2C5545"
    logo_url = _esc((inv or {}).get("logo_url") or "")
    from vula.commerce.brand_sizes import logo_px
    logo_h, logo_w = logo_px("menu", (inv or {}).get("logo_size"))

    ranges = shop_data(products)
    cats = []
    for r in ranges:
        if r["cat"] not in cats:
            cats.append(r["cat"])
    cat_labels = {c: c.replace("_", " ").title() for c in cats}
    number = web_basket.wa_number(tenant_id)
    can_order = bool(number) and web_basket.shop_enabled(tenant_id, products)
    data = json.dumps({"tenant": tenant_id, "ranges": ranges, "cats": cat_labels,
                       "basket": f"/menu/{tenant_id}/basket",
                       "canOrder": can_order, "chat": f"https://wa.me/{number}" if number else ""}
                      ).replace("</", "<\\/")

    page = _SHOP_HTML.replace("{{TITLE}}", shop_name).replace("{{ACCENT}}", accent) \
        .replace("{{LOGO}}", f'<img class="logo" src="{logo_url}" alt="">' if logo_url else "") \
        .replace("{{LOGO_H}}", str(logo_h)).replace("{{LOGO_W}}", str(logo_w)) \
        .replace("{{PLACEHOLDER}}", _PLACEHOLDER_IMG).replace("{{DATA}}", data)
    return HTMLResponse(page)


# A public endpoint that writes rows: a small per-address throttle on top of the server-wide one.
_RECENT: dict = {}
_BASKETS_PER_MINUTE = 10


def _throttled(key: str) -> bool:
    now = time.monotonic()
    q = _RECENT.setdefault(key, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= _BASKETS_PER_MINUTE:
        return True
    q.append(now)
    if len(_RECENT) > 5000:
        _RECENT.clear()
    return False


@router.post("/menu/{tenant_id}/basket")
async def create_basket(tenant_id: str, request: Request) -> JSONResponse:
    """The page's basket → a saved basket + the WhatsApp link that orders it."""
    from vula.commerce import web_basket
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else "?")
    if _throttled(f"{tenant_id}:{ip}"):
        return JSONResponse({"error": "Too many baskets — wait a minute and try again."}, status_code=429)
    number = web_basket.wa_number(tenant_id)
    if not number or not await web_basket.shop_enabled_for(tenant_id):
        return JSONResponse({"error": "This shop isn't taking WhatsApp orders yet."}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        body = {}
    res = await web_basket.create(tenant_id, (body or {}).get("items"))
    if res.get("error"):
        return JSONResponse(res, status_code=400)
    return JSONResponse({**res, "wa_url": web_basket.wa_link(number, res["code"])})


_SHOP_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{TITLE}} — Shop</title>
<style>
:root{--accent:{{ACCENT}};--bg:#FAF8F3;--surface:#fff;--text:#24231F;--muted:#77736B;--line:#E6E1D6;--chip:#F1EDE4;--r:14px}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--surface:#22221F;--text:#EDEAE2;--muted:#A39F96;--line:#34332E;--chip:#2C2B27}}
*{box-sizing:border-box}html,body{margin:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);padding-bottom:96px;-webkit-text-size-adjust:100%}
header{position:sticky;top:0;z-index:5;background:var(--bg);padding:14px 16px 10px;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.brand h1{flex:1}
.hbtn{position:relative;display:flex;align-items:center;justify-content:center;width:42px;height:42px;border-radius:50%;border:1px solid var(--line);background:var(--surface);color:var(--text);text-decoration:none;font-size:19px;cursor:pointer}
.badge{position:absolute;top:-4px;right:-4px;min-width:19px;height:19px;border-radius:10px;background:var(--accent);color:#fff;font-size:11px;font-weight:700;display:none;align-items:center;justify-content:center;padding:0 5px}
.badge.show{display:flex}
.gal{position:relative}
.strip{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none}.strip::-webkit-scrollbar{display:none}
.strip img{flex:none;width:100%;scroll-snap-align:center}
.count{position:absolute;right:10px;bottom:10px;background:rgba(0,0,0,.55);color:#fff;font-size:12px;padding:3px 8px;border-radius:999px}
.logo{max-height:{{LOGO_H}}px;max-width:{{LOGO_W}}px;object-fit:contain}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
.search{width:100%;font-size:16px;padding:11px 14px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--text);outline:none}
.search:focus{border-color:var(--accent)}
.cats{display:flex;gap:8px;overflow-x:auto;padding:10px 0 2px;scrollbar-width:none}.cats::-webkit-scrollbar{display:none}
.cat{flex:none;border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;padding:7px 13px;font-size:14px}
.cat.on{background:var(--accent);border-color:var(--accent);color:#fff}
main{padding:14px 16px;display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));max-width:1100px;margin:0 auto}
.card{background:var(--surface);border-radius:var(--r);overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.06),0 4px 14px rgba(0,0,0,.04);display:flex;flex-direction:column;scroll-margin-top:150px;transition:box-shadow .3s}
.card.flash{box-shadow:0 0 0 3px var(--accent)}
.pic{aspect-ratio:4/3;width:100%;object-fit:cover;display:block;background:var(--chip)}
.body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:8px;flex:1}
.title{font-size:16px;font-weight:650;margin:0;line-height:1.25}
.price{font-size:15px;font-weight:700;color:var(--accent)}
.price small{font-weight:500;color:var(--muted);margin-left:6px}
.desc{font-size:13.5px;color:var(--muted);line-height:1.45;margin:0}
.more{background:none;border:0;color:var(--accent);font-size:13px;padding:0;cursor:pointer;align-self:flex-start}
.sw{display:flex;flex-wrap:wrap;gap:6px}
.swb{display:flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;padding:5px 10px 5px 6px;font-size:13px;cursor:pointer}
.swb i{width:16px;height:16px;border-radius:50%;border:1px solid rgba(0,0,0,.15);display:block}
.swb.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent);font-weight:600}
.buy{display:flex;gap:10px;align-items:center;margin-top:auto;padding-top:4px}
.qty{display:flex;align-items:center;border:1px solid var(--line);border-radius:999px}
.qty button{width:38px;height:40px;border:0;background:none;color:var(--text);font-size:20px;cursor:pointer}
.qty span{min-width:22px;text-align:center;font-weight:600}
.add{flex:1;height:42px;border:0;border-radius:999px;background:var(--accent);color:#fff;font-size:15px;font-weight:650;cursor:pointer}
.add:active{transform:scale(.98)}
.empty{grid-column:1/-1;color:var(--muted);text-align:center;padding:40px 0}
.bar{position:fixed;left:0;right:0;bottom:0;z-index:9;padding:10px 16px calc(10px + env(safe-area-inset-bottom));background:var(--surface);border-top:1px solid var(--line);display:none;align-items:center;gap:12px;box-shadow:0 -6px 20px rgba(0,0,0,.08)}
.bar.show{display:flex}
.bar .sum{flex:1;font-size:14px;cursor:pointer}.bar .sum b{display:block;font-size:16px}
.wa{height:46px;border:0;border-radius:999px;background:#25D366;color:#073b1d;font-weight:700;font-size:15px;padding:0 18px;cursor:pointer;white-space:nowrap}
.wa[disabled]{opacity:.6}
.sheet{position:fixed;inset:0;z-index:10;background:rgba(0,0,0,.45);display:none}
.sheet.show{display:block}
.panel{position:absolute;left:0;right:0;bottom:0;max-height:82vh;overflow:auto;background:var(--surface);border-radius:18px 18px 0 0;padding:16px 16px calc(16px + env(safe-area-inset-bottom))}
.panel h2{font-size:18px;margin:0 0 10px}
.line{display:flex;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.line img{width:52px;height:52px;border-radius:10px;object-fit:cover;background:var(--chip)}
.line .n{flex:1;font-size:14px}.line .n small{display:block;color:var(--muted)}
.x{border:0;background:none;color:var(--muted);font-size:13px;cursor:pointer}
.tot{display:flex;justify-content:space-between;font-weight:700;margin:14px 0 4px}
.note{font-size:12.5px;color:var(--muted);margin:0 0 14px}
.panel .wa{width:100%}
.toast{position:fixed;left:50%;top:calc(12px + env(safe-area-inset-top));max-width:calc(100% - 32px);transform:translateX(-50%);background:var(--text);color:var(--bg);padding:9px 16px;border-radius:999px;font-size:14px;opacity:0;transition:opacity .2s;z-index:11;pointer-events:none}
.toast.show{opacity:.95}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<header>
  <div class="brand">{{LOGO}}<h1>{{TITLE}}</h1>
    <a class="hbtn" id="chat" aria-label="Back to WhatsApp chat" style="display:none">💬</a>
    <button class="hbtn" id="cartbtn" type="button" aria-label="Basket" style="display:none">🛒<span class="badge" id="badge"></span></button></div>
  <input class="search" id="q" type="search" placeholder="Search — e.g. round basin, charcoal" autocomplete="off">
  <div class="cats" id="cats"></div>
</header>
<main id="grid"></main>
<div class="bar" id="bar"><div class="sum" id="sum"></div><button class="wa" id="go">Order on WhatsApp</button></div>
<div class="sheet" id="sheet"><div class="panel" id="panel"></div></div>
<div class="toast" id="toast"></div>
<script id="shop" type="application/json">{{DATA}}</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('shop').textContent),PH="{{PLACEHOLDER}}";
var KEY='vula-basket-'+D.tenant,basket={},sel={},qty={},cat='',q='';
try{basket=JSON.parse(localStorage.getItem(KEY)||'{}')||{}}catch(e){basket={}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(basket))}catch(e){}}
function R(c){return 'R'+(c/100).toLocaleString('en-ZA',{minimumFractionDigits:2,maximumFractionDigits:2}).replace(/ /g,' ')}
function el(t,a,h){var e=document.createElement(t);if(a)for(var k in a)e.setAttribute(k,a[k]);if(h!=null)e.textContent=h;return e}
var byId={};D.ranges.forEach(function(r){byId[r.id]=r;sel[r.id]=0;qty[r.id]=1});
function toast(m){var t=document.getElementById('toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(function(){t.classList.remove('show')},1600)}
function words(s){return (s||'').toLowerCase().replace(/[^a-z0-9 ]+/g,' ').split(' ').filter(Boolean)}
function matches(r){if(cat&&r.cat!==cat)return false;var w=words(q);if(!w.length)return true;
  var hay=(r.title+' '+r.opts.map(function(o){return (o.colour||'')+' '+o.name}).join(' ')+' '+r.short).toLowerCase();
  return w.every(function(x){return hay.indexOf(x.replace(/s$/,''))>=0})}
function card(r){
  var o=r.opts[sel[r.id]],c=el('article',{'class':'card',id:'r-'+r.id});
  var imgs=(o.imgs&&o.imgs.length)?o.imgs:[o.img||PH],gal=el('div',{'class':'gal'}),strip=el('div',{'class':'strip'});
  imgs.forEach(function(u,i){strip.appendChild(el('img',{'class':'pic',src:u,alt:r.title+(imgs.length>1?' photo '+(i+1):''),loading:'lazy'}))});
  gal.appendChild(strip);
  if(imgs.length>1){var cnt=el('span',{'class':'count'},'1 / '+imgs.length);gal.appendChild(cnt);
    strip.addEventListener('scroll',function(){cnt.textContent=(Math.round(strip.scrollLeft/strip.clientWidth)+1)+' / '+imgs.length},{passive:true})}
  c.appendChild(gal);
  var b=el('div',{'class':'body'});c.appendChild(b);
  b.appendChild(el('h3',{'class':'title'},r.title));
  var pr=el('div',{'class':'price'},R(o.cents)+(o.kg?' /kg':''));if(o.colour){pr.appendChild(el('small',null,o.colour))}b.appendChild(pr);
  var step=o.kg?0.5:1;if(qty[r.id]<step||(qty[r.id]*2)%(step*2))qty[r.id]=step;
  if(r.opts.length>1){var sw=el('div',{'class':'sw'});r.opts.forEach(function(op,i){
    var s=el('button',{'class':'swb'+(i===sel[r.id]?' on':''),type:'button','aria-pressed':i===sel[r.id]});
    var dot=el('i');dot.style.background=op.swatch;s.appendChild(dot);s.appendChild(document.createTextNode(op.colour||op.name));
    s.onclick=function(){sel[r.id]=i;c.replaceWith(card(r))};sw.appendChild(s)});b.appendChild(sw)}
  if(r.short){var d=el('p',{'class':'desc'},r.short);b.appendChild(d);
    if(r.full&&r.full.length>r.short.length+5){var m=el('button',{'class':'more',type:'button'},'More');
      m.onclick=function(){var open=m.textContent==='More';d.textContent=open?r.full:r.short;m.textContent=open?'Less':'More'};b.appendChild(m)}}
  function ql(v){return o.kg?(v+' kg'):String(v)}
  var buy=el('div',{'class':'buy'}),qq=el('div',{'class':'qty'}),minus=el('button',{type:'button','aria-label':'Less'},'−'),n=el('span',null,ql(qty[r.id])),plus=el('button',{type:'button','aria-label':'More'},'+');
  minus.onclick=function(){qty[r.id]=Math.max(step,qty[r.id]-step);n.textContent=ql(qty[r.id])};plus.onclick=function(){qty[r.id]=Math.min(99,qty[r.id]+step);n.textContent=ql(qty[r.id])};
  qq.appendChild(minus);qq.appendChild(n);qq.appendChild(plus);buy.appendChild(qq);
  if(D.canOrder){var add=el('button',{'class':'add',type:'button'},'Add to basket');add.onclick=function(){
    var op=r.opts[sel[r.id]],cur=basket[op.pid]||{qty:0};basket[op.pid]={qty:Math.min(99,cur.qty+qty[r.id]),name:op.name,cents:op.cents,img:op.img,kg:op.kg};
    save();bar();toast('✓ Added '+ql(qty[r.id])+(op.colour?' × '+op.colour:'')+' to your basket');qty[r.id]=step;n.textContent=ql(step)};buy.appendChild(add)}
  b.appendChild(buy);return c}
function render(){var g=document.getElementById('grid');g.innerHTML='';var list=D.ranges.filter(matches);
  list.forEach(function(r){g.appendChild(card(r))});
  if(!list.length)g.appendChild(el('p',{'class':'empty'},q?'Nothing matches "'+q+'" — try fewer words.':'Nothing in stock right now — check back soon.'))}
function cats(){var box=document.getElementById('cats'),keys=Object.keys(D.cats);if(keys.length<2){box.style.display='none';return}
  [['','All']].concat(keys.map(function(k){return [k,D.cats[k]]})).forEach(function(kv){
    var b=el('button',{'class':'cat'+(kv[0]===cat?' on':''),type:'button'},kv[1]);
    b.onclick=function(){cat=kv[0];box.innerHTML='';cats();render()};box.appendChild(b)})}
function totals(){var n=0,c=0;for(var k in basket){n+=basket[k].kg?1:basket[k].qty;c+=Math.round(basket[k].qty*basket[k].cents)}return {n:n,c:c}}
function bar(){var t=totals(),b=document.getElementById('bar');b.classList.toggle('show',t.n>0&&D.canOrder);
  var bd=document.getElementById('badge');bd.textContent=t.n;bd.classList.toggle('show',t.n>0);
  var s=document.getElementById('sum');s.innerHTML='';s.appendChild(el('span',null,'🛒 '+t.n+' item'+(t.n===1?'':'s')+' · tap to review'));s.appendChild(el('b',null,R(t.c)))}
function sheet(){var p=document.getElementById('panel');p.innerHTML='';p.appendChild(el('h2',null,'Your basket'));
  Object.keys(basket).forEach(function(pid){var it=basket[pid],l=el('div',{'class':'line'});
    l.appendChild(el('img',{src:it.img||PH,alt:''}));var nm=el('div',{'class':'n'},it.name);nm.appendChild(el('small',null,(it.kg?it.qty+' kg':it.qty+' ×')+' '+R(it.cents)+(it.kg?'/kg':'')));l.appendChild(nm);
    var x=el('button',{'class':'x',type:'button'},'Remove');x.onclick=function(){delete basket[pid];save();bar();if(!totals().n)close();else sheet()};l.appendChild(x);p.appendChild(l)});
  var t=totals(),tot=el('div',{'class':'tot'});tot.appendChild(el('span',null,'Subtotal'));tot.appendChild(el('span',null,R(t.c)));p.appendChild(tot);
  p.appendChild(el('p',{'class':'note'},'Delivery and payment are sorted out with you on WhatsApp.'));
  var go=el('button',{'class':'wa',type:'button'},'Send order on WhatsApp');go.onclick=send;p.appendChild(go);
  document.getElementById('sheet').classList.add('show')}
function close(){document.getElementById('sheet').classList.remove('show')}
function send(){var btns=document.querySelectorAll('.wa');btns.forEach(function(b){b.disabled=true});
  var items=Object.keys(basket).map(function(pid){return {product_id:pid,qty:basket[pid].qty}});
  fetch(D.basket,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:items})})
  .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j}})})
  .then(function(res){if(!res.ok||!res.j.wa_url){throw new Error(res.j.error||'Could not send')}
    basket={};save();bar();close();location.href=res.j.wa_url})
  .catch(function(e){toast(e.message||'Could not send — try again')})
  .finally(function(){btns.forEach(function(b){b.disabled=false})})}
document.getElementById('q').addEventListener('input',function(e){q=e.target.value.trim();render()});
document.getElementById('sum').onclick=sheet;document.getElementById('go').onclick=sheet;
if(D.canOrder){var cb=document.getElementById('cartbtn');cb.style.display='flex';cb.onclick=function(){if(totals().n)sheet();else toast('Your basket is empty')}}
if(D.chat){var ch=document.getElementById('chat');ch.href=D.chat;ch.style.display='flex'}
document.getElementById('sheet').onclick=function(e){if(e.target.id==='sheet')close()};
var h=location.hash||'';if(h.indexOf('#c-')===0){cat=decodeURIComponent(h.slice(3))}
cats();render();bar();
if(h.indexOf('#r-')===0){var t=document.getElementById(h.slice(1));if(t){t.scrollIntoView({block:'start'});t.classList.add('flash');setTimeout(function(){t.classList.remove('flash')},1800)}}
})();
</script></body></html>"""


# ── The paid-order slip (vula/commerce/order_slip.py) — like Tap to Pay's receipt ──────────
async def _slip_or_404(token: str):
    from fastapi import HTTPException
    from vula.commerce import order_slip
    oid = order_slip.order_id_from(token)
    data = await order_slip.load(oid) if oid else None
    if not data:
        raise HTTPException(status_code=404, detail="This receipt link is no longer available.")
    return data


@router.get("/receipt/{token}/slip.png")
async def receipt_slip_png(token: str):
    import asyncio
    from fastapi.responses import Response
    from vula.commerce import order_slip
    data = await _slip_or_404(token)
    png = await asyncio.to_thread(order_slip.render_png, data)
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300", "X-Robots-Tag": "noindex"})


@router.get("/receipt/{token}", response_class=HTMLResponse)
async def receipt_page(token: str) -> HTMLResponse:
    """Full screen, the slip prints down out of the top of the phone, the way Tap to Pay's
    receipt does (vula_dashboard/src/receipt/PrintedSlip.jsx) — on the shop's own address, in
    its colour. No Save or Replay (Ian, 13 Oct): the slip is already in the WhatsApp chat."""
    from vula.commerce import order_slip
    d = await _slip_or_404(token)
    accent = d["accent"] if re.fullmatch(r"#[0-9a-fA-F]{3,8}", d["accent"] or "") else "#2C5545"
    lines = "".join(f"<li>{_esc(i['qty'])} × {_esc(i['name'])} — {order_slip.rands(i['cents'])}</li>"
                    for i in d["items"])
    chat = f'<a class="b chat" href="{_esc(d["chat"])}">Back to chat</a>' if d.get("chat") else ""
    shop = f'<a class="b shop" href="{_esc(d["shop_url"])}">Shop again</a>' if d.get("shop_url") else ""
    # The page wears the slip picture's own background, so the slip runs edge to edge.
    rgb = order_slip._hex(accent)
    bg = "#%02x%02x%02x" % tuple(min(255, int(c * 0.25 + 255 * 0.75)) for c in rgb)
    page = _RECEIPT_HTML.replace("{{ACCENT}}", accent).replace("{{BG}}", bg).replace("{{TITLE}}", _esc(f"Receipt {d['order']} — {d['shop']}")) \
        .replace("{{IMG}}", f"{_esc(token)}/slip.png").replace("{{ORDER}}", _esc(d["order"])) \
        .replace("{{ALT}}", _esc(f"Receipt for order {d['order']} from {d['shop']}, "
                                 f"{order_slip.rands(d['total_cents'])}, paid {d['paid_at']}")) \
        .replace("{{LINES}}", lines).replace("{{CHAT}}", chat).replace("{{SHOP}}", shop)
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


_RECEIPT_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="robots" content="noindex">
<meta name="referrer" content="no-referrer"><meta name="theme-color" content="#1c1c19"><title>{{TITLE}}</title>
<style>
:root{--accent:{{ACCENT}};--bg:{{BG}}}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow-x:hidden}
.printer{position:fixed;top:0;left:0;right:0;z-index:3;height:calc(30px + env(safe-area-inset-top));
background:linear-gradient(#34342f,#1c1c19);box-shadow:0 6px 16px rgba(0,0,0,.35);transform:translateY(-100%);
animation:bar .45s ease-out forwards}
.printer:after{content:"";position:absolute;left:18px;right:18px;bottom:6px;height:4px;border-radius:2px;background:#0b0b09;
box-shadow:0 0 12px 2px var(--accent);animation:head 1.9s ease-in-out .55s 1 forwards}
.slot{padding-top:calc(24px + env(safe-area-inset-top));overflow:hidden;min-height:100%}
.slip{display:block;width:100%;max-width:640px;margin:0 auto;height:auto;transform:translateY(-102%);
filter:brightness(1.3) saturate(.3);animation:feed 2.2s cubic-bezier(.3,.75,.3,1) .55s forwards}
@keyframes bar{to{transform:none}}
@keyframes head{0%{opacity:1}90%{opacity:1}100%{opacity:.25}}
@keyframes feed{65%{filter:brightness(1.08) saturate(.85)}86%{transform:translateY(1.2%)}100%{transform:none;filter:none}}
.btns{position:sticky;bottom:0;display:flex;gap:10px;padding:12px 16px calc(14px + env(safe-area-inset-bottom));
max-width:640px;margin:0 auto;opacity:0;animation:fade .4s ease 2.8s forwards}
@keyframes fade{to{opacity:1}}
.b{flex:1;text-align:center;text-decoration:none;padding:14px 10px;border-radius:999px;font-weight:650;font-size:15px}
.chat{background:#25D366;color:#073b1d}.shop{background:#fff;color:#24231F;box-shadow:0 1px 4px rgba(0,0,0,.15)}
.sr{position:absolute;left:-9999px}
@media (prefers-reduced-motion:reduce){.printer,.slip,.btns,.printer:after{animation:none;transform:none;filter:none;opacity:1}}
@media print{.printer,.btns{display:none}.slip{animation:none;transform:none;filter:none}}
</style></head><body>
<div class="printer" aria-hidden="true"></div>
<div class="slot"><img class="slip" id="slip" src="{{IMG}}" alt="{{ALT}}"></div>
<ul class="sr">{{LINES}}</ul>
<div class="btns">{{CHAT}}{{SHOP}}</div>
<script>
setTimeout(function(){try{navigator.vibrate&&navigator.vibrate([12,40,12])}catch(e){}},2600);
</script></body></html>"""


# Landing pages for hosted-checkout redirects (invoice and WhatsApp-order pay links point here
# when the tenant has no storefront). The payment itself is confirmed by the gateway webhook,
# never by this page — it only tells the customer where they stand.
def _result_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title></head><body style='font-family:system-ui;text-align:center;"
        f"padding:48px 16px;color:#2C5545'><h2>{_esc(title)}</h2><p>{_esc(body)}</p></body></html>")


@router.get("/payment/success")
async def payment_success() -> HTMLResponse:
    return _result_page("Thank you — payment received",
                        "You'll get a WhatsApp confirmation shortly. You can close this page.")


@router.get("/payment/cancel")
async def payment_cancel() -> HTMLResponse:
    return _result_page("Payment not completed",
                        "Nothing was charged. Reply on WhatsApp if you'd like a new payment link.")
