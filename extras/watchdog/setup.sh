#!/usr/bin/env bash
# 🛡 Установка Сторожа UNI одной командой: bash extras/watchdog/setup.sh
#
# Спросит токен бота-сторожа и ваш Telegram id, сам придумает общий секрет, развернёт Сторожа на Cloudflare,
# подключит вебхук и покажет две строки для .env основного бота (и по желанию допишет их).
# Нужны Node.js 20+, curl и бесплатный аккаунт Cloudflare (вход откроется в браузере).
set -euo pipefail

cd "$(dirname "$0")"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

command -v npx >/dev/null || die "Нужен Node.js 20+ (npx не найден): https://nodejs.org"
command -v curl >/dev/null || die "Нужен curl"

ENV_FILE="${1:-}"
if [[ -z "$ENV_FILE" ]]; then
    for f in /opt/homebot/.env ../../.env; do [[ -r "$f" ]] && { ENV_FILE="$f"; break; }; done
fi

say "1/5 Бот-сторож"
echo "Создайте бота у @BotFather (/newbot), откройте его и нажмите «Старт»."
read -rp "Токен бота-сторожа: " BOT_TOKEN
[[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || die "Это не похоже на токен бота (вида 123456:ABC...)"

ADMIN=""
[[ -n "$ENV_FILE" ]] && ADMIN="$(sed -n 's/^ADMIN_ID=\([0-9]*\).*/\1/p' "$ENV_FILE" | tail -1)"
read -rp "Ваш Telegram id${ADMIN:+ [$ADMIN]}: " CHAT_ID
CHAT_ID="${CHAT_ID:-$ADMIN}"
[[ "$CHAT_ID" =~ ^-?[0-9]+$ ]] || die "Telegram id — это число (его показывает @userinfobot)"

SECRET="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"

say "2/5 Вход в Cloudflare"
WHO="$(npx --yes wrangler@4 whoami 2>&1 || true)"
grep -qi "not authenticated" <<<"$WHO" && npx --yes wrangler@4 login

say "3/5 Развёртывание"
OUT="$(npx --yes wrangler@4 deploy 2>&1 | tee /dev/stderr)"
URL="$(grep -oE 'https://[A-Za-z0-9.-]+\.workers\.dev' <<<"$OUT" | head -1)"
[[ -n "$URL" ]] || die "Не нашёл адрес Сторожа в выводе wrangler. Включите поддомен workers.dev в панели Cloudflare и запустите снова."

say "4/5 Секреты"
printf '%s' "$BOT_TOKEN" | npx --yes wrangler@4 secret put BOT_TOKEN >/dev/null
printf '%s' "$CHAT_ID" | npx --yes wrangler@4 secret put CHAT_ID >/dev/null
printf '%s' "$SECRET" | npx --yes wrangler@4 secret put HEARTBEAT_TOKEN >/dev/null
echo "✅ BOT_TOKEN, CHAT_ID, HEARTBEAT_TOKEN"

say "5/5 Подключение к Telegram"
ok=""
for _ in 1 2 3 4 5 6; do  # новым секретам нужно несколько секунд
    if curl -fsS -X POST "$URL/setup" -H "Authorization: Bearer $SECRET" | grep -q '"ok":true'; then ok=1; break; fi
    sleep 5
done
[[ -n "$ok" ]] || die "Сторож не ответил на /setup. Проверьте, что нажали «Старт» у бота-сторожа, и запустите скрипт снова."
echo "✅ Бот-сторож прислал «Сторож UNI подключён»"

LINES="WATCHDOG_URL=$URL
WATCHDOG_TOKEN=$SECRET"
say "Готово. Строки для .env основного бота:"
echo "$LINES"
if [[ -n "$ENV_FILE" && -w "$ENV_FILE" ]]; then
    read -rp "Дописать их в $ENV_FILE? [y/N] " yn
    if [[ "$yn" =~ ^[YyДд]$ ]]; then
        sed -i '/^WATCHDOG_URL=/d; /^WATCHDOG_TOKEN=/d' "$ENV_FILE"
        printf '\n%s\n' "$LINES" >>"$ENV_FILE"
        echo "✅ Записано. Перезапустите бота: sudo systemctl restart homebot (или docker compose restart)"
    fi
else
    echo "Добавьте их в .env и перезапустите бота."
fi
echo "Через минуту /status у бота-сторожа покажет 🟢."
