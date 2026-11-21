"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const main = $("#main"), dialog = $("#dialog");
const S = { state: { state: "offline" }, view: "now", folder: null, synced: 0, queue: [], queueKey: "", cfg: null };

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M6 5v14l9-7zM16 5h3v14h-3z"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M18 5v14l-9-7zM5 5h3v14H5z"/></svg>',
  card: '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 15h4M15 10a3 3 0 0 1 0 4"/></svg>',
  trash: '<svg class="icon" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>',
  edit: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  back: '<svg class="icon" viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>',
  plus: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  headphones: '<svg class="icon" viewBox="0 0 24 24"><path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 15a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2zM3 15a2 2 0 0 0 2 2h1v-6H5a2 2 0 0 0-2 2z"/></svg>',
  bt: '<svg class="icon" viewBox="0 0 24 24"><path d="M7 7l10 10-5 5V2l5 5L7 17"/></svg>',
  sort: '<svg class="icon" viewBox="0 0 24 24"><path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4"/></svg>',
  up: '<svg class="icon" viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg>',
  down: '<svg class="icon" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>',
  camera: '<svg class="icon" viewBox="0 0 24 24"><path d="M4 8a2 2 0 0 1 2-2h2l2-2h4l2 2h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.5"/></svg>',
  move: '<svg class="icon" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M10 13h6M13 10l3 3-3 3"/></svg>',
  vol: '<svg class="icon" viewBox="0 0 24 24"><path d="M11 5L6 9H3v6h3l5 4zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>',
  music: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
};

// ---------- Helfer ----------
async function api(path, body, method) {
  const opt = body !== undefined
    ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { method: method || "GET" };
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw new Error("Die Box ist nicht erreichbar"); }
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = text; }
  if (!r.ok) throw new Error((data && data.error) || `Fehler ${r.status}`);
  return data;
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg; t.setAttribute("role", kind === "error" ? "alert" : "status");
  $("#toasts").append(t); setTimeout(() => t.remove(), kind === "error" ? 6000 : 3000);
}
const run = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, "error"); } };
const fmt = (s) => { s = Math.max(0, Math.round(s || 0)); const m = Math.floor(s / 60); return (m >= 60 ? Math.floor(m / 60) + ":" + String(m % 60).padStart(2, "0") : m) + ":" + String(s % 60).padStart(2, "0"); };
const size = (b) => b > 1e9 ? (b / 1e9).toFixed(1).replace(".", ",") + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB";
function color(name) { let h = 0; for (const c of String(name)) h = (h * 31 + c.codePointAt(0)) % 360; return `hsl(${h} 55% 48%)`; }
function initials(name) {
  const l = String(name).split("/").pop().trim();
  const m = l.match(/^(Folge|Teil|Band|Episode|Kapitel|CD|Nr\.?)\s*(\d+)/i) || l.match(/^(\d+)\b/);
  if (m) return m[m.length - 1];
  const p = l.split(/\s+/); return ((p[0]?.[0] || "♪") + (p[1]?.[0] || "")).toUpperCase();
}
function cover(folder, hasImg = true, v = "") {
  if (folder == null || folder === "") return `<div class="cover" style="--c:var(--surface-2);color:var(--muted)">${ICON.music.replace("<svg", '<svg width="40%"')}</div>`;
  const img = hasImg ? `<img loading="lazy" alt="" src="/api/cover?folder=${encodeURIComponent(folder)}${v}" onerror="this.remove()">` : "";
  return `<div class="cover" style="--c:${color(folder)}">${esc(initials(folder))}${img}</div>`;
}
const leaf = (f) => String(f || "").split("/").pop();
// 3D-Figur, die halb über dem Cover steht (PNG mit durchsichtigem Hintergrund)
const figUrl = (folder, v) => v ? `/api/figure?folder=${encodeURIComponent(folder)}&v=${v}` : "";
const figImg = (url) => url ? `<img class="fig" alt="" src="${esc(url)}" onerror="this.remove()">` : "";
const act = (action, value) => run(() => api("/api/player", { action, value }))();

function openDialog(html) { dialog.innerHTML = html; if (!dialog.open) dialog.showModal(); return dialog; }
function closeDialog() { if (dialog.open) dialog.close(); }
dialog.addEventListener("close", () => { if (S.onDialogClose) { const f = S.onDialogClose; S.onDialogClose = null; f(); } });
function ask(title, text, okLabel = "OK", danger = false) {
  return new Promise((res) => {
    openDialog(`<h2>${esc(title)}</h2><p>${esc(text)}</p><div class="actions"><button class="btn" data-x="0">Abbrechen</button><button class="btn ${danger ? "danger" : "primary"}" data-x="1">${esc(okLabel)}</button></div>`);
    let val = false; S.onDialogClose = () => res(val);
    $$("[data-x]", dialog).forEach((b) => b.onclick = () => { val = b.dataset.x === "1"; closeDialog(); });
  });
}
function prompt2(title, label, value = "", okLabel = "Speichern") {
  return new Promise((res) => {
    openDialog(`<form method="dialog"><h2>${esc(title)}</h2><label class="field"><span>${esc(label)}</span><input type="text" id="pv" value="${esc(value)}" autocomplete="off"></label><div class="actions"><button class="btn" type="button" id="pc">Abbrechen</button><button class="btn primary" value="ok">${esc(okLabel)}</button></div></form>`);
    let val = null; S.onDialogClose = () => res(val);
    $("#pc", dialog).onclick = closeDialog;
    $("form", dialog).onsubmit = () => { val = $("#pv", dialog).value.trim() || null; };
    $("#pv", dialog).select();
  });
}

// ---------- Navigation ----------
function go(view, folder = null) {
  S.view = view; S.folder = folder;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  history.replaceState(null, "", "#" + view + (folder ? "/" + encodeURIComponent(folder) : ""));
  render(); window.scrollTo(0, 0);
}
$$("#nav button").forEach((b) => b.onclick = () => go(b.dataset.view));
function render() { ({ now: viewNow, hoerspiele: S.folder ? viewFolder : viewMusic, musik: S.folder ? viewFolder : viewMusic, cards: viewCards, settings: viewSettings }[S.view])(); renderMini(); }

// ---------- Live-Status ----------
function elapsedNow() { const s = S.state; return s.state === "play" ? Math.min(s.duration || 1e9, s.elapsed + (performance.now() - S.synced) / 1000) : s.elapsed || 0; }
function onState(s) {
  const prev = S.state; S.state = s; S.synced = performance.now();
  $("#brand-name").textContent = s.name || "Klangkiste"; document.title = s.name || "Klangkiste";
  const key = [s.folder, s.url, s.tracks].join("|");
  if (key !== S.queueKey) { S.queueKey = key; loadQueue(); }
  if (S.view === "now") { if (prev.folder !== s.folder || prev.controller !== s.controller || prev.cover_url !== s.cover_url || prev.figure !== s.figure || prev.state === "offline" || s.state === "offline" || !$(".now")) viewNow(); else updateNow(); }
  renderMini(); updateDot();
}
async function loadQueue() { try { S.queue = await api("/api/queue"); } catch { S.queue = []; } if (S.view === "now") renderQueue(); }
function connect() {
  const es = new EventSource("/api/events");
  es.addEventListener("state", (e) => onState(JSON.parse(e.data)));
  es.addEventListener("toast", (e) => { const d = JSON.parse(e.data); toast(d.message, d.kind); });
  es.addEventListener("card", (e) => { const d = JSON.parse(e.data); if (!d.known) unknownCard(d.id); else hideBanner(); if (S.view === "cards") viewCards(); });
  es.addEventListener("learned", (e) => { const d = JSON.parse(e.data); closeDialog(); toast("Karte gespeichert: " + leaf(d.target)); if (S.view !== "now") render(); });
  es.addEventListener("backup", (e) => { S.backup = JSON.parse(e.data); if (S.view === "settings") paintBackup(); });
  es.addEventListener("update", () => { S.state.update = true; updateDot(); });
  es.onerror = () => {
    if (S.state.state !== "offline") onState({ ...S.state, state: "offline", lost: true });
    // Manche Browser geben nach einem Fehler auf – dann selbst neu verbinden
    if (es.readyState === 2) setTimeout(connect, 3000);
  };
}
setInterval(() => { if (S.view === "now" && S.state.state === "play") tick(); }, 500);
setInterval(run(async () => { if (!document.hidden && S.state.state === "play") { S.state = await api("/api/state"); S.synced = performance.now(); } }), 15000);

function updateDot() { $('#nav button[data-view="settings"]').classList.toggle("dot", !!S.state.update); }
function nowCover(s, idle) {
  if (s.spotify) return `<div class="cover spotify">${s.cover_url ? `<img alt="" src="${esc(s.cover_url)}" onerror="this.remove()">` : ""}<svg viewBox="0 0 24 24" width="38%" fill="currentColor"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm4.6 14.4a.6.6 0 0 1-.9.2c-2.4-1.5-5.4-1.8-9-1a.6.6 0 1 1-.3-1.2c3.9-.9 7.2-.5 9.9 1.1.3.2.4.6.3.9Zm1.2-2.7a.8.8 0 0 1-1 .3c-2.8-1.7-7-2.2-10.3-1.2a.8.8 0 1 1-.4-1.5c3.8-1.1 8.4-.6 11.5 1.3.3.2.4.7.2 1.1Zm.1-2.8C14.6 9 9.2 8.8 6.1 9.7a.9.9 0 1 1-.5-1.8c3.6-1.1 9.5-.9 13.3 1.3a.9.9 0 0 1-1 1.7Z"/></svg></div>`;
  return `<div class="figwrap${s.figure && !idle ? " has-fig" : ""}">${cover(idle ? null : s.folder ?? "Radio", s.folder != null)}${idle ? "" : figImg(s.figure)}</div>`;
}

