// Проверка логики Сторожа: node --test extras/watchdog/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { cleanMetrics, duration, guessCause, historyText, onAlarm, onBeat, onUpdate, statusText } from "./logic.js";

class Mem {
  constructor() {
    this.map = new Map();
  }
  async get(k) {
    return this.map.get(k);
  }
  async put(k, v) {
    this.map.set(k, structuredClone(v));
  }
  async delete(k) {
    this.map.delete(k);
  }
}

const MIN = 60 * 1000;
const env = { TZ: "Europe/Kyiv", TIMEOUT_MIN: "3", CHAT_ID: "42" };
const T0 = Date.UTC(2026, 10, 12, 12, 0); // 14:00 по Киеву

function setup() {
  const sent = [];
  return { storage: new Mem(), env, sent, send: async (t) => sent.push(t) };
}

test("duration", () => {
  assert.equal(duration(20 * 1000), "меньше минуты");
  assert.equal(duration(5 * MIN), "5 мин");
  assert.equal(duration(134 * MIN), "2 ч 14 мин");
  assert.equal(duration(26 * 60 * MIN + 5 * MIN), "1 д 2 ч");
});

test("cleanMetrics keeps only known fields", () => {
  assert.deepEqual(cleanMetrics({ cpu: 5, token: "x", disks: [1], host: "h".repeat(100) }), { cpu: 5, host: "h".repeat(40) });
  assert.deepEqual(cleanMetrics(null), {});
});

test("guessCause by uptime", () => {
  assert.match(guessCause(60 * MIN, 120), /свет/);
  assert.match(guessCause(10 * MIN, 86400), /интернет/);
  assert.equal(guessCause(10 * MIN, undefined), "");
});

test("beat schedules the alarm, silence alerts once, return reports outage", async () => {
  const s = setup();
  const next = await onBeat({ ...s, now: T0, metrics: { cpu: 10, uptime: 9999 } });
  assert.equal(next, T0 + 3 * MIN);

  // Будильник до истечения таймаута (сигнал пришёл позже) — переносится, без сообщений.
  await onBeat({ ...s, now: T0 + MIN, metrics: { uptime: 9999 } });
  assert.equal(await onAlarm({ ...s, now: T0 + 3 * MIN }), T0 + 4 * MIN);
  assert.equal(s.sent.length, 0);

  assert.equal(await onAlarm({ ...s, now: T0 + 4 * MIN }), null);
  assert.equal(s.sent.length, 1);
  assert.match(s.sent[0], /не на связи с 14:01/);
  await onAlarm({ ...s, now: T0 + 10 * MIN });
  assert.equal(s.sent.length, 1, "повторно не пишем");
  assert.match(await statusText({ ...s, now: T0 + 61 * MIN }), /🔴.*1 ч/);

  // Через 2 часа вернулся с аптаймом 5 минут — был перезапуск, значит свет.
  await onBeat({ ...s, now: T0 + 121 * MIN, metrics: { uptime: 300, cpu: 3 } });
  assert.equal(s.sent.length, 2);
  assert.match(s.sent[1], /снова на связи\. Не было 2 ч \(с 14:01\)/);
  assert.match(s.sent[1], /свет/);
  assert.equal(await s.storage.get("down"), undefined);
  assert.match(await statusText({ ...s, now: T0 + 121 * MIN }), /🟢.*только что/s);
  const hist = await historyText({ ...s, now: T0 + 122 * MIN });
  assert.match(hist, /🔌 12\.11 14:01 – 16:01, 2 ч/);
  assert.match(hist, /1 раз, всего 2 ч/);
});

test("failed Telegram send keeps state for a retry", async () => {
  const s = setup();
  await onBeat({ ...s, now: T0, metrics: {} });
  const failing = async () => {
    throw new Error("telegram down");
  };
  await assert.rejects(onAlarm({ ...s, send: failing, now: T0 + 5 * MIN }));
  assert.equal(await s.storage.get("down"), undefined);
  await onAlarm({ ...s, now: T0 + 6 * MIN });
  await assert.rejects(onBeat({ ...s, send: failing, now: T0 + 30 * MIN, metrics: {} }));
  assert.ok(await s.storage.get("down"), "возвращение сообщим со следующим сигналом");
});

test("timezone comes from the home bot when TZ is not set", async () => {
  const s = { ...setup(), env: { CHAT_ID: "42" } };
  await onBeat({ ...s, now: T0, metrics: { tz: "Asia/Tokyo" } });
  await onAlarm({ ...s, now: T0 + 5 * MIN });
  assert.match(s.sent[0], /с 21:00/);
});

test("only the owner gets answers", async () => {
  const s = setup();
  const msg = (id, text) => ({ message: { chat: { id }, text } });
  assert.equal(await onUpdate({ ...s, now: T0, update: msg(7, "/status") }), null);
  assert.match(await onUpdate({ ...s, now: T0, update: msg(42, "/status") }), /ещё не было/);
  assert.match(await onUpdate({ ...s, now: T0, update: msg(42, "/history@uni_bot") }), /не было/);
  assert.match(await onUpdate({ ...s, now: T0, update: msg(42, "привет") }), /Сторож UNI/);
  assert.equal(await onUpdate({ ...s, now: T0, update: {} }), null);
});
