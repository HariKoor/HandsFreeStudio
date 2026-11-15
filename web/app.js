// HandsFree Studio UI. app.py streams its state here (/events); this page
// draws it, reports where its buttons are (/layout) and sends keys and the
// mouse back (/input). Every clickable element carries data-hit: the JSON
// that app.py's click() receives when the head pointer clicks it.
"use strict";

const W = 1280, H = 720;
const $ = (q, el = document) => el.querySelector(q);
const stage = $("#stage");
let S = null;               // latest state from app.py
let structKey = "";         // re-render the body only when this changes
let lastClicks = 0;
const cur = { x: 640, y: 360, tx: 640, ty: 360, seen: false };
const mouse = { x: 640, y: 360, down: false, dirty: false };

// ---------------------------------------------------------------- icons
const I = {
  play: '<path d="M8 5.2l10.5 6.8L8 18.8z" fill="currentColor" stroke="none"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.5" fill="currentColor" stroke="none"/>',
  rec: '<circle cx="12" cy="12" r="6" fill="#FF4646" stroke="none"/>',
  recon: '<circle cx="12" cy="12" r="6" fill="#fff" stroke="none"/>',
  minus: '<path d="M6 12h12"/>', plus: '<path d="M6 12h12M12 6v12"/>',
  clear: '<path d="M4.5 7h15M9.5 7V4.8h5V7M6.8 7l.9 12.2h8.6l.9-12.2"/>',
  beat: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M18.5 16v4M16.5 18h4"/>',
  menu: '<path d="M5 8h14M5 16h14"/><circle cx="9" cy="8" r="2.2" fill="#0D0D0F"/><circle cx="15" cy="16" r="2.2" fill="#0D0D0F"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
  pads: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>',
  drums: '<rect x="3.5" y="6" width="3.6" height="12" rx="1.8"/><rect x="10.2" y="6" width="3.6" height="12" rx="1.8" fill="currentColor"/><rect x="16.9" y="6" width="3.6" height="12" rx="1.8"/>',
  hum: '<path d="M3 12h2.5M7 8v8M10.5 5v14M14 9v6M17.5 7v10M21 12h-1"/>',
  mix: '<path d="M6 4v16M12 4v16M18 4v16"/><rect x="4" y="13" width="4" height="3.4" rx="1.2" fill="currentColor"/><rect x="10" y="7" width="4" height="3.4" rx="1.2" fill="currentColor"/><rect x="16" y="10.5" width="4" height="3.4" rx="1.2" fill="currentColor"/>',
  spk: '<path d="M5 9.5h3l4-3.5v12l-4-3.5H5z"/><path d="M15.5 9a4 4 0 0 1 0 6"/>',
  mute: '<path d="M5 9.5h3l4-3.5v12l-4-3.5H5z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8" fill="currentColor" stroke="none"/>',
  undo: '<path d="M9 7 5 11l4 4"/><path d="M5 11h9a5 5 0 0 1 0 10h-2"/>',
};
// Grapes: the HandsFree Studio mark, drawn as SVG so it scales and each
// grape can move on its own. One shared <defs> block holds the gradients.
// Each grape: a soft shadow it casts on the grapes behind it, the coloured
// body, shading towards the lower right, reflected light along the lower
// edge, a broad sheen and a sharp highlight. Grapes are drawn back to front.
const GRAPE_DEFS = `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <radialGradient id="gpA" cx=".36" cy=".3" r=".8"><stop offset="0" stop-color="#ffb08a"/><stop offset=".4" stop-color="#fb6a76"/><stop offset=".78" stop-color="#e2386f"/><stop offset="1" stop-color="#a91a63"/></radialGradient>
  <radialGradient id="gpB" cx=".36" cy=".3" r=".8"><stop offset="0" stop-color="#ffa2d6"/><stop offset=".4" stop-color="#f04fb2"/><stop offset=".78" stop-color="#c62c9c"/><stop offset="1" stop-color="#7c1488"/></radialGradient>
  <radialGradient id="gpC" cx=".36" cy=".3" r=".8"><stop offset="0" stop-color="#d9a2ff"/><stop offset=".4" stop-color="#a654ff"/><stop offset=".78" stop-color="#7a30ea"/><stop offset="1" stop-color="#4413b8"/></radialGradient>
  <radialGradient id="gpO" cx=".4" cy=".34" r=".72"><stop offset=".55" stop-color="#1a0020" stop-opacity="0"/><stop offset="1" stop-color="#1a0020" stop-opacity=".5"/></radialGradient>
  <radialGradient id="gpR" cx=".5" cy=".5" r=".5" fx=".72" fy=".9"><stop offset="0" stop-color="#ffd9f6" stop-opacity=".38"/><stop offset=".4" stop-color="#ffd9f6" stop-opacity="0"/></radialGradient>
  <radialGradient id="gpS" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff" stop-opacity=".9"/><stop offset=".55" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
  <linearGradient id="gpL" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#f26a00"/><stop offset=".55" stop-color="#ff9a1f"/><stop offset="1" stop-color="#ffc35a"/></linearGradient>
  <linearGradient id="gpT" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#c94a00"/><stop offset=".5" stop-color="#ff8a2a"/><stop offset="1" stop-color="#d45400"/></linearGradient>
  <filter id="gpBlur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="16"/></filter>
  <filter id="gpBlurBig" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="34"/></filter>
</defs></svg>`;
// x, y, colour. Back row first; the bunch's middle is (620, 700).
const GRAPES = [[337, 463, "A"], [620, 463, "A"], [903, 463, "A"], [478, 722, "B"], [762, 722, "B"], [608, 980, "C"]];
function grapesSVG(size, cls = "") {
  const g = GRAPES.map(([x, y, k], i) => {
    const dx = ((x - 620) / 300).toFixed(2), dy = ((y - 700) / 300).toFixed(2);
    return `<g class="gr" style="--i:${i};--dx:${dx};--dy:${dy}"><g class="gb">
      <circle cx="${x + 18}" cy="${y + 30}" r="134" fill="#14001c" opacity=".55" filter="url(#gpBlur)"/>
      <circle cx="${x}" cy="${y}" r="138" fill="url(#gp${k})"/>
      <circle cx="${x}" cy="${y}" r="138" fill="url(#gpO)"/>
      <circle cx="${x}" cy="${y}" r="138" fill="url(#gpR)"/>
      <ellipse cx="${x - 40}" cy="${y - 56}" rx="62" ry="40" fill="url(#gpS)" opacity=".55" transform="rotate(-30 ${x - 40} ${y - 56})"/>
      <ellipse cx="${x - 52}" cy="${y - 66}" rx="20" ry="13" fill="#fff" opacity=".92" transform="rotate(-30 ${x - 52} ${y - 66})"/></g></g>`;
  }).join("");
  return `<svg class="grapes ${cls}" width="${size}" height="${size}" viewBox="150 90 940 1080" aria-hidden="true">
    <ellipse class="gshadow" cx="620" cy="1128" rx="190" ry="26" fill="#000" opacity=".55" filter="url(#gpBlurBig)"/>
    <g class="bunch"><g class="leafg"><path d="M628 306 L660 196" stroke="url(#gpT)" stroke-width="30" stroke-linecap="round"/>
    <path d="M655 238 C688 158 752 118 874 138 C846 230 782 280 712 270 C688 266 668 256 655 238 Z" fill="url(#gpL)"/>
    <path d="M668 240 C720 214 780 180 846 150" stroke="#c75400" stroke-width="7" stroke-linecap="round" fill="none" opacity=".55"/>
    <path d="M700 168 C740 150 780 140 830 142" stroke="#fff" stroke-width="8" stroke-linecap="round" fill="none" opacity=".35"/></g>${g}</g></svg>`;
}
document.body.insertAdjacentHTML("afterbegin", GRAPE_DEFS);
// Re-run a one-shot grape animation (pop) on an element that may already have it.
function grapePop(el) {
  if (!el) return;
  el.classList.remove("pop"); void el.getBoundingClientRect(); el.classList.add("pop");
  clearTimeout(el._pop); el._pop = setTimeout(() => el.classList.remove("pop"), 900);   // back to idle motion
}

