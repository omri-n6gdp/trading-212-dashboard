/* =============================================================================
   jarvis-core.js  ·  shared data layer + SVG chart library
   One engine, three designs. Designs supply look (CSS, layout, palette);
   this file supplies data, formatters and themeable chart primitives.
   No build step, no dependencies.
   ============================================================================= */
const JC = (() => {
  "use strict";

  /* ---------------------------------------------------------------- API ---- */
  async function api(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(path + " -> " + r.status);
    return r.json();
  }

  /* -------------------------------------------------------------- Store ---- */
  const store = { data: {}, cfg: {}, listeners: new Set(), connected: false };
  function sub(fn) { store.listeners.add(fn); return () => store.listeners.delete(fn); }
  function notify(slice) { store.listeners.forEach(fn => { try { fn(slice); } catch (e) { console.error(e); } }); }

  async function boot() {
    const [snap, cfg] = await Promise.all([
      api("/api/snapshot"),
      api("/api/config").catch(() => ({})),
    ]);
    store.data = snap || {};
    store.cfg = cfg || {};
    setCurrency(store.cfg.resolved_currency || store.cfg.account_currency || "GBP");
    notify("*");
    connectWS();
    return store;
  }

  function connectWS() {
    let ws;
    try { ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/stream"); }
    catch (e) { return; }
    ws.onopen = () => { store.connected = true; notify("__conn"); };
    ws.onmessage = ev => { let m; try { m = JSON.parse(ev.data); } catch { return; } applyDelta(m.type, m.payload); };
    ws.onclose = () => { store.connected = false; notify("__conn"); setTimeout(connectWS, 2500); };
    ws.onerror = () => { try { ws.close(); } catch {} };
  }

  function applyDelta(type, payload) {
    const dot = String(type).lastIndexOf(".");
    const slice = String(type).slice(0, dot), verb = String(type).slice(dot + 1);
    const d = store.data;
    if (verb === "update") d[slice] = Object.assign({}, d[slice] || {}, payload);
    else if (verb === "replace") d[slice] = payload;
    else if (verb === "append") {
      const it = (payload && payload.item !== undefined) ? payload.item : payload;
      if (!Array.isArray(d[slice])) d[slice] = [];
      d[slice].unshift(it); d[slice] = d[slice].slice(0, 200);
    } else return;
    if (slice === "config") { store.cfg = Object.assign({}, store.cfg, payload); }
    notify(slice);
  }
  const get = (slice, def) => (store.data[slice] !== undefined ? store.data[slice] : def);

  /* --------------------------------------------------------- Formatters ---- */
  let CCY = "GBP";
  const _fmt = new Map();
  function setCurrency(c) { CCY = c || "GBP"; }
  function fmtr() { if (!_fmt.has(CCY)) _fmt.set(CCY, new Intl.NumberFormat("en-GB", { style: "currency", currency: CCY, maximumFractionDigits: 2 })); return _fmt.get(CCY); }
  function symbol() { try { return fmtr().formatToParts(0).find(p => p.type === "currency").value; } catch { return "£"; } }
  const money = v => (v == null || isNaN(v)) ? "—" : fmtr().format(v);
  function moneyC(v) {
    if (v == null || isNaN(v)) return "—";
    const a = Math.abs(v), s = v < 0 ? "-" : "", sym = symbol();
    if (a >= 1e6) return s + sym + (a / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return s + sym + (a / 1e3).toFixed(1) + "K";
    return s + sym + a.toFixed(a < 10 ? 2 : 0);
  }
  const pct = (v, d = 2) => (v == null || isNaN(v)) ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%";
  const pctp = (v, d = 1) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d) + "%";
  const num = (v, d = 2) => (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d);
  const compact = v => (v == null || isNaN(v)) ? "—" : (Math.abs(v) >= 1e9 ? (v / 1e9).toFixed(2) + "B" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(v));
  const sgn = v => v > 0 ? "pos" : (v < 0 ? "neg" : "flat");
  const dshort = t => (t || "").slice(2);   // 2026-05-30 -> 26-05-30

  /* ------------------------------------------------------ DOM + colours ---- */
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (v == null) continue;
      if (k === "class") e.className = v;
      else if (k === "html") e.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    }
    for (const c of kids.flat()) { if (c == null || c === false) continue; e.append(c.nodeType ? c : document.createTextNode(String(c))); }
    return e;
  }
  const _rgb = h => { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map(x => x + x).join(""); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; };
  const lerp = (a, b, t) => a + (b - a) * t;
  function mix(c1, c2, t) { const a = _rgb(c1), b = _rgb(c2); return `rgb(${Math.round(lerp(a[0], b[0], t))},${Math.round(lerp(a[1], b[1], t))},${Math.round(lerp(a[2], b[2], t))})`; }
  function diverge(v, neg, mid, pos) { // v in [-1,1]
    v = Math.max(-1, Math.min(1, v));
    return v < 0 ? mix(mid, neg, -v) : mix(mid, pos, v);
  }

  const W = c => Math.max(220, (c && c.clientWidth) || 600);
  function tip(container) {
    let t = container.querySelector(".jc-tip");
    if (!t) { t = el("div", { class: "jc-tip" }); t.style.cssText = "position:absolute;pointer-events:none;z-index:30;display:none;white-space:nowrap;padding:6px 9px;font-size:11px;border-radius:4px;transform:translate(-50%,-115%)"; container.style.position = container.style.position || "relative"; container.append(t); }
    return t;
  }

  /* ------------------------------------------------------------ charts ---- */
  // Multi-series line / area chart with crosshair + tooltip.
  // series: [{name,color,points:[{t,y}],fill?,dash?,width?}]
  function lineChart(container, series, opts = {}) {
    const o = Object.assign({ height: 300, padL: 54, padR: 58, padT: 14, padB: 24, grid: "#1b2730", axis: "#5b6b78", ink: "#8aa0b0", zeroLine: false, fmtY: v => v.toFixed(1), tipRow: null }, opts);
    const w = W(container), h = o.height;
    container.innerHTML = "";
    const t = tip(container);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", w); svg.setAttribute("height", h); svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.style.display = "block";
    const valid = series.filter(s => s.points && s.points.length > 1);
    if (!valid.length) { container.innerHTML = `<div style="height:${h}px;display:flex;align-items:center;justify-content:center;color:${o.axis};font-size:12px">awaiting data…</div>`; return; }
    const n = Math.max(...valid.map(s => s.points.length));
    let mn = Infinity, mx = -Infinity;
    valid.forEach(s => s.points.forEach(p => { if (p.y < mn) mn = p.y; if (p.y > mx) mx = p.y; }));
    if (mn === mx) { mn -= 1; mx += 1; }
    const pad = (mx - mn) * 0.10; mn -= pad; mx += pad;
    if (o.zeroLine) { if (mn > 0) mn = 0; if (mx < 0) mx = 0; }
    const xs = i => o.padL + (i / (n - 1)) * (w - o.padL - o.padR);
    const ys = v => o.padT + (mx - v) / (mx - mn) * (h - o.padT - o.padB);
    let g = "", defs = "", paths = "", last = "";
    for (let i = 0; i <= 5; i++) {
      const v = mn + (mx - mn) * i / 5, y = ys(v), zero = o.zeroLine && Math.abs(v) < 1e-9;
      g += `<line x1="${o.padL}" y1="${y.toFixed(1)}" x2="${w - o.padR}" y2="${y.toFixed(1)}" stroke="${zero ? o.axis : o.grid}" stroke-width="${zero ? 1.2 : 1}"/>`;
      g += `<text x="${o.padL - 8}" y="${(y + 3).toFixed(1)}" fill="${o.axis}" text-anchor="end" font-size="9" font-family="inherit">${o.fmtY(v)}</text>`;
    }
    const ref = valid[0].points;
    [0, Math.floor((n - 1) / 3), Math.floor(2 * (n - 1) / 3), n - 1].forEach(i => {
      const d = (ref[Math.min(i, ref.length - 1)] || {}).t || "";
      g += `<text x="${xs(i).toFixed(1)}" y="${h - 7}" fill="${o.axis}" text-anchor="middle" font-size="9" font-family="inherit">${dshort(d)}</text>`;
    });
    valid.forEach((s, si) => {
      const pts = s.points.map((p, i) => `${xs(i).toFixed(1)},${ys(p.y).toFixed(1)}`).join(" ");
      if (s.fill) {
        const id = "g" + si + Math.random().toString(36).slice(2, 6);
        defs += `<linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity="0.30"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient>`;
        const base = ys(mn).toFixed(1);
        paths += `<polygon points="${xs(0).toFixed(1)},${base} ${pts} ${xs(s.points.length - 1).toFixed(1)},${base}" fill="url(#${id})"/>`;
      }
      paths += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round" opacity="${s.dash ? 0.9 : 1}"/>`;
      const lp = s.points[s.points.length - 1];
      last += `<text x="${w - o.padR + 6}" y="${(ys(lp.y) + 3).toFixed(1)}" fill="${s.color}" font-size="10" font-weight="600" font-family="inherit">${o.fmtY(lp.y)}</text>`;
    });
    let dots = "";
    valid.forEach((s, si) => { dots += `<circle class="cd${si}" r="3.4" fill="${s.color}" stroke="#000" stroke-width="1.4" style="display:none"/>`; });
    svg.innerHTML = `<defs>${defs}</defs>${g}${paths}${last}<line class="cx" y1="${o.padT}" y2="${h - o.padB}" stroke="${valid[0].color}" stroke-width="1" stroke-dasharray="2 3" style="display:none"/>${dots}<rect x="${o.padL}" y="${o.padT}" width="${w - o.padL - o.padR}" height="${h - o.padT - o.padB}" fill="transparent"/>`;
    container.append(svg);
    svg.addEventListener("mousemove", e => {
      const r = svg.getBoundingClientRect();
      let i = Math.round(((e.clientX - r.left) / r.width * w - o.padL) / (w - o.padL - o.padR) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      const cx = svg.querySelector(".cx"); cx.setAttribute("x1", xs(i)); cx.setAttribute("x2", xs(i)); cx.style.display = "block";
      let rows = "";
      valid.forEach((s, si) => { const p = s.points[Math.min(i, s.points.length - 1)]; const dt = svg.querySelector(".cd" + si); if (p) { dt.setAttribute("cx", xs(i)); dt.setAttribute("cy", ys(p.y)); dt.style.display = "block"; rows += `<div style="display:flex;justify-content:space-between;gap:14px"><span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${s.color};margin-right:5px"></span>${s.name}</span><b>${o.fmtY(p.y)}</b></div>`; } });
      t.innerHTML = `<div style="opacity:.6;margin-bottom:3px">${(ref[Math.min(i, ref.length - 1)] || {}).t || ""}</div>${rows}`;
      t.style.display = "block"; t.style.left = Math.min(r.width - 70, Math.max(70, xs(i))) + "px"; t.style.top = (o.padT + 30) + "px";
    });
    svg.addEventListener("mouseleave", () => { t.style.display = "none"; svg.querySelectorAll("circle[class^=cd]").forEach(d => d.style.display = "none"); const cx = svg.querySelector(".cx"); if (cx) cx.style.display = "none"; });
  }

  // Monte-Carlo fan chart. fan: [{d,p5,p25,p50,p75,p95}] as % returns.
  function fanChart(container, fan, opts = {}) {
    const o = Object.assign({ height: 250, padL: 50, padR: 16, padT: 14, padB: 24, color: "#5ad1ff", grid: "#1b2730", axis: "#5b6b78", band1: "rgba(90,209,255,.10)", band2: "rgba(90,209,255,.22)" }, opts);
    const w = W(container), h = o.height; container.innerHTML = ""; const t = tip(container);
    if (!fan || fan.length < 2) { container.innerHTML = `<div style="height:${h}px;display:flex;align-items:center;justify-content:center;color:${o.axis};font-size:12px">awaiting simulation…</div>`; return; }
    const n = fan.length; let mn = Infinity, mx = -Infinity;
    fan.forEach(f => { mn = Math.min(mn, f.p5); mx = Math.max(mx, f.p95); });
    mn = Math.min(mn, 0); mx = Math.max(mx, 0); const pad = (mx - mn) * 0.08; mn -= pad; mx += pad;
    const xs = i => o.padL + (i / (n - 1)) * (w - o.padL - o.padR);
    const ys = v => o.padT + (mx - v) / (mx - mn) * (h - o.padT - o.padB);
    let g = "";
    for (let i = 0; i <= 5; i++) { const v = mn + (mx - mn) * i / 5, y = ys(v), z = Math.abs(v) < 1e-9; g += `<line x1="${o.padL}" y1="${y.toFixed(1)}" x2="${w - o.padR}" y2="${y.toFixed(1)}" stroke="${z ? o.axis : o.grid}"/><text x="${o.padL - 8}" y="${(y + 3).toFixed(1)}" fill="${o.axis}" text-anchor="end" font-size="9" font-family="inherit">${v > 0 ? "+" : ""}${v.toFixed(0)}%</text>`; }
    for (let k = 0; k <= 4; k++) { const i = Math.round(k * (n - 1) / 4); g += `<text x="${xs(i).toFixed(1)}" y="${h - 7}" fill="${o.axis}" text-anchor="middle" font-size="9" font-family="inherit">+${Math.round(fan[i].d / 21)}M</text>`; }
    const band = (lo, hi, fill) => `<polygon points="${fan.map((f, i) => `${xs(i).toFixed(1)},${ys(f[hi]).toFixed(1)}`).join(" ")} ${fan.map((f, i) => `${xs(i).toFixed(1)},${ys(f[lo]).toFixed(1)}`).reverse().join(" ")}" fill="${fill}"/>`;
    const med = fan.map((f, i) => `${xs(i).toFixed(1)},${ys(f.p50).toFixed(1)}`).join(" ");
    const svg = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block">${g}${band("p5", "p95", o.band1)}${band("p25", "p75", o.band2)}<polyline points="${med}" fill="none" stroke="${o.color}" stroke-width="2"/><line class="cx" y1="${o.padT}" y2="${h - o.padB}" stroke="${o.color}" stroke-width="1" stroke-dasharray="2 3" style="display:none"/><rect x="${o.padL}" y="${o.padT}" width="${w - o.padL - o.padR}" height="${h - o.padT - o.padB}" fill="transparent"/></svg>`;
    container.innerHTML = svg; container.append(t);
    const sv = container.querySelector("svg");
    sv.addEventListener("mousemove", e => { const r = sv.getBoundingClientRect(); let i = Math.round(((e.clientX - r.left) / r.width * w - o.padL) / (w - o.padL - o.padR) * (n - 1)); i = Math.max(0, Math.min(n - 1, i)); const f = fan[i]; const cx = sv.querySelector(".cx"); cx.setAttribute("x1", xs(i)); cx.setAttribute("x2", xs(i)); cx.style.display = "block"; t.innerHTML = `<div style="opacity:.6;margin-bottom:3px">+${Math.round(f.d / 21)} months</div><div style="display:flex;justify-content:space-between;gap:14px"><span>P95</span><b class="pos">+${f.p95}%</b></div><div style="display:flex;justify-content:space-between;gap:14px"><span>Median</span><b>${f.p50 >= 0 ? "+" : ""}${f.p50}%</b></div><div style="display:flex;justify-content:space-between;gap:14px"><span>P5</span><b class="neg">${f.p5}%</b></div>`; t.style.display = "block"; t.style.left = Math.min(r.width - 60, Math.max(60, xs(i))) + "px"; t.style.top = (o.padT + 26) + "px"; });
    sv.addEventListener("mouseleave", () => { t.style.display = "none"; const cx = sv.querySelector(".cx"); if (cx) cx.style.display = "none"; });
  }

  // Donut. data:[{label,value,pct}], opts.colors[]
  function donut(container, data, opts = {}) {
    const o = Object.assign({ size: 130, thickness: 16, colors: ["#5ad1ff", "#b48cff", "#ffce6b", "#ff9b6b", "#6bf0a3", "#ff7a72", "#7fe0e8", "#c79bff"], center: "", centerSub: "", hole: "#0b0f13", ink: "#cdd8e2", sub: "#5ad1ff" }, opts);
    const r = (o.size - o.thickness) / 2, c = 2 * Math.PI * r, cx = o.size / 2;
    const tot = data.reduce((s, d) => s + d.value, 0) || 1; let acc = 0;
    const segs = data.map((d, i) => { const f = d.value / tot, dash = `${f * c} ${c}`, off = -acc * c; acc += f; return `<circle r="${r}" cx="${cx}" cy="${cx}" fill="none" stroke="${o.colors[i % o.colors.length]}" stroke-width="${o.thickness}" stroke-dasharray="${dash}" stroke-dashoffset="${off}" transform="rotate(-90 ${cx} ${cx})"/>`; }).join("");
    container.innerHTML = `<svg width="${o.size}" height="${o.size}" viewBox="0 0 ${o.size} ${o.size}" style="display:block">${segs}<circle r="${r - o.thickness / 2 - 2}" cx="${cx}" cy="${cx}" fill="${o.hole}"/><text x="${cx}" y="${cx - 2}" fill="${o.ink}" text-anchor="middle" font-size="9" font-family="inherit" letter-spacing="2">${o.center}</text><text x="${cx}" y="${cx + 14}" fill="${o.sub}" text-anchor="middle" font-size="13" font-weight="600" font-family="inherit">${o.centerSub}</text></svg>`;
  }

  // Horizontal bars. data:[{label,pct,value,color?}]
  function bars(container, data, opts = {}) {
    const o = Object.assign({ colors: ["#5ad1ff", "#b48cff", "#ffce6b", "#ff9b6b", "#6bf0a3", "#ff7a72"], track: "#11181f", ink: "#cdd8e2", sub: "#7f8d99", fmt: d => pctp(d.pct), showVal: true }, opts);
    const max = Math.max(...data.map(d => d.pct), 1);
    container.innerHTML = data.map((d, i) => `<div style="display:flex;align-items:center;gap:10px;padding:5px 0;font-size:11px"><div style="flex:0 0 88px;color:${o.ink};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.label}</div><div style="flex:1;height:7px;background:${o.track};border-radius:2px;position:relative;overflow:hidden"><div style="position:absolute;inset:0 auto 0 0;width:${(d.pct / max * 100).toFixed(1)}%;background:${d.color || o.colors[i % o.colors.length]};border-radius:2px"></div></div><div style="flex:0 0 52px;text-align:right;color:${o.sub};font-variant-numeric:tabular-nums">${o.fmt(d)}</div>${o.showVal ? `<div style="flex:0 0 64px;text-align:right;color:${o.sub};opacity:.7;font-variant-numeric:tabular-nums">${moneyC(d.value)}</div>` : ""}</div>`).join("");
  }

  // Inline sparkline -> svg string. arr: number[]
  function sparkline(arr, opts = {}) {
    const o = Object.assign({ w: 72, h: 20, up: "#6bf0a3", down: "#ff7a72", flat: "#7f8d99", fill: false }, opts);
    if (!arr || arr.length < 2) return `<svg width="${o.w}" height="${o.h}"></svg>`;
    const mn = Math.min(...arr), mx = Math.max(...arr), rng = (mx - mn) || 1;
    const pts = arr.map((v, i) => `${(i / (arr.length - 1) * o.w).toFixed(1)},${(o.h - (v - mn) / rng * (o.h - 2) - 1).toFixed(1)}`).join(" ");
    const col = arr[arr.length - 1] >= arr[0] ? o.up : o.down;
    const area = o.fill ? `<polygon points="0,${o.h} ${pts} ${o.w},${o.h}" fill="${col}" opacity="0.12"/>` : "";
    return `<svg width="${o.w}" height="${o.h}" viewBox="0 0 ${o.w} ${o.h}" style="display:inline-block;vertical-align:middle">${area}<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.3"/></svg>`;
  }

  // Heatmap. {rows,cols,matrix} matrix[r][c]. opts.scale(value)->color, opts.fmt
  function heatmap(container, payload, opts = {}) {
    const o = Object.assign({ cell: 22, gap: 1, neg: "#ff5d52", mid: "#0c1116", pos: "#39d98a", ink: "#9fb0bd", labelEvery: 1, fmt: v => v.toFixed(2), max: 1, diagDim: true, showLabels: true }, opts);
    const rows = payload.rows || [], cols = payload.cols || [], m = payload.matrix || [];
    if (!rows.length) { container.innerHTML = `<div style="color:${o.ink};font-size:12px;padding:20px">awaiting matrix…</div>`; return; }
    const t = tip(container);
    const cell = o.cell, lab = o.showLabels ? 54 : 4;
    const wsvg = lab + cols.length * (cell + o.gap), hsvg = lab + rows.length * (cell + o.gap);
    let s = `<svg width="${wsvg}" height="${hsvg}" viewBox="0 0 ${wsvg} ${hsvg}" style="display:block;max-width:none">`;
    if (o.showLabels) cols.forEach((c, j) => { s += `<text x="${lab + j * (cell + o.gap) + cell / 2}" y="${lab - 6}" fill="${o.ink}" font-size="8.5" text-anchor="start" font-family="inherit" transform="rotate(-55 ${lab + j * (cell + o.gap) + cell / 2} ${lab - 6})">${c}</text>`; });
    rows.forEach((rname, i) => {
      if (o.showLabels) s += `<text x="${lab - 6}" y="${lab + i * (cell + o.gap) + cell / 2 + 3}" fill="${o.ink}" font-size="8.5" text-anchor="end" font-family="inherit">${rname}</text>`;
      cols.forEach((cname, j) => {
        const v = (m[i] || [])[j]; if (v == null) return;
        const col = o.scale ? o.scale(v) : diverge(v / o.max, o.neg, o.mid, o.pos);
        const dim = (o.diagDim && rname === cname) ? 0.35 : 1;
        s += `<rect class="hc" data-i="${i}" data-j="${j}" x="${lab + j * (cell + o.gap)}" y="${lab + i * (cell + o.gap)}" width="${cell}" height="${cell}" fill="${col}" opacity="${dim}"/>`;
      });
    });
    s += `</svg>`; container.innerHTML = s; container.append(t);
    container.querySelectorAll(".hc").forEach(rc => {
      rc.addEventListener("mousemove", e => { const i = +rc.dataset.i, j = +rc.dataset.j; const cr = container.getBoundingClientRect(); t.innerHTML = `<b>${rows[i]} · ${cols[j]}</b><br>${o.fmt((m[i] || [])[j])}`; t.style.display = "block"; t.style.left = (e.clientX - cr.left) + "px"; t.style.top = (e.clientY - cr.top) + "px"; });
      rc.addEventListener("mouseleave", () => t.style.display = "none");
    });
  }

  // Treemap (slice-and-dice). items:[{label,value,sub?,color}]
  function treemap(container, items, opts = {}) {
    const o = Object.assign({ height: 260, ink: "#0b0f13", stroke: "#0b0f13", fmt: it => it.label, fmt2: null, pad: 2 }, opts);
    const w = W(container), h = o.height;
    const data = items.filter(d => d.value > 0).slice().sort((a, b) => b.value - a.value);
    const total = data.reduce((s, d) => s + d.value, 0) || 1;
    const t = tip(container);
    // squarified-ish: alternate split direction by aspect
    const rects = [];
    function layout(list, x, y, ww, hh) {
      if (!list.length) return;
      if (list.length === 1) { rects.push({ d: list[0], x, y, w: ww, h: hh }); return; }
      const sum = list.reduce((s, d) => s + d.value, 0);
      let half = 0, idx = 0; const target = sum / 2;
      for (let i = 0; i < list.length; i++) { if (half + list[i].value > target && i > 0) { idx = i; break; } half += list[i].value; idx = i + 1; }
      const a = list.slice(0, idx), b = list.slice(idx);
      const af = a.reduce((s, d) => s + d.value, 0) / sum;
      if (ww >= hh) { layout(a, x, y, ww * af, hh); layout(b, x + ww * af, y, ww * (1 - af), hh); }
      else { layout(a, x, y, ww, hh * af); layout(b, x, y + hh * af, ww, hh * (1 - af)); }
    }
    layout(data, 0, 0, w, h);
    let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block">`;
    rects.forEach((r, k) => {
      const pad = o.pad; const cw = Math.max(0, r.w - pad), ch = Math.max(0, r.h - pad);
      const col = r.d.color || "#5ad1ff";
      s += `<g class="tm" data-k="${k}"><rect x="${r.x + pad / 2}" y="${r.y + pad / 2}" width="${cw}" height="${ch}" fill="${col}" stroke="${o.stroke}" stroke-width="1" rx="2"/>`;
      if (cw > 46 && ch > 22) { s += `<text x="${r.x + 6}" y="${r.y + 15}" fill="${o.ink}" font-size="11" font-weight="700" font-family="inherit">${o.fmt(r.d)}</text>`; if (o.fmt2 && ch > 36) s += `<text x="${r.x + 6}" y="${r.y + 29}" fill="${o.ink}" font-size="9" opacity=".75" font-family="inherit">${o.fmt2(r.d)}</text>`; }
      s += `</g>`;
    });
    s += `</svg>`; container.innerHTML = s; container.append(t);
    container.querySelectorAll(".tm").forEach(g2 => { const r = rects[+g2.dataset.k]; g2.addEventListener("mousemove", e => { const cr = container.getBoundingClientRect(); t.innerHTML = `<b>${r.d.label}</b><br>${o.tip ? o.tip(r.d) : moneyC(r.d.value)}`; t.style.display = "block"; t.style.left = (e.clientX - cr.left) + "px"; t.style.top = (e.clientY - cr.top) + "px"; }); g2.addEventListener("mouseleave", () => t.style.display = "none"); });
  }

  // Histogram with normal overlay. {bins:[{x,count}], normal:[{x,y}]}
  function histogram(container, payload, opts = {}) {
    const o = Object.assign({ height: 180, bar: "#5ad1ff", line: "#ffce6b", grid: "#1b2730", axis: "#5b6b78", padL: 30, padR: 10, padT: 10, padB: 22 }, opts);
    const w = W(container), h = o.height; const bins = payload.bins || [], norm = payload.normal || [];
    if (!bins.length) { container.innerHTML = `<div style="height:${h}px;display:flex;align-items:center;justify-content:center;color:${o.axis};font-size:12px">no distribution</div>`; return; }
    const xmin = bins[0].x, xmax = bins[bins.length - 1].x, cmax = Math.max(...bins.map(b => b.count), ...norm.map(p => p.y || 0), 1);
    const xs = x => o.padL + (x - xmin) / ((xmax - xmin) || 1) * (w - o.padL - o.padR);
    const ys = c => o.padT + (1 - c / cmax) * (h - o.padT - o.padB);
    const bw = Math.max(1, (w - o.padL - o.padR) / bins.length - 1);
    let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block">`;
    s += `<line x1="${xs(0)}" y1="${o.padT}" x2="${xs(0)}" y2="${h - o.padB}" stroke="${o.axis}" stroke-dasharray="2 3" opacity=".7"/>`;
    bins.forEach(b => { const x = xs(b.x) - bw / 2, y = ys(b.count); s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(h - o.padB - y).toFixed(1)}" fill="${o.bar}" opacity=".75"/>`; });
    if (norm.length) s += `<polyline points="${norm.map(p => `${xs(p.x).toFixed(1)},${ys(p.y).toFixed(1)}`).join(" ")}" fill="none" stroke="${o.line}" stroke-width="2"/>`;
    s += `<text x="${xs(0)}" y="${h - 6}" fill="${o.axis}" font-size="9" text-anchor="middle" font-family="inherit">0%</text></svg>`;
    container.innerHTML = s;
  }

  // Scatter. points:[{x,y,r?,color?,label?}], opts axes labels
  function scatter(container, points, opts = {}) {
    const o = Object.assign({ height: 300, padL: 44, padR: 16, padT: 14, padB: 30, grid: "#1b2730", axis: "#5b6b78", xlab: "x", ylab: "y", fmtX: v => v.toFixed(0), fmtY: v => v.toFixed(0), color: "#5ad1ff", xmid: null, ymid: null }, opts);
    const w = W(container), h = o.height; container.innerHTML = ""; const t = tip(container);
    if (!points.length) { container.innerHTML = `<div style="height:${h}px"></div>`; return; }
    const xmin = Math.min(...points.map(p => p.x)), xmax = Math.max(...points.map(p => p.x));
    const ymin = Math.min(...points.map(p => p.y)), ymax = Math.max(...points.map(p => p.y));
    const px = (xmax - xmin) * 0.08 || 1, py = (ymax - ymin) * 0.08 || 1;
    const xs = v => o.padL + (v - (xmin - px)) / ((xmax + px) - (xmin - px)) * (w - o.padL - o.padR);
    const ys = v => o.padT + (1 - (v - (ymin - py)) / ((ymax + py) - (ymin - py))) * (h - o.padT - o.padB);
    let s = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block">`;
    for (let i = 0; i <= 4; i++) { const y = o.padT + i / 4 * (h - o.padT - o.padB); const v = (ymax + py) - i / 4 * ((ymax + py) - (ymin - py)); s += `<line x1="${o.padL}" y1="${y}" x2="${w - o.padR}" y2="${y}" stroke="${o.grid}"/><text x="${o.padL - 6}" y="${y + 3}" fill="${o.axis}" font-size="9" text-anchor="end" font-family="inherit">${o.fmtY(v)}</text>`; }
    if (o.xmid != null) s += `<line x1="${xs(o.xmid)}" y1="${o.padT}" x2="${xs(o.xmid)}" y2="${h - o.padB}" stroke="${o.axis}" stroke-dasharray="3 3" opacity=".6"/>`;
    if (o.ymid != null) s += `<line x1="${o.padL}" y1="${ys(o.ymid)}" x2="${w - o.padR}" y2="${ys(o.ymid)}" stroke="${o.axis}" stroke-dasharray="3 3" opacity=".6"/>`;
    points.forEach((p, k) => { s += `<circle class="sp" data-k="${k}" cx="${xs(p.x).toFixed(1)}" cy="${ys(p.y).toFixed(1)}" r="${p.r || 5}" fill="${p.color || o.color}" fill-opacity="0.7" stroke="${p.color || o.color}" stroke-width="1"/>`; });
    s += `<text x="${(o.padL + w - o.padR) / 2}" y="${h - 4}" fill="${o.axis}" font-size="9" text-anchor="middle" font-family="inherit">${o.xlab}</text></svg>`;
    container.innerHTML = s; container.append(t);
    container.querySelectorAll(".sp").forEach(c => { const p = points[+c.dataset.k]; c.addEventListener("mousemove", e => { const cr = container.getBoundingClientRect(); t.innerHTML = `<b>${p.label || ""}</b><br>${o.xlab} ${o.fmtX(p.x)} · ${o.ylab} ${o.fmtY(p.y)}`; t.style.display = "block"; t.style.left = (e.clientX - cr.left) + "px"; t.style.top = (e.clientY - cr.top) + "px"; }); c.addEventListener("mouseleave", () => t.style.display = "none"); });
  }

  // Semicircle gauge. value, max
  function gauge(container, value, max, opts = {}) {
    const o = Object.assign({ height: 80, track: "#1a242e", from: "#6bf0a3", to: "#ff7a72", ink: "#cdd8e2", label: "", fmt: v => v.toFixed(0) }, opts);
    const w = W(container), cx = w / 2, cy = o.height - 8, r = Math.min(cx - 10, o.height - 16);
    const frac = Math.max(0, Math.min(1, value / max));
    const a0 = Math.PI, a1 = Math.PI * (1 - frac);
    const arc = (a) => `${(cx + r * Math.cos(a)).toFixed(1)} ${(cy - r * Math.sin(a)).toFixed(1)}`;
    const big = frac > 0.5 ? 1 : 0;
    container.innerHTML = `<svg width="${w}" height="${o.height}" viewBox="0 0 ${w} ${o.height}" style="display:block"><path d="M ${arc(a0)} A ${r} ${r} 0 0 1 ${arc(0)}" fill="none" stroke="${o.track}" stroke-width="7"/><path d="M ${arc(a0)} A ${r} ${r} 0 ${big} 1 ${arc(a1)}" fill="none" stroke="${mix(o.from, o.to, frac)}" stroke-width="7" stroke-linecap="round"/><text x="${cx}" y="${cy - 6}" fill="${o.ink}" text-anchor="middle" font-size="18" font-weight="700" font-family="inherit">${o.fmt(value)}</text><text x="${cx}" y="${cy + 8}" fill="${o.ink}" opacity=".5" text-anchor="middle" font-size="8" letter-spacing="2" font-family="inherit">${o.label}</text></svg>`;
  }

  /* --------------------------------------------------- live updates ---- */
  // Smooth live refresh: ignore high-frequency / irrelevant slices, update the
  // lightweight chrome immediately, and only rebuild the heavy view when the
  // user is IDLE (no recent mouse/scroll, no focused input) and a minimum gap
  // has passed. This kills the "twitching" that came from re-rendering on every
  // sub-second crypto tick.
  function liveSubscribe(onChrome, onRender, opts = {}) {
    const rel = opts.slices || null;          // Set of slices worth re-rendering for
    const minGap = opts.minGap || 60000;      // at most one heavy render per minute
    const idleMs = opts.idleMs || 3000;       // wait for this much quiet first
    let last = Date.now(), lastMove = Date.now(), timer = null, dirty = false;
    const bump = () => { lastMove = Date.now(); };
    window.addEventListener("mousemove", bump, { passive: true });
    window.addEventListener("scroll", bump, { passive: true, capture: true });
    window.addEventListener("touchstart", bump, { passive: true });
    function tick() {
      timer = null;
      const tag = ((document.activeElement || {}).tagName || "");
      const idle = (Date.now() - lastMove) >= idleMs && !/INPUT|TEXTAREA|SELECT/.test(tag);
      const due = (Date.now() - last) >= minGap;
      if (dirty && idle && due) { dirty = false; last = Date.now(); try { onRender(); } catch (e) { console.error(e); } }
      if (dirty) timer = setTimeout(tick, 1500);   // keep watching until we get a quiet moment
    }
    return sub(slice => {
      if (slice === "__conn") { if (onChrome) onChrome(); return; }
      if (rel && slice !== "*" && !rel.has(slice)) return;   // ignore crypto/polymarket/system/...
      if (onChrome) onChrome();
      dirty = true;
      if (!timer) timer = setTimeout(tick, 1500);
    });
  }

  return {
    api, boot, sub, liveSubscribe, get, store, setCurrency, symbol,
    money, moneyC, pct, pctp, num, compact, sgn, dshort, el, mix, diverge,
    lineChart, fanChart, donut, bars, sparkline, heatmap, treemap, histogram, scatter, gauge,
  };
})();
window.JC = JC;
