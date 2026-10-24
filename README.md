# contract-review

Веб-сервис для проверки договоров поставки с помощью ИИ и выпуска протокола разногласий.

1. Менеджер загружает договор, который прислал покупатель.
2. ИИ находит риски для поставщика, объясняет их простыми словами и предлагает новые редакции пунктов.
3. Одной кнопкой система собирает из результата протокол разногласий в Word и PDF — с подписью и печатью или без них.

## Возможности

- чтение docx, doc, rtf, PDF и сканов (распознавание Tesseract) с разметкой на пункты и восстановлением автонумерации Word;
- анализ по промпту администратора: строгая схема ответа, сверка цитат и номеров пунктов с текстом договора, переспрос модели, если ответ не по схеме;
- подключения ИИ: API, совместимый с OpenAI (RouterAI, OpenRouter, OpenAI, Ollama и другие), API Anthropic и Codex CLI по подписке ChatGPT;
- сверка реквизитов вашей организации в договоре и определение вашей стороны — без ИИ;
- протокол разногласий в docx и PDF: шаблон текстов, колонки, подпись и печать картинками;
- интерфейс менеджера по шагам «Анализ → Риски → Протокол»; с телефона — тот же интерфейс, перестроенный под узкий экран;
- админка: пользователи и роли, промпт с версиями, сравнением и прогоном черновика, подключения ИИ и вход в Codex, данные организации, шаблон протокола, лимиты, журнал действий, перенос настроек файлом;
- договоры не хранятся дольше срока проверки (по умолчанию 24 часа), а их тексты не попадают в логи и резервные копии; защита от CSRF, ограничение попыток входа, заголовки безопасности, HTTPS через Caddy; для сервера — шифрование копий (age) и диска (LUKS).

Стек: FastAPI, SQLAlchemy, Alembic, PostgreSQL (SQLite для разработки), React 19, TypeScript, Vite, Ant Design, Docker Compose, LibreOffice, Tesseract.

## Документация

| Файл | О чём |
|---|---|
| [documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md) | компоненты, обработка договора, протокол, подключение ИИ, данные, безопасность |
| [documentation/DATA_HANDLING.md](documentation/DATA_HANDLING.md) | куда уходит договор, где и сколько хранятся данные |
| [documentation/PROTOCOL_FORMAT.md](documentation/PROTOCOL_FORMAT.md) | оформление протокола разногласий: страница, таблица, подписи, тексты |
| [documentation/DEPLOYMENT.md](documentation/DEPLOYMENT.md) | установка, HTTPS, обновление, резервные копии, перенос, диагностика |
| [documentation/USER_GUIDE.md](documentation/USER_GUIDE.md) | инструкция менеджеру и администратору |

## Запуск в Docker

Нужны Docker Desktop (Windows, macOS) или Docker Engine с плагином compose (Linux) и Python 3 для скрипта настройки.

1. Создайте `.env` со случайными секретами:

   ```bash
   python deploy/scripts/init_env.py
   ```

2. Запустите систему. Первая сборка образа занимает несколько минут: в образ ставятся LibreOffice и Tesseract.

   ```bash
   docker compose up -d --build
   ```

3. Откройте http://localhost:8080 и войдите. Логин — `INITIAL_ADMIN_LOGIN` из `.env` (по умолчанию `admin`), пароль — `INITIAL_ADMIN_PASSWORD` оттуда же.

Состояние и логи:

```bash
docker compose ps
```

```bash
docker compose logs -f app worker
```

Остановить систему (данные сохранятся в томах Docker):

```bash
docker compose down
```

Сервер с HTTPS, локальная сеть, обновление, резервные копии и перенос на другую машину — в [documentation/DEPLOYMENT.md](documentation/DEPLOYMENT.md). Резервная копия без договоров:

```bash
python deploy/scripts/backup.py
```

### Тесты в Docker

Тесты, которым нужны LibreOffice, Tesseract и PostgreSQL, локально пропускаются, а в Docker проходят. Нужен образ с инструментами разработки и отдельная база `contract_review_test`. Тест очереди пересоздаёт её схему, а на базу без `_test` в имени не запустится.

