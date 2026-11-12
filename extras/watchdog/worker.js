// 🛡 Сторож UNI: маленький сервис на Cloudflare Workers, который работает, даже когда дома нет света.
//
// Домашний бот раз в минуту шлёт POST /beat (см. homebot/jobs/heartbeat.py). Каждый сигнал переводит будильник
// Durable Object вперёд; если сигналов нет дольше TIMEOUT_MIN минут, будильник срабатывает и Сторож пишет в Telegram.
// Отдельный бот со своим токеном: у одного токена может быть только один получатель обновлений, основной бот
// продолжает работать дома как раньше.
//
// Секреты (wrangler secret put): BOT_TOKEN — токен бота-сторожа, CHAT_ID — ваш id, HEARTBEAT_TOKEN — общий секрет
// с домашним ботом. Переменные (wrangler.toml): TZ, TIMEOUT_MIN.

import { DurableObject } from "cloudflare:workers";
import { onAlarm, onBeat, onUpdate } from "./logic.js";

const MAX_BODY = 16 * 1024;

function sameSecret(a, b) {
  const x = new TextEncoder().encode(String(a));
  const y = new TextEncoder().encode(String(b));
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

function bearerOk(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return Boolean(env.HEARTBEAT_TOKEN) && env.HEARTBEAT_TOKEN.length >= 16 && sameSecret(auth, `Bearer ${env.HEARTBEAT_TOKEN}`);
}

// Секрет вебхука Telegram выводится из HEARTBEAT_TOKEN, чтобы не заводить ещё один.
async function webhookSecret(env) {
  const data = new TextEncoder().encode(`uni-watchdog:${env.HEARTBEAT_TOKEN}`);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  return Array.from(hash.slice(0, 24), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function telegram(env, method, payload) {
  const resp = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`Telegram ${method}: HTTP ${resp.status}`);
  return resp.json();
}

async function readJson(request) {
  const text = await request.text();
  if (text.length > MAX_BODY) throw new Error("too large");
  return text ? JSON.parse(text) : {};
}

export class Watchdog extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    const ctx = {
      storage: this.ctx.storage,
      env: this.env,
      now: Date.now(),
      send: (text) => telegram(this.env, "sendMessage", { chat_id: this.env.CHAT_ID, text }),
    };
    if (url.pathname === "/beat") {
      const next = await onBeat({ ...ctx, metrics: await readJson(request) });
      await this.ctx.storage.setAlarm(next);
      return Response.json({ ok: true });
    }
    if (url.pathname === "/telegram") {
      const text = await onUpdate({ ...ctx, update: await readJson(request) });
      // Ответ прямо в теле вебхука: Telegram сам отправит сообщение, лишний запрос не нужен.
      return text ? Response.json({ method: "sendMessage", chat_id: this.env.CHAT_ID, text }) : new Response("ok");
    }
    return new Response("not found", { status: 404 });
  }

  async alarm() {
    const next = await onAlarm({
      storage: this.ctx.storage,
      env: this.env,
      now: Date.now(),
      send: (text) => telegram(this.env, "sendMessage", { chat_id: this.env.CHAT_ID, text }),
    });
    if (next) await this.ctx.storage.setAlarm(next);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const home = () => env.WATCHDOG.get(env.WATCHDOG.idFromName("home"));
    if (request.method === "GET" && url.pathname === "/") return new Response("UNI watchdog: ok\n");
    if (request.method !== "POST") return new Response("method not allowed", { status: 405 });

    if (url.pathname === "/beat") {
      if (!bearerOk(request, env)) return new Response("unauthorized", { status: 401 });
      return home().fetch(request);
    }
    if (url.pathname === "/telegram") {
      const got = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (!env.HEARTBEAT_TOKEN || !sameSecret(got, await webhookSecret(env))) {
        return new Response("unauthorized", { status: 401 });
      }
      return home().fetch(request);
    }
    if (url.pathname === "/setup") {
      // Один раз после развёртывания: привязать вебхук бота-сторожа и меню команд.
      if (!bearerOk(request, env)) return new Response("unauthorized", { status: 401 });
      const hook = await telegram(env, "setWebhook", {
        url: `${url.origin}/telegram`,
        secret_token: await webhookSecret(env),
        allowed_updates: ["message"],
        drop_pending_updates: true,
      });
      await telegram(env, "setMyCommands", {
        commands: [
          { command: "status", description: "На связи ли дом" },
          { command: "history", description: "Отключения за 30 дней" },
        ],
      });
      await telegram(env, "sendMessage", { chat_id: env.CHAT_ID, text: "🛡 Сторож UNI подключён. /status — проверить." });
      return Response.json({ ok: Boolean(hook.ok) });
    }
    return new Response("not found", { status: 404 });
  },
};
