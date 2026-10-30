#!/usr/bin/env bash
# HomeBot: установка и обновление одной командой.
#
#   sudo bash deploy/update.sh          # из распакованного архива
#   sudo bash deploy/update.sh --test   # то же + прогон тестов перед перезапуском
#
# Первая установка удобнее через deploy/install.sh: он ставит зависимости, спрашивает токен и вызывает этот скрипт.
# Нужен systemd; без него — Docker (sudo bash deploy/install.sh --docker).
#
# Что делает (всё идемпотентно, повторный запуск безопасен):
#   1. копирует код в /opt/homebot (.env, .venv и данные не трогает);
#   2. ставит/обновляет зависимости в venv;
#   3. дописывает в .env новые параметры со значениями по умолчанию (ваши значения не меняет);
#   4. ставит smartmontools и таймер SMART-снимков (от root);
#   5. пишет polkit-правило: перезапуск служб из RESTART_SERVICES и (если POWER_ENABLED=true) питание;
#   6. обновляет unit-файл и перезапускает бота, показывает статус.
set -euo pipefail

DEST=/opt/homebot
ENV_FILE="$DEST/.env"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_TESTS=0
[[ "${1:-}" == "--test" ]] && RUN_TESTS=1

c_step=$'\033[1;36m'; c_ok=$'\033[1;32m'; c_warn=$'\033[1;33m'; c_err=$'\033[1;31m'; c_off=$'\033[0m'
step() { echo; echo "${c_step}==> $*${c_off}"; }
ok()   { echo "${c_ok}  ✓ $*${c_off}"; }
warn() { echo "${c_warn}  ! $*${c_off}"; }
die()  { echo "${c_err}  ✗ $*${c_off}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Запустите через sudo: sudo bash $0"
[[ -f "$SRC/homebot/__init__.py" ]] || die "Не найден код бота рядом со скриптом ($SRC)"
# shellcheck source=deploy/pkg.sh
source "$SRC/deploy/pkg.sh"
has_systemd || die "нет systemd — этот способ установки не подойдёт. Используйте Docker: sudo bash deploy/install.sh --docker"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/homebot/__init__.py")

env_get() {  # значение ключа из .env без кавычек
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

# ---------------------------------------------------------------------------- 1
step "HomeBot $VERSION: код → $DEST"
NOLOGIN=$(command -v nologin || echo /usr/sbin/nologin)
id homebot &>/dev/null || { useradd --system --home "$DEST" --shell "$NOLOGIN" homebot; ok "создан пользователь homebot"; }
mkdir -p "$DEST"
command -v rsync &>/dev/null || pkg_install rsync || die "нужен rsync: $(pkg_hint rsync)"
if [[ "$SRC" != "$DEST" ]]; then
    rsync -a --delete \
        --exclude '.env' --exclude '.venv/' --exclude 'data/' \
        --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' \
        "$SRC/" "$DEST/"
    ok "код скопирован"
fi

# ---------------------------------------------------------------------------- 2
step "Зависимости"
if [[ ! -x "$DEST/.venv/bin/python" ]]; then
    python3 -m venv "$DEST/.venv" || die "не удалось создать venv ($(pkg_hint python))"
    ok "создано окружение .venv"
fi
"$DEST/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$DEST/requirements.txt"
ok "пакеты установлены"

# ---------------------------------------------------------------------------- 3
step "Конфигурация .env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$DEST/.env.example" "$ENV_FILE"
    chown homebot:homebot "$ENV_FILE"; chmod 600 "$ENV_FILE"
    die "создан $ENV_FILE из примера — впишите BOT_TOKEN (ADMIN_ID можно не задавать) и запустите скрипт снова"
fi
added=()
while IFS= read -r line; do
    [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)= ]] || continue
    key="${BASH_REMATCH[1]}"
    grep -Eq "^#?[[:space:]]*${key}=" "$ENV_FILE" && continue
    if [[ ${#added[@]} -eq 0 ]]; then
        printf '\n# --- добавлено update.sh %s (v%s) ---\n' "$(date +%F)" "$VERSION" >> "$ENV_FILE"
    fi
    echo "$line" >> "$ENV_FILE"
    added+=("$key")
done < "$DEST/.env.example"
if [[ ${#added[@]} -gt 0 ]]; then ok "новые параметры: ${added[*]}"; else ok "новых параметров нет"; fi
grep -q '^BOT_TOKEN=.\+' "$ENV_FILE" || die "в $ENV_FILE не задан BOT_TOKEN"
grep -q '^ADMIN_ID=[0-9]\+' "$ENV_FILE" || [[ -f /var/lib/homebot/owner.json ]] \
    || warn "ADMIN_ID не задан — после запуска возьмите код: journalctl -u homebot | grep start"

# ---------------------------------------------------------------------------- 4
step "SMART"
if ! command -v smartctl &>/dev/null; then
    if pkg_install smartmontools; then
        ok "установлен smartmontools"
    else
        warn "smartmontools не установлен — раздел «Диски» будет пустым"
    fi
fi
# ---------------------------------------------------------------------------- 4б
step "Инструменты (конвертер файлов)"
# ffmpeg — аудио и видео, poppler — PDF в картинки, tesseract — текст с фото, ghostscript — сжатие PDF.
# Всё необязательно: чего нет, того бот просто не предлагает (подробности — /doctor). LibreOffice тяжёлый (~400 МБ):
# ставится только по просьбе — sudo OFFICE=1 bash deploy/update.sh — или руками, команду покажет /doctor.
tool_pkgs=()
command -v ffmpeg &>/dev/null || tool_pkgs+=(ffmpeg)
command -v pdftoppm &>/dev/null || tool_pkgs+=(poppler-utils)
command -v tesseract &>/dev/null || tool_pkgs+=(tesseract)
command -v gs &>/dev/null || tool_pkgs+=(ghostscript)
if [[ "${OFFICE:-0}" == "1" ]] && ! command -v soffice &>/dev/null; then
    tool_pkgs+=(libreoffice)
fi
if [[ ${#tool_pkgs[@]} -eq 0 ]]; then
    ok "всё нужное уже установлено"
else
    # По одному: если в дистрибутиве нет какого-то пакета (ffmpeg в Fedora без RPM Fusion), остальные всё равно встанут.
    failed=()
    for p in "${tool_pkgs[@]}"; do pkg_install "$p" || failed+=("$p"); done
    if [[ ${#failed[@]} -eq 0 ]]; then
        ok "установлено: ${tool_pkgs[*]}"
    else
        warn "не удалось поставить: ${failed[*]} — бот будет работать без этих функций ($(pkg_hint "${failed[@]}"))"
    fi
fi
command -v soffice &>/dev/null || warn "LibreOffice не установлен: Word/Excel → PDF недоступно (sudo OFFICE=1 bash deploy/update.sh)"

install -d -o homebot -g homebot -m 0750 /var/lib/homebot
# 🧩 Автонастройка: ключи Radarr, Sonarr, Jellyfin, changedetection, Actual из их контейнеров → integrations.json
# (заданное в .env и уже подключённое не трогает; в терминале спрашивает про каждое найденное)
if command -v docker &>/dev/null; then
    step "Автонастройка приложений"
    ac_args=(--env "$ENV_FILE" --state /var/lib/homebot)
    [[ -t 0 ]] && ac_args+=(--ask)
    python3 -I "$DEST/deploy/autoconfig.py" "${ac_args[@]}" || warn "автонастройка не удалась — приложения можно подключить в боте: ⚙️ → 🧩 Приложения"
fi
install -m 0644 "$DEST/deploy/homebot-smart.service" /etc/systemd/system/homebot-smart.service
install -m 0644 "$DEST/deploy/homebot-smart.timer" /etc/systemd/system/homebot-smart.timer
systemctl daemon-reload
systemctl enable --now homebot-smart.timer >/dev/null
if command -v smartctl &>/dev/null; then
    systemctl start homebot-smart.service && ok "снимок SMART обновлён" || warn "снимок SMART не удался: journalctl -u homebot-smart"
fi

# ---------------------------------------------------------------------------- 4b
step "Docker"
docker_restart_js="false"
if [[ -n "$(env_get DOCKER_URL)" ]] && command -v docker &>/dev/null; then
    docker compose -p homebot -f "$DEST/deploy/docker-socket-proxy.yml" up -d --quiet-pull >/dev/null \
        && ok "прокси Docker (только чтение) запущен на 127.0.0.1:2375" \
        || warn "не удалось запустить прокси Docker — раздел Docker в боте работать не будет"
    install -m 0644 "$DEST/deploy/homebot-docker-restart@.service" /etc/systemd/system/homebot-docker-restart@.service
    if [[ "$(env_get DOCKER_RESTART_ENABLED | tr '[:upper:]' '[:lower:]')" =~ ^(true|1|yes|on|)$ ]]; then
        docker_restart_js="true"
        ok "перезапуск контейнеров из бота: разрешён"
    fi
else
    warn "Docker-интеграция выключена (нет docker или пустой DOCKER_URL)"
fi

# ---------------------------------------------------------------------------- 4c
step "Обновление из чата"
install -d -o homebot -g homebot -m 0750 /var/lib/homebot/update
install -m 0755 -o root -g root "$DEST/deploy/homebot_updater.py" /usr/local/sbin/homebot-updater
install -m 0644 "$DEST/deploy/homebot-update.service" /etc/systemd/system/homebot-update.service
install -m 0644 "$DEST/deploy/homebot-update.path" /etc/systemd/system/homebot-update.path
systemctl daemon-reload
systemctl enable --now homebot-update.path >/dev/null 2>&1 \
    && ok "обновлятор установлен: архивы .tar.gz можно присылать боту в чат" \
    || warn "не удалось включить homebot-update.path — обновление из чата работать не будет"

# 📦 Каталог приложений: только если явно включён (бот получает право ставить контейнеры из белого списка)
if [[ "$(env_get CATALOG_ENABLED)" == "true" ]] && command -v docker &>/dev/null; then
    install -d -o homebot -g homebot -m 0750 /var/lib/homebot/catalog
    install -m 0755 -o root -g root "$DEST/deploy/homebot_catalog.py" /usr/local/sbin/homebot-catalog
    install -m 0755 -o root -g root "$DEST/deploy/autoconfig.py" /usr/local/sbin/homebot-autoconfig
    install -m 0644 "$DEST/deploy/homebot-catalog.service" /etc/systemd/system/homebot-catalog.service
    install -m 0644 "$DEST/deploy/homebot-catalog.path" /etc/systemd/system/homebot-catalog.path
    systemctl daemon-reload
    systemctl enable --now homebot-catalog.path >/dev/null 2>&1 && ok "каталог приложений включён" \
        || warn "не удалось включить homebot-catalog.path — каталог работать не будет"
elif [[ -f /etc/systemd/system/homebot-catalog.path ]]; then
    systemctl disable --now homebot-catalog.path >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/homebot-catalog.path /etc/systemd/system/homebot-catalog.service /usr/local/sbin/homebot-catalog
    systemctl daemon-reload
    ok "каталог приложений выключен"
fi

# ---------------------------------------------------------------------------- 5
step "Права на управление (polkit)"
units_js=""
for unit in $(env_get RESTART_SERVICES | tr ',' ' '); do
    [[ "$unit" =~ ^[A-Za-z0-9:_.@-]+$ ]] || { warn "пропущено подозрительное имя службы: $unit"; continue; }
    [[ "$unit" == *.* ]] || unit="$unit.service"
    units_js+="\"$unit\", "
done
power_js=""
if [[ "$(env_get POWER_ENABLED | tr '[:upper:]' '[:lower:]')" =~ ^(true|1|yes|on)$ ]]; then
    power_js='"org.freedesktop.login1.reboot", "org.freedesktop.login1.reboot-multiple-sessions", "org.freedesktop.login1.power-off", "org.freedesktop.login1.power-off-multiple-sessions", "org.freedesktop.login1.set-wall-message"'
fi
if [[ -d /etc/polkit-1/rules.d ]]; then
    cat > /etc/polkit-1/rules.d/50-homebot.rules <<EOF
// Создано deploy/update.sh — файл перезаписывается при каждом обновлении.
// Пользователю homebot разрешены только: restart служб из списка, перезапуск Docker-контейнеров
// через homebot-docker-restart@<имя>.service и (опционально) питание.
polkit.addRule(function (action, subject) {
    if (subject.user !== "homebot") {
        return polkit.Result.NOT_HANDLED;
    }
    var units = [${units_js%, }];
    if (action.id === "org.freedesktop.systemd1.manage-units" &&
        action.lookup("verb") === "restart" && units.indexOf(action.lookup("unit")) >= 0) {
        return polkit.Result.YES;
    }
    var dockerRestart = ${docker_restart_js};
    if (dockerRestart && action.id === "org.freedesktop.systemd1.manage-units" &&
        action.lookup("verb") === "start" &&
        /^homebot-docker-restart@[A-Za-z0-9][A-Za-z0-9_.-]*\.service\$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
    }
    var power = [${power_js}];
    if (power.indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
    return polkit.Result.NOT_HANDLED;
});
EOF
    chmod 0644 /etc/polkit-1/rules.d/50-homebot.rules
    ok "перезапуск разрешён для: ${units_js%, }"
    ok "питание из бота: $([[ -n "$power_js" ]] && echo включено || echo выключено)"
else
    warn "polkit не найден (/etc/polkit-1/rules.d) — перезапуск служб и питание из бота работать не будут"
fi

# ---------------------------------------------------------------------------- 6
step "Служба"
chown -R homebot:homebot "$DEST"
chmod 600 "$ENV_FILE"
install -m 0644 "$DEST/deploy/homebot.service" /etc/systemd/system/homebot.service
systemctl daemon-reload

if [[ $RUN_TESTS -eq 1 ]]; then
    "$DEST/.venv/bin/pip" install --quiet --disable-pip-version-check pytest
    (cd "$DEST" && sudo -u homebot "$DEST/.venv/bin/python" -m pytest -q -p no:cacheprovider) \
        || die "тесты не прошли — служба НЕ перезапущена, работает прежняя версия"
    ok "тесты пройдены"
fi

systemctl enable homebot >/dev/null 2>&1
systemctl restart homebot
sleep 4
if systemctl is-active --quiet homebot; then
    ok "HomeBot $VERSION запущен"
    journalctl -u homebot -n 6 --no-pager -o cat | sed 's/^/    /'
else
    journalctl -u homebot -n 30 --no-pager -o cat | sed 's/^/    /'
    die "служба не запустилась — журнал выше"
fi