// ---------- 3D-Figur aus einer STL-Datei ----------
const FIGICON = `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="2.6"/><path d="M8.5 21l1-7.5L7 11l2.2-2.6h5.6L17 11l-2.5 2.5 1 7.5"/><path d="M6 21h12"/></svg>`;
function loadScript(src) {
  return new Promise((res, rej) => {
    if (document.querySelector(`script[src="${src}"]`) && window.KlangkisteModel) return res();
    const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = () => rej(new Error("Konnte den Figuren-Zeichner nicht laden")); document.head.appendChild(s);
  });
}
function figureDialog(folder, has, after) {
  const COLORS = ["#e8dfd0", "#d9b38c", "#9c6b45", "#5b6b73", "#2f3a3d", "#c94f3d", "#e0a33a", "#4f8f5b", "#3d74b8", "#a46bb8"];
  const st = { kind: null, model: null, photo: null, color: localStorage.getItem("kk-figcolor") || COLORS[0], yaw: -25, pitch: 12, zUp: null, original: true, tol: 30 };
  openDialog(`<h2>Figur für „${esc(leaf(folder))}“</h2>
    <p class="small muted" style="margin-top:-4px">Die Figur steht dann halb über dem Cover – in der Übersicht, in der Ordneransicht und bei „Läuft“.</p>
    <div class="fg-pick">
      <label class="btn">${FIGICON} 3D-Datei<input type="file" id="fg-file" multiple hidden></label>
      <label class="btn">${ICON.camera} Foto<input type="file" id="fg-photo" accept="image/*" hidden></label>
    </div>
    <p class="small muted" style="margin:8px 0 0">3D: STL, 3MF (mit Druckfarben aus Bambu Studio), GLB/glTF oder OBJ (mit Textur). Bei OBJ die .mtl und das Bild mit auswählen. Foto: Figur vor einfarbigem Hintergrund, am besten weißes Papier.</p>
    <div id="fg-stage" class="fg-stage" hidden><div class="figwrap has-fig fg-prev">${cover(folder, true)}<canvas id="fg-canvas"></canvas></div></div>
    <p id="fg-msg" class="small muted" style="min-height:1.2em;margin:10px 0 0"></p>
    <div id="fg-ctl" hidden>
      <div id="fg-c-model">
        <label class="switch" id="fg-orig-row"><span>Originalfarben der Datei<br><span class="muted small">Aus: eine Farbe für die ganze Figur</span></span><input type="checkbox" id="fg-orig" checked></label>
        <div class="field" id="fg-colrow"><span>Farbe</span><div class="row wrap" style="gap:6px">${COLORS.map((c) => `<button class="fg-sw" data-c="${c}" style="background:${c}" aria-label="Farbe ${c}"></button>`).join("")}<input type="color" id="fg-color" aria-label="Eigene Farbe" style="width:34px;height:34px;padding:0;border:0;background:none"></div></div>
        <label class="field"><span>Drehen: <b id="fg-yaw-v">-25</b>°</span><input type="range" id="fg-yaw" min="-180" max="180" value="-25"></label>
        <label class="field"><span>Von oben: <b id="fg-pitch-v">12</b>°</span><input type="range" id="fg-pitch" min="-10" max="45" value="12"></label>
        <label class="switch"><span>Figur liegt falsch herum<br><span class="muted small">Wenn sie auf dem Rücken liegt oder kopfsteht</span></span><input type="checkbox" id="fg-axis"></label>
      </div>
      <div id="fg-c-photo">
        <label class="field"><span>Hintergrund entfernen: <b id="fg-tol-v">30</b></span><input type="range" id="fg-tol" min="5" max="100" value="30"></label>
        <p class="small muted" style="margin:-4px 0 0">Bleibt Hintergrund stehen: weiter nach rechts. Fehlen Teile der Figur: nach links.</p>
      </div>
    </div>
    <div class="actions">${has ? `<button class="btn danger" id="fg-del" style="margin-right:auto">Entfernen</button>` : ""}<button class="btn" data-x="0">Abbrechen</button><button class="btn primary" id="fg-save" disabled>Speichern</button></div>`);
  $$("[data-x]", dialog).forEach((b) => b.onclick = closeDialog);
  const msg = (t) => $("#fg-msg").textContent = t;
  const M = () => window.KlangkisteModel;
  const make = (size) => st.kind === "photo" ? M().cutPhoto(st.photo, st.tol, size) : M().render(st.model, { ...st, zUp: st.zUp ?? st.model.zUp, size });
  const draw = (size = 300) => {
    const out = make(size);
    const cv = $("#fg-canvas"); cv.width = out.width; cv.height = out.height; cv.getContext("2d").drawImage(out, 0, 0);
    return out;
  };
  let timer;
  const redraw = () => { clearTimeout(timer); timer = setTimeout(() => { try { draw(); } catch (e) { msg(e.message); } }, 60); };
  const markColor = () => $$(".fg-sw").forEach((b) => b.classList.toggle("on", b.dataset.c === st.color));
  const show = () => {
    $("#fg-stage").hidden = false; $("#fg-ctl").hidden = false; $("#fg-save").disabled = false;
    $("#fg-c-model").hidden = st.kind !== "model"; $("#fg-c-photo").hidden = st.kind !== "photo";
    if (st.kind === "model") {
      $("#fg-orig-row").hidden = !st.model.colored;
      $("#fg-colrow").hidden = st.model.colored && st.original;
      $("#fg-axis").checked = false; st.zUp = null;
    }
    $("#fg-color").value = st.color; markColor();
  };
  const busyMsg = async (t) => { msg(t); await new Promise((r) => setTimeout(r, 30)); };
  $("#fg-file").onchange = run(async (e) => {
    const files = [...e.target.files]; if (!files.length) return;
    await busyMsg("Lese die Datei …");
    await loadScript("/static/model.js");
    st.model = await M().load(files); st.kind = "model"; st.photo = null;
    const n = st.model.pos.length / 9;
    show();
    await busyMsg(`${n.toLocaleString("de-DE")} Dreiecke${st.model.colored ? (st.model.textures.length ? ", mit Textur" : ", mit Farben") : ""} – zeichne …`);
    draw();
    msg(`${n.toLocaleString("de-DE")} Dreiecke${st.model.colored ? (st.model.textures.length ? ", mit Textur" : ", mit Farben") : ""} – so steht die Figur auf dem Cover:`);
    e.target.value = "";
  });
  $("#fg-photo").onchange = run(async (e) => {
    const f = e.target.files[0]; if (!f) return;
    await busyMsg("Lese das Foto …");
    await loadScript("/static/model.js");
    st.photo = await M().loadPhoto(f); st.kind = "photo"; st.model = null;
    show();
    $("#fg-c-photo").hidden = st.photo.cutout;
    draw();
    msg(st.photo.cutout ? "Das Bild ist schon freigestellt." : "Hintergrund entfernt – so steht die Figur auf dem Cover:");
    e.target.value = "";
  });
  $$(".fg-sw").forEach((b) => b.onclick = () => { st.color = b.dataset.c; $("#fg-color").value = st.color; markColor(); redraw(); });
  $("#fg-color").oninput = (e) => { st.color = e.target.value; markColor(); redraw(); };
  $("#fg-orig").onchange = (e) => { st.original = e.target.checked; $("#fg-colrow").hidden = st.original; redraw(); };
  $("#fg-yaw").oninput = (e) => { st.yaw = +e.target.value; $("#fg-yaw-v").textContent = st.yaw; redraw(); };
  $("#fg-pitch").oninput = (e) => { st.pitch = +e.target.value; $("#fg-pitch-v").textContent = st.pitch; redraw(); };
  $("#fg-axis").onchange = (e) => { st.zUp = e.target.checked ? !st.model.zUp : null; redraw(); };
  $("#fg-tol").oninput = (e) => { st.tol = +e.target.value; $("#fg-tol-v").textContent = st.tol; redraw(); };
  $("#fg-save").onclick = run(async (e) => {
    const btn = e.currentTarget; btn.disabled = true;
    await busyMsg("Zeichne die Figur …");
    const out = make(560);
    const blob = await new Promise((r) => out.toBlob(r, "image/png"));
    const r = await fetch("/api/figure?folder=" + encodeURIComponent(folder), { method: "POST", headers: { "Content-Type": "image/png" }, body: blob });
    if (!r.ok) { btn.disabled = false; throw new Error((await r.json().catch(() => ({}))).error || "Speichern fehlgeschlagen"); }
    try { localStorage.setItem("kk-figcolor", st.color); } catch (err) {}
    closeDialog(); toast("Figur gespeichert"); after && after();
  });
  if ($("#fg-del")) $("#fg-del").onclick = run(async () => {
    await api("/api/figure?folder=" + encodeURIComponent(folder), undefined, "DELETE");
    closeDialog(); toast("Figur entfernt"); after && after();
  });
}

