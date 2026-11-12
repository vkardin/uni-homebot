// Логика Сторожа UNI без привязки к Cloudflare: хранилище, отправка в Telegram и время передаются снаружи.
// worker.js подключает её к Durable Object, test.mjs проверяет её в Node.

export const DEFAULT_TIMEOUT_MIN = 3;
export const MAX_OUTAGES = 100;
export const MONTH_MS = 30 * 24 * 3600 * 1000;

const KEEP_FIELDS = ["host", "version", "cpu", "ram", "disk", "temp", "uptime", "load", "containers", "tz"]

// Часовой пояс: TZ из wrangler.toml, иначе тот, что прислал домашний бот, иначе UTC.
export const zone = (env, m) => env.TZ || (m && m.tz) || "UTC";

export function timeoutMs(env) {
  const n = Number(env.TIMEOUT_MIN);
  return (Number.isFinite(n) && n >= 1 && n <= 60 ? n : DEFAULT_TIMEOUT_MIN) * 60 * 1000;
}

// Сводка от домашнего бота: только числа и короткие строки из белого списка, ничего лишнего не храним.
export function cleanMetrics(raw) {
  const out = {};
  if (!raw || typeof raw !== "object") return out;
  for (const key of KEEP_FIELDS) {
    const v = raw[key];
    if (typeof v === "number" && Number.isFinite(v)) out[key] = v;
    else if (typeof v === "string") out[key] = v.slice(0, 40);
  }
  return out;
}

export function duration(ms) {
  const min = Math.max(0, Math.round(ms / 60000));
  if (min < 1) return "меньше минуты";
  const d = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  const m = min % 60;
  const parts = [];
  if (d) parts.push(`${d} д`);
  if (h) parts.push(`${h} ч`);
  if (m && !d) parts.push(`${m} мин`);
  return parts.join(" ");
}

export function clock(ts, tz, withDate = false) {
  const opts = { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz || "UTC" };
  if (withDate) Object.assign(opts, { day: "2-digit", month: "2-digit" });
  try {
    return new Intl.DateTimeFormat("ru-RU", opts).format(new Date(ts)).replace(",", "");
  } catch {
    return new Intl.DateTimeFormat("ru-RU", { ...opts, timeZone: "UTC" }).format(new Date(ts)) + " UTC";
  }
}

// Почему пропала связь: если сервер успел перезагрузиться за время тишины, скорее всего пропадал свет.
export function guessCause(outageMs, uptimeSec) {
  if (typeof uptimeSec !== "number") return "";
  return uptimeSec * 1000 <= outageMs + 5 * 60 * 1000
    ? "Сервер перезагрузился: похоже, пропадал свет."
    : "Сервер не перезагружался: похоже, пропадал интернет.";
}

export function metricsLine(m) {
  const parts = [];
  if (typeof m.cpu === "number") parts.push(`CPU ${Math.round(m.cpu)}%`);
  if (typeof m.ram === "number") parts.push(`RAM ${Math.round(m.ram)}%`);
  if (typeof m.disk === "number") parts.push(`диск ${Math.round(m.disk)}%`);
  if (typeof m.temp === "number") parts.push(`${Math.round(m.temp)}°C`);
  if (typeof m.uptime === "number") parts.push(`аптайм ${duration(m.uptime * 1000)}`);
  return parts.join(" · ");
}

// Сигнал «я жив». Возвращает, когда ждать следующий (для будильника), и при возвращении после тишины шлёт сообщение.
export async function onBeat({ storage, env, now, send, metrics }) {
  const m = cleanMetrics(metrics);
  const down = await storage.get("down");
  await storage.put("last", { ts: now, m });
  if (down) {
    const outage = { from: down.since, to: now, uptime: m.uptime ?? null };
    const list = ((await storage.get("outages")) || []).concat([outage]).slice(-MAX_OUTAGES);
    const lost = now - down.since;
    const cause = guessCause(lost, m.uptime);
    // Сначала сообщение: если Telegram не ответил, «down» останется и следующий сигнал попробует снова.
    await send(`⚡ Дом снова на связи. Не было ${duration(lost)} (с ${clock(down.since, zone(env, m))}).${cause ? "\n" + cause : ""}`);
    await storage.put("outages", list);
    await storage.delete("down");
  }
  return now + timeoutMs(env);
}

