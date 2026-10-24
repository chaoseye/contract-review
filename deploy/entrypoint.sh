#!/bin/sh
# Точка входа контейнера.
#   app     — дождаться базы, применить миграции, создать первого администратора, запустить API
#   worker  — дождаться базы, запустить фоновый процесс
#   другое  — выполнить как команду (например: python -m app.cli create-user --login ivanov)
set -eu

case "${1:-app}" in
  app)
    python -m app.cli wait-db --timeout 120
    python -m app.cli migrate
    python -m app.cli bootstrap
    # Адрес клиента за обратным прокси (Caddy) uvicorn берёт из X-Forwarded-For, только если запрос пришёл
    # с адреса из FORWARDED_ALLOW_IPS (по умолчанию 127.0.0.1): по адресу клиента ограничивается подбор
    # пароля. Заголовок Server с названием сервера не отдаётся.
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-server-header
    ;;
  worker)
    python -m app.cli wait-db --timeout 120
    exec python -m app.jobs.worker
    ;;
  *)
    exec "$@"
    ;;
esac