// ---------- Ansicht: Läuft ----------
function viewNow() {
  const s = S.state;
  if (s.state === "offline") {
    main.innerHTML = `<div class="empty">${ICON.music}<h2>${s.lost ? "Verbindung zur Box verloren" : "Der Abspieler startet noch …"}</h2><p>${s.lost ? "Ich versuche es automatisch erneut." : "Einen Moment bitte."}</p></div>`; return;
  }
  const idle = !s.file;
  main.innerHTML = `<div class="now">
    <div id="np-cover">${nowCover(s, idle)}</div>
    <div>
      <p class="muted small" id="np-folder" style="margin:0 0 4px;font-weight:700;text-transform:uppercase;letter-spacing:.06em"></p>
      <h1 id="np-title"></h1><p class="muted" id="np-sub"></p>
      <div class="progress" id="np-prog" role="slider" aria-label="Position"><div><i></i></div></div>
      <div class="times"><span id="np-el">0:00</span><span id="np-du">0:00</span></div>
      <div class="controls">
        <button aria-label="Zurück" data-a="prev">${ICON.prev}</button>
        <button class="play" aria-label="Play/Pause" data-a="play_pause" id="np-play"></button>
        <button aria-label="Weiter" data-a="next">${ICON.next}</button>
      </div>
      <div class="vol">${ICON.vol}<input type="range" id="np-vol" min="0" max="100" aria-label="Lautstärke"><b id="np-volv"></b></div>
      <p id="np-hp" class="small" hidden style="margin:8px 0 0;color:var(--accent);font-weight:700">${ICON.headphones} Ton über Kopfhörer</p>
      ${s.spotify ? `<p class="muted small" style="margin-top:14px">Läuft über Spotify. Die Knöpfe an der Box steuern jetzt Spotify; eine aufgelegte Figur übernimmt wieder.</p>` : ""}
      <label class="switch" style="margin-top:14px" ${s.spotify ? "hidden" : ""}><span><b>Im Haus streamen</b><br><span class="muted small">Stellt den Ton für MusicCast bereit</span></span><input type="checkbox" id="np-stream"></label>
    </div></div>
    <div class="card" style="max-width:880px;margin:28px auto 0" id="np-queue-card" hidden><h2>Titel</h2><ol class="queue" id="np-queue"></ol></div>`;
  $$(".controls button").forEach((b) => b.onclick = () => act(b.dataset.a));
  const vol = $("#np-vol");
  vol.oninput = () => { $("#np-volv").textContent = vol.value; vol.style.setProperty("--p", (vol.value / vol.max * 100) + "%"); S.volBusy = Date.now(); };
  vol.onchange = () => act("volume", +vol.value);
  $("#np-stream").onchange = (e) => act("stream", e.target.checked);
  $("#np-prog").onclick = (e) => { const r = e.currentTarget.getBoundingClientRect(); if (S.state.duration) act("seek", (e.clientX - r.left) / r.width * S.state.duration); };
  updateNow(); renderQueue();
}
function updateNow() {
  const s = S.state; if (!$(".now")) return;
  const idle = !s.file;
  $("#np-folder").textContent = idle ? "" : s.spotify ? "Spotify" : s.url ? "Radio" : pretty((s.folder || "").replace(/^(Hörspiele|Musik)\//, ""));
  $("#np-title").textContent = idle ? "Nichts ausgewählt" : s.title;
  $("#np-sub").textContent = idle ? "Leg eine Karte auf oder wähle etwas unter „Musik“." : [s.artist, s.album && s.spotify ? s.album : "", s.tracks > 1 ? `Titel ${s.track} von ${s.tracks}` : ""].filter(Boolean).join(" · ");
  $("#np-play").innerHTML = s.state === "play" ? ICON.pause : ICON.play;
  const vol = $("#np-vol");
  if (!S.volBusy || Date.now() - S.volBusy > 1500) { vol.max = (s.headphones ? S.cfg?.bluetooth?.volume_max : S.cfg?.audio.volume_max) || 100; vol.value = s.volume; $("#np-volv").textContent = s.volume; vol.style.setProperty("--p", (s.volume / vol.max * 100) + "%"); }
  $("#np-stream").checked = !!s.stream;
  $("#np-hp").hidden = !s.headphones;
  $("#np-du").textContent = fmt(s.duration);
  $$("#np-queue li").forEach((li, i) => li.classList.toggle("current", i === s.track - 1));
  tick();
}
function tick() {
  const el = $("#np-el"); if (!el) return; const s = S.state, e = elapsedNow();
  el.textContent = fmt(e); $("#np-prog i").style.width = (s.duration ? Math.min(100, e / s.duration * 100) : 0) + "%";
}
function renderQueue() {
  const card = $("#np-queue-card"); if (!card) return;
  card.hidden = S.queue.length < 2 || S.state.spotify;
  $("#np-queue").innerHTML = S.queue.map((q, i) => `<li class="${i === S.state.track - 1 ? "current" : ""}" data-i="${i}"><span>${esc(q.title)}</span><small>${q.duration ? fmt(q.duration) : ""}</small></li>`).join("");
  $$("#np-queue li").forEach((li) => li.onclick = () => act("jump", +li.dataset.i));
}
function renderMini() {
  const m = $("#mini"), s = S.state;
  if (S.view === "now" || !s.file || s.state === "offline" || s.state === "stop") { m.hidden = true; return; }
  m.hidden = false;
  m.innerHTML = `<div class="bar">${nowCover(s, false)}<div class="t"><b>${esc(s.title)}</b><span>${esc(s.spotify ? "Spotify" + (s.artist ? " · " + s.artist : "") : pretty((s.folder || "").replace(/^(Hörspiele|Musik)\//, "")))}</span></div><button aria-label="Play/Pause">${s.state === "play" ? ICON.pause : ICON.play}</button></div>`;
  $(".bar", m).onclick = () => go("now");
  $("button", m).onclick = (e) => { e.stopPropagation(); act("play_pause"); };
}

// ---------- Ansicht: Musik ----------
const CATS = ["Hörspiele", "Musik"];
const pretty = (p) => String(p || "").split("/").join(" › ");
const catOf = (p) => CATS.find((c) => p === c || String(p).startsWith(c + "/"));
const childrenOf = (folders, parent) => folders.filter((f) => f.name.startsWith(parent + "/") && !f.name.slice(parent.length + 1).includes("/"));
const isMusic = (p) => catOf(p) === "Musik";
const subLabel = (f) => f.subfolders ? `${f.subfolders} ${isMusic(f.name) ? (f.subfolders === 1 ? "Ordner" : "Ordner") : (f.subfolders === 1 ? "Folge" : "Folgen")}` : `${f.tracks} Titel`;
function tiles(list) {
  return `<div class="folders">${list.map((f) => `<button class="folder" data-f="${esc(f.name)}">
    <div class="figwrap${f.figure_v ? " has-fig" : ""}">${cover(f.name, f.cover, "&v=" + f.cover_v)}${figImg(figUrl(f.name, f.figure_v))}${f.cards.length ? `<span class="badge">${ICON.card.replace('class="icon"', "")} Karte</span>` : ""}</div>
    <b>${esc(leaf(f.name))}</b><small>${subLabel(f)}</small></button>`).join("")}</div>`;
}
function bindTiles() { $$(".folder[data-f]").forEach((b) => b.onclick = () => goFolder(b.dataset.f)); }
const viewOfCat = (c) => c === "Musik" ? "musik" : "hoerspiele";
const goFolder = (p) => go(viewOfCat(catOf(p)), p);
const goCat = (c) => go(viewOfCat(c));
const goUp = (p) => { const up = p.split("/").slice(0, -1).join("/"); CATS.includes(up) || !up ? goCat(up || catOf(p)) : goFolder(up); };

const viewMusic = run(async () => {
  const d = await api("/api/library"); S.lib = d;
  const cat = S.view === "musik" ? "Musik" : "Hörspiele";
  const list = childrenOf(d.folders, cat);
  const loose = cat === "Hörspiele" ? d.folders.filter((f) => !f.name.includes("/") && !CATS.includes(f.name)) : [];
  main.innerHTML = `<div class="head"><div><h1>${cat}</h1><span class="muted small">${list.length} ${cat === "Musik" ? "Ordner" : "Hörspiele und Serien"} · ${size(d.disk.free)} frei</span></div>
    <div class="row wrap">${list.length > 1 ? `<button class="btn" id="sort">${ICON.sort} Reihenfolge</button>` : ""}<button class="btn primary" id="new">${ICON.plus} ${cat === "Musik" ? "Neuer Ordner" : "Neues Hörspiel / neue Serie"}</button></div></div>
    ${list.length ? `<div class="search"><input type="search" id="q" placeholder="${cat} durchsuchen …" value="${esc((S.q || {})[cat] || "")}" autocomplete="off" aria-label="Suchen"></div>` : ""}
    <div id="results" hidden></div>
    <div id="area">${list.length ? tiles(list) : `<div class="empty">${ICON.music}<h2>Noch nichts unter „${cat}“</h2><p>${cat === "Musik" ? "Leg einen Ordner an, zum Beispiel für einen Künstler oder ein Album." : "Leg ein Hörspiel an oder eine Serie, in der du die einzelnen Folgen sammelst."}</p></div>`}</div>
    ${loose.length ? `<h2 style="margin-top:30px">Ohne Kategorie</h2><p class="muted small">Diese Ordner liegen außerhalb von „Hörspiele“ und „Musik“. Öffne sie und wähle „Verschieben“.</p>${tiles(loose)}` : ""}`;
  $("#new").onclick = run(async () => {
    const n = await prompt2(cat === "Musik" ? "Neuer Ordner" : "Neues Hörspiel oder neue Serie", cat === "Musik" ? "Name, z. B. „Rolf Zuckowski“" : "Name, z. B. „Bibi Blocksberg“", "", "Anlegen");
    if (n) { const r = await api("/api/library/folder", { name: cat + "/" + n }); goFolder(r.name); }
  });
  if ($("#sort")) $("#sort").onclick = () => sortEditor($("#area"), cat, "folders", list.map((f) => ({ name: leaf(f.name), path: f.name, cover_v: f.cover_v, sub: subLabel(f) })), viewMusic);
  bindTiles();
  const q = $("#q");
  if (q) { q.oninput = () => searchLib(d.folders, cat, q.value); if (q.value) searchLib(d.folders, cat, q.value); }
});

// Suche: Ordner sofort im Browser, Titel auf der Box
const norm = (t) => String(t).toLowerCase().replace(/ß/g, "ss").normalize("NFKD").replace(/[\u0300-\u036f]/g, "");

// Ordnerauswahl mit Suche statt langer Aufklappliste (Karten zuweisen, Verschieben).
// folders: Liste der Ordnerpfade; extra: zusätzlich wählbare Einträge (z. B. die Bereiche selbst)
function folderPicker(folders, selected, extra = []) {
  const all = [...extra, ...folders];
  const kids = (p) => folders.filter((f) => f.startsWith(p + "/") && !f.slice(p.length + 1).includes("/"));
  const open = new Set();
  if (selected) { const parts = selected.split("/"); for (let k = 1; k < parts.length; k++) open.add(parts.slice(0, k).join("/")); }
  const html = `<div class="picker">
    <div class="search picker-q"><input type="search" placeholder="Suchen …" autocomplete="off" aria-label="Ordner suchen"></div>
    <div class="picker-list" role="listbox"></div>
    <div class="picker-sel small muted"></div></div>`;
  return {
    html,
    value: () => selected,
    mount(root, onChange) {
      const list = $(".picker-list", root), q = $(".picker-q input", root), sel = $(".picker-sel", root);
      const row = (f, depth, toggle) => {
        const name = CATS.includes(f) ? f : f.split("/").pop();
        const sub = kids(f).length;
        return `<div class="picker-row${f === selected ? " on" : ""}" role="option" aria-selected="${f === selected}" data-v="${esc(f)}" style="--d:${depth}">
          ${toggle && sub ? `<button class="picker-tg" data-t="${esc(f)}" aria-label="${open.has(f) ? "Zuklappen" : "Aufklappen"}">${open.has(f) ? "▾" : "▸"}</button>` : `<span class="picker-tg"></span>`}
          <span class="picker-name">${esc(name)}</span>${sub && toggle ? `<span class="picker-n">${sub}</span>` : ""}</div>`;
      };
      const paint = () => {
        const terms = norm(q.value.trim()).split(/\s+/).filter(Boolean);
        let out = "";
        if (terms.length) {
          // Jedes Wort muss vorkommen, egal wo – „drache 3“ findet „Der kleine Drache Kokosnuss 3“
          const depth = (f) => f.split("/").length;
          const hits = all.filter((f) => { const n = norm(f); return terms.every((t) => n.includes(t)); })
            .sort((a, b) => depth(a) - depth(b) || a.localeCompare(b, "de", { numeric: true })).slice(0, 80);
          const short = (f) => { const c = catOf(f); return c && f !== c ? pretty(f.slice(c.length + 1)) : f; };
          out = hits.map((f) => `<div class="picker-row${f === selected ? " on" : ""}" role="option" data-v="${esc(f)}" style="--d:0"><span class="picker-tg"></span><span class="picker-name">${esc(short(f))}${catOf(f) && f !== catOf(f) ? ` <span class="picker-n">${catOf(f)}</span>` : ""}</span></div>`).join("")
            || `<p class="small muted" style="padding:10px">Nichts gefunden.</p>`;
        } else {
          const walk = (p, depth) => kids(p).map((f) => row(f, depth, true) + (open.has(f) ? walk(f, depth + 1) : "")).join("");
          for (const c of CATS) {
            const inCat = kids(c);
            if (!inCat.length && !extra.includes(c)) continue;
            out += extra.includes(c) ? row(c, 0, false).replace('class="picker-row', 'class="picker-row cat') : `<div class="picker-head">${c}</div>`;
            out += walk(c, extra.includes(c) ? 1 : 0);
          }
          const loose = folders.filter((f) => !catOf(f) && !f.includes("/"));
          out += loose.map((f) => row(f, 0, true) + (open.has(f) ? walk(f, 1) : "")).join("");
          if (!out) out = `<p class="small muted" style="padding:10px">Noch keine Ordner.</p>`;
        }
        list.innerHTML = out;
        sel.textContent = selected ? "Gewählt: " + pretty(selected) : "Noch nichts gewählt";
      };
      list.onclick = (e) => {
        const t = e.target.closest("[data-t]");
        if (t) { const v = t.dataset.t; open.has(v) ? open.delete(v) : open.add(v); paint(); return; }
        const r = e.target.closest("[data-v]"); if (!r) return;
        selected = r.dataset.v; paint(); onChange && onChange(selected);
      };
      q.oninput = () => { paint(); list.scrollTop = 0; };
      paint();
      const on = $(".picker-row.on", list); if (on) on.scrollIntoView({ block: "center" });
      // Auf dem Handy nicht gleich die Tastatur aufklappen – sie würde die Liste verdecken
      if (matchMedia("(pointer: coarse)").matches) setTimeout(() => { if (document.activeElement === q) q.blur(); }, 0);
    },
  };
}
let searchSeq = 0;
function searchLib(folders, cat, text) {
  (S.q ||= {})[cat] = text;
  const res = $("#results"), area = $("#area"), terms = norm(text).trim().split(/\s+/).filter(Boolean);
  const sortBtn = $("#sort");
  if (!terms.length) { res.hidden = true; area.hidden = false; if (sortBtn) sortBtn.hidden = false; return; }
  res.hidden = false; area.hidden = true; if (sortBtn) sortBtn.hidden = true;
  const hits = folders.filter((f) => catOf(f.name) === cat && terms.every((t) => norm(f.name.slice(cat.length + 1)).includes(t)));
  const label = (f) => { const sub = f.name.slice(cat.length + 1).split("/").slice(0, -1).join(" › "); return sub || subLabel(f); };
  res.innerHTML = `${hits.length ? `<h2>Ordner</h2><div class="folders">${hits.slice(0, 40).map((f) => `<button class="folder" data-f="${esc(f.name)}"><div style="position:relative">${cover(f.name, f.cover, "&v=" + f.cover_v)}</div><b>${esc(leaf(f.name))}</b><small>${esc(label(f))}</small></button>`).join("")}</div>` : ""}
    <div id="trackhits"><p class="muted small" style="margin-top:18px">Suche Titel …</p></div>`;
  bindTiles();
  const seq = ++searchSeq;
  clearTimeout(S.searchT);
  S.searchT = setTimeout(run(async () => {
    const tracks = await api(`/api/library/search?cat=${encodeURIComponent(cat)}&q=${encodeURIComponent(text)}`);
    if (seq !== searchSeq || !$("#trackhits")) return;
    $("#trackhits").innerHTML = tracks.length
      ? `<h2 style="margin-top:22px">Titel</h2><div class="card"><ul class="list">${tracks.map((t, i) => `<li data-tf="${i}" style="cursor:pointer"><div style="width:40px;flex:none">${cover(t.folder, true)}</div><div class="grow"><b style="font-weight:600">${esc(t.file.replace(/\.[^.]+$/, ""))}</b><span>${esc(pretty(t.folder.slice(cat.length + 1)))}</span></div></li>`).join("")}</ul></div>`
      : (hits.length ? "" : `<div class="empty" style="padding:30px">${ICON.music}<p>Nichts gefunden für „${esc(text)}“.</p></div>`);
    $$("[data-tf]").forEach((li) => li.onclick = () => goFolder(tracks[li.dataset.tf].folder));
  }), 250);
}

// Reihenfolge bearbeiten: Liste mit Pfeilen, „Fertig“ speichert
function sortEditor(el, folder, kind, items, done) {
  let list = items.slice();
  const paint = () => {
    el.innerHTML = `<div class="card sorter"><p class="muted small" style="margin-top:0">${kind === "folders" ? "So werden die Ordner angezeigt und – bei einer Karte für den ganzen Ordner – nacheinander abgespielt." : "In dieser Reihenfolge werden die Titel abgespielt."}</p>
      <ol class="list">${list.map((it, i) => `<li>${it.path ? `<div style="width:42px;flex:none">${cover(it.path, true, "&v=" + it.cover_v)}</div>` : `<span class="muted small" style="min-width:1.8em;text-align:right">${i + 1}</span>`}
        <div class="grow"><b style="font-weight:600">${esc(it.name)}</b>${it.sub ? `<span>${esc(it.sub)}</span>` : ""}</div>
        <button class="iconbtn" data-up="${i}" aria-label="Nach oben" ${i ? "" : "disabled"}>${ICON.up}</button>
        <button class="iconbtn" data-dn="${i}" aria-label="Nach unten" ${i < list.length - 1 ? "" : "disabled"}>${ICON.down}</button></li>`).join("")}</ol>
      <div class="row wrap" style="margin-top:14px"><button class="btn" id="s-az">A–Z sortieren</button><span class="grow"></span><button class="btn" id="s-x">Abbrechen</button><button class="btn primary" id="s-ok">Fertig</button></div></div>`;
    $$("[data-up]", el).forEach((b) => b.onclick = () => { const i = +b.dataset.up; [list[i - 1], list[i]] = [list[i], list[i - 1]]; paint(); $(`[data-up="${i - 1}"]`, el)?.focus(); });
    $$("[data-dn]", el).forEach((b) => b.onclick = () => { const i = +b.dataset.dn; [list[i + 1], list[i]] = [list[i], list[i + 1]]; paint(); $(`[data-dn="${i + 1}"]`, el)?.focus(); });
    $("#s-az", el).onclick = () => { list.sort((a, b) => a.name.localeCompare(b.name, "de", { numeric: true })); paint(); };
    $("#s-x", el).onclick = done;
    $("#s-ok", el).onclick = run(async () => { await api("/api/library/order", { folder, kind, items: list.map((x) => x.name) }); toast("Reihenfolge gespeichert"); done(); });
  };
  paint(); el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Titelbild: quadratisch zuschneiden und verkleinern, bevor es hochgeladen wird
async function squareJpeg(file, px = 800) {
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error("Das Bild konnte nicht gelesen werden")); i.src = URL.createObjectURL(file); });
  const side = Math.min(img.naturalWidth, img.naturalHeight), c = document.createElement("canvas");
  c.width = c.height = Math.min(px, side);
  c.getContext("2d").drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, c.width, c.height);
  URL.revokeObjectURL(img.src);
  return new Promise((res) => c.toBlob(res, "image/jpeg", 0.86));
}