В контейнере пропускаются тесты скриптов копирования и скрипта сигналов `contract-review-monitor.sh` — им нужна папка `deploy/`, а её нет в образе, — и два теста работы без LibreOffice и Tesseract.

```bash
docker compose build --build-arg INSTALL_DEV=true
```

```bash
docker compose up -d db
```

Базу создают один раз:

```bash
docker compose exec db createdb -U contract_review contract_review_test
```

```bash
docker compose run --rm --no-deps app sh -c 'TEST_POSTGRES_URL="${DATABASE_URL%/*}/contract_review_test" python -m pytest -p no:cacheprovider -rs'
```

Права файлов копий (600) и скрипт сигналов `deploy/server/contract-review-monitor.sh` проверяются только в Linux — в том же образе с примонтированным репозиторием:

```bash
docker compose run --rm --no-deps -v "${PWD}:/repo" -w /repo/backend app python -m pytest -p no:cacheprovider tests/test_backup_scripts.py tests/test_server_monitor.py
```

Образ с инструментами разработки заменяет обычный: с ним запустятся `app` и `worker` после следующего `docker compose up -d` или восстановления копии. После тестов соберите обычный образ:

```bash
docker compose build
```

```bash
docker compose up -d
```

Разбор договора для ручной сверки. Папка с договором подключается только для чтения и в образ не попадает:

```bash
docker compose run --rm --no-deps -v ./contracts:/docs:ro app python -m app.cli extract "/docs/договор поставки.pdf" --width 90
```

Протокол в Word и PDF через LibreOffice, как в продукте. Данные протокола кладутся в `data/protocol/data.json` (папка `data/` не попадает в git), туда же сохраняются результаты. В Linux у пользователя контейнера (uid 10001) должно быть право записи в эту папку:

```bash
docker compose run --rm --no-deps -v ./data/protocol:/out app python -m app.cli protocol /out/data.json --out /out/protocol.docx --pdf /out/protocol.pdf
```

### Администрирование

