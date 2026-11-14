#!/usr/bin/env bash
# Сброс пароля веб-интерфейса qBittorrent и подключение его к UNI одной командой.
#
#   sudo bash tools/qbit-reset.sh                 # новый случайный пароль
#   sudo bash tools/qbit-reset.sh 'МойПароль'     # свой пароль
#
# Что делает (можно запускать повторно):
#   1. находит контейнер qBittorrent (или QBIT_CONTAINER=имя) и его qBittorrent.conf на диске;
#   2. останавливает контейнер, кладёт копию конфига рядом (qBittorrent.conf.bak-ДАТА) и записывает
#      новый пароль (WebUI\Password_PBKDF2, тот же формат, что пишет сам qBittorrent); логин — QBIT_USER или admin;
#   3. запускает контейнер (заодно снимается бан IP после неудачных входов) и проверяет вход;
#   4. только если вход прошёл — пишет QBIT_URL, QBIT_USERNAME, QBIT_PASSWORD в .env бота (с копией) и
#      перезапускает бота.
set -euo pipefail

say() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите через sudo"
command -v docker >/dev/null || die "docker не найден"
command -v python3 >/dev/null || die "python3 не найден"
command -v curl >/dev/null || die "curl не найден"
STAMP=$(date +%Y%m%d-%H%M%S)
USER_NAME=${QBIT_USER:-admin}
PASSWORD=${1:-$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(20)))')}
[[ ${#PASSWORD} -ge 6 ]] || die "пароль короче 6 символов — qBittorrent такой не примет"

# ---------------------------------------------------------------- 1. контейнер и конфиг
NAME=${QBIT_CONTAINER:-}
if [[ -z "$NAME" ]]; then
    NAME=$(docker ps -a --format '{{.Names}} {{.Image}}' | awk 'tolower($2) ~ /qbittorrent/ {print $1; exit}')
fi
[[ -n "$NAME" ]] || die "контейнер qBittorrent не найден; укажите: sudo QBIT_CONTAINER=имя bash $0"
ok "контейнер: $NAME"

CONF=""
while IFS='|' read -r src dst; do
    [[ -n "$src" && -d "$src" ]] || continue
    case "$dst" in *config*|*Config*) ;; *) continue ;; esac
    found=$(find "$src" -maxdepth 4 -name qBittorrent.conf -type f 2>/dev/null | head -n1 || true)
    if [[ -n "$found" ]]; then CONF=$found; break; fi
done < <(docker inspect -f '{{range .Mounts}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' "$NAME")
[[ -n "$CONF" ]] || die "не нашёл qBittorrent.conf в папках конфигурации контейнера $NAME"
ok "конфиг: $CONF"

# ---------------------------------------------------------------- 2. .env бота и адрес
ENV_FILE=${HOMEBOT_ENV:-}
if [[ -z "$ENV_FILE" ]] && command -v systemctl >/dev/null; then
    ENV_FILE=$(systemctl show -p EnvironmentFiles homebot 2>/dev/null | sed -n 's/^EnvironmentFiles=\([^ ]*\).*/\1/p' | head -n1 || true)
fi
[[ -n "$ENV_FILE" && -f "$ENV_FILE" ]] || ENV_FILE=/opt/homebot/.env
[[ -f "$ENV_FILE" ]] || die "не нашёл .env бота; укажите: sudo HOMEBOT_ENV=/путь/.env bash $0"
ok ".env бота: $ENV_FILE"

URL=${QBIT_URL:-$(sed -n "s/^QBIT_URL=['\"]\{0,1\}\([^'\"]*\).*/\1/p" "$ENV_FILE" | tail -n1 || true)}
if [[ -z "$URL" ]]; then
    port=$(docker port "$NAME" 8080/tcp 2>/dev/null | head -n1 | sed 's/.*://' || true)
    URL="http://127.0.0.1:${port:-8080}"
fi
URL=${URL%/}
ok "адрес qBittorrent: $URL"

# ---------------------------------------------------------------- 3. новый пароль
say "останавливаю $NAME (qBittorrent перезаписывает конфиг при выходе, поэтому правим его остановленным)"
docker stop "$NAME" >/dev/null
cp -a "$CONF" "$CONF.bak-$STAMP"
ok "копия конфига: $CONF.bak-$STAMP"

QB_USER="$USER_NAME" QB_PASS="$PASSWORD" python3 - "$CONF" <<'PY'
import base64, hashlib, os, re, sys

path = sys.argv[1]
salt = os.urandom(16)
digest = hashlib.pbkdf2_hmac("sha512", os.environ["QB_PASS"].encode(), salt, 100_000, 64)
value = '"@ByteArray(' + base64.b64encode(salt).decode() + ":" + base64.b64encode(digest).decode() + ')"'
want = {"WebUI\\Password_PBKDF2": value, "WebUI\\Username": os.environ["QB_USER"]}
drop = {"WebUI\\Password_ha1"}  # старый формат пароля — убираем, чтобы не мешал

with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()
out, section, done, pref_end = [], "", set(), None
for line in lines:
    m = re.match(r"^\[(.+)\]\s*$", line)
    if m:
        if section == "Preferences":
            pref_end = len(out)
        section = m.group(1)
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if section == "Preferences" and key in drop:
        continue
    if section == "Preferences" and key in want:
        out.append(f"{key}={want[key]}")
        done.add(key)
        continue
    out.append(line)
if section == "Preferences":
    pref_end = len(out)
missing = [f"{k}={v}" for k, v in want.items() if k not in done]
if missing:
    if pref_end is None:
        out += ["", "[Preferences]", *missing]
    else:
        while pref_end > 0 and not out[pref_end - 1].strip():  # перед пустой строкой между секциями
            pref_end -= 1
        out[pref_end:pref_end] = missing
with open(path, "r+", encoding="utf-8") as fh:  # на месте: владелец и права файла не меняются
    fh.write("\n".join(out) + "\n")
    fh.truncate()
PY
ok "пароль записан (логин: $USER_NAME)"

say "запускаю $NAME"
docker start "$NAME" >/dev/null

# ---------------------------------------------------------------- 4. проверка входа
code=""
for _ in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H "Referer: $URL" \
        --data-urlencode "username=$USER_NAME" --data-urlencode "password=$PASSWORD" "$URL/api/v2/auth/login" || true)
    [[ "$code" == "200" || "$code" == "204" ]] && break
    sleep 2