const viewFolder = run(async () => {
  const name = S.folder;
  let tracks, lib;
  try { [tracks, lib] = await Promise.all([api("/api/library/tracks?folder=" + encodeURIComponent(name)), api("/api/library")]); }
  catch (e) { toast(e.message, "error"); return goCat(catOf(name) || "Hörspiele"); }
  const f = lib.folders.find((x) => x.name === name) || { cards: [], cover: false, cover_v: 0, total_tracks: tracks.length, subfolders: 0 };
  const subs = childrenOf(lib.folders, name);
  const cat = catOf(name), music = cat === "Musik";
  const total = tracks.reduce((a, t) => a + t.size, 0);
  const parts = name.split("/");
  const crumbs = parts.slice(0, -1).map((p, i) => `<button class="crumb" data-p="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</button>`).join('<span class="muted">›</span>');
  const subWord = music ? "Unterordner" : "Folgen";
  const meta = [subs.length ? `${subs.length} ${music ? "Unterordner" : subs.length === 1 ? "Folge" : "Folgen"}` : "", `${f.total_tracks} Titel`, tracks.length ? size(total) : ""].filter(Boolean).join(" · ");
  main.innerHTML = `<nav class="crumbs">${ICON.back}${crumbs}</nav>
    <div class="detail-head"><div><div class="figwrap${f.figure_v ? " has-fig" : ""}">${cover(name, true, "&v=" + f.cover_v)}${figImg(figUrl(name, f.figure_v))}</div>
        <div class="row cvrow"><button class="btn small" id="cv">${ICON.camera} Titelbild</button>${f.custom_cover ? `<button class="iconbtn" id="cvdel" aria-label="Titelbild entfernen" title="Titelbild entfernen" style="width:36px;height:36px">${ICON.trash}</button>` : ""}<button class="btn small" id="fig">${FIGICON} Figur</button></div></div>
      <div><h1 style="overflow-wrap:anywhere">${esc(leaf(name))}</h1><p class="muted">${meta} ${f.cards.length ? `<span class="pill accent">${f.cards.length === 1 ? "Karte zugewiesen" : f.cards.length + " Karten"}</span>` : ""}</p>
      <div class="row wrap"><button class="btn primary" id="play" ${f.total_tracks ? "" : "disabled"}>${ICON.play.replace("<svg", '<svg class="icon" style="fill:currentColor;stroke:none"')} ${subs.length ? "Alles abspielen" : "Abspielen"}</button>
      <button class="btn" id="assign">${ICON.card} Karte zuweisen</button>
      <button class="iconbtn" id="sub" aria-label="${music ? "Unterordner anlegen" : "Folge anlegen"}" title="${music ? "Unterordner anlegen" : "Folge anlegen"}">${ICON.plus}</button>
      <button class="iconbtn" id="ren" aria-label="Umbenennen" title="Umbenennen">${ICON.edit}</button>
      <button class="iconbtn" id="mv" aria-label="Verschieben" title="Verschieben">${ICON.move}</button>
      <button class="iconbtn" id="del" aria-label="Ordner löschen" title="Ordner löschen">${ICON.trash}</button></div></div></div>
    ${subs.length ? `<div class="row" style="justify-content:space-between;margin-bottom:10px"><h2 style="margin:0">${subWord}</h2>${subs.length > 1 ? `<button class="btn small" id="sortf">${ICON.sort} Reihenfolge</button>` : ""}</div><div id="subarea">${tiles(subs)}</div><div style="height:26px"></div>` : ""}
    ${subs.length && !tracks.length ? `${music ? "" : `<div class="drop" id="drop" data-ep="1" tabindex="0" role="button"><b>Folgen hinzufügen</b><br><span class="small">Mehrere Dateien hierher ziehen oder tippen – jede Datei wird eine eigene Folge</span><div id="up"></div></div>`}
    <p class="muted small">${music ? `Titel lädst du in die einzelnen ${subWord} hoch.` : `Besteht eine Folge aus mehreren Dateien, lege sie mit + an und lade die Dateien dort hoch.`} Eine Karte für „${esc(leaf(name))}“ spielt alle ${subWord} nacheinander, in der Reihenfolge oben.</p>` : `
    <div class="drop" id="drop" tabindex="0" role="button"><b>Titel hinzufügen</b><br><span class="small">Dateien hierher ziehen oder tippen (MP3, M4A, OGG, FLAC, WAV)</span><div id="up"></div></div>
    ${!music && !tracks.length ? `<label class="switch"><span>Jede Datei als eigene Folge<br><span class="muted small">Für Serien: aus jeder Datei wird ein eigener Unterordner, dem du eine Karte geben kannst</span></span><input type="checkbox" id="epmode"></label>` : ""}
    <div id="trackarea"><div class="card">${tracks.length ? `${tracks.length > 1 ? `<div class="row" style="justify-content:flex-end;margin:-6px 0 4px"><button class="btn small" id="sortt">${ICON.sort} Reihenfolge</button></div>` : ""}<ul class="list">${tracks.map((t, i) => `<li><span class="muted small" style="min-width:1.8em;text-align:right">${i + 1}</span><div class="grow"><b style="font-weight:600">${esc(t.file)}</b></div><span class="muted small">${size(t.size)}</span><button class="iconbtn" data-del="${i}" aria-label="Titel löschen">${ICON.trash}</button></li>`).join("")}</ul>` : `<p class="muted" style="margin:0">Dieser Ordner ist noch leer.${music ? "" : " Für eine Serie schalte „Jede Datei als eigene Folge“ ein oder lege mit + Folgen an; für ein einzelnes Hörspiel lade die Titel direkt hier hoch."}</p>`}</div></div>`}
    <input type="file" id="files" multiple accept="audio/*,.mp3,.m4a,.m4b,.ogg,.opus,.flac,.wav,.aac,image/*" hidden>
    <input type="file" id="cvfile" accept="image/*" hidden>`;
  $$(".crumbs .crumb").forEach((b) => b.onclick = () => { const p = b.dataset.p; CATS.includes(p) ? goCat(p) : goFolder(p); });
  $(".crumbs svg").onclick = () => goUp(name);
  bindTiles();
  $("#play").onclick = () => { act("play_folder", name); go("now"); };
  $("#assign").onclick = () => learnCard({ type: "folder", target: name });
  $("#sub").onclick = run(async () => { const n = await prompt2(music ? "Neuer Unterordner" : "Neue Folge", music ? "Name, z. B. ein Albumtitel" : "Name, z. B. „Folge 12 – Hexen gibt es doch“", "", "Anlegen"); if (n) { const r = await api("/api/library/folder", { name: name + "/" + n }); goFolder(r.name); } });
  $("#ren").onclick = run(async () => { const n = await prompt2("Umbenennen", "Neuer Name", leaf(name)); if (n && n !== leaf(name)) { const r = await api("/api/library/rename", { folder: name, name: n }); goFolder(r.name); } });
  $("#mv").onclick = run(async () => {
    const targets = [...CATS, ...lib.folders.map((x) => x.name)].filter((t) => t !== name && !t.startsWith(name + "/") && t !== parts.slice(0, -1).join("/"));
    const pick = folderPicker(targets.filter((t) => !CATS.includes(t)), null, CATS.filter((c) => targets.includes(c)));
    openDialog(`<h2>Verschieben</h2><p class="muted">„${esc(leaf(name))}“ verschieben nach …</p>
      <div class="field" id="mvf"><span>Ziel</span>${pick.html}</div>
      <p class="muted small">So machst du aus einzelnen Hörspielen eine Serie: Serie anlegen, dann die Folgen hineinschieben. Zugewiesene Karten bleiben gültig.</p>
      <div class="actions"><button class="btn" id="mvc">Abbrechen</button><button class="btn primary" id="mvo">Verschieben</button></div>`);
    pick.mount($("#mvf", dialog));
    $("#mvc", dialog).onclick = closeDialog;
    $("#mvo", dialog).onclick = run(async () => { if (!pick.value()) throw new Error("Bitte ein Ziel auswählen"); const r = await api("/api/library/move", { folder: name, parent: pick.value() }); closeDialog(); toast("Verschoben"); goFolder(r.name); });
  });
  $("#del").onclick = run(async () => { if (await ask("Ordner löschen?", `„${leaf(name)}“ mit ${f.total_tracks} Titeln${subs.length ? ` und ${subs.length} ${subWord}` : ""} wird endgültig gelöscht. Zugewiesene Karten werden frei.`, "Löschen", true)) { await api("/api/library/delete", { folder: name }); toast("Ordner gelöscht"); goUp(name); } });
  $$("[data-del]").forEach((b) => b.onclick = run(async () => { const t = tracks[b.dataset.del]; if (await ask("Titel löschen?", t.file, "Löschen", true)) { await api("/api/library/delete", { folder: name, file: t.file }); viewFolder(); } }));
  if ($("#sortf")) $("#sortf").onclick = () => sortEditor($("#subarea"), name, "folders", subs.map((x) => ({ name: leaf(x.name), path: x.name, cover_v: x.cover_v, sub: subLabel(x) })), viewFolder);
  if ($("#sortt")) $("#sortt").onclick = () => sortEditor($("#trackarea"), name, "tracks", tracks.map((t) => ({ name: t.file })), viewFolder);
  const drop = $("#drop"), files = $("#files");
  if (drop) {
    drop.onclick = () => files.click(); drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") files.click(); };
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(name, [...e.dataTransfer.files], epMode()); };
  }
  if ($("#epmode")) $("#epmode").onchange = (e) => { $("#drop b").textContent = e.target.checked ? "Folgen hinzufügen" : "Titel hinzufügen"; };
  const epMode = () => !!(drop && drop.dataset.ep) || !!($("#epmode") && $("#epmode").checked);
  files.onchange = () => { upload(name, [...files.files], epMode()); files.value = ""; };
  $("#cv").onclick = () => $("#cvfile").click();
  $("#cvfile").onchange = run(async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const blob = await squareJpeg(file);
    const fd = new FormData(); fd.append("file", blob, "cover.jpg");
    const r = await fetch("/api/cover?folder=" + encodeURIComponent(name), { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).error);
    toast("Titelbild gespeichert"); viewFolder();
  });
  $("#fig").onclick = () => figureDialog(name, !!f.figure_v, viewFolder);
  if ($("#cvdel")) $("#cvdel").onclick = run(async () => { if (await ask("Titelbild entfernen?", "Danach wird – falls vorhanden – das Bild aus den Musikdateien angezeigt.", "Entfernen", true)) { await api("/api/cover?folder=" + encodeURIComponent(name), {}, "DELETE"); viewFolder(); } });
});

function xhrUpload(url, file, onProgress) {
  return new Promise((res, rej) => {
    const x = new XMLHttpRequest(), fd = new FormData(); fd.append("file", file, file.name);
    x.open("POST", url); x.upload.onprogress = (e) => onProgress(e.loaded, e.total);
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch {} x.status < 300 ? res(d) : rej(new Error(d.error || "Upload fehlgeschlagen")); };
    x.onerror = () => rej(new Error("Verbindung beim Hochladen abgebrochen")); x.send(fd);
  });
}
async function upload(folder, list, episodes = false) {
  if (!list.length || S.uploading) return; S.uploading = true;
  const up = $("#up"), all = list.reduce((a, f) => a + f.size, 0); let done = 0, made = 0; const errors = [];
  for (const [i, f] of list.entries()) {
    try {
      const r = await xhrUpload("/api/library/upload?folder=" + encodeURIComponent(folder) + (episodes ? "&episodes=1" : ""), f, (l) => { if (up.isConnected) up.innerHTML = `<progress max="${all}" value="${done + l}"></progress><div class="small">${i + 1} von ${list.length}: ${esc(f.name)}</div>`; });
      errors.push(...(r.errors || [])); made += (r.episodes || []).length;
    } catch (e) { errors.push(f.name + ": " + e.message); }
    done += f.size;
  }
  S.uploading = false;
  errors.length ? toast(errors[0] + (errors.length > 1 ? ` (+${errors.length - 1} weitere)` : ""), "error")
    : toast(made ? (made === 1 ? "1 Folge angelegt" : made + " Folgen angelegt") : list.length === 1 ? "Datei hochgeladen" : list.length + " Dateien hochgeladen");
  if (S.folder === folder) viewFolder();
}

