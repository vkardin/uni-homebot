# shellcheck shell=bash
# Общие функции установщика: пакетный менеджер и имена пакетов для apt / dnf / pacman / apk.
# Подключается из install.sh и update.sh:  source "$SRC/deploy/pkg.sh"

pkg_manager() {
    local m
    for m in apt-get dnf pacman apk; do
        command -v "$m" &>/dev/null && { echo "$m"; return 0; }
    done
    return 1
}
PKG_MGR="$(pkg_manager || true)"

# Общее имя пакета → имена в конкретном дистрибутиве (через пробел; пусто — пакета нет или не нужен).
pkg_names() {
    case "$PKG_MGR:$1" in
        apt-get:python)    echo "python3 python3-venv python3-pip" ;;
        dnf:python)        echo "python3 python3-pip" ;;
        pacman:python)     echo "python python-pip" ;;
        apk:python)        echo "python3 py3-pip" ;;
        dnf:ffmpeg)        echo "ffmpeg-free" ;;
        pacman:poppler-utils) echo "poppler" ;;
        apt-get:tesseract) echo "tesseract-ocr tesseract-ocr-rus tesseract-ocr-ukr" ;;
        dnf:tesseract)     echo "tesseract tesseract-langpack-rus tesseract-langpack-ukr" ;;
        pacman:tesseract)  echo "tesseract tesseract-data-eng tesseract-data-rus tesseract-data-ukr" ;;
        apk:tesseract)     echo "tesseract-ocr tesseract-ocr-data-rus tesseract-ocr-data-ukr" ;;
        apt-get:libreoffice) echo "libreoffice-writer libreoffice-calc libreoffice-impress" ;;
        dnf:libreoffice)   echo "libreoffice-writer libreoffice-calc libreoffice-impress" ;;
        pacman:libreoffice) echo "libreoffice-fresh" ;;
        apk:libreoffice)   echo "libreoffice" ;;
        *) echo "$1" ;;
    esac
}

# Команда, которую показать человеку, если поставить не удалось.
pkg_hint() {
    local names=() p
    for p in "$@"; do read -ra n <<<"$(pkg_names "$p")"; names+=("${n[@]}"); done
    case "$PKG_MGR" in
        apt-get) echo "sudo apt install ${names[*]}" ;;
        dnf)     echo "sudo dnf install ${names[*]}" ;;
        pacman)  echo "sudo pacman -S ${names[*]}" ;;
        apk)     echo "sudo apk add ${names[*]}" ;;
        *)       echo "установите вручную: ${names[*]}" ;;
    esac
}

# Ставит пакеты по общим именам. Код возврата 0 — всё поставилось.
_pkg_updated=0  # apt-get update — один раз за запуск
pkg_install() {
    local names=() p
    for p in "$@"; do read -ra n <<<"$(pkg_names "$p")"; names+=("${n[@]}"); done
    [[ ${#names[@]} -gt 0 ]] || return 0
    case "$PKG_MGR" in
        apt-get)
            if [[ $_pkg_updated -eq 0 ]]; then apt-get update -qq >/dev/null 2>&1 || true; _pkg_updated=1; fi
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${names[@]}" >/dev/null 2>&1 ;;
        dnf)
            dnf install -y -q "${names[@]}" >/dev/null 2>&1 ;;
        pacman)
            # Без -Sy, чтобы не делать частичное обновление системы; база пакетов пуста — тогда -Sy.
            pacman -S --noconfirm --needed "${names[@]}" >/dev/null 2>&1 \
                || pacman -Sy --noconfirm --needed "${names[@]}" >/dev/null 2>&1 ;;
        apk)
            apk add --no-cache -q "${names[@]}" >/dev/null 2>&1 ;;
        *)
            return 1 ;;
    esac
}

has_systemd() { [[ -d /run/systemd/system ]] && command -v systemctl &>/dev/null; }