// Будильник: сигнала не было дольше таймаута. Сообщаем один раз, пока дом не вернётся.
export async function onAlarm({ storage, env, now, send }) {
  const last = await storage.get("last");
  if (!last || (await storage.get("down"))) return null;
  if (now - last.ts < timeoutMs(env)) return last.ts + timeoutMs(env);
  // Сначала сообщение: если Telegram не ответил, будильник сработает повторно (Cloudflare повторяет упавшие).
  await send(
    `🔌 Дом не на связи с ${clock(last.ts, zone(env, last.m))}.\nВозможно, нет света или интернета. Напишу, когда сервер вернётся.`,
  );
  await storage.put("down", { since: last.ts, notified: now });
  return null;
}

export async function statusText({ storage, env, now }) {
  const last = await storage.get("last");
  if (!last) return "Сигналов от дома ещё не было. Проверьте WATCHDOG_URL и WATCHDOG_TOKEN в .env бота.";
  const down = await storage.get("down");
  const ago = duration(now - last.ts);
  const host = last.m.host ? ` (${last.m.host})` : "";
  if (down) {
    return `🔴 Дом${host} не на связи ${duration(now - down.since)}, с ${clock(down.since, zone(env, last.m), true)}.\nПоследние данные:\n${metricsLine(last.m) || "нет"}`;
  }
  const line = metricsLine(last.m);
  return `🟢 Дом${host} на связи, сигнал ${ago === "меньше минуты" ? "только что" : ago + " назад"}.${line ? "\n" + line : ""}${last.m.version ? "\nUNI " + last.m.version : ""}`;
}

export async function historyText({ storage, env, now }) {
  const list = (await storage.get("outages")) || [];
  const down = await storage.get("down");
  const tz = zone(env, (await storage.get("last"))?.m);
  const month = list.filter((o) => now - o.to < MONTH_MS);
  if (!month.length && !down) return "За 30 дней отключений не было. 🎉";
  const lines = [];
  if (down) lines.push(`🔴 сейчас: с ${clock(down.since, tz, true)}, ${duration(now - down.since)}`);
  for (const o of month.slice(-15).reverse()) {
    const icon = guessCause(o.to - o.from, o.uptime ?? undefined).includes("свет") ? "🔌" : "📡";
    lines.push(`${icon} ${clock(o.from, tz, true)} – ${clock(o.to, tz)}, ${duration(o.to - o.from)}`);
  }
  const total = month.reduce((sum, o) => sum + (o.to - o.from), 0);
  lines.push("", `За 30 дней: ${month.length} раз, всего ${duration(total)}.`, "🔌 свет · 📡 интернет (догадка по аптайму)");
  return lines.join("\n");
}

export const HELP =
  "🛡 Сторож UNI следит за домашним сервером из облака и пишет, когда тот пропадает и возвращается.\n\n" +
  "/status — на связи ли дом и последние цифры\n/history — отключения за 30 дней";

// Сообщение из Telegram. Отвечаем только владельцу (CHAT_ID), остальным молчим.
export async function onUpdate({ storage, env, now, update }) {
  const msg = update && update.message;
  if (!msg || !msg.text || String(msg.chat?.id) !== String(env.CHAT_ID)) return null;
  const cmd = msg.text.trim().split(/[\s@]/)[0].toLowerCase();
  if (cmd === "/status") return statusText({ storage, env, now });
  if (cmd === "/history") return historyText({ storage, env, now });
  return HELP;
}