// ---------- Karten ----------
function learnCard(spec) {
  api("/api/cards/learn", spec).then(() => {
    openDialog(`<h2>Karte auflegen</h2><div class="pulse">${ICON.card.replace('class="icon"', 'fill="none" stroke="currentColor" stroke-width="1.8"')}</div>
      <p style="text-align:center">Leg jetzt die Karte für <b>${esc(spec.label || pretty(spec.target))}</b> auf die Box.<br><span class="muted small">Eine bereits vergebene Karte wird neu zugewiesen.</span></p>
      <div class="actions"><button class="btn" id="lc">Abbrechen</button></div>`);
    const timer = setTimeout(() => { if (dialog.open) { closeDialog(); toast("Keine Karte erkannt", "error"); } }, 30000);
    S.onDialogClose = () => { clearTimeout(timer); api("/api/cards/learn/cancel", {}).catch(() => {}); };
    $("#lc", dialog).onclick = closeDialog;
  }).catch((e) => toast(e.message, "error"));
}
function hideBanner() { $("#banner").hidden = true; }
function unknownCard(id) {
  const b = $("#banner"); b.hidden = false;
  b.innerHTML = `<div class="grow">Unbekannte Karte aufgelegt</div><button class="btn small" id="bz">Zuweisen</button><button class="iconbtn" id="bx" aria-label="Schließen" style="color:inherit">✕</button>`;
  $("#bx").onclick = hideBanner; $("#bz").onclick = () => { hideBanner(); cardDialog(id); };
  clearTimeout(S.bannerT); S.bannerT = setTimeout(hideBanner, 20000);
}
const CMD = { play_pause: "Play / Pause", next: "Nächster Titel", prev: "Vorheriger Titel", vol_up: "Lauter", vol_down: "Leiser", stop: "Stopp", shutdown: "Ausschalten", announce: "Adresse ansagen" };
const cardDialog = run(async (id = null, card = null) => {
  const lib = await api("/api/library");
  const t = card?.type || "folder";
  const pick = folderPicker(lib.folders.map((f) => f.name), t === "folder" ? card?.target : null);
  openDialog(`<h2>${card ? "Karte bearbeiten" : "Neue Karte"}</h2>
    <label class="field"><span>Was soll die Karte tun?</span><select id="ct"><option value="folder">Ordner abspielen</option><option value="url">Radio / Stream abspielen</option><option value="command">Befehl ausführen</option></select></label>
    <div class="field" id="f-folder"><span>Ordner</span>${pick.html}</div>
    <label class="switch" id="f-shuffle"><span>Zufällige Reihenfolge</span><input type="checkbox" id="cshuffle"></label>
    <label class="field" id="f-url"><span>Adresse des Streams</span><input type="url" id="curl" placeholder="https://…"></label>
    <label class="field" id="f-url2"><span>Name</span><input type="text" id="clabel" placeholder="z. B. Kinderradio"></label>
    <label class="field" id="f-cmd"><span>Befehl</span><select id="ccmd">${Object.entries(CMD).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
    <div class="actions"><button class="btn" id="cc">Abbrechen</button><button class="btn primary" id="cs">${card ? "Speichern" : id ? "Zuweisen" : "Weiter"}</button></div>`);
  const ct = $("#ct", dialog); ct.value = t;
  pick.mount($("#f-folder", dialog));
  if (card) { if (t === "url") $("#curl", dialog).value = card.target; if (t === "command") $("#ccmd", dialog).value = card.target; $("#cshuffle", dialog).checked = !!card.shuffle; $("#clabel", dialog).value = card.label || ""; }
  const show = () => { const v = ct.value; $("#f-folder", dialog).hidden = $("#f-shuffle", dialog).hidden = v !== "folder"; $("#f-url", dialog).hidden = $("#f-url2", dialog).hidden = v !== "url"; $("#f-cmd", dialog).hidden = v !== "command"; };
  ct.onchange = show; show();
  $("#cc", dialog).onclick = closeDialog;
  $("#cs", dialog).onclick = run(async () => {
    const v = ct.value, spec = { type: v, target: v === "folder" ? pick.value() : v === "url" ? $("#curl", dialog).value.trim() : $("#ccmd", dialog).value, shuffle: $("#cshuffle", dialog).checked };
    if (v === "url") spec.label = $("#clabel", dialog).value.trim(); if (v === "command") spec.label = CMD[spec.target];
    if (!spec.target) throw new Error(v === "folder" ? (lib.folders.length ? "Bitte einen Ordner auswählen" : "Bitte erst einen Ordner anlegen") : "Bitte eine Adresse eintragen");
    if (card) { await api("/api/cards/update", { id, ...spec }); closeDialog(); toast("Gespeichert"); viewCards(); }
    else if (id) { await api("/api/cards/learn", { id, ...spec }); closeDialog(); toast("Karte gespeichert"); if (S.view === "cards") viewCards(); }
    else { closeDialog(); learnCard(spec); }
  });
});
const viewCards = run(async () => {
  const d = await api("/api/cards");
  const label = (c) => c.type === "command" ? "Befehl: " + (CMD[c.target] || c.target) : c.type === "url" ? (c.label || c.target) : pretty(c.target);
  main.innerHTML = `<div class="head"><div><h1>Karten</h1><div class="status-line" style="margin:4px 0 0"><span class="pill ${d.reader_ok ? "ok" : "bad"}">${d.reader_ok ? "Kartenleser bereit" : "Kartenleser nicht bereit"}</span><span class="muted small">${d.cards.length} Karten</span></div></div>
    <button class="btn primary" id="add">${ICON.plus} Neue Karte</button></div>
    ${d.reader_error ? `<div class="card"><b>${esc(d.reader_error)}</b></div>` : ""}
    ${d.cards.length > 5 ? `<div class="search"><input type="search" id="cq" placeholder="Karten durchsuchen …" autocomplete="off" aria-label="Karten suchen"></div>` : ""}
    <div class="card">${d.cards.length ? `<ul class="list">${d.cards.map((c, i) => `<li>
      <div style="width:46px;flex:none" class="figwrap${c.figure_v ? " has-fig mini" : ""}">${c.type === "folder" || !c.type ? cover(c.target, !c.missing) + figImg(figUrl(c.target, c.figure_v)) : `<div class="cover" style="--c:var(--surface-2);color:var(--muted);font-size:1.2rem">${c.type === "url" ? "📻" : "⚙"}</div>`}</div>
      <div class="grow"><b>${esc(label(c))}</b><span>Karte ${esc(c.id)}${c.shuffle ? " · Zufall" : ""} ${c.missing ? '<span class="pill bad">Ordner fehlt</span>' : ""}</span></div>
      ${(c.type === "folder" || !c.type) && !c.missing ? `<button class="iconbtn" data-g="${i}" aria-label="3D-Figur" title="3D-Figur">${FIGICON}</button>` : ""}<button class="iconbtn" data-e="${i}" aria-label="Bearbeiten">${ICON.edit}</button><button class="iconbtn" data-d="${i}" aria-label="Karte löschen">${ICON.trash}</button></li>`).join("")}</ul>`
      : `<div class="empty" style="padding:24px">${ICON.music}<p>Noch keine Karten. Tippe auf „Neue Karte“ oder leg einfach eine Karte auf die Box.</p></div>`}</div>`;
  $("#add").onclick = () => cardDialog();
  if ($("#cq")) $("#cq").oninput = (e) => { const terms = norm(e.target.value).trim().split(/\s+/).filter(Boolean); $$(".card .list > li").forEach((li, i) => { const c = d.cards[i]; li.hidden = terms.length > 0 && !terms.every((t) => norm(label(c) + " " + c.id).includes(t)); }); };
  $$("[data-e]").forEach((b) => b.onclick = () => { const c = d.cards[b.dataset.e]; cardDialog(c.id, { type: "folder", ...c }); });
  $$("[data-g]").forEach((b) => b.onclick = () => { const c = d.cards[b.dataset.g]; figureDialog(c.target, !!c.figure_v, viewCards); });
  $$("[data-d]").forEach((b) => b.onclick = run(async () => { const c = d.cards[b.dataset.d]; if (await ask("Karte löschen?", `Die Karte für „${label(c)}“ wird frei. Die Musik bleibt erhalten.`, "Löschen", true)) { await api("/api/cards/delete", { id: c.id }); viewCards(); } }));
});

