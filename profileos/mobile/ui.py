"""The touch interface, as one self-contained page.

It is one file with no external requests on purpose. A shop floor has patchy
wifi and no reason to reach the internet, and a phone in a stairwell measuring
an opening has neither — so the page must work from the office machine alone,
and keep working when the connection drops mid-form.

Laid out for a hand rather than a mouse: one column, targets at least 48 px,
the tab bar at the bottom where a thumb reaches, numbers in a monospaced face
so 8 and 3 cannot be confused at arm's length, and Hebrew right to left.
"""

from __future__ import annotations

from typing import Any

PAGE = """<!doctype html>
<html lang="__LANG__" dir="__DIR__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0F0E0D">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>__TITLE__</title>
<style>
__FONT_FACE__
:root{
  --bg:#0F0E0D; --card:#151413; --field:#0A0908; --line:#262320; --text:#F4F2EF;
  --muted:#A7A199; --accent:#E29A47; --accent-strong:#D07E2F;
  --ok:#3FA672; --warn:#D9B13B; --bad:#DE5D5D;
  --radius:14px; --tap:52px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text)}
body{
  font-family:'Heebo',-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans Hebrew",Arial,sans-serif;
  font-size:16px;line-height:1.45;padding-bottom:calc(var(--tap) + 28px + env(safe-area-inset-bottom));
}
.num{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
header{
  position:sticky;top:0;z-index:5;background:rgba(15,14,13,.94);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line);padding:14px 16px calc(14px + env(safe-area-inset-top));
  padding-top:calc(14px + env(safe-area-inset-top));
}
header h1{margin:0;font-size:19px;font-weight:650;letter-spacing:-.01em}
header p{margin:2px 0 0;font-size:13px;color:var(--muted)}
main{padding:16px;max-width:640px;margin:0 auto}
section{display:none}
section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
      padding:16px;margin:0 0 12px}
.card h2{margin:0 0 10px;font-size:16px;font-weight:600}
label{display:block;font-size:13px;color:var(--muted);margin:12px 0 5px}
input,select,textarea{
  width:100%;min-height:var(--tap);background:var(--field);color:var(--text);
  border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:17px;
  font-family:inherit;
}
input[inputmode=numeric]{font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:.04em}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);border-color:var(--accent)}
.row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
button{
  width:100%;min-height:var(--tap);border:0;border-radius:12px;background:var(--accent-strong);
  color:#1C0F02;font-size:17px;font-weight:600;font-family:inherit;margin-top:14px;cursor:pointer;
}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--text)}
button.bad{background:var(--bad)}
button:active{transform:translateY(1px)}
.stack button{margin-top:8px}
nav{
  position:fixed;bottom:0;inset-inline:0;z-index:6;display:grid;grid-auto-flow:column;
  background:rgba(15,14,13,.97);border-top:1px solid var(--line);
  padding-bottom:env(safe-area-inset-bottom);
}
nav button{
  margin:0;border-radius:0;background:transparent;color:var(--muted);
  font-size:12px;font-weight:500;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:3px;padding:8px 0;min-height:var(--tap);
}
nav button.on{color:var(--accent)}
nav svg{width:22px;height:22px;stroke:currentColor;fill:none;stroke-width:1.7}
.item{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}
.item:last-child{border-bottom:0}
.item .grow{flex:1;min-width:0}
.item b{display:block;font-size:15px;font-weight:600}
.item small{color:var(--muted);font-size:13px}
.pill{font-size:12px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);
      color:var(--muted);white-space:nowrap}
.pill.ok{color:var(--ok);border-color:rgba(63,166,114,.4)}
.pill.warn{color:var(--warn);border-color:rgba(217,177,59,.4)}
.pill.bad{color:var(--bad);border-color:rgba(222,93,93,.4)}
.bar{height:6px;border-radius:999px;background:var(--field);overflow:hidden;margin:10px 0 2px}
.bar i{display:block;height:100%;background:var(--accent)}
.note{font-size:13px;color:var(--muted);margin:8px 0 0}
.msg{border-radius:10px;padding:10px 12px;margin:10px 0 0;font-size:14px;display:none}
.msg.on{display:block}
.msg.ok{background:rgba(63,166,114,.15);color:#8FD9B2}
.msg.bad{background:rgba(222,93,93,.15);color:#F0A8A8}
.big{font-size:34px;font-weight:700;letter-spacing:.16em;text-align:center}
.empty{color:var(--muted);font-size:14px;text-align:center;padding:26px 8px}
svg.dwg{width:100%;height:auto;background:#fff;border-radius:10px}
</style>
</head>
<body>
<header><h1 id="hdr">__TITLE__</h1><p id="sub">__SUB__</p></header>

<main>
  <!-- pairing -->
  <section id="s-pair" class="on">
    <div class="card">
      <h2 data-t="mobile.pair_title"></h2>
      <p class="note"><span data-t="mobile.pair_help"></span>
        <span class="num">profileos mobile pair</span></p>
      <label for="code" data-t="mobile.pair_code"></label>
      <input id="code" class="big num" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="000000">
      <label for="devname" data-t="mobile.device_name"></label>
      <input id="devname" data-tp="mobile.device_example">
      <button onclick="pair()" data-t="mobile.pair_button"></button>
      <div id="pair-msg" class="msg"></div>
      <label data-t="mobile.language"></label>
      <div id="langs" class="stack"></div>
    </div>
  </section>

  <!-- jobs -->
  <section id="s-jobs">
    <div class="card">
      <h2 data-t="mobile.scan"></h2>
      <label for="scan" data-t="mobile.scan_field"></label>
      <input id="scan" autocapitalize="characters" autocomplete="off" placeholder="EL-12AB34">
      <label for="stage" data-t="mobile.stage"></label>
      <select id="stage"></select>
      <button onclick="scan()" data-t="mobile.set_stage"></button>
      <div id="scan-msg" class="msg"></div>
    </div>
    <div class="card">
      <h2 id="wo-title" data-t="mobile.work_order"></h2>
      <div class="bar"><i id="wo-bar" style="width:0%"></i></div>
      <p class="note" id="wo-note">—</p>
      <div id="wo-items"></div>
    </div>
  </section>

  <!-- measuring -->
  <section id="s-measure">
    <div class="card">
      <h2 data-t="mobile.site_measure"></h2>
      <p class="note" data-t="mobile.measure_help"></p>
      <label for="mref" data-t="mobile.opening_ref"></label>
      <input id="mref" autocapitalize="characters" placeholder="W-01">
      <label data-t="mobile.widths"></label>
      <div class="row">
        <input id="w1" class="num" inputmode="numeric" data-tp="mobile.top">
        <input id="w2" class="num" inputmode="numeric" data-tp="mobile.middle">
        <input id="w3" class="num" inputmode="numeric" data-tp="mobile.bottom">
      </div>
      <label data-t="mobile.heights"></label>
      <div class="row">
        <input id="h1" class="num" inputmode="numeric" data-tp="mobile.right">
        <input id="h2" class="num" inputmode="numeric" data-tp="mobile.middle">
        <input id="h3" class="num" inputmode="numeric" data-tp="mobile.left">
      </div>
      <label data-t="mobile.diagonals"></label>
      <div class="row2">
        <input id="d1" class="num" inputmode="numeric" data-tp="mobile.diagonal">
        <input id="d2" class="num" inputmode="numeric" data-tp="mobile.diagonal">
      </div>
      <label for="mnote" data-t="mobile.note"></label>
      <input id="mnote" data-tp="mobile.note_example">
      <button onclick="sendMeasurement()" data-t="mobile.send"></button>
      <div id="m-msg" class="msg"></div>
    </div>
    <div class="card">
      <h2 data-t="mobile.recent"></h2>
      <div id="m-list"><p class="empty" data-t="mobile.none_yet"></p></div>
    </div>
  </section>

  <!-- check -->
  <section id="s-check">
    <div class="card">
      <h2 data-t="mobile.feasibility"></h2>
      <p class="note" data-t="mobile.check_help"></p>
      <div class="row2">
        <div><label for="cw" data-t="mobile.width"></label><input id="cw" class="num" inputmode="numeric" value="1200"></div>
        <div><label for="ch" data-t="mobile.height"></label><input id="ch" class="num" inputmode="numeric" value="1400"></div>
      </div>
      <label for="ctype" data-t="mobile.opening_type"></label>
      <select id="ctype">
        <option value="fixed" data-t="opening.fixed"></option>
        <option value="casement" data-t="opening.casement"></option>
        <option value="tilt_turn" selected data-t="opening.tilt_turn"></option>
        <option value="sliding" data-t="opening.sliding"></option>
        <option value="door" data-t="opening.door"></option>
      </select>
      <label for="csill" data-t="mobile.sill_height"></label>
      <input id="csill" class="num" inputmode="numeric" value="900">
      <button onclick="check()" data-t="mobile.check_button"></button>
      <div id="c-msg" class="msg"></div>
      <div id="c-out"></div>
    </div>
  </section>

  <!-- drawings -->
  <section id="s-draw">
    <div class="card">
      <h2 data-t="mobile.drawings"></h2>
      <div id="d-list"><p class="empty" data-t="mobile.no_elements"></p></div>
    </div>
    <div class="card" id="d-view" style="display:none">
      <h2 id="d-title">—</h2>
      <div id="d-svg"></div>
    </div>
  </section>
</main>

<nav id="tabs"></nav>
<datalist id="languages"></datalist>

<script>
const API = "__BASE__";
const T = __CATALOGUE__;          // the whole vocabulary, in one go
const LANGS = __LANGUAGES__;      // what a picker can offer
const LANG = "__LANG__";

function t(key){ return T[key] || key.split(".").pop().replace(/_/g, " "); }

function paint(root){
  (root || document).querySelectorAll("[data-t]").forEach(node => {
    node.textContent = t(node.dataset.t);
  });
  (root || document).querySelectorAll("[data-tp]").forEach(node => {
    node.placeholder = t(node.dataset.tp);
  });
}
let TOKEN = localStorage.getItem("pos.token") || "";
let DEVICE = localStorage.getItem("pos.device") || "";
let SCOPES = (localStorage.getItem("pos.scopes") || "").split(",").filter(Boolean);

const TABS = [
  {id:"jobs",   key:"mobile.tab_jobs",     scope:"jobs",
   icon:'<path d="M4 7h16M4 12h16M4 17h10"/>'},
  {id:"measure",key:"mobile.tab_measure",  scope:"measure",
   icon:'<path d="M3 9h18v6H3z"/><path d="M7 9v3M11 9v4M15 9v3M19 9v4"/>'},
  {id:"check",  key:"mobile.tab_check",    scope:"measure",
   icon:'<path d="M4 12l5 5L20 6"/>'},
  {id:"draw",   key:"mobile.tab_drawings", scope:"drawings",
   icon:'<path d="M4 4h16v16H4z"/><path d="M4 9h16M9 9v11"/>'},
];

function show(id){
  document.querySelectorAll("section").forEach(s => s.classList.toggle("on", s.id === "s"+"-"+id));
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === id));
  if(id === "jobs") loadJobs();
  if(id === "measure") loadMeasurements();
  if(id === "draw") loadDrawings();
}

function buildTabs(){
  const nav = document.getElementById("tabs");
  const usable = TABS.filter(tab => SCOPES.includes(tab.scope));
  nav.innerHTML = usable.map(tab =>
    `<button data-tab="${tab.id}" onclick="show('${tab.id}')">
       <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">${tab.icon}</svg>${t(tab.key)}
     </button>`).join("");
  nav.style.display = TOKEN ? "grid" : "none";
  if(TOKEN && usable.length) show(usable[0].id);
}

function say(id, text, ok){
  const box = document.getElementById(id);
  box.textContent = text;
  box.className = "msg on " + (ok ? "ok" : "bad");
  if(ok) setTimeout(() => box.className = "msg", 4000);
}

async function api(path, options){
  const opts = Object.assign({headers:{}}, options || {});
  opts.headers["Content-Type"] = "application/json";
  if(TOKEN){ opts.headers["X-Device-Id"] = DEVICE; opts.headers["X-Device-Token"] = TOKEN; }
  const response = await fetch(API + path, opts);
  if(response.status === 401){ signOut(); throw new Error(t("mobile.session_expired")); }
  const data = await response.json().catch(() => ({}));
  if(!response.ok) throw new Error(data.detail || "שגיאה");
  return data;
}

function signOut(){
  TOKEN = DEVICE = ""; SCOPES = [];
  localStorage.removeItem("pos.token");
  localStorage.removeItem("pos.device");
  localStorage.removeItem("pos.scopes");
  document.getElementById("tabs").style.display = "none";
  document.querySelectorAll("section").forEach(s => s.classList.remove("on"));
  document.getElementById("s-pair").classList.add("on");
}

async function pair(){
  const code = document.getElementById("code").value.trim();
  const name = document.getElementById("devname").value.trim();
  try{
    const data = await api("/api/pair", {method:"POST", body:JSON.stringify({code, description:name})});
    TOKEN = data.token; DEVICE = data.device_id; SCOPES = data.scopes;
    localStorage.setItem("pos.token", TOKEN);
    localStorage.setItem("pos.device", DEVICE);
    localStorage.setItem("pos.scopes", SCOPES.join(","));
    document.getElementById("sub").textContent = data.name + " · " + (data.station || "");
    buildTabs();
  }catch(err){ say("pair-msg", err.message, false); }
}

async function loadJobs(){
  try{
    const data = await api("/api/jobs");
    document.getElementById("wo-title").textContent = data.name || t("mobile.work_order");
    document.getElementById("wo-bar").style.width = (data.progress || 0) + "%";
    // Counts by stage rather than one percentage: the floor's question is
    // "what is waiting for me", not "how far along is the job".
    document.getElementById("wo-note").textContent =
      data.total ? `${data.total} פריטים · ` +
                   (data.counts || []).map(c => `${c.label} ${c.n}`).join(" · ")
                 : t("mobile.no_work_order");
    const select = document.getElementById("stage");
    if(!select.options.length && data.stages){
      select.innerHTML = data.stages.map(s => `<option value="${s.id}">${s.label}</option>`).join("");
    }
    document.getElementById("wo-items").innerHTML = (data.items || []).map(i => `
      <div class="item">
        <div class="grow"><b>${i.ref}</b><small>${i.description}</small></div>
        <span class="pill ${i.tone}">${i.stage}</span>
      </div>`).join("") || `<p class="empty">${t("mobile.none_yet")}</p>`;
  }catch(err){ document.getElementById("wo-note").textContent = err.message; }
}

async function scan(){
  const payload = document.getElementById("scan").value.trim();
  const stage = document.getElementById("stage").value;
  try{
    const data = await api("/api/scan", {method:"POST", body:JSON.stringify({payload, stage})});
    say("scan-msg", `${data.ref} → ${data.stage}`, true);
    document.getElementById("scan").value = "";
    loadJobs();
  }catch(err){ say("scan-msg", err.message, false); }
}

function num(id){ const v = parseFloat(document.getElementById(id).value); return isNaN(v) ? 0 : v; }

async function sendMeasurement(){
  const body = {
    reference: document.getElementById("mref").value.trim(),
    widths: [num("w1"), num("w2"), num("w3")],
    heights: [num("h1"), num("h2"), num("h3")],
    diagonals: (num("d1") && num("d2")) ? [num("d1"), num("d2")] : null,
    note: document.getElementById("mnote").value.trim(),
  };
  try{
    const data = await api("/api/measurements", {method:"POST", body:JSON.stringify(body)});
    const problems = data.problems || [];
    say("m-msg",
        problems.length ? problems.join(" · ")
                        : `${data.width} × ${data.height} ${t("unit.mm")}`,
        problems.length === 0);
    ["w1","w2","w3","h1","h2","h3","d1","d2","mnote"].forEach(id => document.getElementById(id).value = "");
    loadMeasurements();
  }catch(err){ say("m-msg", err.message, false); }
}

async function loadMeasurements(){
  try{
    const data = await api("/api/measurements");
    document.getElementById("m-list").innerHTML = (data.records || []).map(r => `
      <div class="item">
        <div class="grow"><b>${r.reference}</b><small class="num">${r.width} × ${r.height} · ${r.when}</small></div>
        <span class="pill ${r.problems.length ? "warn" : "ok"}">${r.problems.length ? "לבדוק" : "תקין"}</span>
      </div>`).join("") || `<p class="empty">${t("mobile.none_yet")}</p>`;
  }catch(err){ /* offline: keep what is on screen */ }
}

async function check(){
  const body = {
    width: num("cw"), height: num("ch"),
    opening_type: document.getElementById("ctype").value,
    sill_height: num("csill"),
  };
  try{
    const data = await api("/api/check", {method:"POST", body:JSON.stringify(body)});
    say("c-msg", data.verdict, data.can_be_made);
    document.getElementById("c-out").innerHTML = (data.findings || []).map(f => `
      <div class="item">
        <div class="grow"><b>${f.what}</b><small>${f.where}</small></div>
        <span class="pill ${f.tone}">${f.severity}</span>
      </div>`).join("") + (data.glass ? `
      <div class="item"><div class="grow"><b>${t("member.glass")}</b>
        <small class="num">${data.glass}</small></div></div>` : "");
  }catch(err){ say("c-msg", err.message, false); }
}

async function loadDrawings(){
  try{
    const data = await api("/api/elements");
    document.getElementById("d-list").innerHTML = (data.elements || []).map(e => `
      <div class="item" onclick="openDrawing('${e.ref}')">
        <div class="grow"><b>${e.ref}</b><small class="num">${e.size}</small></div>
        <span class="pill">${t("drawing.elevation")}</span>
      </div>`).join("") || `<p class="empty">${t("mobile.no_elements")}</p>`;
  }catch(err){ /* leave the list as it was */ }
}

async function openDrawing(ref){
  const response = await fetch(API + "/api/elements/" + encodeURIComponent(ref) + "/elevation.svg",
    {headers:{"X-Device-Id":DEVICE, "X-Device-Token":TOKEN}});
  const markup = await response.text();
  document.getElementById("d-title").textContent = ref;
  document.getElementById("d-svg").innerHTML = markup.replace("<svg ", '<svg class="dwg" ');
  document.getElementById("d-view").style.display = "block";
  document.getElementById("d-view").scrollIntoView({behavior:"smooth"});
}

document.getElementById("code").addEventListener("input", event => {
  if(event.target.value.replace(/\\D/g, "").length === 6){ event.target.value = event.target.value.replace(/\\D/g, ""); pair(); }
});
document.getElementById("scan").addEventListener("keydown", event => {
  // A barcode wedge types the payload and presses Enter, so that is the submit.
  if(event.key === "Enter"){ event.preventDefault(); scan(); }
});

function buildLanguages(){
  // A language switch reloads the page rather than re-rendering: the direction
  // of the whole document changes with it, and the browser does that properly.
  document.getElementById("langs").innerHTML = LANGS.map(l =>
    `<button class="ghost" ${l.code === LANG ? 'style="border-color:var(--accent)"' : ""}
       onclick="location.search='?lang=${l.code}'">${l.native}</button>`).join("");
}

paint();
buildLanguages();
if(TOKEN){ document.getElementById("s-pair").classList.remove("on"); buildTabs(); }
else { document.getElementById("tabs").style.display = "none"; }
</script>
</body>
</html>
"""


def render(*, title: str, subtitle: str, base: str, language: Any = None) -> str:
    """The page, in one language, with the whole vocabulary inlined.

    The catalogue is embedded rather than fetched: the phone is on a shop-floor
    network that drops, and a screen whose labels arrive in a second request is
    a screen that sometimes has no labels.
    """
    import json

    from ..design.tokens import font_face_css
    from ..i18n import available, catalogue, get_locale

    locale = get_locale(language)
    return (
        PAGE.replace("__FONT_FACE__", font_face_css(embed=True))
        .replace("__TITLE__", title)
        .replace("__SUB__", subtitle)
        .replace("__BASE__", base)
        .replace("__LANG__", locale.code)
        .replace("__DIR__", "rtl" if locale.rtl else "ltr")
        .replace("__CATALOGUE__", json.dumps(catalogue(locale.language), ensure_ascii=False))
        .replace(
            "__LANGUAGES__",
            json.dumps(
                [{"code": item.code, "native": item.native} for item in available()],
                ensure_ascii=False,
            ),
        )
    )


__all__ = ["PAGE", "render"]