Администратор открывает раздел «Администрирование» вкладкой в шапке (http://localhost:8080/admin). Первый администратор создаётся из `INITIAL_ADMIN_LOGIN` и `INITIAL_ADMIN_PASSWORD` при первом запуске. Порядок настройки новой установки:
1. «Организация» — наименования, подписант, реквизиты для сверки, подпись и печать (PNG с прозрачным фоном или скан на белом фоне). Значения хранятся только в базе.
2. «Подключения ИИ» — подключение с ключом, затем «Проверить связь».
3. «Пользователи» — учётные записи менеджеров.
4. «Промпт» — дописать позиции своей компании: сроки, пределы неустоек, подсудность. При необходимости — «Шаблон протокола» и «Настройки».

Перенести настройки на другую машину можно файлом: «Настройки и перенос» → «Выгрузить настройки», на новой машине — «Загрузить настройки». Ключи API в файл не входят, их вводят заново. Подпись и печать в файле есть — храните его бережно.

Всё это можно сделать и командами — ниже. В Docker перед командой пишется `docker compose exec app`, без Docker команды запускаются из папки `backend/` через `.venv\Scripts\python`.

### Пользователи из командной строки

Пароль запрашивается в консоли:

```bash
docker compose exec app python -m app.cli create-user --login ivanov --name "Иванов Иван" --role manager
```

```bash
docker compose exec app python -m app.cli set-password --login ivanov
```

### Данные организации

Без них протокол не собрать. Обычно их вносят в админке, но можно и командами:

```bash
docker compose exec app python -m app.cli org-set --full-name "Индивидуальный предприниматель …" --short-name "ИП …" --signer "И.О. Фамилия" --city "г. …"
```

Подпись и печать (PNG с прозрачностью) — из папки, подключённой только для чтения:

```bash
docker compose run --rm --no-deps -v ./contracts:/docs:ro app python -m app.cli org-asset signature "/docs/<подпись>.png"
```

Посмотреть, что внесено: `python -m app.cli org-show`. Реквизиты для сверки: `org-set --requisite inn=… --requisite email=…`; формат проверяется так же, как в админке.

### Подключения ИИ

Подключения настраиваются в админке (раздел «Подключения ИИ») или командами. Ключ API команда спрашивает в консоли или читает из stdin, аргументом его не передают. Для ключей нужен `ENCRYPTION_KEY` в `.env`: его создаёт `init_env.py`.

RouterAI. Галочка «Только серверы в РФ» по умолчанию выключена; если договоры нельзя отправлять зарубежным моделям, включите её флагом `--russia-only`:

```bash
docker compose exec app python -m app.cli llm-add RouterAI --preset routerai --model sber/gigachat-3-pro
```

Модели с ценами и проверка связи:

```bash
docker compose exec app python -m app.cli llm-models RouterAI --search gigachat
```

```bash
docker compose exec app python -m app.cli llm-test RouterAI
```

Остальные команды: `llm-list`, `llm-set`, `llm-activate`, `llm-delete`. Справка — `python -m app.cli llm-add --help`.

Модели Claude напрямую — подключение к API Anthropic, напрямую к OpenAI — `--preset openai`. Оба провайдера не обслуживают Россию: такие подключения работают, только если сервер стоит в стране, где провайдер работает. Цен в их API нет, поэтому стоимость не считается. У готового адреса Anthropic глубина рассуждений — `high`, поменять её можно параметром `--effort`:

```bash
docker compose exec app python -m app.cli llm-add Claude --preset anthropic --model claude-opus-5-5
```

После чтения документа анализ запускается сам, если есть активное подключение. Прогнать анализ файла из командной строки и сохранить результат (в нём цитаты договора — держите файл вне репозитория):

```bash
docker compose run --rm --no-deps -v ./data/protocol:/out app python -m app.cli analyze /out/contract.docx --out /out/result.json
```

Оценка по готовому протоколу разногласий к тому же договору, без нового запроса к модели. Ожидаемые строки протокола описываются JSON-файлом, формат — в `backend/app/analysis/evaluate.py`:

```bash
docker compose run --rm --no-deps -v ./data/protocol:/out app python -m app.cli evaluate /out/expected.json --answer /out/result.json
```

Codex CLI по подписке ChatGPT подходит, когда системой пользуется один менеджер: вход — в его аккаунт ChatGPT или в аккаунт компании, сервер — не в России. До входа владелец аккаунта меняет две настройки ChatGPT: отключает «Improve the model for everyone» ([DATA_HANDLING.md](documentation/DATA_HANDLING.md#4-подключения-ии-и-где-обрабатывается-запрос)) и в разделе «Безопасность» (Security) включает вход по коду устройства для Codex. В аккаунте компании на тарифе Business вход по коду включает администратор рабочего пространства. В контейнере песочница Codex не запускается, поэтому команды агента не выполняются — анализу они не нужны. Образ с Codex: задайте в `.env` `INSTALL_CODEX=true` — тогда Codex останется в образе и после обновлений — и пересоберите систему:

```bash
docker compose up -d --build
```

Вход — кнопкой «Войти по коду» в разделе «Подключения ИИ» (шаг 2 карточки «Анализ через Codex по подписке ChatGPT») или командой. Система покажет ссылку и код: откройте ссылку в браузере, войдите в свой аккаунт ChatGPT и введите код. Вход только сохраняет аккаунт на сервере, анализ пойдёт через Codex, когда появится подключение. Добавьте его — в админке это кнопка «Создать подключение Codex» в шаге 3 — и проверьте связь (шаг 4):

```bash
docker compose exec app python -m app.cli codex-login
```

```bash
docker compose exec app python -m app.cli llm-add Codex --kind codex_cli --timeout 900
```

```bash
docker compose exec app python -m app.cli llm-test Codex
```

Без Docker Codex ставится через `npm install -g @openai/codex@0.160.0`, дальше те же команды через `.venv\Scripts\python -m app.cli`.

## Разработка без Docker

Сервер работает на SQLite (`backend/data/dev.db`) и берёт настройки из того же `.env`. Команды для Windows; в Linux и macOS вместо `.venv\Scripts\python` пишется `.venv/bin/python`.

```powershell
cd backend && python -m venv .venv && .venv\Scripts\python -m pip install -r requirements-dev.txt
```

```powershell
backend\.venv\Scripts\python backend\run_dev.py
```

Сервер откроется на http://127.0.0.1:8000, worker запускается в том же процессе. Разбор файла без сервера: `backend\.venv\Scripts\python -m app.cli extract <файл>` из папки `backend/`. Если интерфейс собран (`npm run build` в `frontend/`), он открывается там же. Для работы над интерфейсом удобнее держать второй терминал с dev-сервером Vite: он открывается на http://localhost:5173 и проксирует `/api` на порт 8000.

```bash
cd frontend && npm install && npm run dev
```

### Протокол из командной строки

Протокол можно собрать из JSON (формат — [documentation/PROTOCOL_FORMAT.md](documentation/PROTOCOL_FORMAT.md), раздел 8). `--header`, `--signature` и `--stamp` подставляют картинки бланка, подписи и печати. Для `--pdf <файл>` нужен LibreOffice, поэтому PDF удобнее собирать в Docker (раздел «Тесты в Docker»). Команда для папки `backend/`:

```powershell
.venv\Scripts\python -m app.cli protocol data.json --out protocol.docx --signature подпись.png --stamp печать.png
```

Посмотреть docx глазами, как его увидит менеджер (Windows с установленным Word):

```powershell
powershell -File tools\docx_to_pdf_word.ps1 -Docx protocol.docx -Pdf protocol.pdf
```

### Проверки кода

```powershell
cd backend && .venv\Scripts\python -m pytest && .venv\Scripts\ruff check . && .venv\Scripts\ruff format --check .
```

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

Локальные тесты идут на SQLite. Миграции дополнительно проверяются на совместимость с PostgreSQL через генерацию SQL без подключения к базе.

## Структура

```
contract-review/
  README.md  LICENSE  .env.example  docker-compose.yml  .dockerignore  .gitignore  .gitattributes
  documentation/   документация
  backend/
    app/           FastAPI: api/, core/ (настройки, база, безопасность), models/, jobs/ (worker),
                   documents/ (чтение договоров), protocol/ (протокол разногласий), analysis/ (подключения ИИ),
                   cli.py
    migrations/    миграции Alembic
    tests/         тесты; договоры в них синтетические, реквизиты вымышленные
    run_dev.py     локальный запуск без Docker
  frontend/        React + TypeScript + Vite + Ant Design
  deploy/
    Dockerfile     сборка интерфейса и образ приложения (LibreOffice, Tesseract, шрифты, Codex по INSTALL_CODEX)
    entrypoint.sh  запуск app и worker в контейнере
    Caddyfile      HTTPS для профиля server
    fonts/         дополнительные шрифты для PDF
    scripts/       init_env.py — .env с секретами; backup.py и restore.py — резервные копии
    server/        contract-review-disk.sh — зашифрованный диск на арендованном сервере;
                   contract-review-monitor.sh — сигналы о работе сервера в Healthchecks.io
  tools/           инструменты разработчика: просмотр docx через Word
```

## Важно

- Настоящие договоры, реквизиты, подпись и печать не кладутся в репозиторий: папки `DOCS/`, `data/` и `backups/` исключены из git и из образа Docker. В тестах — только синтетические документы.
- Документация лежит в `documentation/`, а не в `docs/`: на Windows `docs` и `DOCS` — одна и та же папка.
- Договоры в системе не хранятся. Файл договора удаляется сразу после чтения, а вся проверка — через 24 часа после последнего действия с ней (срок меняется в админке). В резервные копии договоры не попадают. Копии шифруются ключом владельца, а на арендованном сервере данные можно держать на зашифрованном диске (documentation/DEPLOYMENT.md, разделы 5 и 11).

## Лицензия

MIT — см. [LICENSE](LICENSE).