// ---------- Einstellungen ----------
const DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
const viewSettings = run(async () => {
  const [c, sys, bk] = await Promise.all([api("/api/config"), api("/api/system"), api("/api/backup/status")]);
  const cfg = c.config; S.cfg = cfg; S.backup = bk; S.sys = sys;
  const open = S.openSection || "general";
  const sec = (id, title, body, extra = "") => `<details class="card" data-s="${id}" ${open === id ? "open" : ""}><summary><span>${title} ${extra}</span></summary>${body}</details>`;
  const sw = (id, label, on, hint = "") => `<label class="switch"><span>${label}${hint ? `<br><span class="muted small">${hint}</span>` : ""}</span><input type="checkbox" id="${id}" ${on ? "checked" : ""}></label>`;
  main.innerHTML = `<div class="head"><h1>Einstellungen</h1></div>
  ${storageCard(sys.storage)}
  ${sec("general", "Allgemein", `
    <label class="field"><span>Name der Box</span><input type="text" id="g-name" value="${esc(cfg.name)}" maxlength="40"></label>
    <div class="grid2">
      <label class="field"><span>Lautstärke beim Einschalten: <b id="g-vs-v">${cfg.audio.volume_start}</b></span><input type="range" id="g-vs" min="5" max="100" value="${cfg.audio.volume_start}"></label>
      <label class="field"><span>Höchste Lautstärke: <b id="g-vm-v">${cfg.audio.volume_max}</b></span><input type="range" id="g-vm" min="10" max="100" value="${cfg.audio.volume_max}"></label>
    </div>
    <p style="font-weight:650;font-size:.9rem;margin:8px 0 6px">Kartenmodus</p>
    <div class="choices">
      <label class="choice"><input type="radio" name="g-mode" value="place" ${cfg.audio.pause_on_remove ? "checked" : ""}><span><b>Auflegen und liegen lassen</b><br><span class="muted small">Karte drauf: spielt. Karte weg: Pause. Dieselbe Karte wieder drauf: geht weiter.</span></span></label>
      <label class="choice"><input type="radio" name="g-mode" value="tap" ${cfg.audio.pause_on_remove ? "" : "checked"}><span><b>Antippen</b><br><span class="muted small">Karte kurz auflegen startet. Wegnehmen ändert nichts, Pause über den Knopf.</span></span></label>
    </div>
    ${sw("g-resume", "Hörspiele fortsetzen", cfg.audio.resume, "Hörspiele gehen an der letzten Stelle weiter, auch nach einer anderen Karte. Musik beginnt immer von vorn.")}
    <p class="small muted">Beim Einschalten spielt die Box einen kurzen Ton, sobald sie bereit ist. Ihre Adresse sagt sie, wenn du Play/Pause drei Sekunden gedrückt hältst – und nachdem du ihr über das eigene Not-WLAN ein neues WLAN beigebracht hast.</p>
    <div class="actions row" style="margin-top:12px"><button class="btn primary" id="g-save">Speichern</button></div>`)}
  ${sec("buttons", "Knöpfe", `<p class="muted small">Du musst die Box nicht öffnen: „Knopf anlernen“ erkennt selbst, an welchem Anschluss ein Knopf hängt.</p>
    <ul class="list" id="b-list"></ul>
    <div class="row wrap" style="margin-top:14px"><button class="btn" id="b-learn">${ICON.plus} Knopf anlernen</button><button class="btn primary" id="b-save">Speichern</button></div>
    <h2 style="margin-top:24px">Drehregler für die Lautstärke</h2>
    ${c.button_errors.encoder ? `<p><span class="pill bad">Fehler</span> <span class="small">${esc(c.button_errors.encoder)}</span></p>` : ""}
    ${sw("e-on", "Drehregler benutzen", cfg.encoder.enabled, "Drehen stellt die Lautstärke – auch bei Spotify und Kopfhörern. Das „+“ des Reglers gehört an 3,3 V, nicht an 5 V.")}
    <div id="e-more">
      <div class="grid3">${[["clk", "CLK"], ["dt", "DT"], ["sw", "SW (Drücken)"]].map(([k, l]) => `<label class="field"><span>${l}</span><select id="e-${k}">${c.pins.map((p) => `<option value="${p}" ${p === cfg.encoder[k] ? "selected" : ""}>GPIO ${p}</option>`).join("")}</select></label>`).join("")}</div>
      <label class="field"><span>Beim Drücken</span><select id="e-press"><option value="none">Nichts</option>${Object.entries(c.actions).map(([k, v]) => `<option value="${k}" ${k === cfg.encoder.press ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field"><span>Schritt pro Raste: <b id="e-step-v">${cfg.encoder.step}</b> %</span><input type="range" id="e-step" min="1" max="10" value="${cfg.encoder.step}"></label>
      ${sw("e-rev", "Drehrichtung umkehren", cfg.encoder.reverse, "Falls Rechtsdrehen leiser macht")}
    </div>
    <div class="actions row" style="margin-top:12px"><button class="btn primary" id="e-save">Speichern</button></div>`)}
  ${sec("wifi", "WLAN", `<div id="wifi"><p class="muted">Lade …</p></div>`)}
  ${sec("bt", "Bluetooth-Kopfhörer", `<div id="bt"><p class="muted">Lade …</p></div>`)}
  ${sec("share", "Netzlaufwerk", `<div id="share"><p class="muted">Lade …</p></div>`)}
  ${sec("house", "Im Haus hören", `
    <h2>MusicCast</h2><p>Die Box stellt ihren Ton als Stream bereit. Home Assistant kann ihn auf jedem MusicCast-Gerät abspielen. Der Ton kommt dort einige Sekunden verzögert an.</p>
    <p class="small muted">Adresse des Streams:</p><pre id="h-url">${esc(sys.stream_url)}</pre>
    <p class="small muted">Skript für Home Assistant:</p><pre id="h-ha">lädt …</pre>
    <button class="btn small" id="h-copy">Skript kopieren</button>
    <h2 style="margin-top:22px">Spotify</h2>
    <div class="status-line"><span class="pill ${sys.spotify ? "ok" : "bad"}">${sys.spotify ? "Spotify Connect bereit" : "Spotify Connect nicht erreichbar"}</span></div>
    <p>Die Box erscheint in der Spotify-App als Lautsprecher „Klangkiste“ (Spotify Premium nötig). So arbeiten Spotify und die Figuren zusammen:</p>
    <ul class="small" style="margin:0 0 8px;padding-left:20px;line-height:1.6"><li>Startet Spotify, pausiert die laufende Figur.</li><li>Trennst du Spotify am Handy oder wählst ein anderes Gerät, läuft die Figur an derselben Stelle weiter (wenn sie noch auf der Box steht).</li><li>Die Knöpfe steuern immer, was zuletzt gespielt hat: Spotify oder die Figur.</li><li>Eine aufgelegte Figur gewinnt und pausiert Spotify.</li></ul>`)}
  ${sec("mqtt", "MQTT / Home Assistant", `
    <div class="status-line"><span class="pill ${c.mqtt.connected ? "ok" : cfg.mqtt.enabled ? "bad" : ""}">${c.mqtt.connected ? "Verbunden" : cfg.mqtt.enabled ? "Nicht verbunden" : "Aus"}</span>${c.mqtt.error ? `<span class="small muted">${esc(c.mqtt.error)}</span>` : ""}</div>
    ${sw("m-on", "MQTT einschalten", cfg.mqtt.enabled)}
    <div class="grid2"><label class="field"><span>Server (Broker)</span><input type="text" id="m-host" value="${esc(cfg.mqtt.host)}" placeholder="z. B. homeassistant.local"></label>
    <label class="field"><span>Port</span><input type="number" id="m-port" value="${cfg.mqtt.port}"></label>
    <label class="field"><span>Benutzer</span><input type="text" id="m-user" value="${esc(cfg.mqtt.username)}" autocomplete="off"></label>
    <label class="field"><span>Passwort</span><input type="password" id="m-pass" placeholder="${cfg.mqtt.password_set ? "gespeichert – leer lassen zum Behalten" : ""}" autocomplete="new-password"></label>
    <label class="field"><span>Themen-Präfix</span><input type="text" id="m-base" value="${esc(cfg.mqtt.base_topic)}"></label></div>
    ${sw("m-disc", "In Home Assistant automatisch anlegen", cfg.mqtt.discovery)}
    <p class="small muted">Befehle: <code>${esc(cfg.mqtt.base_topic)}/cmd/play_pause</code>, <code>next</code>, <code>prev</code>, <code>stop</code>, <code>volume</code> (0–100), <code>stream</code> (ON/OFF), <code>play_folder</code> (Ordnername). Status: <code>${esc(cfg.mqtt.base_topic)}/state</code>.</p>
    <button class="btn primary" id="m-save">Speichern</button>`)}
  ${sec("backup", "Sicherung", `<div id="bk-status" class="status-line"></div>
    <p class="muted small">Gesichert werden Einstellungen, Karten und – wenn gewählt – die gesamte Musik auf dein IONOS HiDrive. In HiDrive muss unter Einstellungen › Zugriffsrechte und Protokolle „WebDAV“ aktiv sein.</p>
    <div class="grid2"><label class="field"><span>HiDrive-Benutzername</span><input type="text" id="k-user" value="${esc(cfg.backup.username)}" autocomplete="off"></label>
    <label class="field"><span>HiDrive-Passwort</span><input type="password" id="k-pass" placeholder="${cfg.backup.password_set ? "gespeichert – leer lassen zum Behalten" : ""}" autocomplete="new-password"></label>
    <label class="field"><span>Ordner in HiDrive</span><input type="text" id="k-path" value="${esc(cfg.backup.remote_path)}"></label>
    <label class="field"><span>WebDAV-Adresse</span><input type="url" id="k-url" value="${esc(cfg.backup.url)}"></label></div>
    ${sw("k-music", "Musik mitsichern", cfg.backup.include_music)}
    <div class="grid2"><label class="field"><span>Automatisch sichern</span><select id="k-sched"><option value="off">Nie</option><option value="daily">Täglich</option><option value="weekly">Wöchentlich</option></select></label>
    <label class="field" id="k-day-f"><span>Wochentag</span><select id="k-day">${DAYS.map((d, i) => `<option value="${i}">${d}</option>`).join("")}</select></label>
    <label class="field" id="k-hour-f"><span>Uhrzeit</span><select id="k-hour">${[...Array(24)].map((_, i) => `<option value="${i}">${i}:00 Uhr</option>`).join("")}</select></label></div>
    <div class="row wrap"><button class="btn" id="k-save">Speichern</button><button class="btn" id="k-test">Verbindung testen</button><button class="btn primary" id="k-run">Jetzt sichern</button><button class="btn" id="k-restore">Wiederherstellen …</button></div>
    <h2 style="margin-top:22px">Ohne HiDrive</h2><p class="muted small">Einstellungen und Karten (ohne Musik) als Datei auf dieses Gerät laden oder von dort zurückspielen.</p>
    <div class="row wrap"><a class="btn" href="/api/backup/download" download>Herunterladen</a><button class="btn" id="k-up">Datei zurückspielen</button><input type="file" id="k-file" accept=".gz,.tar,.tgz" hidden></div>`)}
  ${sec("app", "Als App installieren", `<div id="app-inst"></div>`)}
  ${sec("system", "System", `<ul class="list">
      <li style="display:block"><div class="row"><div class="grow"><b>Software</b><span>Version ${esc(sys.version)}${sys.revision ? " · Stand " + esc(sys.revision) : ""}${sys.revision_date ? " vom " + esc(sys.revision_date) : ""}${sys.mock ? " · Simulationsmodus" : ""}</span></div></div><div id="upd"></div></li>
      <li><div class="grow"><b>Adresse</b><span>http://${esc(sys.ip || sys.hostname)} · ${esc(sys.hostname)}</span></div></li>

      ${sys.temperature ? `<li><div class="grow"><b>Temperatur</b><span>${String(sys.temperature).replace(".", ",")} °C</span></div></li>` : ""}
      <li><div class="grow"><b>Abspieler</b><span>${sys.mpd ? "läuft" : "nicht erreichbar"}</span></div></li></ul>
    ${sys.leds ? sw("s-leds", "Lämpchen am Raspberry Pi", sys.leds_on, "Rotes und grünes Licht auf der Platine. Aus, wenn es durch das Gehäuse scheint.") : `<p class="muted small" style="margin-top:14px">Die Lämpchen am Raspberry Pi lassen sich auf dieser Box noch nicht schalten. Ein Update über „Jetzt aktualisieren“ richtet das ein.</p>`}
    <div class="row wrap" style="margin-top:14px"><button class="btn" id="s-say">${ICON.vol} Adresse ansagen</button><button class="btn" data-p="reboot">Neu starten</button><button class="btn danger" data-p="shutdown">Ausschalten</button></div>`)}`;
  $$("details.card").forEach((d) => d.addEventListener("toggle", () => { if (d.open) { S.openSection = d.dataset.s; $$("details.card").forEach((o) => { if (o !== d) o.open = false; }); } }));
  const save = (patch, msg = "Gespeichert") => run(async () => { await api("/api/config", patch); toast(msg); viewSettings(); })();

  // Allgemein
  for (const k of ["vs", "vm"]) $("#g-" + k).oninput = (e) => $("#g-" + k + "-v").textContent = e.target.value;
  $("#g-save").onclick = () => save({ name: $("#g-name").value, audio: { volume_start: Math.min(+$("#g-vs").value, +$("#g-vm").value), volume_max: +$("#g-vm").value, resume: $("#g-resume").checked, pause_on_remove: $('[name="g-mode"]:checked').value === "place" } });

  // Knöpfe
  let btns = cfg.buttons.map((b) => ({ ...b }));
  const paintButtons = () => {
    $("#b-list").innerHTML = btns.length ? btns.map((b, i) => `<li><div style="min-width:92px"><b>GPIO ${b.pin}</b>${c.button_errors[b.pin] ? '<br><span class="pill bad">Fehler</span>' : ""}</div>
      <div class="grow"><select data-ba="${i}" aria-label="Funktion">${Object.entries(c.actions).map(([k, v]) => `<option value="${k}" ${k === b.action ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <button class="iconbtn" data-bd="${i}" aria-label="Knopf entfernen">${ICON.trash}</button></li>`).join("") : '<li class="muted">Noch keine Knöpfe eingerichtet.</li>';
    $$("[data-ba]").forEach((s) => s.onchange = () => btns[s.dataset.ba].action = s.value);
    $$("[data-bd]").forEach((x) => x.onclick = () => { btns.splice(x.dataset.bd, 1); paintButtons(); });
  };
  paintButtons();
  $("#b-save").onclick = () => save({ buttons: btns });
  const eMore = () => { $("#e-more").hidden = !$("#e-on").checked; }; eMore(); $("#e-on").onchange = eMore;
  $("#e-step").oninput = (e) => $("#e-step-v").textContent = e.target.value;
  $("#e-save").onclick = () => save({ encoder: { enabled: $("#e-on").checked, clk: +$("#e-clk").value, dt: +$("#e-dt").value, sw: +$("#e-sw").value, press: $("#e-press").value, step: +$("#e-step").value, reverse: $("#e-rev").checked } });
  $("#b-learn").onclick = run(async () => {
    openDialog(`<h2>Knopf anlernen</h2><div class="pulse"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg></div><p style="text-align:center">Drück jetzt den Knopf an der Box, den du einrichten möchtest.<br><span class="muted small">Ich warte 15 Sekunden.</span></p>`);
    let r; try { r = await api("/api/buttons/learn", {}); } finally { closeDialog(); }
    if (r.pin == null) return toast("Kein Knopfdruck erkannt", "error");
    const ex = btns.find((b) => b.pin === r.pin);
    if (ex) toast(`Das ist der Knopf „${c.actions[ex.action]}“ (GPIO ${r.pin})`);
    else { btns.push({ pin: r.pin, action: "play_pause" }); paintButtons(); toast(`Knopf an GPIO ${r.pin} gefunden – Funktion wählen und speichern`); }
  });

  // Kopfhörer, WLAN
  api("/api/bluetooth").then(paintBT).catch(() => {});
  api("/api/network").then((n) => paintWifi(n, cfg)).catch(() => {});
  api("/api/share").then(paintShare).catch(() => {});
  paintInstall(sys);

  // Haus
  fetch("/api/homeassistant").then((r) => r.text()).then((t) => { if ($("#h-ha")) $("#h-ha").textContent = t; });
  $("#h-copy").onclick = () => navigator.clipboard?.writeText($("#h-ha").textContent).then(() => toast("Kopiert"), () => toast("Bitte von Hand markieren und kopieren", "error"));

  // MQTT
  $("#m-save").onclick = () => save({ mqtt: { enabled: $("#m-on").checked, host: $("#m-host").value.trim(), port: +$("#m-port").value || 1883, username: $("#m-user").value.trim(), password: $("#m-pass").value, base_topic: $("#m-base").value.trim() || "klangkiste", discovery: $("#m-disc").checked } });

  // Sicherung
  $("#k-sched").value = cfg.backup.schedule; $("#k-day").value = cfg.backup.weekday; $("#k-hour").value = cfg.backup.hour;
  const sched = () => { const v = $("#k-sched").value; $("#k-day-f").hidden = v !== "weekly"; $("#k-hour-f").hidden = v === "off"; };
  $("#k-sched").onchange = sched; sched();
  const bkPatch = () => ({ backup: { username: $("#k-user").value.trim(), password: $("#k-pass").value, remote_path: $("#k-path").value.trim() || "Klangkiste", url: $("#k-url").value.trim(), include_music: $("#k-music").checked, schedule: $("#k-sched").value, weekday: +$("#k-day").value, hour: +$("#k-hour").value } });
  $("#k-save").onclick = () => save(bkPatch());
  $("#k-test").onclick = run(async (e) => { const b = e.currentTarget; b.disabled = true; try { await api("/api/config", bkPatch()); await api("/api/backup/test", {}); toast("Verbindung zu HiDrive steht"); } finally { b.disabled = false; } });
  $("#k-run").onclick = run(async () => { await api("/api/config", bkPatch()); await api("/api/backup/run", {}); toast("Sicherung gestartet"); });
  $("#k-restore").onclick = run(async () => {
    openDialog(`<h2>Wiederherstellen</h2><p class="muted">Suche Sicherungen in HiDrive …</p>`);
    let list; try { list = await api("/api/backup/list"); } catch (e) { closeDialog(); throw e; }
    if (!list.length) { closeDialog(); return toast("In HiDrive liegt noch keine Sicherung", "error"); }
    openDialog(`<h2>Wiederherstellen</h2><label class="field"><span>Sicherung vom</span><select id="r-name">${list.map((n) => `<option value="${esc(n)}">${esc(n.replace("klangkiste-", "").replace(".tar.gz", "").replace("_", " um ").replace(/(\d\d)(\d\d)$/, "$1:$2 Uhr"))}</option>`).join("")}</select></label>
      <label class="switch"><span>Musik zurückholen</span><input type="checkbox" id="r-music" checked></label><p class="muted small">Einstellungen und Karten auf der Box werden durch die Sicherung ersetzt.</p>
      <div class="actions"><button class="btn" id="r-c">Abbrechen</button><button class="btn primary" id="r-ok">Wiederherstellen</button></div>`);
    $("#r-c", dialog).onclick = closeDialog;
    $("#r-ok", dialog).onclick = run(async (e) => { e.currentTarget.disabled = true; e.currentTarget.textContent = "Läuft …"; try { await api("/api/backup/restore", { name: $("#r-name", dialog).value, music: $("#r-music", dialog).checked }); toast("Wiederhergestellt"); } finally { closeDialog(); viewSettings(); } });
  });
  $("#k-up").onclick = () => $("#k-file").click();
  $("#k-file").onchange = run(async (e) => { if (!await ask("Zurückspielen?", "Einstellungen und Karten auf der Box werden durch die Datei ersetzt.", "Ersetzen")) return; await xhrUpload("/api/backup/upload", e.target.files[0], () => {}); toast("Zurückgespielt"); viewSettings(); });
  paintBackup();

  // System
  paintUpdate(sys.update_info || {}, sys);
  if ($("#s-leds")) $("#s-leds").onchange = run(async (e) => { await api("/api/config", { system: { leds: e.target.checked } }); toast(e.target.checked ? "Lämpchen an" : "Lämpchen aus"); });
  $("#s-say").onclick = run(async () => { await api("/api/system", { action: "announce" }); if (!sys.tts) toast("Auf der Box ist noch keine Sprachausgabe installiert – ein Update richtet sie ein", "error"); });
  $$("[data-p]").forEach((b) => b.onclick = run(async () => { const a = b.dataset.p; if (await ask(a === "reboot" ? "Box neu starten?" : "Box ausschalten?", a === "reboot" ? "Die Wiedergabe stoppt, nach etwa einer Minute ist die Box wieder da." : "Zum Einschalten musst du danach kurz den Strom trennen.", a === "reboot" ? "Neu starten" : "Ausschalten", a !== "reboot")) { await api("/api/system", { action: a }); toast(a === "reboot" ? "Die Box startet neu …" : "Die Box schaltet sich aus …"); } }));
});
// Speicher als ein geteilter Balken: System | Hörspiele | Musik | frei
function storageCard(st) {
  if (!st || !st.total) return "";
  const parts = [["system", "System", st.system], ["hoerspiele", "Hörspiele", st.hoerspiele], ["musik", "Musik", st.musik], ["frei", "Frei", st.free]];
  const pct = (v) => (v / st.total * 100);
  const share = (v) => { const p = pct(v); return p > 0 && p < 1 ? "< 1 %" : Math.round(p) + " %"; };
  const desc = parts.map(([, n, v]) => `${n} ${size(v)} (${share(v)})`).join(", ");
  return `<div class="card storage"><div class="row" style="justify-content:space-between;align-items:baseline;margin-bottom:10px"><h2 style="margin:0">Speicher</h2><span class="small muted">${size(st.free)} von ${size(st.total)} frei</span></div>
    <div class="segbar" role="img" aria-label="Speicher: ${esc(desc)}">${parts.filter(([, , v]) => v > 0).map(([k, n, v]) => `<span class="seg-${k}" style="flex-grow:${v}" title="${n}: ${size(v)} (${share(v)})"></span>`).join("")}</div>
    <ul class="legend">${parts.map(([k, n, v]) => `<li><i class="sw seg-${k}"></i><span>${n}</span><b>${size(v)}</b></li>`).join("")}</ul></div>`;
}
function paintInstall(sys = S.sys || {}) {
  const el = $("#app-inst"); if (!el) return;
  const ua = navigator.userAgent, ios = /iPhone|iPad|iPod/.test(ua) || (ua.includes("Mac") && navigator.maxTouchPoints > 1);
  const android = /Android/.test(ua), inApp = ua.includes("KlangkisteApp");
  if (inApp) {
    el.innerHTML = `<p><span class="pill ok">Läuft in der Android-App</span></p>
      <p class="small muted">Die App findet die Box von selbst im WLAN, auch wenn sich ihre Adresse ändert.</p>
      <div class="actions row"><a class="btn" href="klangkiste:setup">Adresse der Box ändern</a></div>`; return;
  }
  if (matchMedia("(display-mode: standalone)").matches || navigator.standalone) {
    el.innerHTML = `<p><span class="pill ok">Läuft als App</span></p><p class="small muted">Die Klangkiste ist auf diesem Gerät installiert.</p>`; return;
  }
  if (S.installPrompt) {
    el.innerHTML = `<p>Die Klangkiste als eigene App auf dem Startbildschirm – ohne Adresszeile, mit eigenem Symbol.</p>
      <div class="actions row"><button class="btn primary" id="app-go">${ICON.plus} Jetzt installieren</button></div>`;
    $("#app-go").onclick = run(async () => { const p = S.installPrompt; S.installPrompt = null; await p.prompt(); await p.userChoice; paintInstall(); });
    return;
  }
  if (ios) {
    el.innerHTML = `<p>In <b>Safari</b> unten auf <b>Teilen</b> tippen (Quadrat mit Pfeil), dann <b>„Zum Home-Bildschirm“</b>. Die Klangkiste öffnet sich danach wie eine eigene App.</p>
      <p class="small muted">Geht nur in Safari, nicht in Chrome oder anderen Browsern auf dem iPhone.</p>`; return;
  }
  const dl = sys.apk
    ? `<div class="actions row"><a class="btn primary" href="/klangkiste.apk" download="klangkiste.apk">${ICON.plus} Android-App herunterladen</a></div>`
    : `<p class="small muted"><span class="pill">Noch nicht auf der Box</span> Die App wird beim nächsten Update geladen (System › „Jetzt aktualisieren“).</p>`;
  el.innerHTML = android
    ? `<p>Die Klangkiste als eigene App für Android: findet die Box selbst im WLAN, auch wenn sich ihre Adresse ändert.</p>${dl}
      <ol class="small" style="padding-left:20px;line-height:1.7;margin:12px 0 0">
        <li>Herunterladen und die Datei <b>klangkiste.apk</b> öffnen (Benachrichtigung oder „Downloads“).</li>
        <li>Fragt Android nach <b>„Unbekannte Apps installieren“</b>: für den Browser erlauben, dann zurück.</li>
        <li><b>Installieren</b> tippen. Warnt Google Play Protect, „Trotzdem installieren“ wählen – die App kommt nicht aus dem Play Store, sondern direkt von deiner Box.</li></ol>`
    : `<p>Für <b>Android</b>: die Seite der Box auf dem Handy öffnen und hier die App herunterladen. Für das <b>iPhone</b>: in Safari Teilen › „Zum Home-Bildschirm“.</p>
      <p class="small muted">Am Computer bieten Chrome und Edge im Menü „App installieren“ an, wenn die Seite über https läuft – bei der Box ist das nicht der Fall.</p>${sys.apk ? `<p class="small muted">Direkt-Link zur Android-App: <a href="/klangkiste.apk">${esc(location.origin)}/klangkiste.apk</a></p>` : ""}`;
}