done
if [[ "$code" != "200" && "$code" != "204" ]]; then
    die "вход не прошёл (ответ $code). .env бота не менял. Вернуть старый конфиг: docker stop $NAME && cp -a '$CONF.bak-$STAMP' '$CONF' && docker start $NAME"
fi
ok "вход в qBittorrent работает"

# ---------------------------------------------------------------- 5. .env и перезапуск бота
cp -a "$ENV_FILE" "$ENV_FILE.bak-$STAMP"
ok "копия .env: $ENV_FILE.bak-$STAMP"
Q_URL="$URL" Q_USER="$USER_NAME" Q_PASS="$PASSWORD" python3 - "$ENV_FILE" <<'PY'
import os, re, sys

path = sys.argv[1]
want = {"QBIT_URL": os.environ["Q_URL"], "QBIT_USERNAME": os.environ["Q_USER"], "QBIT_PASSWORD": os.environ["Q_PASS"]}


def quote(v: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", v):
        return v
    return "'" + v + "'" if "'" not in v else '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()
out, seen = [], set()
for line in lines:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
    key = m.group(1) if m else ""
    if key in want:
        if key not in seen:
            out.append(f"{key}={quote(want[key])}")
            seen.add(key)
        continue
    out.append(line)
out += [f"{k}={quote(v)}" for k, v in want.items() if k not in seen]
with open(path, "r+", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
    fh.truncate()
PY
ok ".env: QBIT_URL, QBIT_USERNAME, QBIT_PASSWORD обновлены"

if command -v systemctl >/dev/null && systemctl cat homebot >/dev/null 2>&1; then
    systemctl restart homebot && ok "бот перезапущен"
elif docker inspect homebot >/dev/null 2>&1; then
    docker restart homebot >/dev/null && ok "контейнер бота перезапущен"
else
    say "бота перезапустите сами"
fi

printf '\n\033[1;32mГотово.\033[0m Вход в веб-интерфейс qBittorrent (%s):\n  логин:  %s\n  пароль: %s\n' "$URL" "$USER_NAME" "$PASSWORD"
printf 'Сохраните пароль. В боте проверьте: /doctor\n'
