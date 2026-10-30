#!/usr/bin/env bash
# HomeBot: первая установка одной командой.
#
#   sudo bash deploy/install.sh            # обычная установка (systemd, код в /opt/homebot)
#   sudo bash deploy/install.sh --docker   # в Docker (compose.yml в корне архива)
#
# Без вопросов: BOT_TOKEN=... ADMIN_ID=... sudo -E bash deploy/install.sh
#
# Работает на apt / dnf / pacman / apk. Если бот уже установлен (в .env есть токен), ничего не спрашивает
# и просто обновляет — то же, что deploy/update.sh. Ваш .env не перезаписывается.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=auto
for a in "$@"; do
    case "$a" in
        --docker) MODE=docker ;;
        --systemd) MODE=systemd ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "неизвестный параметр: $a" >&2; exit 2 ;;
    esac
done

c_step=$'\033[1;36m'; c_ok=$'\033[1;32m'; c_warn=$'\033[1;33m'; c_err=$'\033[1;31m'; c_off=$'\033[0m'
step() { echo; echo "${c_step}==> $*${c_off}"; }
ok()   { echo "${c_ok}  ✓ $*${c_off}"; }
warn() { echo "${c_warn}  ! $*${c_off}"; }
die()  { echo "${c_err}  ✗ $*${c_off}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Запустите через sudo: sudo bash $0"
[[ -f "$SRC/homebot/__init__.py" ]] || die "Не найден код бота рядом со скриптом ($SRC)"
# shellcheck source=deploy/pkg.sh
source "$SRC/deploy/pkg.sh"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/homebot/__init__.py")

if [[ $MODE == auto ]]; then
    if has_systemd; then MODE=systemd
    elif command -v docker &>/dev/null; then MODE=docker
    else
        die "нет ни systemd, ни Docker. Поставьте Docker (https://docs.docker.com/engine/install/) и запустите: sudo bash $0 --docker"
    fi
fi
if [[ $MODE == systemd ]]; then
    has_systemd || die "нет systemd — используйте Docker: sudo bash $0 --docker"
    ENV_FILE=/opt/homebot/.env
else
    command -v docker &>/dev/null || die "Docker не найден. Установка: https://docs.docker.com/engine/install/"
    docker compose version &>/dev/null || die "нет плагина docker compose: https://docs.docker.com/compose/install/linux/"
    ENV_FILE="$SRC/.env"
fi

echo "HomeBot $VERSION — установка ($MODE, пакеты: ${PKG_MGR:-не найден})"

# ---------------------------------------------------------------------------- 1
if [[ $MODE == systemd ]]; then
    step "Системные пакеты"
    [[ -n "$PKG_MGR" ]] || die "не нашёл apt-get, dnf, pacman или apk — поставьте python3 (с venv), rsync и curl вручную"
    need=()
    command -v python3 &>/dev/null && python3 -c 'import venv, ensurepip' &>/dev/null || need+=(python)
    command -v rsync &>/dev/null || need+=(rsync)
    command -v curl &>/dev/null || need+=(curl)
    if [[ ${#need[@]} -gt 0 ]]; then
        pkg_install "${need[@]}" || die "не удалось поставить пакеты: $(pkg_hint "${need[@]}")"
        ok "установлено: ${need[*]}"
    else
        ok "всё нужное уже есть"
    fi
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
        || die "нужен Python 3.11 или новее (сейчас $(python3 -V 2>&1)). Используйте Docker: sudo bash $0 --docker"
fi

# ---------------------------------------------------------------------------- 2
step "Токен и администратор"
env_get() { [[ -f "$ENV_FILE" ]] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
env_set() {  # заменить или дописать ключ, не трогая остальное
    if grep -q "^$1=" "$ENV_FILE"; then
        local v=${2//\\/\\\\}; v=${v//|/\\|}; v=${v//&/\\&}
        sed -i "s|^$1=.*|$1=$v|" "$ENV_FILE"
    else
        echo "$1=$2" >> "$ENV_FILE"
    fi
}
ask() {  # ask "вопрос" → ответ; читает с терминала, даже если скрипт пришёл через curl | bash
    local answer=""
    if [[ -r /dev/tty ]]; then read -rp "  $1: " answer </dev/tty || true; fi
    echo "$answer"
}
token_ok() {  # проверка токена через getMe; печатает @имя бота
    local resp
    resp=$(curl -fsS --max-time 15 "https://api.telegram.org/bot$1/getMe" 2>/dev/null) || return 1
    grep -q '"ok":true' <<<"$resp" || return 1
    sed -n 's/.*"username":"\([^"]*\)".*/@\1/p' <<<"$resp"
}

fresh=0
if [[ ! -f "$ENV_FILE" ]]; then
    fresh=1
    install -d -m 0755 "$(dirname "$ENV_FILE")"
    cp "$SRC/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "создан $ENV_FILE из примера"
fi

cur_token=$(env_get BOT_TOKEN)
if [[ -n "$cur_token" && "$cur_token" != *replace-me* ]]; then
    ok "токен уже задан — не трогаю"
else
    token=${BOT_TOKEN:-}
    for _ in 1 2 3; do
        [[ -n "$token" ]] || token=$(ask "Токен бота от @BotFather")
        [[ -n "$token" ]] || die "токен не введён. Впишите BOT_TOKEN в $ENV_FILE и запустите скрипт снова"
        if name=$(token_ok "$token"); then ok "токен подходит: ${name:-бот}"; break; fi
        if ! curl -fsS --max-time 10 -o /dev/null https://api.telegram.org 2>/dev/null; then
            warn "нет связи с api.telegram.org — проверить токен не могу, сохраняю как есть"; break
        fi
        warn "Telegram не принял токен, попробуйте ещё раз"; token=""
    done
    [[ -n "$token" ]] || die "токен так и не подошёл"
    env_set BOT_TOKEN "$token"
fi

cur_admin=$(env_get ADMIN_ID)
if [[ "$cur_admin" =~ ^[0-9]+$ && "$cur_admin" != 123456789 ]]; then
    ok "ADMIN_ID уже задан — не трогаю"
else
    admin=${ADMIN_ID:-}
    [[ "$admin" =~ ^[0-9]+$ ]] || admin=$(ask "Ваш Telegram ID (число, @userinfobot; Enter — назначить себя кодом после запуска)")
    if [[ "$admin" =~ ^[0-9]+$ ]]; then
        env_set ADMIN_ID "$admin"
        ok "администратор: $admin"
    else
        env_set ADMIN_ID ""
        ok "ADMIN_ID не задан — после запуска отправьте боту /start КОД (код: journalctl -u homebot | grep start)"
    fi
fi

# Только при первой установке: если стоит Tailscale — включаем tailscale serve и подставляем WEB_URL.
# В уже настроенном сервере настройки Tailscale не трогаем.
if [[ $fresh -eq 1 && -z "$(env_get WEB_URL)" ]] && command -v tailscale &>/dev/null && tailscale status &>/dev/null; then
    port=$(env_get WEB_PORT); port=${port:-8088}
    if tailscale serve --bg "$port" &>/dev/null; then
        host=$(tailscale status --json 2>/dev/null | sed -n 's/.*"DNSName": *"\([^"]*\)\.".*/\1/p' | head -n1)
        if [[ -n "$host" ]]; then env_set WEB_URL "https://$host"; ok "панель: https://$host (tailscale serve)"; fi
    else
        warn "tailscale serve не включился — панель будет только на 127.0.0.1:$port"
    fi
fi
chmod 600 "$ENV_FILE"

# ---------------------------------------------------------------------------- 3
if [[ $MODE == systemd ]]; then
    exec bash "$SRC/deploy/update.sh"
fi

step "Docker"
cd "$SRC"
docker compose up -d --build
sleep 5
if [[ "$(docker inspect -f '{{.State.Running}}' homebot 2>/dev/null)" == true ]]; then
    ok "HomeBot $VERSION запущен в Docker"
    docker logs --tail 6 homebot 2>&1 | sed 's/^/    /'
    echo "  Журнал: docker logs -f homebot · обновление: распакуйте новый архив сюда же и снова запустите этот скрипт"
else
    docker logs --tail 30 homebot 2>&1 | sed 's/^/    /'
    die "контейнер не запустился — журнал выше"
fi