function paintShare(sh) {
  const el = $("#share"); if (!el) return;
  const sw = (id, label, on, hint) => `<label class="switch"><span>${label}<br><span class="muted small">${hint}</span></span><input type="checkbox" id="${id}" ${on ? "checked" : ""}></label>`;
  const busy = (btn, text) => { btn.disabled = true; btn.textContent = text; };
  if (!sh.available) { el.innerHTML = `<p class="muted">Das Netzlaufwerk ist auf dieser Box nicht eingerichtet. Ein Update über System › „Jetzt aktualisieren“ holt das nach.</p>`; return; }
  el.innerHTML = `<p>Hörspiele und Musik als Laufwerk am Computer einbinden – praktisch, um viele Dateien zu kopieren oder umzusortieren. Neue Dateien erkennt die Box nach etwa einer halben Minute von selbst.</p>
    <div class="status-line"><span class="pill ${sh.enabled ? "ok" : ""}">${sh.enabled ? "Eingeschaltet" : "Ausgeschaltet"}</span></div>
    ${sw("sh-on", "Netzlaufwerk einschalten", sh.enabled, "Jedes Gerät im WLAN, das das Kennwort kennt, kann dann Dateien ändern und löschen.")}
    <label class="field"><span>Kennwort ${sh.enabled ? "(leer lassen = bleibt)" : "(mindestens 8 Zeichen)"}</span><input type="password" id="sh-pw" autocomplete="new-password"></label>
    ${sh.enabled ? `<p class="small muted">Windows – im Explorer in die Adresszeile:</p><pre>${esc(sh.windows)}</pre>
    <p class="small muted">Mac – im Finder „Gehe zu › Mit Server verbinden“:</p><pre>${esc(sh.mac)}</pre>
    <p class="small muted">Benutzername: <b>${esc(sh.user)}</b>, dazu dein Kennwort.</p>` : ""}
    <div class="actions row"><button class="btn primary" id="sh-save">Speichern</button></div>`;
  $("#sh-save").onclick = run(async (e) => {
    busy(e.currentTarget, "Speichere …");
    try { paintShare(await api("/api/share", { enabled: $("#sh-on").checked, password: $("#sh-pw").value })); toast("Gespeichert"); }
    catch (err) { paintShare(await api("/api/share")); throw err; }
  });
}

