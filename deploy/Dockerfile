# syntax=docker/dockerfile:1
# Образ приложения: один на контейнеры app и worker (documentation/ARCHITECTURE.md, раздел 1).
# Собирается из корня репозитория: docker compose build (контекст — корень, см. docker-compose.yml).

# --- 1. Сборка интерфейса ---------------------------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Codex CLI (только с INSTALL_CODEX=true) -------------------------------------------
# Подключение «Codex CLI» по подписке ChatGPT; INSTALL_CODEX приходит из .env через
# docker-compose.yml или флагом --build-arg. npm-пакет содержит готовый
# статический бинарник для Linux (musl): копируется только он, Node в образ приложения не попадает.
FROM node:24-alpine AS codex
ARG INSTALL_CODEX=false
ARG CODEX_VERSION=0.160.0
RUN mkdir -p /opt/codex \
 && if [ "$INSTALL_CODEX" = "true" ]; then \
      npm install -g --no-audit --no-fund "@openai/codex@${CODEX_VERSION}" \
      && binary="$(find "$(npm root -g)/@openai" -type f -path '*/vendor/*-unknown-linux-musl/bin/codex' | head -n 1)" \
      && test -n "$binary" \
      && cp "$binary" /opt/codex/codex \
      && chmod 0755 /opt/codex/codex; \
    fi

# --- 3. Приложение ----------------------------------------------------------------------
FROM python:3.12-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATIC_DIR=/app/static \
    DATA_DIR=/data

# LibreOffice (только Writer, без интерфейса) — перевод .doc в .docx и сборка PDF протокола.
# Tesseract с русским и английским — распознавание PDF без текстового слоя.
# fonts-liberation — метрически совместимая замена Times New Roman и Arial.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui \
      tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
      fonts-liberation fonts-dejavu-core fontconfig \
      tini \
 && rm -rf /var/lib/apt/lists/*

# Дополнительные шрифты с лицензией, разрешающей распространение (сейчас папка пуста: Jost не нужен).
COPY deploy/fonts/ /usr/local/share/fonts/contract-review/
RUN fc-cache -f >/dev/null

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown app:app /data

WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-dev.txt ./
# INSTALL_DEV=true добавляет pytest и ruff: тогда тесты можно запускать в контейнере с PostgreSQL.
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; else pip install -r requirements.txt; fi

COPY backend/ ./
COPY --from=frontend /frontend/dist /app/static
# Пустая папка, если Codex не ставили: тогда подключение Codex сообщит, что CLI не установлен.
COPY --from=codex /opt/codex/ /usr/local/bin/
COPY deploy/entrypoint.sh /app/entrypoint.sh
# Защита от CRLF, если файл попал в контекст сборки с Windows-переводами строк.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 0755 /app/entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["tini", "--", "/app/entrypoint.sh"]
CMD ["app"]