const ic = (k, size = 22, sw = 1.8) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${I[k]}</svg>`;
const hit = (v) => `data-hit='${JSON.stringify(v)}'`;
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap1 = (t) => t ? t[0].toUpperCase() + t.slice(1) : t;
const CAL = () => S.view === "calibrate" || S.view === "gazecal";     // full-screen steps
const EYES = () => !!(S.eye && S.eye.on);

function noteHTML(label) {
  const m = /^([A-G])([#b]?)(-?\d+)$/.exec(label || "");
  if (!m) return `<span class="note">${esc((label || "").replace("#", "♯"))}</span>`;
  return `<span class="note">${m[1]}${m[2] === "#" ? "♯" : m[2] ? "♭" : ""}<i>${m[3]}</i></span>`;
}

// ---------------------------------------------------------------- chrome
function topHTML() {
  const tabs = [["pads", "Pads"], ["drums", "Drums"], ["hum", "Hum"], ["mix", "Mix"]]
    .map(([k, n]) => `<button ${hit(["mode", k])} class="${S.mode === k && !S.menu ? "on" : ""}">${ic(k, 17)}${n}</button>`).join("");
  const nav = CAL() ? "" : `<div id="nav">${tabs}</div>`;
  return `<div id="top">
    <div class="brand"><div style="position:relative;width:34px;height:34px"><div class="bloom" style="inset:-4px;filter:blur(12px);opacity:.45"></div>
      <img src="logo.png" alt="" width="34" height="34" style="position:relative;display:block"></div>
      <span class="disp" style="font-size:20px;letter-spacing:-.03em">HandsFree <span style="color:var(--tx3)">Studio</span></span></div>
    ${nav}
    <div id="right">
      ${EYES() && !CAL() ? `<div class="pill" id="playp" title="Play mode: look at a pad, raise your eyebrows to play it">${ic("eye", 16)}<span>Play</span></div>` : ""}
      <div class="pill" id="midi"><span class="dotk"></span><span>FL Studio</span></div>
      <div class="pill" id="micp">${ic("mic", 15)}<span>Listening</span></div>
      <div id="mini" title="Mouth"><div class="bloom" style="inset:4px;filter:blur(14px);opacity:.4"></div>${grapesSVG(48, "sway")}<svg id="face" width="56" height="56" viewBox="0 0 56 56"></svg></div>
    </div></div>`;
}

function dockHTML() {
  const key = (h, icon, label, extra = "", aria = "") =>
    `<button class="key ${label ? "lab" : ""} ${extra}" ${hit(h)} aria-label="${aria || label}">${ic(icon, label ? 21 : 24)}${label ? `<span>${label}</span>` : ""}</button>`;
  return `<div id="dock">
    ${key(["button", "PLAY"], "play", "", "k-play", "Play")}${key(["button", "STOP"], "stop", "", "", "Stop")}${key(["button", "REC"], "rec", "", "k-rec", "Record")}
    <div class="vdiv"></div>
    ${key(["button", "SLOWER"], "minus", "", "", "Slower").replace('class="key', 'style="width:52px" class="key')}
    <div style="width:84px;display:flex;flex-direction:column;align-items:center"><span class="disp" id="bpm" style="font-size:30px;font-weight:300;letter-spacing:-.03em;line-height:34px">120</span><span class="cap" style="font-size:11px">BPM</span></div>
    ${key(["button", "FASTER"], "plus", "", "", "Faster").replace('class="key', 'style="width:52px" class="key')}
    <div class="vdiv"></div>
    ${key(["button", "CLEAR"], "clear", "Clear")}${key(["button", "BEAT"], "beat", "Beat", "k-beat")}${key(["button", "MENU"], "menu", "Menu", "k-menu")}
  </div>
  <div class="ro" style="left:28px"><span class="cap" id="roLc"></span><span class="v" id="roL"></span></div>
  <div class="ro" style="right:28px;align-items:flex-end;text-align:right"><span class="cap">Status</span><span class="v" id="roR"></span></div>`;
}

// ---------------------------------------------------------------- views
function padsHTML() {
  const p = S.pads, out = [];
  for (let r = p.rows - 1; r >= 0; r--)
    for (let c = 0; c < p.cols; c++) {
      const i = r * p.cols + c;
      if (i >= p.labels.length) { out.push("<div></div>"); continue; }
      const h = `var(--k${p.hues[i] % 7})`, root = p.degrees[i] === "1";
      out.push(`<div class="pad surf" data-pad="${i}" style="--h:${h}"><span class="deg">${p.chords ? "" : p.degrees[i]}</span>${noteHTML(p.labels[i])}
        <span class="led ${root ? "root" : ""}"></span><div class="lit"><span class="deg" style="color:rgba(255,255,255,.7)">${p.chords ? "" : p.degrees[i]}</span>${noteHTML(p.labels[i])}</div></div>`);
    }
  return `<div class="pads ${p.rows > 3 ? "small" : ""}" ${hit(["pads"])} style="position:absolute;grid-template-columns:repeat(${p.cols},minmax(0,1fr));grid-template-rows:repeat(${p.rows},minmax(0,1fr))">${out.join("")}</div>`;
}

const sx = (k) => k * 64 + Math.floor(k / 4) * 12;
function drumsHTML() {
  const d = S.drums;
  const rows = d.names.map((nm, r) => {
    const m = d.muted[r];
    const cells = d.grid[r].map((on, k) =>
      `<button class="step" ${hit(["step", r, k])} aria-label="${nm} step ${k + 1}" style="margin-left:${k === 0 ? 0 : k % 4 === 0 ? 18 : 6}px">${on ? '<div class="son"></div>' : ""}</button>`).join("");
    return `<div class="drow ${m ? "mut" : ""}" style="--h:var(--k${[0, 1, 2, 3][r]})">
      <button class="dlab surf ${m ? "mut" : ""}" ${hit(["label", r])}><span class="nm disp"><span class="dotk" style="width:8px;height:8px;background:${m ? "var(--tx3)" : "var(--h)"};${m ? "" : "box-shadow:0 0 10px var(--h)"}"></span>${cap1(nm)}</span>
      <span class="sub">${ic(m ? "mute" : "spk", 14)}${m ? "Muted" : "Mute"}</span></button><div class="steps">${cells}</div></div>`;
  }).join("");
  return `<div class="drums" style="position:absolute"><div id="ph"></div>${rows}</div>`;
}

function mixHTML() {
  const d = S.drums;
  return `<div class="mix" style="position:absolute">${d.names.map((nm, i) => {
    const m = d.muted[i], v = d.volume[i], n = Math.round(v / 127 * 16);
    const segs = Array.from({ length: 16 }, (_, j) => `<div class="seg ${15 - j < n ? "on" : ""}"></div>`).join("");
    return `<div class="strip surf ${m ? "mut" : ""}" style="--h:var(--k${i})">
      <div class="hd"><span class="nm disp" style="${m ? "color:var(--tx3)" : ""}"><span class="dotk" style="width:8px;height:8px;background:${m ? "var(--tx3)" : "var(--h)"}"></span>${cap1(nm)}</span>
      <span class="val disp" style="${m ? "color:var(--tx3)" : ""}">${Math.round(v / 1.27)}</span></div>
      <div style="display:flex;gap:14px;flex:1;min-height:0"><div class="meter">${segs}</div>
        <div style="flex:1;display:flex;flex-direction:column;gap:12px">
          <button class="bigbtn" ${hit(["cmd", ["volume_by", i, 13]])} aria-label="${nm} louder">${ic("plus", 28)}</button>
          <button class="bigbtn" ${hit(["cmd", ["volume_by", i, -13]])} aria-label="${nm} quieter">${ic("minus", 28)}</button></div></div>
      <button class="pillbtn ${m ? "inv" : ""}" ${hit(["cmd", ["mute", i, null]])}>${ic(m ? "mute" : "spk", 19)}${m ? "Muted" : "Mute"}</button></div>`;
  }).join("")}</div>`;
}

function humHTML() {
  if (!S.hum) return `<div class="hum" style="position:absolute;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px">
      <div class="disp" style="font-size:44px;letter-spacing:-.035em">No microphone.</div>
      <div style="color:var(--tx3);font-size:16px">Hum mode needs a microphone. Check the permission for Terminal in System Settings.</div></div>`;
  const cells = Array.from({ length: 16 }, (_, k) => `<div class="cell" style="margin-left:${k === 0 ? 0 : k % 4 === 0 ? 14 : 4}px"></div>`).join("");
  return `<div class="hum" style="position:absolute">
    <div class="bloom" style="left:40px;top:10px;width:260px;height:240px;opacity:.18;filter:blur(60px)"></div>
    <div style="position:absolute;left:40px;top:6px;width:250px;height:250px"><div id="horb" style="position:absolute;left:25px;top:22px">${grapesSVG(200, "sway")}</div></div>
    <div style="position:absolute;left:340px;top:20px;display:flex;flex-direction:column;gap:14px">
      <span class="cap"><b>Hum</b><span class="sep">·</span>Notes snap to the scale and play one bar later</span>
      <div class="metro" id="metro"><div>1</div><div>2</div><div>3</div><div>4</div></div></div>
    <div style="position:absolute;left:340px;top:126px;display:flex;gap:60px">
      <div><div class="cap">Hearing</div><div class="disp" id="hHeard" style="font-size:84px;font-weight:300;letter-spacing:-.04em;line-height:1.1">–</div></div>
      <div><div class="cap">Playing</div><div class="disp" id="hPlay" style="font-size:84px;font-weight:300;letter-spacing:-.04em;line-height:1.1;color:var(--g5)">–</div></div></div>
    <div class="lane" id="lane">${cells}<div id="runs"></div><div id="hph"></div></div></div>`;
}

function menuHTML() {
  const st = S.settings, p = S.pads;
  const tile = (h, cap, v, opts = {}) =>
    `<button class="tile surf" ${hit(h)}><span class="tx"><span class="cap">${cap}</span><span style="display:flex;flex-direction:column"><span class="v disp">${esc(v)}</span>${opts.sub ? `<span class="s2">${opts.sub}</span>` : ""}</span></span>${opts.tog !== undefined ? `<span class="tog ${opts.tog ? "on" : ""}"></span>` : ""}${opts.right || ""}</button>`;
  const pair = (cap, v, a, b) =>
    `<div class="tile pair surf"><span class="cap" style="display:flex;justify-content:space-between">${cap}<span style="color:var(--tx)">${esc(v)}</span></span>
     <span class="row"><button ${hit(a[0])} class="${a[2] ? "on" : ""}">${a[1]}</button><button ${hit(b[0])} class="${b[2] ? "on" : ""}">${b[1]}</button></span></div>`;
  const themes = ["sunset", "aurora", "ultraviolet", "mono"];
  const sw = `<span class="sw">${themes.map((t) => `<span class="th-${t} ${t === st.theme ? "on" : ""}" style="background:linear-gradient(135deg,var(--g1),var(--g2),var(--g4),var(--g6))"></span>`).join("")}</span>`;
  const col = (name, tiles) => `<div class="mcol"><span class="cap">${name}</span><div class="tiles">${tiles.join("")}</div></div>`;
  return `<div class="menu" style="position:absolute">
    ${col("Sound", [tile(["cmd", ["scale"]], "Scale", cap1(p.scale)), tile(["cmd", ["key"]], "Key", p.root),
      pair("Octave", p.octave, [["cmd", ["octave", -1]], "−"], [["cmd", ["octave", 1]], "+"]),
      tile(["cmd", ["toggle", "chords"]], "Chords", st.chords ? "On" : "Off", { tog: st.chords, sub: "each pad plays a chord" }),
      pair("Sound", "Row " + (st.melody_channel + 1), [["cmd", ["instrument", -1]], "−"], [["cmd", ["instrument", 1]], "+"])])}
    ${col("Pointer", [pair("Mode", st.pointer === "eyes" ? "Eyes" : "Head", [["cmd", ["pointer", "head"]], "Navigate", st.pointer !== "eyes"], [["cmd", ["pointer", "eyes"]], "Play", st.pointer === "eyes"]),
      st.pointer === "eyes" ? tile(["cmd", ["gazecal"]], "Calibrate eyes", "Follow the dot", { sub: "about 30 seconds" })
        : tile(["cmd", ["center"]], "Recenter", "Look at center", { sub: "or close eyes 1.5 s" }),
      pair("Head turn to reach the edge", Math.round(st.range_yaw) + "°", [["cmd", ["range", -1]], "Less"], [["cmd", ["range", 1]], "More"]),
      pair("Flip direction", "", [["cmd", ["toggle", "flip_x"]], "Left–right", st.flip_x], [["cmd", ["toggle", "flip_y"]], "Up–down", st.flip_y]),
      tile(["cmd", ["cycle", "cursor"]], "Cursor", { solid: "Solid", hand: "Fingertip", mouth: "Mouth" }[st.cursor] || "Solid", { sub: "Solid · Fingertip · Mouth" })])}
    ${col("Clicking", [tile(["cmd", ["toggle", "dwell"]], "Dwell click", st.dwell ? "On" : "Off", { tog: st.dwell, sub: "hold still to click" }),
      tile(["cmd", ["toggle", "brow_click"]], "Eyebrow click", st.brow_click ? "On" : "Off", { tog: st.brow_click, sub: "raise eyebrows, in play mode" }),
      tile(["cmd", ["toggle", "voice_guard"]], "Voice guard", st.voice_guard ? "On" : "Off", { tog: st.voice_guard, sub: "no clicks while talking" }),
      tile(["cmd", ["toggle", "wake_word_on"]], "Wake word", st.wake_word_on ? "On" : "Off", { tog: st.wake_word_on, sub: "say “" + cap1(S.wake_word || "mozart") + "” first" }),
      tile(["cmd", ["toggle", "preview"]], "Face dots", st.preview ? "On" : "Off", { tog: st.preview, sub: "over the grapes, top right" })])}
    ${col("Session", [tile(["cmd", ["grid"]], "Pad grid", `${p.cols} × ${p.rows}`),
      tile(["cmd", ["undo"]], "Undo", "Last change", { sub: "in FL Studio" }),
      tile(["cmd", ["reset"]], "Reset", "Octave 4 · 120 BPM"),
      tile(["cmd", ["cycle", "theme"]], "Theme", cap1(st.theme), { right: sw }),
      tile(["cmd", ["quit"]], "Quit", S.quit_armed ? "Click again" : "Click twice")])}
  </div>`;
}

function calHTML() {
  const msg = S.eyes ? "Open your eyes." : S.message && !/centre/i.test(S.message) ? S.message.replace(/ - /, ". ") : "Look at the center.";
  return `<div class="cal">
    <div class="bloom" style="left:470px;top:120px;width:340px;height:300px;opacity:.22;filter:blur(70px)"></div>
    <div style="position:absolute;left:0;top:86px;width:1280px;display:flex;flex-direction:column;align-items:center">
      <div style="position:relative;width:320px;height:320px">
        <div class="ring" style="inset:6px"></div>
        <div style="position:absolute;left:55px;top:50px">${grapesSVG(210, "sway")}</div>
        <svg width="320" height="320" viewBox="0 0 320 320" aria-hidden="true" style="position:absolute;inset:0;transform:rotate(-90deg)">
          <defs><linearGradient id="cg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--g2)"/><stop offset=".5" stop-color="var(--g4)"/><stop offset="1" stop-color="var(--g6)"/></linearGradient></defs>
          <circle id="calarc" cx="160" cy="160" r="151" fill="none" stroke="url(#cg)" stroke-width="3" stroke-linecap="round" stroke-dasharray="948.8" stroke-dashoffset="948.8" style="transition:stroke-dashoffset 120ms linear"/></svg>
        <div style="position:absolute;left:150px;top:150px;width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 0 0 2.5px #000,0 0 24px 2px color-mix(in oklab,var(--g4) 60%,transparent)"></div>
      </div>
      <h1 class="disp" id="calmsg" style="margin:30px 0 0;font-size:56px;letter-spacing:-.035em;line-height:64px;font-weight:400">${esc(msg)}</h1>
      <p style="margin:12px 0 0;font-size:18px;line-height:1.5;color:var(--tx3);text-align:center;max-width:520px">Keep your mouth closed for two seconds while we learn your resting face.</p>
    </div>
    <p style="position:absolute;left:0;bottom:24px;width:1280px;margin:0;text-align:center;font-size:13px;color:var(--tx4)">Recenter any time: close both eyes for 1.5 seconds, or say “center”.</p></div>`;
}

// Eye calibration. The dot, its ring and the texts are updated in frame(),
// so the page is not rebuilt while the dot moves. Two kinds: "follow" (the
// dot glides over the screen in two laps) and the older 12 fixed dots.
function gazecalHTML() {
  return `<div class="cal gcal">
    <div id="gintro"><span class="cap"><b>Play mode</b><span class="sep">·</span><span id="gkind"></span></span>
      <h1 class="disp" id="gh1"></h1><p id="gp"></p></div>
    <div id="gdot"><div class="bloom"></div>
      <svg width="96" height="96" viewBox="0 0 96 96" aria-hidden="true"><circle cx="48" cy="48" r="40" fill="none" stroke="rgba(255,255,255,.16)" stroke-width="3"/>
        <circle id="garc" cx="48" cy="48" r="40" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-dasharray="251.3" stroke-dashoffset="251.3"/></svg>
      <svg class="core" width="30" height="30" viewBox="0 0 30 30" aria-hidden="true"><circle cx="15" cy="15" r="13.5" fill="url(#gpC)"/><ellipse cx="10.5" cy="9.5" rx="4.2" ry="2.8" fill="url(#gpS)" transform="rotate(-28 10.5 9.5)"/><circle cx="15" cy="15" r="2.6" fill="#fff"/></svg><div id="ghint" class="cap"></div></div>
    <div id="gcount" class="cap"></div><div id="gmsg" class="pill"></div></div>`;
}
const GHINT = { look: "Look here", turn: "Turn your head", nod: "Now nod", hold: "Hold" };
const GLAP = { still: "Lap 1 of 2 · follow with your eyes, head still", move: "Lap 2 of 2 · keep following, and slowly turn and nod your head" };
function gazecalFrame() {
  const c = S.eye && S.eye.cal, dot = $("#gdot");
  if (!c || !dot) return;
  const follow = c.phase === "follow" || c.phase === "pause" || c.n === 2;
  const card = c.phase === "intro" || c.phase === "pause";
  $("#gkind").textContent = follow ? "about 30 seconds" : "12 dots";
  $("#gh1").textContent = c.phase === "pause" ? "Once more, moving your head." : "Follow the dot with your eyes.";
  $("#gp").textContent = c.phase === "pause" ? "Keep your eyes on the dot while you slowly turn and nod your head. That teaches it to ignore head movement."
    : follow ? "It will glide across the screen twice. First lap: keep your head still. Second lap: move your head too."
    : "Keep looking at each dot while you slowly turn your head left and right, then nod. That teaches it to ignore head movement.";
  $("#gintro").classList.toggle("show", card);
  dot.classList.toggle("show", c.phase !== "intro");
  dot.classList.toggle("glide", c.phase === "follow");
  dot.style.transform = `translate(${c.x.toFixed(1)}px,${c.y.toFixed(1)}px)`;
  $("#garc").style.strokeDashoffset = (251.3 * (1 - Math.min(1, c.p))).toFixed(1);
  const hint = $("#ghint");
  hint.textContent = follow ? "" : GHINT[c.hint] || "";
  hint.className = "cap " + (c.y > 560 ? "above" : "below");
  $("#gcount").textContent = c.phase === "intro" ? "" : follow ? (c.phase === "pause" ? "" : GLAP[c.hint] || "") : `${c.i + 1} of ${c.n}`;
  const msg = $("#gmsg");
  msg.textContent = (S.message || "").replace(" - ", ". ");
  msg.classList.toggle("show", !!S.message);
}

function beatCardHTML() {
  if (!S.beat_card) return "";
  const label = S.beat_card.label.replace(/\s*\((preset|Gemini)\)/i, "");
  const src = /gemini/i.test(S.beat_card.label) ? "Gemini" : "Preset";
  return `<div class="veil" style="background:radial-gradient(540px 300px at 50% 46%,rgba(0,0,0,.9),rgba(0,0,0,.45));z-index:4"></div>
  <div class="card" ${hit(["none"])} style="position:absolute;left:380px;top:186px;width:520px;z-index:5;transform:rotate(-1.5deg);box-shadow:0 40px 100px -20px color-mix(in oklab,var(--g2) 60%,transparent)">
    <span class="cap"><b>New beat</b><span class="sep">·</span>${src}</span>
    <div class="disp" style="margin-top:12px;font-size:40px;letter-spacing:-.035em;line-height:1">${esc(cap1(label))}.</div>
    <p style="margin:12px 0 0;font-size:15px;line-height:1.55;color:var(--tx3)">Your last pattern is one Undo away.</p>
    <div style="display:flex;gap:10px;margin-top:22px">
      <button class="btn pri" ${hit(["cmd", ["beat_keep"]])} style="width:180px">${ic("check", 20, 2.2)}Keep it</button>
      <button class="btn" ${hit(["cmd", ["beat", null]])} style="width:150px">${ic("beat", 19)}Another</button>
      <button class="btn" ${hit(["cmd", ["beat_undo"]])} style="width:126px">${ic("undo", 19)}Undo</button></div></div>`;
}

// "Mozart help": every voice command on one screen, from studio.HELP.
// The face controls and Travis are added here, since they are not phrases.
function helpHTML() {
  const ww = cap1(S.wake_word || "mozart");
  const say = (t) => t.split(" / ").map((p) => `<span class="say">“${esc(p)}”</span>`).join('<span class="or">or</span>');
  const group = (g) => `<div class="hgrp"><span class="cap">${esc(g.group)}</span>${g.items.map((it) =>
    `<div class="hrow"><div class="says">${say(it.say)}</div><div class="does">${esc(it.does)}</div></div>`).join("")}</div>`;
  const G = S.help || [];
  const faceRows = [["Turn your head", "move the pointer"], ["Open your mouth", "click what is under it"],
    ["Raise your eyebrows", "play the pad you look at (play mode)"], ["Close your eyes 1.5 s", "recenter the pointer"]];
  const col = (gs) => `<div class="hcol">${gs.join("")}</div>`;
  return `<div class="hsheet" style="position:absolute">
    <div class="hhead"><div><span class="cap"><b>Help</b><span class="sep">·</span>Voice commands</span>
      <div class="disp" style="font-size:30px;letter-spacing:-.03em;margin-top:6px">Say “${esc(ww)}”, then a command.</div></div>
      <button class="pillbtn" ${hit(["cmd", ["help", false]])} style="width:230px;height:56px">${ic("check", 18, 2.2)}Close<span style="color:var(--tx3);margin-left:4px">· “${esc(ww)} close”</span></button></div>
    <div class="hgrid">
      ${col([G[0]].filter(Boolean).map(group))}
      ${col([G[2], G[3]].filter(Boolean).map(group))}
      ${col([G[4], G[1]].filter(Boolean).map(group))}
      ${col([`<div class="hgrp travis"><span class="cap"><b>Travis</b><span class="sep">·</span>no fixed commands</span>
        <div class="disp" style="font-size:20px;letter-spacing:-.025em;line-height:1.2">Say “Travis”, then describe the music.</div>
        <div class="does">It plays it into FL Studio. “Travis help” explains more.</div>
        <div class="hex"><span class="say">“Travis, a sad progression in A minor”</span><span class="say">“Travis, now an octave higher”</span></div></div>`,
        `<div class="hgrp"><span class="cap">Without your voice</span>${faceRows.map(([a, b]) =>
        `<div class="hrow"><div class="says"><span class="say face">${a}</span></div><div class="does">${b}</div></div>`).join("")}</div>`])}
    </div></div>`;
}

const VIEWS = { pads: padsHTML, drums: drumsHTML, mix: mixHTML, hum: humHTML, menu: menuHTML, help: helpHTML };

// ---------------------------------------------------------------- render
function makeKey() {
  const k = [S.view, S.mode, S.settings, S.quit_armed, !!S.beat_card && S.beat_card.label, !!S.hum];
  if (S.view === "help") k.push(S.help, S.wake_word);
  if (S.view === "pads" || S.view === "menu") k.push(S.pads.labels, S.pads.cols, S.pads.rows, S.pads.root, S.pads.scale, S.pads.octave, S.pads.chords, S.pads.hues);
  if (S.view === "drums" || S.view === "mix") k.push(S.drums.grid, S.drums.muted, S.drums.volume);
  if (S.view === "calibrate") k.push(S.eyes, S.message);
  k.push(EYES());
  return JSON.stringify(k);
}

function rebuild() {
  stage.className = "hf th-" + (S.settings.theme || "sunset") + (EYES() ? " eyes" : "");
  const body = S.view === "calibrate" ? calHTML() : S.view === "gazecal" ? gazecalHTML() : `<div id="body">${(VIEWS[S.view] || padsHTML)()}</div>`;
  $("#ui").innerHTML = topHTML() + body + (CAL() || S.view === "help" ? "" : dockHTML() + beatCardHTML());
  for (const el of stage.querySelectorAll("[data-hit]")) el.dataset.key = JSON.stringify(JSON.parse(el.dataset.hit));
  sendLayout();
}

function stagePos(el) {          // element rect in stage coordinates
  const s = stage.getBoundingClientRect(), r = el.getBoundingClientRect(), k = s.width / W;
  return { x: (r.left - s.left) / k, y: (r.top - s.top) / k, w: r.width / k, h: r.height / k };
}

function sendLayout() {
  // Report rects without the hover zoom, so targets do not grow when hovered.
  stage.classList.add("measuring");
  const targets = [...stage.querySelectorAll("[data-hit]")].map((el) => ({ hit: JSON.parse(el.dataset.hit), ...stagePos(el) }));
  stage.classList.remove("measuring");
  post("/layout", { view: S.view, targets });
}

function post(url, data) {
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }).catch(() => {});
}

let lastHover = "";
function frame() {
  requestAnimationFrame(frame);
  if (!S) return;
  if (S.closed) { $("#closed").style.display = "flex"; return; }
  const key = makeKey();
  if (key !== structKey) { structKey = key; rebuild(); }

  // hover ring
  const hv = S.hover && S.hover[0] !== "pads" ? JSON.stringify(S.hover) : "";
  if (hv !== lastHover) {
    for (const el of stage.querySelectorAll(".hv")) el.classList.remove("hv");
    if (hv) for (const el of stage.querySelectorAll("[data-hit]")) if (el.dataset.key === hv) el.classList.add("hv");
    lastHover = hv;
  }

  // top strip
  const midi = $("#midi");
  if (midi) {
    const dry = /dry run/i.test(S.midi), virt = /virtual/i.test(S.midi);
    midi.querySelector(".dotk").style.cssText = `background:${dry ? "#5F616D" : virt ? "var(--g2)" : "var(--ok)"};box-shadow:0 0 8px ${dry ? "transparent" : virt ? "var(--g2)" : "var(--ok)"}`;
    midi.title = S.midi;
    midi.lastChild.textContent = dry ? "Dry run" : "FL Studio";
    $("#micp").lastChild.textContent = !S.mic ? "Mic off" : S.mode === "hum" ? "Humming" : S.speaking ? "Hearing you" : S.voice ? (S.settings.wake_word_on ? "Say “" + cap1(S.wake_word || "mozart") + " …”" : "Listening") : "Voice off";
    const mg = $("#mini .grapes");
    mg.style.setProperty("--lv", Math.min(1, S.level || 0).toFixed(3));
    const beat = 60 / Math.max(40, S.tempo || 120);
    if (S.playing && !mg.classList.contains("groove")) {
      mg.style.setProperty("--beat", beat.toFixed(3) + "s");
      mg.style.setProperty("--beat-phase", (-(((S.beat_pos || 0) / 4) % 1) * beat).toFixed(3) + "s");   // land on the beat
      mg.classList.add("groove");
    } else if (!S.playing && mg.classList.contains("groove")) mg.classList.remove("groove");
    else if (S.playing) mg.style.setProperty("--beat", beat.toFixed(3) + "s");
    const face = $("#face");
    const pts = S.points || [];
    face.innerHTML = pts.map(([x, y]) => `<circle cx="${(10 + x * 36).toFixed(1)}" cy="${(10 + y * 36).toFixed(1)}" r="1.3" fill="#fff" opacity=".9"/>`).join("");
  }

  // dock
  const play = $(".k-play");
  if (play) {
    play.classList.toggle("on", S.playing);
    const rec = $(".k-rec"); rec.classList.toggle("rec", S.rec); rec.innerHTML = ic(S.rec ? "recon" : "rec", 24);
    $(".k-beat").classList.toggle("on", !!S.beat_card || /Making a/.test(S.status));
    $(".k-menu").classList.toggle("on", S.menu);
    $("#bpm").textContent = S.tempo;
    const p = S.pads;
    const L = { pads: ["Key", `${p.chords ? "Chords in " : ""}${p.root} ${p.scale} · oct ${p.octave}`], drums: ["Pattern", "16 steps"],
      hum: ["Hum", `${p.root} ${p.scale} · oct ${p.octave}`], mix: ["Sound", "Channel rack row " + (S.settings.melody_channel + 1)], menu: ["Menu", "Say “menu” to close"] }[S.view] || ["", ""];
    $("#roLc").textContent = L[0]; $("#roL").textContent = L[1];
    $("#roR").textContent = S.status;
  }

  // views
  if (S.view === "pads") {
    for (const el of stage.querySelectorAll(".pad")) {
      const i = +el.dataset.pad;
      el.classList.toggle("on", S.pads.playing === i);
      el.classList.toggle("sel", S.pads.selected === i && S.pads.playing !== i);
    }
  } else if (S.view === "drums") {
    const ph = $("#ph");
    if (ph) {
      if (S.beat_pos == null) ph.style.display = "none";
      else { const k = Math.floor(S.beat_pos) % 16; ph.style.display = "block"; ph.style.left = (150 + 16 + sx(k) - 5) + "px"; }
    }
  } else if (S.view === "calibrate") {
    const arc = $("#calarc"); if (arc) arc.style.strokeDashoffset = (948.8 * (1 - Math.min(1, S.calib))).toFixed(1);
  } else if (S.view === "gazecal") gazecalFrame();
  else if (S.view === "hum" && S.hum) humFrame(S.hum);

  mozartFrame(); travisFrame(); toastFrame(); cursorFrame();
}

let humBeat = null;
function humFrame(h) {
  const m = $("#metro"); if (!m) return;
  [...m.children].forEach((d, b) => { d.classList.toggle("lit", h.beat === b && h.flash); d.classList.toggle("one", b === 0); });
  $("#hHeard").textContent = (h.heard || "–").replace("#", "♯");
  $("#hPlay").textContent = (h.playing || "–").replace("#", "♯");
  const hg = $("#horb .grapes");
  hg.style.setProperty("--lv", h.heard ? "0.7" : "0");
  if (h.flash && h.beat !== humBeat) grapePop(hg);
  humBeat = h.flash ? h.beat : null;
  const x = (k) => k * 74 + Math.floor(k / 4) * 10;
  const runs = [];
  const taken = new Set();
  for (const [s, len, note] of h.runs) { for (let i = s; i < s + len; i++) taken.add(i); runs.push(["fresh", s, len, note]); }
  for (const [s, len, note] of h.echo_runs) { let clash = false; for (let i = s; i < s + len; i++) if (taken.has(i)) clash = true; if (!clash) runs.push(["echo", s, len, note]); }
  const names = (n) => { const N = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]; return N[n % 12] + (Math.floor(n / 12) - 1); };
  $("#runs").innerHTML = runs.map(([c, s, len, note]) => `<div class="run ${c}" style="left:${x(s)}px;width:${x(s + len - 1) - x(s) + 70}px">${names(note)}</div>`).join("");
  $("#hph").style.left = (x(h.step) - 4) + "px";
}

// Mozart: a warm capsule drops out of the top-right corner, beside the mic,
// the moment "Mozart" is heard. Sound bars while listening, then the
// command it took. (This replaced the old "Heard" card in the middle.)
let heardShown = "", mzWasShown = false;
function mozartFrame() {
  let el = $("#mz");
  if (!el) {
    stage.insertAdjacentHTML("beforeend", `<div id="mz" aria-live="polite"><div class="mzbars"><i></i><i></i><i></i><i></i><i></i></div>
      <div class="mzok">${ic("check", 18, 2.6)}</div>
      <div class="mztx"><span class="cap"><b>Mozart</b><span class="sep">·</span><span id="mzS">Listening</span></span><span class="disp" id="mzT">I'm listening…</span></div></div>`);
    el = $("#mz");
  }
  const W = S.wake || {}, heard = !!S.heard;
  const show = !CAL() && (heard || !!W.mozart);
  if (heard && S.heard !== heardShown) {
    heardShown = S.heard;
    $("#mzT").textContent = cap1(S.heard) + ".";
    grapePop($("#mini .grapes"));
  }
  if (!heard) heardShown = "";
  if (!heard && W.mozart) $("#mzT").textContent = "Listening…";
  $("#mzS").textContent = heard ? (S.status || "Done") : "Say a command";
  el.classList.toggle("heard", heard);
  el.classList.toggle("show", show);
  const mic = $("#micp"); if (mic) mic.classList.toggle("woke", show && !heard);
  mzWasShown = show;
}