function paintWifi(n, cfg) {
  const el = $("#wifi"); if (!el) return;
  if (!n.available) { el.innerHTML = `<p class="muted">Die WLAN-Verwaltung braucht NetworkManager. Der ist auf dieser Box nicht vorhanden.</p>`; return; }
  el.innerHTML = `
    <div class="status-line"><span class="pill ${n.connected ? "ok" : n.hotspot ? "accent" : "bad"}">${n.connected ? "Verbunden" : n.hotspot ? "Eigenes WLAN aktiv" : "Kein Netzwerk"}</span>
      <span class="small muted">${n.connected ? esc(n.ssid) + " · " + esc(n.ip) : n.hotspot ? `„${esc(n.hotspot_ssid)}“ · ${esc(n.ip)}` : ""}</span></div>
    ${n.saved.length ? `<p class="small muted" style="margin-bottom:4px">Bekannte WLANs – die Box verbindet sich mit dem ersten, das sie findet:</p><ul class="list">${n.saved.map((s, i) => `<li><div class="grow"><b style="font-weight:600">${esc(s)}</b>${s === n.ssid ? "<span>aktuell verbunden</span>" : ""}</div><button class="iconbtn" data-wdel="${i}" aria-label="WLAN vergessen" ${s === n.ssid ? "disabled" : ""}>${ICON.trash}</button></li>`).join("")}</ul>` : ""}
    <button class="btn" id="w-add" style="margin-top:10px">${ICON.plus} WLAN hinzufügen</button>
    <h2 style="margin-top:24px">Eigenes WLAN als Notlösung</h2>
    <p class="small muted">Findet die Box nach dem Einschalten etwa eine Minute lang kein bekanntes WLAN, öffnet sie ein eigenes. Verbinde dich damit und öffne <b>http://10.42.0.1</b>, um ihr ein WLAN beizubringen. Die Box sagt das auch an. Solange niemand die Oberfläche benutzt, sucht sie alle fünf Minuten erneut nach bekannten WLANs.</p>
    <label class="switch"><span>Eigenes WLAN öffnen, wenn nötig</span><input type="checkbox" id="w-hs" ${n.hotspot_enabled ? "checked" : ""}></label>
    <div class="grid2"><label class="field"><span>Name</span><input type="text" id="w-ssid" value="${esc(n.hotspot_ssid)}" maxlength="32"></label>
    <label class="field"><span>Kennwort (mind. 8 Zeichen)</span><input type="password" id="w-pw" placeholder="${n.hotspot_password_set ? "gespeichert – leer lassen zum Behalten" : ""}" autocomplete="new-password"></label></div>
    <button class="btn small" id="w-save">Speichern</button>`;
  $$("[data-wdel]", el).forEach((b) => b.onclick = run(async () => { const name = n.saved[b.dataset.wdel]; if (await ask("WLAN vergessen?", `„${name}“ wird gelöscht. Die Box verbindet sich dann nicht mehr von selbst damit.`, "Vergessen", true)) paintWifi(await api("/api/network/forget", { name }), cfg); }));
  $("#w-save").onclick = run(async () => { await api("/api/config", { wifi: { hotspot: $("#w-hs").checked, hotspot_ssid: $("#w-ssid").value.trim(), hotspot_password: $("#w-pw").value } }); toast("Gespeichert"); paintWifi(await api("/api/network"), cfg); });
  $("#w-add").onclick = run(async () => {
    openDialog(`<h2>WLAN hinzufügen</h2><p class="muted">Suche WLANs …</p>`);
    let nets = []; try { nets = await api("/api/network/scan", {}); } catch {}
    openDialog(`<form method="dialog"><h2>WLAN hinzufügen</h2>
      <label class="field"><span>WLAN</span><select id="wn">${nets.map((x) => `<option value="${esc(x.ssid)}">${esc(x.ssid)} ${x.signal >= 70 ? "▂▄▆█" : x.signal >= 45 ? "▂▄▆" : x.signal >= 25 ? "▂▄" : "▂"}${x.secure ? " 🔒" : ""}</option>`).join("")}<option value="">Anderes WLAN (Name eingeben) …</option></select></label>
      <label class="field" id="wn2f" ${nets.length ? "hidden" : ""}><span>Name des WLANs</span><input type="text" id="wn2" autocomplete="off"></label>
      <label class="field"><span>Kennwort</span><input type="password" id="wp" autocomplete="new-password"></label>
      <p class="small muted">Die Box wechselt sofort in dieses WLAN und ist danach unter einer neuen Adresse erreichbar. Sie sagt die Adresse an. Klappt die Verbindung nicht, bleibt sie im bisherigen Netz${n.hotspot ? " bzw. öffnet wieder ihr eigenes WLAN" : ""}.</p>
      <div class="actions"><button class="btn" type="button" id="wx">Abbrechen</button><button class="btn primary" id="wok">Verbinden</button></div></form>`);
    const sel = $("#wn", dialog); sel.onchange = () => $("#wn2f", dialog).hidden = sel.value !== "";
    $("#wx", dialog).onclick = closeDialog;
    $("form", dialog).onsubmit = (e) => {
      e.preventDefault();
      const ssid = sel.value || $("#wn2", dialog).value.trim(), password = $("#wp", dialog).value;
      if (!ssid) return toast("Bitte ein WLAN wählen", "error");
      $("#wok", dialog).disabled = true; $("#wok", dialog).textContent = "Verbinde …";
      api("/api/network/connect", { ssid, password })
        .then((r) => { closeDialog(); toast(`Verbunden mit „${ssid}“`); paintWifi(r, cfg); })
        .catch((err) => {
          if (/nicht erreichbar/.test(err.message)) { closeDialog(); openDialog(`<h2>Box wechselt das WLAN</h2><p>Die Box verbindet sich mit „${esc(ssid)}“ und ist darum hier nicht mehr erreichbar. Wechsle mit deinem Gerät ebenfalls in dieses WLAN und öffne die Adresse, die die Box ansagt.</p><div class="actions"><button class="btn primary" onclick="document.getElementById('dialog').close()">OK</button></div>`); }
          else { $("#wok", dialog).disabled = false; $("#wok", dialog).textContent = "Verbinden"; toast(err.message, "error"); }
        });
    };
  });
}
function paintBT(b) {
  const el = $("#bt"); if (!el) return;
  if (!b.available) { el.innerHTML = `<p class="muted">Bluetooth ist auf dieser Box noch nicht eingerichtet. Ein Update unter „System“ richtet es ein.</p>`; return; }
  const d = b.device;
  el.innerHTML = `
    ${d ? `<ul class="list"><li><div style="flex:none;color:var(--muted)">${ICON.headphones}</div><div class="grow"><b>${esc(d.name)}</b><span>${d.connected ? "verbunden – der Ton läuft über den Kopfhörer" : "nicht verbunden – verbindet sich automatisch, sobald er eingeschaltet ist"}</span></div>
      <span class="pill ${d.connected ? "ok" : ""}">${d.connected ? "Verbunden" : "Aus"}</span></li></ul>
      <div class="row wrap" style="margin:12px 0 4px">${d.connected ? `<button class="btn" id="bt-off">Trennen</button>` : `<button class="btn" id="bt-on">Jetzt verbinden</button>`}<button class="btn" id="bt-scan">Anderen Kopfhörer koppeln</button><button class="btn danger" id="bt-del">Entfernen</button></div>`
      : `<p>Noch kein Kopfhörer gekoppelt. Versetze ihn in den Kopplungsmodus (meist die Ein-Taste lang drücken, bis eine Lampe blinkt) und tippe dann auf „Kopfhörer suchen“.</p>
      <button class="btn primary" id="bt-scan">${ICON.headphones} Kopfhörer suchen</button>`}
    <label class="field" style="margin-top:18px"><span>Höchste Lautstärke mit Kopfhörer: <b id="bt-vm-v">${b.volume_max}</b></span><input type="range" id="bt-vm" min="10" max="100" value="${b.volume_max}"></label>
    <button class="btn small" id="bt-save">Speichern</button>
    <ul class="small muted" style="margin:14px 0 0;padding-left:20px;line-height:1.6"><li>Ist der Kopfhörer verbunden, kommt der Ton nur dort – von Figuren und von Spotify.</li><li>Wird er ausgeschaltet oder geht die Verbindung verloren, pausiert die Wiedergabe, statt laut aus dem Lautsprecher weiterzuspielen.</li><li>Beim Wechsel verbindet sich Spotify neu; am Handy dann „Klangkiste“ noch einmal wählen.</li><li>Stockt der Ton, liegt das meist am WLAN im 2,4-GHz-Band, das sich beim Pi 3 die Antenne mit Bluetooth teilt. 5-GHz-WLAN hilft.</li></ul>`;
  const busy = (btn, text) => { btn.disabled = true; btn.textContent = text; };
  $("#bt-vm").oninput = (e) => $("#bt-vm-v").textContent = e.target.value;
  $("#bt-save").onclick = run(async () => { await api("/api/config", { bluetooth: { volume_max: +$("#bt-vm").value } }); S.cfg = (await api("/api/config")).config; toast("Gespeichert"); });
  if ($("#bt-on")) $("#bt-on").onclick = run(async (e) => { busy(e.currentTarget, "Verbinde …"); try { paintBT(await api("/api/bluetooth/connect", {})); } catch (err) { paintBT(await api("/api/bluetooth")); throw err; } });
  if ($("#bt-off")) $("#bt-off").onclick = run(async (e) => { busy(e.currentTarget, "Trenne …"); paintBT(await api("/api/bluetooth/disconnect", {})); });
  if ($("#bt-del")) $("#bt-del").onclick = run(async () => { if (await ask("Kopfhörer entfernen?", `„${d.name}“ wird vergessen. Zum erneuten Verwenden musst du ihn wieder koppeln.`, "Entfernen", true)) paintBT(await api("/api/bluetooth/forget", {})); });
  $("#bt-scan").onclick = run(async () => {
    openDialog(`<h2>Kopfhörer suchen</h2><div class="pulse">${ICON.headphones.replace('class="icon"', 'width="50" height="50" fill="none" stroke="currentColor" stroke-width="1.8"')}</div><p style="text-align:center">Suche etwa 12 Sekunden …<br><span class="muted small">Der Kopfhörer muss im Kopplungsmodus sein.</span></p>`);
    let list; try { list = await api("/api/bluetooth/scan", {}); } catch (err) { closeDialog(); throw err; }
    const audioCount = list.filter((x) => x.audio).length;
    openDialog(`<h2>Gefundene Geräte</h2>${list.length ? `<ul class="list">${list.map((x, i) => `<li ${!x.audio && audioCount ? 'class="other" hidden' : ""}><div style="flex:none;color:var(--muted)">${x.audio ? ICON.headphones : ICON.bt}</div><div class="grow"><b>${esc(x.name)}</b><span>${x.audio ? "Kopfhörer / Lautsprecher" : "anderes Gerät"}${x.paired ? " · bereits gekoppelt" : ""}</span></div><button class="btn small ${x.audio ? "primary" : ""}" data-pair="${i}">Koppeln</button></li>`).join("")}</ul>` : `<p class="muted">Nichts gefunden. Ist der Kopfhörer im Kopplungsmodus und in der Nähe der Box?</p>`}
      ${audioCount && audioCount < list.length ? `<button class="btn small" id="bt-more" style="margin-top:8px">Andere Geräte anzeigen (${list.length - audioCount})</button>` : ""}
      ${list.length && !audioCount ? `<p class="muted small">Kein Kopfhörer erkannt. Steht deiner unter einem anderen Namen in der Liste, kannst du ihn trotzdem koppeln.</p>` : ""}
      <div class="actions"><button class="btn" id="bt-x">Schließen</button><button class="btn" id="bt-again">Noch einmal suchen</button></div>`);
    $("#bt-x", dialog).onclick = closeDialog;
    if ($("#bt-more", dialog)) $("#bt-more", dialog).onclick = (e) => { $$("li.other", dialog).forEach((li) => li.hidden = false); e.currentTarget.remove(); };
    $("#bt-again", dialog).onclick = () => { closeDialog(); setTimeout(() => $("#bt-scan")?.click(), 50); };
    $$("[data-pair]", dialog).forEach((btn) => btn.onclick = run(async () => {
      const x = list[btn.dataset.pair];
      $$("[data-pair]", dialog).forEach((o) => o.disabled = true); btn.textContent = "Kopple …";
      try { paintBT(await api("/api/bluetooth/pair", { mac: x.mac })); closeDialog(); toast(`„${x.name}“ gekoppelt`); }
      catch (err) { btn.textContent = "Koppeln"; $$("[data-pair]", dialog).forEach((o) => o.disabled = false); throw err; }
    }));
  });
}
function paintUpdate(u, sys) {
  const el = $("#upd"); if (!el) return;
  const when = u.checked ? new Date(u.checked).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "";
  el.innerHTML = u.available
    ? `<div class="status-line" style="margin-top:10px"><span class="pill accent">${u.count === 1 ? "1 Update" : u.count + " Updates"} verfügbar</span></div>
       <ul class="small" style="margin:0 0 10px;padding-left:20px;line-height:1.6">${(u.changes || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
       <button class="btn primary" id="u-go">Jetzt aktualisieren</button>`
    : `<div class="row wrap" style="margin-top:10px"><span class="small muted grow">${when ? "Auf dem neuesten Stand (geprüft " + when + ")" : "Noch nicht geprüft"}</span><button class="btn small" id="u-check">Nach Updates suchen</button></div>`;
  if (sys.update_log) el.insertAdjacentHTML("beforeend", `<details style="margin-top:10px"><summary class="small muted" style="cursor:pointer">Protokoll des letzten Updates</summary><pre>${esc(sys.update_log)}</pre></details>`);
  const c = $("#u-check");
  if (c) c.onclick = run(async () => { c.disabled = true; c.textContent = "Suche …"; try { const r = await api("/api/system", { action: "check_update" }); S.state.update = r.available; updateDot(); paintUpdate(r, sys); if (!r.available) toast("Die Box ist auf dem neuesten Stand"); } finally { if (c.isConnected) { c.disabled = false; c.textContent = "Nach Updates suchen"; } } });
  const g = $("#u-go");
  if (g) g.onclick = run(async () => {
    if (!await ask("Jetzt aktualisieren?", "Die Box holt die neue Version und startet danach neu. Die Wiedergabe stoppt dabei kurz. Das dauert ein bis zwei Minuten.", "Aktualisieren")) return;
    await api("/api/system", { action: "update" });
    openDialog(`<h2>Update läuft …</h2><div class="pulse"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5"/></svg></div><p style="text-align:center">Die Box holt die neue Version und startet neu.<br><span class="muted small">Diese Seite lädt sich danach von selbst neu.</span></p>`);
    const before = sys.revision, start = Date.now();
    const poll = async () => {
      try { const n = await api("/api/system"); if (n.revision && n.revision !== before) { location.reload(); return; } } catch {}
      if (Date.now() - start > 10 * 60000) { closeDialog(); toast("Das Update dauert ungewöhnlich lange – Protokoll unter System ansehen", "error"); return; }
      setTimeout(poll, 3000);
    };
    setTimeout(poll, 8000);
  });
}
function paintBackup() {
  const el = $("#bk-status"), b = S.backup || {}; if (!el) return;
  const when = (t) => t ? new Date(t).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }) : "";
  el.innerHTML = b.running ? `<span class="pill accent">Läuft</span><span class="small">${esc(b.step || "")}</span>`
    : b.ok === false ? `<span class="pill bad">Letzte Sicherung fehlgeschlagen</span><span class="small muted">${esc(b.error || "")}</span>`
    : b.last_ok ? `<span class="pill ok">Gesichert</span><span class="small muted">zuletzt am ${when(b.last_ok)}</span>`
    : `<span class="pill">Noch nie gesichert</span>`;
  const r = $("#k-run"); if (r) r.disabled = !!b.running;
}

// ---------- Start ----------
(async function start() {
  try { S.cfg = (await api("/api/config")).config; } catch {}
  const [v, f] = location.hash.slice(1).split("/");
  S.view = ["now", "hoerspiele", "musik", "cards", "settings"].includes(v) ? v : "now"; S.folder = f ? decodeURIComponent(f) : null;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === S.view));
  render(); connect();
  // Service Worker geht nur über HTTPS (oder wenn der Browser die Adresse als sicher einstuft)
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
  // Installations-Angebot des Browsers (Chrome/Edge) merken, statt es sofort zu zeigen
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); S.installPrompt = e; if ($("#app-inst")) paintInstall(); });
  window.addEventListener("appinstalled", () => { S.installPrompt = null; toast("Klangkiste ist jetzt als App installiert"); if ($("#app-inst")) paintInstall(); });
})();
