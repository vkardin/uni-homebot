/* UNI — веб-панель (Telegram Mini App). Без сборки и зависимостей; всё содержимое вставляется как текст (textContent). */
"use strict";
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); try { tg.setHeaderColor("secondary_bg_color"); tg.setBackgroundColor("secondary_bg_color"); } catch (e) {} }

// ---------------------------------------------------------------- утилиты
// null/false/undefined никогда не должны превращаться в текст «null» на странице (так было у пустых блоков)
for (const m of ["append", "prepend", "replaceChildren"]) {
  const orig = Element.prototype[m];
  Element.prototype[m] = function (...nodes) { return orig.apply(this, nodes.flat(Infinity).filter((x) => x != null && x !== false)); };
}
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
const ICONS = {
  server: [["rect", { x: 3, y: 3, width: 18, height: 7, rx: 2 }], ["rect", { x: 3, y: 14, width: 18, height: 7, rx: 2 }], ["path", { d: "M7 6.5h.01M7 17.5h.01" }]],
  search: [["circle", { cx: 11, cy: 11, r: 7 }], ["path", { d: "M16.5 16.5L21 21" }]],
  film: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }], ["path", { d: "M8 4v16M16 4v16M3 9h5M3 15h5M16 9h5M16 15h5" }]],
  wallet: [["rect", { x: 3, y: 6, width: 18, height: 14, rx: 2 }], ["path", { d: "M3 10h18M16.5 14.5h.01" }]],
  news: [["rect", { x: 4, y: 4, width: 16, height: 16, rx: 2 }], ["path", { d: "M8 9h8M8 13h8M8 17h4" }]],
  more: [["circle", { cx: 5, cy: 12, r: 1.4 }], ["circle", { cx: 12, cy: 12, r: 1.4 }], ["circle", { cx: 19, cy: 12, r: 1.4 }]],
  notes: [["path", { d: "M6 3h9l3 3v15H6z" }], ["path", { d: "M9 12h6M9 16h6" }]],
  bell: [["path", { d: "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z" }], ["path", { d: "M10 21h4" }]],
  swap: [["path", { d: "M7 7h13l-3-3M17 17H4l3 3" }]],
  home: [["path", { d: "M4 11l8-7 8 7M6 10v10h12V10" }]],
  plus: [["path", { d: "M12 5v14M5 12h14" }]],
  link: [["path", { d: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" }]],
  spark: [["path", { d: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" }]],
  save: [["path", { d: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6" }]],
  gear: [["circle", { cx: 12, cy: 12, r: 3 }], ["path", { d: "M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6" }]],
  tools: [["path", { d: "M14.5 6.5a4 4 0 0 0-5 5L4 17l3 3 5.5-5.5a4 4 0 0 0 5-5l-2.5 2.5-2.5-.5-.5-2.5z" }]],
  chev: [["path", { d: "M9 6l6 6-6 6" }]],
  back: [["path", { d: "M15 6l-6 6 6 6" }]],
};
function icon(name) {
  const NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round", class: "ico" })) svg.setAttribute(k, v);
  for (const [tag, attrs] of ICONS[name] || []) { const el = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); svg.append(el); }
  return svg;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
const $ = (id) => document.getElementById(id);
const fmtBytes = (n) => { n = Number(n) || 0; const u = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i]; };
const fmtSpeed = (n) => fmtBytes(n) + "/с";
const fmtDur = (sec) => { sec = Math.floor(sec || 0); const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60); return d ? `${d} д ${hh} ч` : hh ? `${hh} ч ${m} мин` : `${m} мин`; };
const fmtAgo = (ts) => { if (!ts) return "ещё нет"; const d = Math.max(0, Date.now() / 1000 - ts); return d < 90 ? "только что" : d < 3600 ? `${Math.floor(d / 60)} мин назад` : d < 86400 ? `${Math.floor(d / 3600)} ч назад` : `${Math.floor(d / 86400)} дн назад`; };
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const num = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: 0 }));
const money = (v, cur) => (v == null ? "—" : num(v, Math.abs(v - Math.round(v)) < 0.005 ? 0 : 2) + " " + (cur || ""));
const cents = (c, cur) => money(c / 100, cur);
const SIGNS = { UAH: "₴", USD: "$", EUR: "€", RUB: "₽", KZT: "₸", PLN: "zł", GBP: "£" };
const fiatSign = (code) => SIGNS[String(code || "").toUpperCase()] || code || "";
// Плитки из /api/me: какие интеграции подключены. Пока не знаем (гость, старый сервер) — ничего не прячем.
let TILES = null;
const has = (id) => !TILES || TILES.some((t) => t.id === id && t.connected);
const pct = (v) => (v == null ? "—" : Math.round(v) + "%");
const sev = (v, w = 75, b = 90) => (v >= b ? "bad" : v >= w ? "warn" : "ok");

let toastTimer;
function toast(msg, err) { const t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 5000 : 2600); }
function haptic(kind) { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (e) {} }

async function api(method, path, body) {
  const init = tg ? tg.initData : "";
  let res;
  try {
    res = await fetch(path, { method, headers: { Authorization: "tma " + init, "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) { throw new Error("нет связи с сервером"); }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && data.error) || "ошибка " + res.status);
  return data;
}
const get = (p) => api("GET", p);
const post = (p, b) => api("POST", p, b || {});
const del = (p) => api("DELETE", p);
// действие по кнопке: показывает результат и блокирует кнопку на время запроса
function act(fn, ok) {
  return async (ev) => {
    const btn = ev.currentTarget; btn.disabled = true;
    try { const r = await fn(); haptic("success"); if (ok) toast(typeof ok === "function" ? ok(r) : ok); } catch (e) { haptic("error"); toast(e.message, true); } finally { btn.disabled = false; }
  };
}
function confirmAsk(text) {
  return new Promise((resolve) => { if (tg && tg.showConfirm) tg.showConfirm(text, resolve); else resolve(window.confirm(text)); });
}

// ---------------------------------------------------------------- листы и графики
function sheet(...content) { const c = $("sheet-card"); c.replaceChildren(...content.flat().filter((x) => x != null && x !== false)); $("sheet").hidden = false; }
function closeSheet() { $("sheet").hidden = true; }
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });

function tile(label, value, text, color) {
  const v = Math.max(0, Math.min(100, value || 0)), col = color || { ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)" }[sev(v)];
  return h("div", { class: "tile" }, h("div", { class: "l" }, label), h("div", { class: "v" }, text), h("div", { class: "bar" }, h("i", { style: `width:${v}%;background:${col}` })));
}

// series: [{name, color, points:[[x,y],...]}]; opts: {height, fmtY, fmtX, step, target, minY, maxY}
function lineChart(series, opts = {}) {
  const W = 640, H = opts.height || 190, L = 40, R = 10, T = 10, B = 22;
  const all = series.flatMap((x) => x.points);
  if (!all.length) return h("div", { class: "empty" }, "Данных пока нет");
  let x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0]));
  let y0 = opts.minY ?? Math.min(...all.map((p) => p[1])), y1 = opts.maxY ?? Math.max(...all.map((p) => p[1]));
  if (opts.target != null) { y0 = Math.min(y0, opts.target); y1 = Math.max(y1, opts.target); }
  if (x1 === x0) x1 = x0 + 1; if (y1 === y0) { y1 += 1; y0 -= 1; }
  const pad = (y1 - y0) * 0.08; if (opts.minY == null) y0 -= pad; if (opts.maxY == null) y1 += pad;
  const X = (x) => L + (x - x0) / (x1 - x0) * (W - L - R), Y = (y) => T + (1 - (y - y0) / (y1 - y0)) * (H - T - B);
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img" });
  const fy = opts.fmtY || ((v) => num(v, 0));
  for (let i = 0; i <= 3; i++) {
    const v = y0 + (y1 - y0) * i / 3, y = Y(v);
    svg.append(s("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: "var(--line)", "stroke-width": 1, opacity: .6 }), s("text", { x: L - 6, y: y + 3, "text-anchor": "end" }, fy(v)));
  }
  const fx = opts.fmtX || ((v) => new Date(v * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }));
  svg.append(s("text", { x: L, y: H - 6 }, fx(x0)), s("text", { x: W - R, y: H - 6, "text-anchor": "end" }, fx(x1)));
  if (opts.target != null) svg.append(s("line", { x1: L, x2: W - R, y1: Y(opts.target), y2: Y(opts.target), stroke: "var(--pink)", "stroke-dasharray": "5 4", "stroke-width": 1.5 }));
  for (const ser of series) {
    let d = "";
    ser.points.forEach((p, i) => {
      if (opts.step && i) d += `L${X(p[0]).toFixed(1)} ${Y(ser.points[i - 1][1]).toFixed(1)}`;
      d += (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1);
    });
    svg.append(s("path", { d, fill: "none", stroke: ser.color, "stroke-width": 2, "stroke-linejoin": "round" }));
    if (ser.points.length < 40) for (const p of ser.points) svg.append(s("circle", { cx: X(p[0]), cy: Y(p[1]), r: 2.6, fill: ser.color }));
  }
  const legend = series.length > 1 || series[0].name ? h("div", { class: "legend" }, series.map((x) => h("span", {}, h("i", { style: `background:${x.color}` }), x.name))) : null;
  return h("div", {}, svg, legend);
}
function spark(values, color) {
  if (values.length < 2) return h("span");
  const W = 120, H = 28, mn = Math.min(...values), mx = Math.max(...values) || 1, span = mx - mn || 1;
  const d = values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1) * W).toFixed(1)} ${(H - 2 - (v - mn) / span * (H - 4)).toFixed(1)}`).join("");
  return s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H }, s("path", { d, fill: "none", stroke: color || "var(--neon)", "stroke-width": 1.6 }));
}

const card = (title, ...body) => h("section", { class: "card" }, title ? h("h3", {}, title) : null, ...body);
const row = (main, sub, right) => h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { class: "t" }, main), sub ? h("div", { class: "s" }, sub) : null), right);
const loading = () => h("div", { class: "skel" }, h("i"), h("i"), h("i"));
const failure = (e) => card(null, h("div", { class: "bad" }, "⚠️ " + e.message), h("div", { class: "dim" }, "Если сервис не подключён — проверьте .env и /doctor в боте."));
async function section(title, loader, render) {
  const box = card(title, loading());
  loader().then((d) => { box.replaceChildren(title ? h("h3", {}, title) : null, ...[].concat(render(d))); }).catch((e) => { box.replaceChildren(title ? h("h3", {}, title) : null, h("div", { class: "bad" }, "⚠️ " + e.message)); });
  return box;
}

// ---------------------------------------------------------------- 🖥 СЕРВЕР
let timers = [];
function every(fn, ms) { timers.push(setInterval(fn, ms)); }
function clearTimers() { timers.forEach(clearInterval); timers = []; }

async function viewServer(root) {
  const top = h("div"), svcBox = h("div"), histBox = h("div");
  root.append(top, histBox, svcBox);
  async function refresh() {
    let o;
    try { o = await get("/api/overview"); } catch (e) { top.replaceChildren(failure(e)); return; }
    $("host").textContent = `${o.host} · v${o.version}`;
    const sy = o.system;
    if (sy) {
      const disk = (sy.disks || []).slice().sort((a, b) => b.percent - a.percent)[0];
      top.replaceChildren(card(null, h("div", { class: "tiles" },
        tile("CPU", sy.cpu_percent, pct(sy.cpu_percent)), tile("RAM", sy.ram_percent, pct(sy.ram_percent)),
        disk ? tile("Диск " + disk.mountpoint, disk.percent, pct(disk.percent)) : null,
        sy.temperature ? tile("Температура", sy.temperature.current, Math.round(sy.temperature.current) + "°", sev(sy.temperature.current, 70, 85) === "ok" ? "var(--ok)" : sev(sy.temperature.current, 70, 85) === "warn" ? "var(--warn)" : "var(--bad)") : null),
        h("div", { class: "row" }, h("div", { class: "grow dim" }, "Работает " + fmtDur(sy.uptime_sec) + (sy.load_avg ? " · нагрузка " + sy.load_avg.map((x) => x.toFixed(2)).join(" / ") : "") + ` · ${sy.cpu_threads} потоков`),
          o.spark.length > 2 ? spark(o.spark.map((p) => p.cpu)) : null)));
    }
    svcBox.replaceChildren(
      card("Службы", ...(o.services.error ? [h("div", { class: "bad" }, o.services.error)] : o.services.map((u) =>
        row([h("span", { class: "dot " + (u.active_state === "active" ? "ok" : u.active_state === "failed" ? "bad" : "warn") }), " ", u.name], u.error || `${u.active_state}${u.sub_state ? " · " + u.sub_state : ""}`,
          o.restartable.includes(u.name) ? h("button", { class: "btn sm", onclick: act(async () => { if (!(await confirmAsk("Перезапустить " + u.name + "?"))) return; const r = await post("/api/services/restart", { unit: u.name }); if (!r.ok) throw new Error(r.message); return r; }, "Перезапущено") }, "↻") : null)))),
      o.tasks.length ? card("Уведомления и фоновые задачи", ...o.tasks.map((t) => row(t.title, t.description, sw(t.running, async (on) => { const r = await post("/api/tasks/toggle", { name: t.name }); if (r.running !== on) throw new Error("Не переключилось"); })))) : null,
      o.power_enabled ? card("Питание", h("div", { class: "inline" },
        h("button", { class: "btn", onclick: act(async () => { if (await confirmAsk("Перезагрузить сервер?")) await post("/api/power", { action: "reboot", confirm: true }); }, "Команда отправлена") }, "🔄 Перезагрузка"),
        h("button", { class: "btn bad", onclick: act(async () => { if (await confirmAsk("ВЫКЛЮЧИТЬ сервер? Включить его удалённо будет нельзя.")) await post("/api/power", { action: "poweroff", confirm: true }); }, "Команда отправлена") }, "⏻ Выключить"))) : null);
  }
  await refresh(); every(() => { if (!svcBox.contains(document.activeElement)) refresh(); }, 6000);
  // история
  let period = "24h";
  async function hist() {
    histBox.replaceChildren(card("История", h("div", { class: "chips" }, ["6h", "24h", "7d"].map((p) => h("button", { class: "chip" + (p === period ? " on" : ""), onclick: () => { period = p; hist(); } }, p === "6h" ? "6 ч" : p === "24h" ? "24 ч" : "7 дн"))), loading()));
    try {
      const d = await get("/api/history?period=" + period);
      const pts = (k) => d.rows.filter((r) => r[k] != null).map((r) => [r.ts, r[k]]);
      const st = d.stats;
      histBox.replaceChildren(card("История",
        h("div", { class: "chips" }, ["6h", "24h", "7d"].map((p) => h("button", { class: "chip" + (p === period ? " on" : ""), onclick: () => { period = p; hist(); } }, p === "6h" ? "6 ч" : p === "24h" ? "24 ч" : "7 дн"))),
        lineChart([{ name: "CPU %", color: "var(--neon)", points: pts("cpu") }, { name: "RAM %", color: "var(--pink)", points: pts("ram") }], { minY: 0, maxY: 100, fmtY: (v) => Math.round(v) + "%" }),
        pts("temp").length ? lineChart([{ name: "Температура °C", color: "var(--warn)", points: pts("temp") }], { height: 140, fmtY: (v) => Math.round(v) + "°" }) : null,
        st ? h("div", { class: "dim" }, `CPU ср. ${pct(st.cpu_avg)} / макс ${pct(st.cpu_max)} · RAM ср. ${pct(st.ram_avg)} / макс ${pct(st.ram_max)}` + (st.temp_max != null ? ` · темп. макс ${Math.round(st.temp_max)}°` : "")) : null));
    } catch (e) { histBox.replaceChildren(failure(e)); }
  }
  hist();
  root.append(
    has("docker") ? await dockerCard() : null, await appsCards(),
    fold("Процессы", () => get("/api/top"), (list) => ({ sum: list.length ? list[0].name : "", body: list.map((p) => row(p.name, `PID ${p.pid}`, h("span", { class: "dim" }, fmtBytes(p.rss) + " · " + num(p.mem_percent, 1) + "%"))) })),
    await logsCard());
}

async function dockerCard() {
  const box = card("Docker", loading());
  const load = async () => {
    try {
      const list = await get("/api/docker");
      box.replaceChildren(h("h3", {}, "Docker", h("small", {}, list.length + " контейнеров")), ...list.map((c) =>
        row([h("span", { class: "dot " + (c.state === "running" && !c.problem ? "ok" : c.state === "running" ? "warn" : "bad") }), " ", c.name], c.problem || c.status,
          h("span", {}, h("button", { class: "btn sm", onclick: () => showLogs(c.name) }, "📜"), " ", h("button", { class: "btn sm", onclick: act(async () => { if (!(await confirmAsk("Перезапустить " + c.name + "?"))) return; const r = await post("/api/docker/restart", { name: c.name }); if (!r.ok) throw new Error(r.message); load(); }, "Перезапущен") }, "↻")))));
    } catch (e) { box.replaceChildren(h("h3", {}, "Docker"), h("div", { class: "dim" }, e.message)); }
  };
  async function showLogs(name) {
    sheet(h("h3", {}, name), loading());
    try { const r = await get("/api/docker/logs?name=" + encodeURIComponent(name)); sheet(h("h3", {}, "Логи " + name), h("pre", { class: "log" }, r.text || "пусто"), h("button", { class: "btn", onclick: closeSheet }, "Закрыть")); } catch (e) { sheet(failure(e), h("button", { class: "btn", onclick: closeSheet }, "Закрыть")); }
  }
  load(); return box;
}

async function logsCard() {
  return fold("Журналы служб", () => get("/api/logs"), (first) => {
    const sel = h("select", {}, first.units.map((u) => h("option", { value: u }, u)));
    const out = h("pre", { class: "log" }, "Выберите службу и нажмите «Показать»");
    return { sum: first.units.length + " служб", body: [h("div", { class: "inline" }, sel, h("button", { class: "btn", onclick: act(async () => { const r = await get(`/api/logs?unit=${encodeURIComponent(sel.value)}&lines=80`); out.textContent = r.error || r.text || "пусто"; out.scrollTop = out.scrollHeight; }) }, "Показать")), out] };
  });
}

async function appsCards() {
  const wrap = h("div", { class: "grid" });
  wrap.append(loading());
  get("/api/apps").then((a) => {
    wrap.replaceChildren();
    if (a.qbit) wrap.append(qbitCard(a.qbit));
    if (a.adguard) wrap.append(adguardCard(a.adguard));
    if (a.speedtest) wrap.append(speedCard(a.speedtest));
    if (a.jellyfin) wrap.append(jellyCard(a.jellyfin));
    if (a.tailscale) wrap.append(tailCard(a.tailscale));
  }).catch((e) => wrap.replaceChildren(failure(e)));
  return wrap;
}
const errCard = (title, d) => h("section", { class: "card note" }, h("span", { class: "dot bad" }), h("b", {}, title), h("span", { class: "dim" }, String(d.error || "").split(" (")[0]));
function qbitCard(d) {
  if (d.error) return errCard("qBittorrent", d);
  const magnet = h("input", { placeholder: "magnet:?xt=…" });
  return card("qBittorrent", h("div", { class: "inline" }, h("div", {}, h("div", { class: "big ok" }, "↓ " + fmtSpeed(d.transfer.dl_speed)), h("div", { class: "dim" }, "↑ " + fmtSpeed(d.transfer.up_speed))),
    h("span", { class: "right" }, h("button", { class: "btn sm", onclick: act(() => post("/api/qbit/toggle", { pause: true }), "Все на паузе") }, "⏸"), " ", h("button", { class: "btn sm", onclick: act(() => post("/api/qbit/toggle", { pause: false }), "Запущено") }, "▶"))),
    ...d.torrents.slice(0, 12).map((t) => h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { class: "t" }, t.name), h("div", { class: "bar" }, h("i", { style: `width:${Math.round(t.progress * 100)}%` })), h("div", { class: "s" }, `${Math.round(t.progress * 100)}% · ${t.state} · ${fmtBytes(t.size)}` + (t.dlspeed ? " · " + fmtSpeed(t.dlspeed) : "")))),
    ), h("div", { class: "inline", style: "margin-top:10px" }, magnet, h("button", { class: "btn", onclick: act(async () => { await post("/api/qbit/magnet", { magnet: magnet.value }); magnet.value = ""; }, "🧲 Добавлено") }, "Добавить")));
}
function adguardCard(d) {
  if (d.error) return errCard("AdGuard Home", d);
  return card("AdGuard Home", h("div", { class: "grid g2" }, h("div", {}, h("div", { class: "big" }, num(d.queries)), h("div", { class: "dim" }, "запросов")), h("div", {}, h("div", { class: "big pink" }, pct(d.queries ? d.blocked / d.queries * 100 : 0)), h("div", { class: "dim" }, "заблокировано"))),
    h("div", { class: "row" }, h("div", { class: "grow" }, "Защита " + (d.protection ? "включена" : "выключена")),
      h("button", { class: "btn sm" + (d.protection ? "" : " pink"), onclick: act(() => post("/api/adguard/protection", { enabled: !d.protection }), "Готово") }, d.protection ? "Выключить" : "Включить"),
      d.protection ? h("button", { class: "btn sm", onclick: act(() => post("/api/adguard/protection", { enabled: false, minutes: 15 }), "На 15 минут") }, "на 15 мин") : null),
    ...(d.top_blocked || []).slice(0, 5).map(([name, n]) => row(name, null, h("span", { class: "dim" }, n))));
}
function speedCard(d) {
  if (d.error) return errCard("Speedtest", d);
  return card("Speedtest", d ? h("div", { class: "grid g4" }, h("div", {}, h("div", { class: "big ok" }, num(d.download_mbps, 0)), h("div", { class: "dim" }, "↓ Мбит/с")), h("div", {}, h("div", { class: "big" }, num(d.upload_mbps, 0)), h("div", { class: "dim" }, "↑ Мбит/с")), h("div", {}, h("div", { class: "big" }, num(d.ping_ms, 0)), h("div", { class: "dim" }, "пинг мс"))) : h("div", { class: "empty" }, "Замеров нет"),
    h("button", { class: "btn", onclick: act(() => post("/api/speedtest/run"), "Замер запущен — результат через минуту") }, "▶ Новый замер"));
}
function jellyCard(d) {
  if (d.error) return errCard("Jellyfin", d);
  return card("Jellyfin", ...(d.now_playing.length ? d.now_playing.map((p) => row("▶ " + p.title, `${p.user} · ${p.device}`, h("span", { class: "dim" }, p.progress != null ? Math.round(p.progress) + "%" : ""))) : [h("div", { class: "dim" }, "Сейчас ничего не играет")]),
    h("div", { class: "dim", style: "margin-top:8px" }, "Недавно добавлено: " + d.recent.map((m) => m.title).join(" · ")));
}
function tailCard(d) {
  if (d.error) return errCard("Tailscale", d);
  return card("Tailscale", h("div", { class: "dim" }, `${d.nodes.filter((n) => n.online).length} из ${d.nodes.length} онлайн`), ...d.nodes.map((n) => row([h("span", { class: "dot " + (n.online ? "ok" : "bad") }), " ", n.name + (n.is_self ? " (этот сервер)" : "")], `${n.ip} · ${n.os}`)));
}

// ---------------------------------------------------------------- 🔎 САЙТЫ
async function viewSites(root) {
  const list = h("div", { class: "grid" });
  root.append(addSiteCard(() => load()), list);
  async function load() {
    list.replaceChildren(loading());
    try {
      const d = await get("/api/sites");
      if (!d.sites.length) { list.replaceChildren(card(null, h("div", { class: "empty" }, "Пока ничего не отслеживается. Добавьте ссылку на товар выше."))); return; }
      list.replaceChildren(...d.sites.map((x) => siteRow(x, d.currency)));
    } catch (e) { list.replaceChildren(failure(e)); }
  }
  function siteRow(x, cur) {
    const icon = x.paused ? "⏸" : x.error ? "⚠️" : x.is_price ? "💰" : "📄";
    const drop = x.last_price != null && x.price != null && x.price !== x.last_price;
    return h("div", { class: "card", style: "cursor:pointer", onclick: () => openSite(x.uuid, cur) },
      h("div", { class: "row", style: "border:0;padding:0" }, h("div", { class: "grow" }, h("div", { class: "t" }, icon + " " + x.name), h("div", { class: "s" }, new URL(x.url).hostname + " · " + (x.is_price ? "проверено " : "изменялось ") + fmtAgo(x.is_price ? x.last_checked : x.last_changed))),
        x.is_price && x.price != null ? h("div", { style: "text-align:right" }, h("div", { class: "big " + (drop ? (x.price < x.last_price ? "ok" : "bad") : "") }, money(x.price, cur)), drop ? h("div", { class: "s" }, "было " + money(x.last_price, cur)) : null, x.target ? h("div", { class: "s pink" }, "🎯 " + money(x.target, cur)) : null) : null),
      x.error ? h("div", { class: "bad s" }, x.error) : null);
  }
  async function openSite(uuid, cur) {
    sheet(loading());
    try {
      const d = await get("/api/sites/" + uuid), x = d.site, st = d.stats;
      const target = h("input", { type: "number", step: "any", placeholder: "Цена-цель, например 1100", value: x.target || "" });
      const act2 = (fn, ok) => act(async () => { await fn(); openSite(uuid, cur); load(); }, ok);
      sheet(h("h3", { style: "margin:0;color:var(--text)" }, x.name), h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer", class: "dim" }, new URL(x.url).hostname + " ↗"),
        x.is_price ? h("div", {}, h("div", { class: "big" }, money(x.price, cur)), st ? h("div", { class: "dim" }, `За всё время: ${money(st.lowest, cur)} … ${money(st.highest, cur)}`) : null, x.in_stock != null ? h("div", { class: x.in_stock ? "ok" : "bad" }, x.in_stock ? "В наличии" : "Нет в наличии") : null) : null,
        d.series.length ? lineChart([{ name: "", color: "var(--neon)", points: d.series }], { step: true, target: x.target, fmtY: (v) => num(v, 0) }) : null,
        x.error ? h("div", { class: "bad" }, "⚠️ " + x.error + (x.blocked ? (x.browser ? " — сайт не пускает даже браузер" : " — включите «Браузер» ниже") : "")) : null,
        h("div", { class: "dim" }, `Проверено ${fmtAgo(x.last_checked)} · изменялось ${fmtAgo(x.last_changed)}`),
        x.is_price ? h("div", { class: "inline" }, target, h("button", { class: "btn", onclick: act2(() => post(`/api/sites/${uuid}/target`, { price: target.value }), "Цель сохранена") }, "🎯")) : null,
        h("div", { class: "chips" },
          h("button", { class: "btn sm", onclick: act2(() => post(`/api/sites/${uuid}/check`), "Проверяю…") }, "⟳ Проверить"),
          h("button", { class: "btn sm", onclick: act2(() => post(`/api/sites/${uuid}/pause`, { paused: !x.paused }), "Готово") }, x.paused ? "▶ Возобновить" : "⏸ Пауза"),
          h("button", { class: "btn sm" + (x.browser ? " pink" : ""), onclick: act2(() => post(`/api/sites/${uuid}/browser`, { on: !x.browser }), "Переключено, проверяю заново") }, "🌐 Браузер: " + (x.browser ? "вкл" : "выкл"))),
        h("select", { onchange: (ev) => post(`/api/sites/${uuid}/interval`, { minutes: Number(ev.target.value) }).then(() => toast("Частота изменена")).catch((e) => toast(e.message, true)) },
          [[0, "Частота: как в changedetection"], [15, "Каждые 15 минут"], [60, "Каждый час"], [180, "Каждые 3 часа"], [720, "Каждые 12 часов"], [1440, "Раз в сутки"]].map(([v, t]) => h("option", { value: v, selected: v === x.interval_min }, t))),
        h("div", { class: "inline" }, h("button", { class: "btn bad", onclick: act(async () => { if (!(await confirmAsk("Удалить слежку?"))) return; await del("/api/sites/" + uuid); closeSheet(); load(); }, "Удалено") }, "🗑 Удалить"), h("button", { class: "btn", onclick: closeSheet }, "Закрыть")));
    } catch (e) { sheet(failure(e), h("button", { class: "btn", onclick: closeSheet }, "Закрыть")); }
  }
  await load();
}
function addSiteCard(done) {
  const urls = h("textarea", { placeholder: "Ссылка на товар или страницу (можно несколько, по одной в строке)", rows: 2 });
  let mode = "price"; const modes = h("div", { class: "seg" });
  const drawModes = () => modes.replaceChildren(...[["price", "Цена товара"], ["page", "Любые изменения"]].map(([k, t]) => h("button", { class: mode === k ? "on" : "", onclick: () => { mode = k; drawModes(); } }, t)));
  drawModes();
  const browser = h("input", { type: "checkbox" });
  return card("Новая слежка", urls, modes, h("label", { class: "opt" }, h("span", {}, "Открывать браузером", h("small", {}, "для сайтов с защитой от ботов")), browser),
    h("button", { class: "btn primary block", onclick: act(async () => { const r = await post("/api/sites", { urls: urls.value, mode, browser: browser.checked }); urls.value = ""; if (r.failed.length) toast("Не добавлено: " + r.failed.map((f) => f.error).join("; "), true); else toast("Добавлено: " + r.added.length); done(); }) }, "Добавить"));
}

// ---------------------------------------------------------------- 🎬 КИНО
// Серии одного сериала сворачиваем в одну строку: «Джентльмены · 8 сер.»
function groupRecent(items) {
  const kinds = { Episode: "Серия", Movie: "Фильм", Series: "Сериал", Season: "Сезон" }, out = [], seen = new Map();
  for (const m of items) {
    const g = /^(.*?)\s+S\d+E\d+/.exec(m.title || "");
    if (!g) { out.push({ ...m, kind: kinds[m.kind] || m.kind }); continue; }
    const hit = seen.get(g[1]);
    if (hit) { hit.n++; hit.title = `${g[1]} · ${hit.n} сер.`; continue; }
    const entry = { title: g[1], n: 1, kind: "Сериал", year: m.year }; seen.set(g[1], entry); out.push(entry);
  }
  return out;
}
async function viewMovies(root) {
  const q = h("input", { placeholder: "Название фильма или сериала", enterkeyhint: "search" });
  const results = h("div", { class: "grid" });
  const go = act(async () => {
    if (q.value.trim().length < 2) return;
    results.replaceChildren(loading());
    const d = await get("/api/movies/search?q=" + encodeURIComponent(q.value.trim()));
    const items = [...(Array.isArray(d.movies) ? d.movies.map((m) => ({ kind: "movie", id: m.tmdb_id, m, have: m.radarr_id })) : []), ...(Array.isArray(d.shows) ? d.shows.map((m) => ({ kind: "show", id: m.tvdb_id, m, have: m.sonarr_id })) : [])];
    results.replaceChildren(items.length ? card("Найдено", ...items.slice(0, 14).map((it) => row((it.kind === "show" ? "📺 " : "🎬 ") + it.m.title + (it.m.year ? ` (${it.m.year})` : ""), (it.m.overview || "").slice(0, 110), it.have ? h("span", { class: "badge" }, "уже есть") : h("button", { class: "btn sm", onclick: act(async () => { const r = await post("/api/movies/add", { kind: it.kind, id: it.id }); load(); return r; }, (r) => "Добавлено: " + r.title) }, "＋")))) : card(null, h("div", { class: "empty" }, "Ничего не нашлось")));
  });
  root.append(card("Поиск", h("div", { class: "inline" }, q, h("button", { class: "btn", onclick: go }, "Найти"))), results);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") q.nextSibling.click(); });
  const box = h("div", { class: "grid" }); root.append(box);
  async function load() {
    try {
      const d = await get("/api/movies"); box.replaceChildren();
      const queueCard = (title, data) => {
        if (data == null) return;
        if (data.error) { box.append(errCard(title, data)); return; }
        const rows = data.map((x) => h("div", { class: "row" },
          h("div", { class: "grow" },
            h("div", { class: "t" }, x.title || `Сериал: ${x.episodes} серий в очереди`),
            h("div", { class: "bar" }, h("i", { style: `width:${Math.round(x.progress)}%` })),
            h("div", { class: "s" }, `${Math.round(x.progress)}%` + (x.timeleft ? " · " + x.timeleft : "") + (x.problem ? " · ⚠️ " + x.problem : "")))));
        box.append(card(title, ...(rows.length ? rows : [h("div", { class: "dim" }, "Очередь пуста")])));
      };
      queueCard("Radarr: загрузки", d.movie_queue); queueCard("Sonarr: загрузки", d.show_queue);
      if (d.recent && !d.recent.error) box.append(card("Недавно добавлено", ...groupRecent(d.recent).map((m) => row(m.title, [m.kind, m.year].filter(Boolean).join(" · ")))));
      if (!d.radarr && !d.sonarr) box.append(card(null, h("div", { class: "empty" }, "Radarr/Sonarr не подключены")));
    } catch (e) { box.replaceChildren(failure(e)); }
  }
  load();
}

// ---------------------------------------------------------------- 💰 ДЕНЬГИ
function p2pCard(d) {
  const q = d.quote, st = d.stats, cur = fiatSign(d.fiat);
  return card("P2P · продать " + (d.asset || "USDT"), h("div", { class: "grid g2" }, h("div", {}, h("div", { class: "big pink" }, num(q.best, 2) + " " + cur), h("div", { class: "dim" }, "лучшая цена")), h("div", {}, h("div", { class: "big" }, num(q.typical, 2) + " " + cur), h("div", { class: "dim" }, "типичная"))),
    st ? h("div", { class: "dim" }, `24 ч: ${num(st.low, 2)} … ${num(st.high, 2)} (${st.points} точек)`) : null,
    d.points.length > 1 ? lineChart([{ name: "лучшая", color: "var(--pink)", points: d.points.map((p) => [p[0], p[1]]) }, { name: "типичная", color: "var(--neon)", points: d.points.map((p) => [p[0], p[2]]) }], { fmtY: (v) => num(v, 1) }) : null,
    ...q.ads.slice(0, 5).map((a) => row(`${num(a.price, 2)} ${cur} · ${a.merchant || "—"}`, `${a.methods.slice(0, 2).join(", ")}${a.methods.length > 2 ? " +" + (a.methods.length - 2) : ""}${a.finish_rate != null ? " · " + Math.round(a.finish_rate * 100) + "% сделок" : ""}`)));
}
async function viewMoney(root) {
  const text = h("input", { placeholder: "кофе 85  ·  такси 120  ·  +5000 зарплата", enterkeyhint: "done" });
  const box = h("div", { class: "grid" });
  const add = act(async () => { const r = await post("/api/finance/expense", { text: text.value }); text.value = ""; load(); return r; }, (r) => `Записано: ${cents(Math.abs(r.cents))}` + (r.category ? " · " + r.category : ""));
  if (has("finance")) root.append(card("Быстрая запись", h("div", { class: "inline" }, text, h("button", { class: "btn", onclick: add }, "Записать"))));
  root.append(box);
  text.addEventListener("keydown", (e) => { if (e.key === "Enter") text.nextSibling.click(); });
  async function load() {
    box.replaceChildren(loading());
    const off = Promise.resolve(null);
    const [fin, p2p] = await Promise.allSettled([has("finance") ? get("/api/finance") : off, has("p2p") ? get("/api/p2p") : off]);
    box.replaceChildren();
    if (fin.status === "fulfilled" && fin.value) {
      const d = fin.value, m = d.month, cur = d.currency, max = Math.max(1, ...m.by_category.map((c) => c[1]));
      box.append(card("Этот месяц", h("div", { class: "grid g2" }, h("div", {}, h("div", { class: "big bad" }, cents(m.spent, cur)), h("div", { class: "dim" }, "потрачено")), h("div", {}, h("div", { class: "big ok" }, cents(m.earned, cur)), h("div", { class: "dim" }, "доходы"))),
        ...m.by_category.slice(0, 8).map(([n, v]) => h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { class: "t" }, n), h("div", { class: "bar" }, h("i", { style: `width:${Math.round(v / max * 100)}%` }))), h("span", { class: "dim" }, cents(v, cur))))),
        card("Счета", ...d.accounts.map((a) => row(a.name, a.offbudget ? "вне бюджета" : "", h("b", {}, a.balance != null ? cents(a.balance, cur) : "—")))),
        card("Последние операции", ...(d.recent.length ? d.recent.map((t) => row(t.payee || t.notes || "—", [t.date, t.category].filter(Boolean).join(" · "), h("b", { class: t.cents < 0 ? "" : "ok" }, cents(t.cents, cur)))) : [h("div", { class: "empty" }, "Операций нет")])));
    } else if (fin.status === "rejected") box.append(errCard("Actual Budget", { error: fin.reason.message }));
    if (p2p.status === "fulfilled" && p2p.value) {
      box.append(p2pCard(p2p.value));
    } else if (p2p.status === "rejected") box.append(errCard("P2P", { error: p2p.reason.message }));
  }
  load();
}

// ---------------------------------------------------------------- 🗞 НОВОСТИ
async function viewNews(root) {
  if (!IS_GUEST) root.append(group(null, li("Категории и источники", "Настроить, что приходит в новости", null, () => show("more/settings"))));
  root.append(await section(null, () => get("/api/news"), (items) => items.length ? items.map((n) => h("article", { class: "news" },
    h("div", { class: "meta" }, `${n.feed} · ${fmtDate(n.sent_ts)}`),
    h("div", { class: "head" }, /^https?:\/\//.test(n.link || "") ? h("a", { href: n.link, onclick: (e) => { e.preventDefault(); reader(n.link, n.title); } }, n.title) : n.title),
    n.text ? h("div", { class: "sum" }, n.text) : null)) : [h("div", { class: "empty" }, IS_GUEST ? "Пока ничего не присылал" : "Пока ничего не присылал. Выберите подборки или добавьте свои ленты в «Категории и источники» выше, и новости начнут приходить по расписанию.")]));
}

// 📖 Режим чтения: статья открывается внутри панели (текстом), оригинал — во встроенном браузере Telegram
function reader(url, title) {
  const host = (() => { try { return new URL(url).hostname; } catch (e) { return ""; } })();
  const body = h("div", { class: "reader" }, loading());
  sheet(h("h3", { style: "margin:0;color:var(--text)" }, title || host), h("div", { class: "dim" }, host), body,
    h("button", { class: "btn", onclick: () => openLink(url) }, "Открыть оригинал ↗"));
  get("/api/read?url=" + encodeURIComponent(url))
    .then((d) => body.replaceChildren(...d.paragraphs.map((p) => h("p", {}, p))))
    .catch((e) => body.replaceChildren(h("div", { class: "bad" }, "Не получилось показать текст: " + e.message), h("div", { class: "dim" }, "Откройте оригинал кнопкой ниже.")));
}

// ---------------------------------------------------------------- ✨ ЕЩЁ: чат, напоминания, заметки, бэкап
// Сворачиваемая карточка: в закрытом виде — одна строка с краткой сводкой
function fold(title, loader, render) {
  const box = h("details", { class: "card fold" }, h("summary", {}, title, h("small", {}, "…")), loading());
  loader().then((d) => { const r = render(d); box.replaceChildren(h("summary", {}, title, h("small", {}, r.sum || "")), ...r.body); })
    .catch((e) => { box.replaceChildren(h("summary", {}, title, h("small", { class: "bad" }, "ошибка")), h("div", { class: "bad" }, e.message)); });
  return box;
}
function chatCard() {
  const history = [], log = h("div", { class: "grid" }), input = h("input", { placeholder: "Спросить локальную модель…", enterkeyhint: "send" });
  const send = act(async () => {
    const t = input.value.trim(); if (!t) return; input.value = "";
    history.push({ role: "user", content: t }); log.append(h("div", { class: "bubble me" }, t));
    const wait = h("div", { class: "bubble bot dim" }, "думаю… (на процессоре это может занять минуту)"); log.append(wait);
    try { const r = await post("/api/chat", { messages: history }); history.push({ role: "assistant", content: r.reply }); wait.className = "bubble bot"; wait.textContent = r.reply; } catch (e) { wait.className = "bubble bot bad"; wait.textContent = "⚠️ " + e.message; history.pop(); }
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.nextSibling.click(); });
  return card("Спросить ИИ", log, h("div", { class: "inline" }, input, h("button", { class: "btn", onclick: send, "aria-label": "Отправить" }, "→")));
}
async function remindersCard() {
  const box = card("Напоминания", loading()), text = h("input", { placeholder: "завтра 9:00 оплатить · каждый пн 9:00 планёрка" });
  async function load() {
    try {
      const items = await get("/api/reminders");
      box.replaceChildren(h("h3", {}, "Напоминания", items.length ? null : h("small", {}, "пока нет")), ...(items.length ? items.map((r) => row(r.text, fmtDate(r.due_ts) + (r.rule_text ? " · 🔁 " + r.rule_text : ""), h("button", { class: "btn sm bad", onclick: act(async () => { await del("/api/reminders/" + r.id); load(); }) }, "✕"))) : []),
        h("div", { class: "inline", style: "margin-top:10px" }, text, h("button", { class: "btn", onclick: act(async () => { const r = await post("/api/reminders", { text: text.value }); text.value = ""; load(); return r; }, (r) => "Напомню " + fmtDate(r.due)) }, "＋")));
    } catch (e) { box.replaceChildren(h("h3", {}, "Напоминания"), h("div", { class: "bad" }, e.message)); }
  }
  await load(); return box;
}
async function notesCard() {
  const box = card("Заметки", loading()), text = h("textarea", { placeholder: "Новая заметка" });
  async function load() {
    try {
      const items = await get("/api/notes");
      box.replaceChildren(h("h3", {}, "Заметки", items.length ? null : h("small", {}, "пока нет")), ...(items.length ? items.map((n) => row(n.text, fmtDate(n.ts), h("button", { class: "btn sm bad", onclick: act(async () => { await del("/api/notes/" + n.id); load(); }) }, "✕"))) : []),
        text, h("button", { class: "btn", style: "margin-top:8px", onclick: act(async () => { await post("/api/notes", { text: text.value }); text.value = ""; load(); }, "Сохранено") }, "＋ Сохранить"));
    } catch (e) { box.replaceChildren(h("h3", {}, "Заметки"), h("div", { class: "bad" }, e.message)); }
  }
  await load(); return box;
}

// ---------------------------------------------------------------- 🏠 ГЛАВНАЯ
const greet = () => { const hr = new Date().getHours(); return hr < 5 ? "Доброй ночи" : hr < 12 ? "Доброе утро" : hr < 18 ? "Добрый день" : "Добрый вечер"; };
const plural = (n, a, b, c) => { const m = n % 100, k = n % 10; return n + " " + (m > 10 && m < 20 ? c : k === 1 ? a : k > 1 && k < 5 ? b : c); };
const group = (title, ...rows) => h("section", { class: "grp" }, title ? h("h4", {}, title) : null, h("div", { class: "list" }, ...rows));
const li = (main, sub, right, go) => h(go ? "button" : "div", { class: "li" + (go ? " tap" : ""), onclick: go || null }, h("div", { class: "grow" }, h("div", { class: "t" }, main), sub ? h("div", { class: "s" }, sub) : null), right || null, go ? icon("chev") : null);

function quick(kind) {
  const cfg = {
    spend: { title: "Новая трата", ph: "кофе 85", hint: "Сумма и подпись. Доход — со знаком плюс: +5000 зарплата.", run: (v) => post("/api/finance/expense", { text: v }), ok: (r) => `Записано: ${cents(Math.abs(r.cents))}` + (r.category ? " · " + r.category : "") },
    note: { title: "Новая заметка", ph: "Что запомнить?", multi: true, run: (v) => post("/api/notes", { text: v }), ok: "Заметка сохранена" },
    remind: { title: "Новое напоминание", ph: "завтра 9:00 оплатить", hint: "Можно повторять: «каждый пн 9:00 планёрка».", run: (v) => post("/api/reminders", { text: v }), ok: (r) => "Напомню " + fmtDate(r.due) },
    link: { title: "Следить за ценой", ph: "Ссылка на товар", run: async (v) => { const r = await post("/api/sites", { urls: v, mode: "price", browser: false }); if (r.failed.length) throw new Error(r.failed.map((f) => f.error).join("; ")); return r; }, ok: "Слежка добавлена" },
  }[kind];
  const input = cfg.multi ? h("textarea", { placeholder: cfg.ph, rows: 3 }) : h("input", { placeholder: cfg.ph, enterkeyhint: "done" });
  const go = act(async () => { const v = input.value.trim(); if (!v) return; const r = await cfg.run(v); closeSheet(); if (current === "home") show("home"); return r; }, cfg.ok);
  const btn = h("button", { class: "btn primary block", onclick: go }, "Сохранить");
  if (!cfg.multi) input.addEventListener("keydown", (e) => { if (e.key === "Enter") btn.click(); });
  sheet(h("div", { class: "sheet-title" }, cfg.title), input, cfg.hint ? h("div", { class: "dim small" }, cfg.hint) : null, btn);
  setTimeout(() => input.focus(), 60);
}

// 🚀 доска приложений: веб-интерфейсы подключённого и свои ссылки (долгое нажатие на ссылку — удалить)
async function launcherBoard() {
  const box = h("section", { class: "board" });
  const draw = (st) => {
    const app = (x, extra) => h("button", { class: "app", onclick: () => openLink(x.url), ...extra }, h("span", { class: "ai" }, x.icon), h("span", { class: "at" }, x.title));
    const link = (x) => { const b = app(x, { title: x.url }); let t; b.addEventListener("pointerdown", () => { t = setTimeout(async () => { if (await confirmAsk(`Убрать «${x.title}»?`)) draw(await post("/api/launcher", { op: "remove", url: x.url })); }, 650); });
      ["pointerup", "pointerleave"].forEach((e) => b.addEventListener(e, () => clearTimeout(t))); return b; };
    const add = h("button", { class: "app add", onclick: () => addLink(draw) }, h("span", { class: "ai" }, "＋"), h("span", { class: "at" }, "Ссылка"));
    box.replaceChildren(h("h4", {}, "Приложения"), h("div", { class: "apps" }, ...st.apps.map((x) => app(x)), ...st.links.map(link), add));
  };
  draw(await get("/api/launcher").catch(() => ({ apps: [], links: [] })));
  return box;
}
function addLink(draw) {
  const title = h("input", { placeholder: "Название", maxlength: "32" }), url = h("input", { placeholder: "https://…", inputmode: "url" }), ic = h("input", { placeholder: "Значок, например 📷", maxlength: "4" });
  sheet(h("div", { class: "sheet-title" }, "Новая ссылка"), title, url, ic,
    h("button", { class: "btn primary block", onclick: act(async () => { draw(await post("/api/launcher", { op: "add", title: title.value, url: url.value, icon: ic.value })); $("sheet").hidden = true; }, "Добавлено") }, "Добавить"));
}

// 🧭 начало работы: что ещё не настроено; пропадает, когда всё готово или скрыто
async function setupCard() {
  const st = await get("/api/setup").catch(() => null);
  if (!st || st.hidden || st.done === st.total) return null;
  const box = h("div");
  const open = (x) => (x.panel ? show(x.panel) : sheet(h("div", { class: "sheet-title" }, x.title), h("div", { class: "dim" }, x.hint), h("button", { class: "btn block", onclick: closeSheet }, "Понятно")));
  const rows = st.steps.map((x) => li((x.done ? "✅ " : "▫️ ") + x.title, x.done ? null : x.hint, null, x.done ? null : () => open(x)));
  const hide = h("button", { class: "btn sm", style: "margin-top:8px", onclick: act(async () => { await post("/api/setup", { hidden: true }); box.remove(); }, "Скрыто. Вернуть: /setup в чате") }, "Скрыть");
  box.append(group(`Начало работы: ${st.done} из ${st.total}`, ...rows), hide);
  return box;
}

async function viewHome(root) {
  const d = await get("/api/home");
  const sv = d.server && !d.server.error ? d.server : null;
  const problems = [];
  if (!sv) problems.push("Нет данных о сервере");
  else {
    if (sv.failed.length) problems.push(sv.failed.length === 1 ? `Служба ${sv.failed[0]} не работает` : `Не работают службы: ${sv.failed.length}`);
    if (sv.temp != null && sv.temp >= 80) problems.push(`Жарко: ${Math.round(sv.temp)}°`);
    if (sv.disk != null && sv.disk >= 90) problems.push(`Диск заполнен на ${Math.round(sv.disk)}%`);
    if (sv.cpu != null && sv.cpu >= 90) problems.push(`Процессор занят на ${Math.round(sv.cpu)}%`);
  }
  const bits = [];
  if (d.reminders.length) bits.push(`Ближайшее напоминание: ${d.reminders[0].text}.`);
  root.append(h("header", { class: "hero" },
    h("div", { class: "when" }, greet() + ", " + new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })),
    h("h1", { class: problems.length ? "warn" : "" }, problems.length ? problems[0] : "Всё в порядке"),
    bits.length ? h("p", {}, bits.join(" ")) : null));
  if (sv) root.append(h("button", { class: "card tiles mini", onclick: () => show("server"), "aria-label": "Открыть «Сервер»" },
    tile("CPU", sv.cpu, pct(sv.cpu)), tile("Память", sv.ram, pct(sv.ram)), tile("Диск", sv.disk, pct(sv.disk)),
    sv.temp != null ? tile("Темп.", sv.temp, Math.round(sv.temp) + "°", `var(--${sev(sv.temp, 70, 85)})`) : tile("Службы", sv.failed.length ? 100 : 0, sv.failed.length ? "сбой " + sv.failed.length : "ок")));
  const guide = await setupCard();
  if (guide) root.append(guide);
  root.append(await launcherBoard());
  const pills = [has("finance") ? ["spend", "wallet", "Трата"] : null, ["note", "notes", "Заметка"], ["remind", "bell", "Напоминание"], has("sites") ? ["link", "link", "Слежка"] : null].filter(Boolean);
  root.append(h("div", { class: "quick" }, ...pills.map(([k, ic, t]) => h("button", { class: "pill", onclick: () => quick(k) }, icon(ic), t))));
  const r = d.rate && !d.rate.error ? d.rate : null;
  if (r) root.append(group(`${r.asset} → ${r.fiat}`, h("button", { class: "li tap rate", onclick: () => show("money") },
    h("div", { class: "grow" }, h("div", { class: "big" }, num(r.best, 2) + " " + fiatSign(r.fiat)), h("div", { class: "s" }, "типичная " + num(r.typical, 2) + " " + fiatSign(r.fiat))),
    r.points.length > 2 ? spark(r.points, "var(--neon)") : null, icon("chev"))));
  root.append(group("Скоро", ...(d.reminders.length ? d.reminders.map((x) => li(x.text, fmtDate(x.due_ts) + (x.rule_text ? ", " + x.rule_text : ""), null, () => show("more/reminders"))) : [li("Напоминаний нет", "Добавьте первое кнопкой выше", null, () => quick("remind"))])));
  if (d.news.length) root.append(group("Новости", ...d.news.map((n) => li(n.title, `${n.feed}, ${fmtDate(n.sent_ts)}`, null, () => show("news"))), li("Все новости", null, null, () => show("news"))));
  const m = d.money && !d.money.error ? d.money : null;
  if (m) root.append(group("Деньги", li("Потрачено в этом месяце", null, h("b", {}, cents(m.spent, m.currency)), () => show("money"))));
  const st = d.sites && !d.sites.error ? d.sites : null;
  if (st && st.total) root.append(group("Слежка за ценами", ...(st.drops.length ? st.drops.map((x) => li(x.name, "было " + money(x.was, st.currency), h("b", { class: "ok" }, money(x.price, st.currency)), () => show("more/sites"))) : [li(`Слежек: ${st.total}`, st.problems ? plural(st.problems, "сайт с ошибкой", "сайта с ошибкой", "сайтов с ошибкой") : "Цены пока не менялись", null, () => show("more/sites"))])));
  const dl = d.downloads && !d.downloads.error ? d.downloads : null;
  if (dl && dl.movies + dl.shows > 0) root.append(group("Загрузки", li([dl.movies ? plural(dl.movies, "фильм", "фильма", "фильмов") : null, dl.shows ? plural(dl.shows, "сериал", "сериала", "сериалов") : null].filter(Boolean).join(" и ") + " в очереди", null, null, () => show("more/movies"))));
}


// ---------------------------------------------------------------- ⚙️ НАСТРОЙКИ
const openLink = (u) => { if (!/^https?:\/\//.test(u || "")) return; try { if (tg && tg.openLink) tg.openLink(u, { try_instant_view: true }); else window.open(u, "_blank", "noopener"); } catch (e) { window.open(u, "_blank", "noopener"); } };
function sw(on, fn) {
  const b = h("button", { class: "sw" + (on ? " on" : ""), role: "switch", "aria-checked": String(!!on) });
  b.addEventListener("click", async () => {
    const next = !b.classList.contains("on"); b.disabled = true; b.classList.toggle("on", next); b.setAttribute("aria-checked", String(next));
    try { await fn(next); haptic("success"); } catch (e) { b.classList.toggle("on", !next); b.setAttribute("aria-checked", String(!next)); haptic("error"); toast(e.message, true); } finally { b.disabled = false; }
  });
  return b;
}
function seg(opts, value, fn) {
  const box = h("div", { class: "seg" });
  for (const [v, label] of opts) {
    const b = h("button", { class: v === value ? "on" : "" }, label);
    b.addEventListener("click", async () => {
      if (b.classList.contains("on")) return;
      const was = [...box.children].find((x) => x.classList.contains("on"));
      [...box.children].forEach((x) => x.classList.remove("on")); b.classList.add("on");
      try { await fn(v); haptic("success"); } catch (e) { b.classList.remove("on"); if (was) was.classList.add("on"); haptic("error"); toast(e.message, true); }
    });
    box.append(b);
  }
  return box;
}
const field = (label, control, hint) => h("div", { class: "field" }, h("div", { class: "fl" }, label), control, hint ? h("div", { class: "dim small" }, hint) : null);
const setRow = (title, sub, control) => h("div", { class: "li" }, h("div", { class: "grow" }, h("div", { class: "t" }, title), sub ? h("div", { class: "s" }, sub) : null), control);

// Категории и источники новостей: один редактор для хозяина и для гостей (отличается только адрес API)
function sourcesEditor(path, st0, hint) {
  const box = h("div", { class: "grid" }), input = h("input", { placeholder: "https://сайт/rss", inputmode: "url", autocapitalize: "off", autocomplete: "off", enterkeyhint: "done" });
  let st = st0;
  const apply = (body, ok) => act(async () => { const r = await post(path, body); st = r; if (body.op === "add") input.value = ""; draw(); return r; }, ok);
  const failedNote = (r) => (r.result && r.result.failed && r.result.failed.length ? "Не открылись: " + r.result.failed.join(", ") : "Готово");
  function draw() {
    const add = h("button", { class: "btn", onclick: apply({ op: "add", get url() { return input.value.trim(); } }, (r) => "Добавлено" + (r.result && r.result.name ? ": " + r.result.name : "")) }, "Добавить");
    input.onkeydown = (e) => { if (e.key === "Enter") add.click(); };
    box.replaceChildren(
      card("Категории", h("div", { class: "chips" }, st.presets.map((p) => h("button", { class: "chip" + (p.active ? " on" : ""), "aria-pressed": String(p.active), onclick: apply({ op: "preset", key: p.key, on: !p.active }, failedNote) }, p.label))),
        h("div", { class: "dim small" }, hint || "Нажмите на категорию, чтобы включить или выключить её набор источников.")),
      card(`Мои источники · ${st.feeds.length} из ${st.max}`,
        ...(st.feeds.length ? st.feeds.map((f) => row(f.name, f.url.replace(/^https?:\/\//, ""), h("button", { class: "btn sm bad", "aria-label": "Убрать " + f.name, onclick: apply({ op: "remove", url: f.url }, "Убрано") }, "✕"))) : [h("div", { class: "empty" }, "Пока пусто — выберите категорию или добавьте ленту")]),
        h("div", { class: "inline", style: "margin-top:12px" }, input, add),
        h("div", { class: "dim small" }, "Подойдёт ссылка на RSS или Atom ленту сайта.")));
  }
  draw();
  return box;
}

async function viewSettings(root) {
  const st = await get("/api/settings/news"), box = h("div", { class: "grid" });
  let cur = st;
  const save = (changes) => async (v) => { const r = await post("/api/settings/news", { op: "set", ...(typeof changes === "string" ? { [changes]: v } : changes(v)) }); cur = r; return r; };
  const lbl = (m) => (m < 60 ? m + "м" : m / 60 + "ч");
  const p = st.prefs;
  box.append(
    card("Новости в чате",
      h("div", { class: "list inner" },
        setRow("Присылать новости", "Бот сам пишет в чат по расписанию", sw(p.enabled, save("enabled"))),
        setRow("Тихие часы", `Ночью не беспокоить (${p.quiet_from}:00–${p.quiet_to}:00)`, sw(p.quiet, save("quiet"))),
        setRow("Пересказ моделью", "Короткое изложение вместо заголовка", sw(p.retell, save("retell")))),
      field("Как часто", seg(st.choices.interval.map((m) => [m, lbl(m)]), p.interval_min, save("interval_min"))),
      field("Сколько новостей за раз", seg(st.choices.count.map((n) => [n, String(n)]), p.count, save("count"))),
      field("Формат", seg([["day", "Одно сообщение в день"], ["new", "Каждый раз новое"]], p.mode, save("mode")))),
    sourcesEditor("/api/settings/news", st),
    await modulesCard(),
    await repairCard(),
    card("О боте", row("Версия", null, h("b", {}, (await get("/api/me")).version)), h("div", { class: "dim small" }, "Доступ для других людей — в чате: Настройки → Доступ.")));
  root.append(box);
}

// 🧱 модули: выключить плитку, не теряя настроек; 🔧 перезапуск упавших фоновых задач
async function modulesCard() {
  const st = await get("/api/modules"), box = h("div");
  const draw = (s) => {
    const rows = s.items.length
      ? s.items.map((m) => setRow(m.title, m.on ? "Работает" : "Выключен, настройки сохранены", sw(m.on, async (v) => draw(await post("/api/modules", { id: m.id, on: v })))))
      : [h("div", { class: "empty" }, "Пока ничего не подключено. Подключение — в чате: ⚙️ → 🧩 Приложения.")];
    const fix = s.broken.length
      ? [h("div", { class: "dim small", style: "margin-top:12px" }, "Остановлены после падений: " + s.broken.map((b) => b.title).join(", ")),
         h("button", { class: "btn", style: "margin-top:8px", onclick: act(async () => draw(await post("/api/repair", {})), "Перезапущено") }, "🔧 Починить")]
      : [];
    box.replaceChildren(card("Модули", h("div", { class: "list inner" }, ...rows), ...fix));
  };
  draw(st);
  return box;
}

// 🛠 ремонт сервера: выключен, пока не включён в .env на сервере; каждое действие — по PIN
async function repairCard() {
  const st = await get("/api/server-repair"), box = h("div");
  const draw = (s) => {
    if (!s.enabled) { box.replaceChildren(card("Ремонт сервера", h("div", { class: "dim small" }, "Выключен. Включается только на сервере: REPAIR_ENABLED=true и REPAIR_PIN в .env."))); return; }
    const pin = h("input", { type: "password", inputmode: "numeric", placeholder: "PIN", maxlength: "12", autocomplete: "off" });
    const run = (a) => act(async () => {
      if (a.danger && !(await confirmAsk(a.title + "?"))) return;
      const r = await post("/api/server-repair", { action: a.id, pin: pin.value }); pin.value = "";
      draw(r); if (!r.ok) throw new Error(r.detail); toast(r.detail);
    });
    box.replaceChildren(card("Ремонт сервера", pin,
      h("div", { class: "chips" }, ...s.actions.map((a) => h("button", { class: "btn sm" + (a.danger ? " bad" : ""), onclick: run(a) }, a.title))),
      s.locked ? h("div", { class: "bad small" }, `Закрыто на ${Math.ceil(s.locked / 60)} мин после неверных PIN`) : null,
      s.history.length ? h("div", { class: "list inner" }, ...s.history.map((x) => row((x.ok ? "✅ " : "❌ ") + x.action, x.detail))) : null));
  };
  draw(st);
  return box;
}

async function viewMySettings(root) {
  const st = await get("/api/me/settings"), box = h("div", { class: "grid" });
  const nick = h("input", { value: st.nick, placeholder: "Как к вам обращаться", maxlength: "32", autocomplete: "off" });
  const saveNick = act(async () => { await post("/api/me/settings", { op: "set", nick: nick.value }); $("host").textContent = nick.value.trim() || $("host").textContent; }, "Сохранено");
  nick.addEventListener("keydown", (e) => { if (e.key === "Enter") nick.nextSibling.click(); });
  box.append(card("Профиль", field("Имя", h("div", { class: "inline" }, nick, h("button", { class: "btn", onclick: saveNick }, "Сохранить")), "Так я буду к вам обращаться на главной.")));
  if (st.news) {
    box.append(card("Новости", field("Сколько показывать", seg(st.count_choices.map((n) => [n, String(n)]), st.count, (v) => post("/api/me/settings", { op: "set", count: v })))),
      sourcesEditor("/api/me/settings", st, "Выберите категории или добавьте свои ленты. Пока ничего не выбрано, показываю общую ленту."));
  }
  root.append(box);
}

// ---------------------------------------------------------------- 🏠 ГЛАВНАЯ ДЛЯ ГОСТЯ: всё, что включено, на одном экране
async function viewGuestHome(root) {
  const me = await get("/api/me"), f = new Set(me.features);
  const ask = (k, path) => (f.has(k) ? get(path) : Promise.resolve(null));
  const [rem, notes, p2p, news] = await Promise.allSettled([ask("reminders", "/api/reminders"), ask("notes", "/api/notes"), ask("p2p", "/api/p2p"), ask("news", "/api/news")]);
  const val = (r) => (r.status === "fulfilled" ? r.value : null);
  const bits = [];
  if (val(rem)) bits.push(val(rem).length ? `Ближайшее напоминание: ${val(rem)[0].text}.` : "Напоминаний нет.");
  if (val(notes)) bits.push(`Заметок: ${val(notes).length}.`);
  root.append(h("header", { class: "hero" },
    h("div", { class: "when" }, greet() + ", " + new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })),
    h("h1", {}, me.name ? "Привет, " + me.name : "Привет"),
    bits.length ? h("p", {}, bits.join(" ")) : null));
  const pills = [f.has("notes") ? ["note", "notes", "Заметка"] : null, f.has("reminders") ? ["remind", "bell", "Напоминание"] : null].filter(Boolean);
  if (pills.length) root.append(h("div", { class: "quick" }, ...pills.map(([k, ic, t]) => h("button", { class: "pill", onclick: () => quick(k) }, icon(ic), t))));
  const trash = (path, id) => h("button", { class: "btn sm bad", "aria-label": "Удалить", onclick: act(async () => { await del(path + id); show("home"); }) }, "✕");
  if (f.has("reminders")) {
    const items = val(rem);
    root.append(items === null ? errCard("Напоминания", { error: rem.reason && rem.reason.message }) : group("Напоминания", ...(items.length ? items.map((x) => li(x.text, fmtDate(x.due_ts) + (x.rule_text ? ", " + x.rule_text : ""), trash("/api/reminders/", x.id))) : [li("Напоминаний нет", "Добавьте первое кнопкой выше")])));
  }
  if (f.has("notes")) {
    const items = val(notes);
    if (items === null) root.append(errCard("Заметки", { error: notes.reason && notes.reason.message }));
    else {
      const mk = (x) => li(x.text, fmtDate(x.ts), trash("/api/notes/", x.id));
      const list = h("div", { class: "list" }, ...(items.length ? items.slice(0, 5).map(mk) : [li("Заметок нет", "Добавьте первую кнопкой выше")]));
      if (items.length > 5) { const more = li(`Показать все (${items.length})`, null, null, () => { more.remove(); list.append(...items.slice(5).map(mk)); }); list.append(more); }
      root.append(h("section", { class: "grp" }, h("h4", {}, "Заметки"), list));
    }
  }
  if (f.has("p2p") && val(p2p)) root.append(p2pCard(val(p2p)));
  if (f.has("news")) {
    const items = val(news);
    root.append(items === null ? errCard("Новости", { error: news.reason && news.reason.message }) : group("Новости", ...(items.length ? items.slice(0, 6).map((n) => li(n.title, [n.feed, n.sent_ts ? fmtDate(n.sent_ts) : ""].filter(Boolean).join(", "), null, /^https?:\/\//.test(n.link || "") ? () => reader(n.link, n.title) : null)) : [li("Пока ничего нет", "Выберите категории в настройках")])));
  }
  if (!f.size) root.append(card("Пока пусто", h("div", { class: "dim" }, "Хозяин ещё не включил вам ни одной функции.")));
  if (f.has("tools")) root.append(group(null, li("Конвертер файлов", "Пришлите боту файл — предложу, во что его превратить", null, () => { try { tg.close(); } catch (e) {} })));
  root.append(group(null, li("Мои настройки", f.has("news") ? "Имя и источники новостей" : "Как к вам обращаться", null, () => show("home/settings"))));
}

// ---------------------------------------------------------------- ещё: список разделов
async function viewAI(root) { root.append(chatCard()); }
async function viewBackupPage(root) {
  root.append(fold("Бэкап", () => get("/api/backup"), (d) => ({
    sum: d.items.length ? `${fmtDate(d.items[0].ts)}, ${fmtBytes(d.items[0].size)}` : "копий нет",
    body: [d.status && d.status.ok === false ? h("div", { class: "bad" }, "⚠️ " + d.status.error) : null,
      ...d.items.map((b) => row(fmtDate(b.ts), null, h("span", { class: "dim" }, fmtBytes(b.size)))),
      h("button", { class: "btn", onclick: act(async () => { await post("/api/backup/now"); }, "Копия сохранена") }, "Сохранить сейчас"),
      h("div", { class: "dim small" }, "Восстановление — в боте: Настройки → Бэкап.")],
  })), fold("Чем вы пользуетесь", () => get("/api/usage"), (items) => ({ sum: items.length ? items[0].label : "пока пусто", body: items.map((u) => row(u.label, null, h("b", {}, u.count))) })));
}
async function viewTools(root) {
  const d = await get("/api/tools");
  root.append(card("Как пользоваться",
    h("div", {}, "Пришлите боту в чате файл — он предложит, во что его превратить. Ничего открывать в панели не нужно."),
    h("div", { class: "dim small" }, "Файлы до 20 МБ. Фото лучше слать «как файл», чтобы не терять качество."),
    h("button", { class: "btn", onclick: () => { try { tg.close(); } catch (e) {} } }, "Перейти в чат")));
  root.append(group("Что умею", ...d.kinds.map((k) => li(k.title, k.ops.length ? k.ops.join(" · ") : "на этом сервере пока нечего предложить"))));
  root.append(group("Сборка и скачивание", li("Собрать PDF", "Из нескольких фото и PDF, кнопка в меню «Инструменты»"), li("Скачать по ссылке", d.download ? "Звук или видео до 50 МБ" : "Не установлен yt-dlp")));
  root.append(group("Что установлено", ...d.caps.map((c) => li(c.name, c.what, h("b", { class: c.ok ? "ok" : "dim" }, c.ok ? "есть" : "нет")))));
  if (d.hint) root.append(card("Как поставить недостающее", h("code", { class: "mono" }, d.hint), h("div", { class: "dim small" }, "Выполните на сервере, потом откройте /doctor в боте.")));
}
const SUBS = {
  settings: ["gear", "Настройки", "Новости, категории и источники", viewSettings],
  tools: ["tools", "Инструменты", "Конвертер файлов, сборка PDF, скачивание", viewTools],
  movies: ["film", "Кино и сериалы", "Поиск, загрузки, новинки", viewMovies], sites: ["search", "Слежка за ценами", "Цены и изменения на страницах", viewSites],
  reminders: ["bell", "Напоминания", "Разовые и повторяющиеся", viewReminders], notes: ["notes", "Заметки", "Быстрые записи", viewNotes],
  ai: ["spark", "Спросить ИИ", "Локальная модель на сервере", viewAI], backup: ["save", "Бэкап и статистика", "Копии данных бота", viewBackupPage],
};
const SUB_TILE = { movies: "movies", sites: "sites" };
const subOn = (k) => !SUB_TILE[k] || has(SUB_TILE[k]);
function connectSheet(t) {
  sheet(h("div", { class: "sheet-title" }, t.icon + " " + t.title),
    h("div", {}, "Проще всего из чата: напишите боту /connect. Я сам найду контейнер, спрошу адрес и ключ и проверю связь."),
    h("div", { class: "dim small" }, "Нет приложения? В чате: 🧩 Приложения → Каталог поставит его из бота."),
    h("div", { class: "dim small", style: "margin-top:10px" }, "Или вручную на сервере в .env, потом перезапуск бота:"),
    h("pre", { class: "log" }, t.needs.map((k) => k + "=…").join("\n")),
    h("button", { class: "btn block", onclick: closeSheet }, "Понятно"));
}
async function viewMore(root) {
  const off = (TILES || []).filter((t) => !t.connected);
  root.append(h("div", { class: "list launch" }, ...Object.entries(SUBS).filter(([k]) => subOn(k)).map(([k, [ic, title, sub]]) => h("button", { class: "li tap", onclick: () => show("more/" + k) },
    h("span", { class: "badge-ic" }, icon(ic)), h("div", { class: "grow" }, h("div", { class: "t" }, title), h("div", { class: "s" }, sub)), icon("chev")))));
  if (off.length) root.append(group("Можно подключить", ...off.map((t) => li(t.icon + "  " + t.title, "Не подключено", null, () => connectSheet(t)))));
}

// ---------------------------------------------------------------- маршрутизация
async function viewP2P(root) { root.append(await section(null, () => get("/api/p2p"), (d) => [p2pCard(d)])); }
async function viewNotes(root) { root.append(await notesCard()); }
async function viewReminders(root) { root.append(await remindersCard()); }
const GUEST_SUBS = { settings: ["gear", "Настройки", "", viewMySettings] };
let TABS = [["home", "home", "Главная", viewHome], ["server", "server", "Сервер", viewServer], ["news", "news", "Новости", viewNews], ["money", "wallet", "Деньги", viewMoney], ["more", "more", "Ещё", viewMore]];
let current = "", currentSub = "";
async function show(name) {
  const [tabName, subName] = String(name || "").split("/");
  const tab = TABS.find((t) => t[0] === tabName) || TABS[0];
  const subs = IS_GUEST ? GUEST_SUBS : SUBS, parent = IS_GUEST ? "home" : "more";
  const sub = tab[0] === parent && subs[subName] && (IS_GUEST || subOn(subName)) ? subs[subName] : null;
  current = tab[0]; currentSub = sub ? subName : ""; clearTimers(); closeSheet(); $("title").textContent = sub ? sub[1] : tab[2];
  history.replaceState(null, "", "#" + current + (currentSub ? "/" + currentSub : ""));
  $("tabs").replaceChildren(...TABS.map((t) => h("button", { class: "tab" + (t[0] === current ? " on" : ""), onclick: () => show(t[0]) }, icon(t[1]), t[2])));
  try { if (tg && tg.BackButton) { if (sub) tg.BackButton.show(); else tg.BackButton.hide(); } } catch (e) {}
  const root = $("view"); root.replaceChildren(loading());
  const mine = current + "/" + currentSub; const holder = h("div", { class: "grid" });
  if (sub) holder.append(h("button", { class: "crumb", onclick: () => show(parent) }, icon("back"), IS_GUEST ? "Главная" : "Ещё"));
  try { await (sub ? sub[3] : tab[3])(holder); } catch (e) { holder.replaceChildren(failure(e)); }
  if (mine === current + "/" + currentSub) root.replaceChildren(holder);
  window.scrollTo(0, 0);
}
let IS_GUEST = false;
try { if (tg && tg.BackButton) tg.BackButton.onClick(() => { if (currentSub) show(IS_GUEST ? "home" : "more"); }); } catch (e) {}
// 🔔 центр уведомлений: сообщения фоновых задач, которые бот присылал в чат
const fmtWhen = (ts) => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function bellCount(n) { const b = $("bell-n"); b.hidden = !n; b.textContent = n > 99 ? "99+" : String(n); }
async function openInbox() {
  const st = await get("/api/inbox");
  const items = st.items.length
    ? st.items.map((i) => h("div", { class: "note-i" + (i.unread ? " new" : "") + (i.critical ? " crit" : "") }, h("div", { class: "dim small" }, (i.critical ? "❗ " : "") + fmtWhen(i.ts)), h("div", { class: "nt" }, i.text)))
    : [h("div", { class: "empty" }, "Пока тихо: здесь появятся уведомления фоновых задач.")];
  sheet(h("div", { class: "sheet-title" }, "🔔 Уведомления"), h("div", { class: "list inner inbox" }, ...items),
    st.items.length ? h("button", { class: "btn sm bad", onclick: act(async () => { await post("/api/inbox", { op: "clear" }); $("sheet").hidden = true; bellCount(0); }, "Очищено") }, "Очистить") : null);
  if (st.unread) { await post("/api/inbox", { op: "read" }); bellCount(0); }
}
$("bell").addEventListener("click", () => openInbox().catch((e) => toast(e.message, true)));

$("reload").addEventListener("click", () => show(current + (currentSub ? "/" + currentSub : "")));
if (!tg || !tg.initData) {
  $("view").replaceChildren(card("Откройте из Telegram", h("div", { class: "dim" }, "Панель работает только как Mini App внутри бота: напишите боту /app или нажмите кнопку «Панель» рядом с полем ввода.")));
  $("tabs").hidden = true;
} else {
  get("/api/me").then((me) => {
    if (me && me.role === "guest") {
      IS_GUEST = true; TABS = [["home", "home", "Главная", viewGuestHome]];
      $("host").textContent = me.name || "гость"; $("tabs").hidden = true; // у гостя один экран — «Главная»
    }
    else if (me && me.tiles) {
      TILES = me.tiles;
      $("bell").hidden = false; get("/api/inbox").then((st) => bellCount(st.unread)).catch(() => {});
      if (!has("finance") && !has("p2p")) TABS = TABS.filter((t) => t[0] !== "money");
    }
    show(location.hash.slice(1));
  }).catch((e) => { $("tabs").hidden = true; $("view").replaceChildren(failure(e)); });
}