// Travis (Gemini voice assistant): listening -> thinking -> the plan as chips,
// the one sounding now lit. Not clickable, so it is not in the layout.
const pretty = (t) => String(t || "").replace(/([A-G])#/g, "$1♯").replace(/([A-G])b/g, "$1♭");
let tvKey = "", tvActive = null;
function travisFrame() {
  let el = $("#travis");
  if (!el) {
    stage.insertAdjacentHTML("beforeend", `<div id="tvb" aria-hidden="true"><div class="halo"></div><div class="glass"></div><div class="gw">${grapesSVG(78)}</div>
      <div id="tvcap" class="pill"><span class="cap"><b>Travis</b></span><span>Listening…</span></div></div>`);
    stage.insertAdjacentHTML("beforeend", `<div class="card" id="travis">
      <div style="display:flex;flex-direction:column;min-width:0"><span class="cap"><b>Travis</b><span class="sep">·</span><span id="tvSrc"></span></span>
      <div class="disp" id="tvText"></div><div id="tvBody"></div></div></div>`);
    el = $("#travis");
  }
  const T = S.travis, W = S.wake || {};
  // the bubble: up while Travis is woken or busy
  const phase = T ? T.phase : W.travis ? "listening" : "";
  const bub = $("#tvb"), gv = $("#tvb .grapes");
  const up = !!phase && !CAL();
  if (up && !bub.classList.contains("show")) {             // 6 grapes fly in
    gv.classList.remove("assemble"); void gv.getBoundingClientRect(); gv.classList.add("assemble");
    clearTimeout(gv._as); gv._as = setTimeout(() => gv.classList.remove("assemble"), 1300);
  }
  bub.className = up ? "show " + phase : "";
  gv.classList.toggle("osc", phase === "thinking" || phase === "playing");
  gv.classList.toggle("dim", phase === "error");
  const card = !!T && T.phase !== "listening" && !CAL();
  if (!T) { el.classList.remove("show"); return; }
  el.className = "card " + T.phase + (card ? " show" : "");
  const key = JSON.stringify([T.phase, T.text, T.chips, T.msg, T.source]);
  if (key !== tvKey) {
    tvKey = key;
    $("#tvSrc").textContent = T.phase === "help" ? "Help" : T.phase === "listening" ? "Listening" : T.phase === "thinking" ? (T.source === "Gemini" ? "Asking Gemini" : "Working it out") : T.source;
    $("#tvText").textContent = T.phase === "help" ? "Say “Travis”, then anything you want to hear." : T.phase === "listening" ? "I'm listening…" : T.text ? "“" + cap1(T.text) + "”" : "";
    let body = "";
    if (T.phase === "help") body = `<div class="tvmsg" style="font-size:15px;color:var(--tx2);white-space:normal;max-width:760px">Describe the music in your own words: chords, a melody, a rhythm, how long each part lasts. Travis plays it into FL Studio, and remembers this project, so you can say “the same again” or “now an octave higher”.</div>
      <div class="tvchips" style="flex-wrap:wrap">${["a sad progression in A minor", "G minor for a quarter note, then F minor", "a C major arpeggio, eighth notes", "the same again, an octave higher"].map((x) =>
        `<div class="tvchip cmd" style="height:auto;padding:10px 14px"><span class="c" style="font-size:15px">“Travis, ${x}”</span></div>`).join("")}</div>`;
    else if (T.phase === "thinking") body = `<div class="tvthink"><i></i><i></i><i></i></div>`;
    else if (T.phase === "error") body = `<div class="tvmsg">${esc(T.msg)}</div>`;
    else if (T.phase === "playing") body = `<div class="tvchips">${T.chips.map((c, i) =>
      `<div class="tvchip ${c.kind === "rest" ? "rest" : c.kind === "command" ? "cmd" : ""}" data-i="${i}"><span class="c disp">${esc(pretty(c.label))}</span><span class="d mono">${esc(c.kind === "command" ? "app" : c.dur)}</span></div>`).join("")}</div><div class="tvmsg" id="tvMsg"></div>`;
    $("#tvBody").innerHTML = body;
  }
  if (T.phase === "playing") {
    for (const c of el.querySelectorAll(".tvchip")) c.classList.toggle("on", +c.dataset.i === T.active);
    if (T.active !== tvActive) { tvActive = T.active; if (T.active != null) grapePop($("#tvb .grapes")); }
    $("#tvMsg").textContent = T.waiting ? "Starts on the next bar" : T.done ? "Done" : "";
  }
}

function toastFrame() {
  let el = $("#toast");
  if (!el) { stage.insertAdjacentHTML("beforeend", `<div class="pill" id="toast"></div>`); el = $("#toast"); }
  const msg = !CAL() && S.message ? S.message.replace(" - ", ". ") : "";
  if (msg) el.textContent = msg;
  el.classList.toggle("show", !!msg);
}

// ---------------------------------------------------------------- cursor
function cursorHTML(kind) {
  const hand = `<div class="hand"><svg width="44" height="54" viewBox="0 0 44 54" aria-hidden="true"><g fill="#fff" stroke="#000" stroke-width="2" stroke-linejoin="round">
      <rect x="2.5" y="27" width="16" height="9" rx="4.5" transform="rotate(34 10.5 31.5)"/><rect x="9" y="22" width="29" height="29" rx="10.5"/>
      <rect x="20.5" y="18.5" width="8.5" height="13" rx="4.25"/><rect x="28.5" y="20.5" width="8.5" height="12" rx="4.25"/>
      <g class="fing" style="transform-origin:17px 30px"><rect x="12" y="1" width="10" height="29" rx="5"/><path d="M14.5 6.5h5" stroke="#B8BEC8" stroke-width="1.6" stroke-linecap="round"/></g></g></svg></div>`;
  const body = kind === "hand" ? hand : kind === "mouth" ? `<div class="jaw"><div class="t"></div><div class="b"></div></div>` : `<div class="dot"></div>`;
  return `<svg class="dw" width="64" height="64" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="28" fill="none" stroke="rgba(255,255,255,.18)" stroke-width="2.5"/>
      <circle class="dwa" cx="32" cy="32" r="28" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="175.9" stroke-dashoffset="175.9" style="filter:drop-shadow(0 0 6px var(--g4))"/></svg>
    <div class="rip"></div>${body}`;
}

let curKind = "";
function cursorFrame() {
  const el = $("#cur");
  const kind = S.settings.cursor || "solid";
  if (kind !== curKind) { el.innerHTML = cursorHTML(kind); curKind = kind; }
  const useMouse = !S.camera;
  const target = !S.cursor ? null : useMouse ? [mouse.x, mouse.y] : S.cursor;
  if (!target) { el.style.opacity = 0; cur.seen = false; return; }
  el.style.opacity = 1;
  el.className = EYES() ? (S.eye.snap ? "eye snap" : "eye") : "";
  if (!cur.seen || useMouse) { cur.x = target[0]; cur.y = target[1]; cur.seen = true; }
  else { cur.x += (target[0] - cur.x) * 0.55; cur.y += (target[1] - cur.y) * 0.55; }
  el.style.transform = `translate(${cur.x.toFixed(1)}px,${cur.y.toFixed(1)}px)`;

  const over = !!S.hover;
  const lv = Math.min(1, S.level || 0);
  if (kind === "solid") $("#cur .dot").style.transform = `scale(${((over ? 1.12 : 1) * (1 - 0.22 * lv)).toFixed(3)})`;
  if (kind === "hand") {
    $("#cur .fing").style.transform = `scaleY(${(1 - 0.2 * lv).toFixed(3)})`;
    $("#cur .hand").style.transform = `translateY(${(2 * lv).toFixed(1)}px) scale(${(1 - 0.03 * lv).toFixed(3)})`;
  }
  if (kind === "mouth") {
    $("#cur .jaw .t").style.transform = `rotate(${(-28 * lv).toFixed(1)}deg)`;
    $("#cur .jaw .b").style.transform = `rotate(${(28 * lv).toFixed(1)}deg)`;
  }
  $("#cur .dwa").style.strokeDashoffset = (175.9 * (1 - (S.dwell || 0))).toFixed(1);
  $("#cur .dw").style.opacity = S.settings.dwell && S.dwell > 0 ? 1 : 0;
  if (S.clicks !== lastClicks) {
    if (lastClicks) { const r = $("#cur .rip"); r.classList.remove("go"); void r.offsetWidth; r.classList.add("go"); grapePop($("#mini .grapes")); }
    lastClicks = S.clicks;
  }
}

// ---------------------------------------------------------------- input
function fit() {
  const k = Math.min(innerWidth / W, innerHeight / H);
  stage.style.transform = `scale(${k})`;
  if (S) setTimeout(sendLayout, 50);
}
addEventListener("resize", fit);

addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey) return;
  if (e.key === "f") { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}); return; }
  const k = e.key === "Tab" ? "tab" : e.key === "Escape" ? "esc" : e.key.length === 1 ? e.key.toLowerCase() : null;
  if (!k) return;
  e.preventDefault();
  post("/input", { type: "key", key: k });
});

function toStage(e) {
  const s = stage.getBoundingClientRect(), k = s.width / W;
  mouse.x = Math.max(0, Math.min(W - 1, (e.clientX - s.left) / k));
  mouse.y = Math.max(0, Math.min(H - 1, (e.clientY - s.top) / k));
  mouse.dirty = true;
}
addEventListener("pointermove", toStage);
addEventListener("pointerdown", (e) => { toStage(e); mouse.down = true; });
addEventListener("pointerup", (e) => { toStage(e); mouse.down = false; });
setInterval(() => {
  if (S && !S.camera && mouse.dirty) { mouse.dirty = false; post("/input", { type: "mouse", x: mouse.x, y: mouse.y, down: mouse.down }); }
}, 16);
addEventListener("pointerdown", () => { mouse.dirty = true; });

// ---------------------------------------------------------------- go
function connect() {
  const es = new EventSource("/events");
  es.onmessage = (e) => { S = JSON.parse(e.data); };
  es.onerror = () => { es.close(); setTimeout(connect, 800); };
}
fit(); connect(); requestAnimationFrame(frame);
document.fonts && document.fonts.ready.then(() => { if (S) setTimeout(sendLayout, 30); });
