# HomeBot в Docker. Обычно собирается через compose.yml: sudo bash deploy/install.sh --docker
FROM python:3.12-slim

# Инструменты конвертера файлов. Всё необязательно: чего нет, бот не предлагает (/doctor).
# LibreOffice (~400 МБ) не ставится; нужен — соберите с --build-arg OFFICE=1.
ARG OFFICE=0
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg poppler-utils ghostscript tesseract-ocr tesseract-ocr-rus tesseract-ocr-ukr curl \
 && if [ "$OFFICE" = "1" ]; then apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress; fi \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin homebot \
 && install -d -o homebot -g homebot -m 0750 /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY homebot ./homebot
COPY pyproject.toml README.md .env.example ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATE_DIR=/data \
    MPLCONFIGDIR=/data/matplotlib \
    HOMEBOT_IN_CONTAINER=1

USER homebot
VOLUME ["/data"]
# umask 077: база и state.json только для владельца, как UMask=0077 в systemd-юните.
CMD ["sh", "-c", "umask 077 && exec python -m homebot"]
