#!/usr/bin/env bash
# Сигналы о работе contract-review на сервере в Healthchecks.io (documentation/DEPLOYMENT.md, раздел 14).
#
#   contract-review-monitor install   один раз: поставить себя, таймер проверки сайта и ночную копию через себя
#   contract-review-monitor site      проверить сайт и отправить сигнал (таймер systemd, раз в 5 минут)
#   contract-review-monitor backup    сделать ночную копию и отправить сигнал с её итогом (cron, 03:00)
#   contract-review-monitor test      отправить пробный сигнал сбоя, чтобы проверить, что письмо доходит
#
# Адреса — в /etc/contract-review-monitor.conf (права 600): SITE_URL — /api/health сервера, SITE_PING и BACKUP_PING —
# адреса проверок contract-review-site и contract-review-backup в Healthchecks.io. Скрипт лежит на незашифрованной части диска
# и работает, пока диск закрыт: тогда он об этом и сообщает. Договоров и других данных в сигналах нет.
set -euo pipefail

# CONTRACT_REVIEW_MONITOR_ROOT — каталог вместо корня файловой системы: только для проверки install.
ROOT=${CONTRACT_REVIEW_MONITOR_ROOT:-}
CONF=${CONTRACT_REVIEW_MONITOR_CONF:-$ROOT/etc/contract-review-monitor.conf}
MOUNT=${CONTRACT_REVIEW_DISK_MOUNT:-/srv/contract-review}
APP_DIR=${CONTRACT_REVIEW_MONITOR_APP:-$MOUNT/app}
BACKUP_LOG=${CONTRACT_REVIEW_MONITOR_BACKUP_LOG:-/var/log/contract-review-backup.log}
KEEP=7
BIN=/usr/local/sbin/contract-review-monitor
UNIT=contract-review-monitor
CRON_FILE=$ROOT/etc/cron.d/contract-review-backup

fail() {
    echo "Ошибка: $*" >&2
    exit 1
}

need_root() {
    # При проверке install (CONTRACT_REVIEW_MONITOR_ROOT) скрипт пишет в свой каталог и root не нужен.
    [[ -n $ROOT || $EUID -eq 0 ]] || fail "запустите от root: sudo contract-review-monitor $1"
}

setting() {
    # Значение КЛЮЧ=… из файла настроек. Файл не исполняется; последняя строка с ключом главнее; перевод
    # строки Windows и кавычки вокруг значения отбрасываются.
    [[ -r $CONF ]] || return 0
    sed -n "s/^[[:space:]]*$1=//p" "$CONF" | tail -n 1 | tr -d '\r' |
        sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

need_url() {
    local value
    [[ -r $CONF ]] || fail "нет файла настроек $CONF: запустите contract-review-monitor install (DEPLOYMENT.md, раздел 14)"
    value=$(setting "$1")
    [[ $value =~ ^https?://[^[:space:]]+$ ]] ||
        fail "в $CONF не задан или неверен $1: нужен адрес вида https://… (DEPLOYMENT.md, раздел 14)"
    printf '%s' "${value%/}"
}

send_signal() {
    # Сигнал в Healthchecks.io. $1 — адрес с окончанием (пусто — «работает», /fail, /log, /start, /<код
    # выхода>), $2 — текст сигнала, $3 — что это, для сообщения об ошибке. Адрес в сообщения не попадает:
    # по нему можно прислать сигнал, это почти пароль. Неотправленный сигнал не останавливает скрипт —
    # без сигналов Healthchecks.io сам сообщит, что сервер молчит.
    curl -fsS -m 10 --retry 3 --retry-delay 5 -o /dev/null --data-raw "$2" "$1" 2>/dev/null ||
        echo "Внимание: сигнал «$3» не отправлен в Healthchecks.io" >&2
}

disk_open() {
    mountpoint -q "$MOUNT"
}

cmd_site() {
    local ping_url site_url answer code body
    ping_url=$(need_url SITE_PING)
    site_url=$(need_url SITE_URL)
    if ! disk_open; then
        # Само не пройдёт: сбой сразу, письмо придёт через несколько минут после перезагрузки.
        echo "Диск закрыт: сервер перезагружался, система ждёт contract-review-disk unlock."
        send_signal "$ping_url/fail" "Диск закрыт: сервер перезагружался, система ждёт contract-review-disk unlock." "сайт"
        return
    fi
    # Остальное может быть минутной заминкой, например сразу после unlock: только запись в журнал проверки.
    # Если не пройдёт, сигналов «работает» не будет, и Healthchecks.io сообщит о сбое после запаса времени.
    if ! systemctl is-active -q docker.service; then
        echo "Docker не запущен."
        send_signal "$ping_url/log" "Диск открыт, но Docker не запущен: contract-review-disk unlock или systemctl start docker." "сайт"
        return
    fi
    # Сертификат не проверяется: по IP он выпущен внутренним центром Caddy (DEPLOYMENT.md, раздел 3.4).
    answer=$(curl -sk -m 15 -w '\n%{http_code}' "$site_url" 2>/dev/null || true)
    code=${answer##*$'\n'}
    body=${answer%$'\n'*}
    if [[ $code == 200 && $body == *'"ok"'* ]]; then
        send_signal "$ping_url" "ok" "сайт"
    else
        echo "Сайт не отвечает: код ${code:-нет ответа}."
        send_signal "$ping_url/log" "Сайт не отвечает: $site_url — код ${code:-нет ответа}." "сайт"
    fi
}

cmd_backup() {
    local ping_url status=0 output summary
    ping_url=$(need_url BACKUP_PING)
    send_signal "$ping_url/start" "" "копия"
    printf '== %s\n' "$(date '+%F %T %Z')" >>"$BACKUP_LOG"
    if ! disk_open; then
        echo "Копия не сделана: диск закрыт, сервер ждёт contract-review-disk unlock." | tee -a "$BACKUP_LOG"
        send_signal "$ping_url/fail" "Копия не сделана: диск закрыт, сервер ждёт contract-review-disk unlock." "копия"
        return 1
    fi
    output=$(cd "$APP_DIR" && python3 deploy/scripts/backup.py --keep "$KEEP" 2>&1) || status=$?
    printf '%s\n' "$output" >>"$BACKUP_LOG"
    # В сигнал — только итог, без строк с командами: в них открытый ключ age, наружу он не нужен.
    summary=$(printf '%s\n' "$output" | { grep -v '^\$ ' || true; } | tail -n 10)
    send_signal "$ping_url/$status" "$summary" "копия"
    return "$status"
}

cmd_test() {
    local ping_url
    ping_url=$(need_url SITE_PING)
    send_signal "$ping_url/fail" "Проверка уведомлений contract-review-monitor: это не сбой. Следующая проверка сайта вернёт «работает»." "проверка"
    echo "Отправлен пробный сигнал сбоя: проверьте почту. Не позже чем через 5 минут проверка сайта вернёт «работает»."
}

cmd_install() {
    need_root install
    local unit_dir=$ROOT/etc/systemd/system
    command -v curl >/dev/null || fail "нет команды curl: apt install curl"
    if [[ ! -e $CONF ]]; then
        mkdir -p "$(dirname "$CONF")"
        (
            umask 077
            printf '%s\n' \
                "# contract-review-monitor: адрес проверки сайта и адреса сигналов Healthchecks.io (DEPLOYMENT.md, раздел 14)." \
                "# SITE_URL — /api/health так, как сайт открывают пользователи, например https://203.0.113.10/api/health." \
                "SITE_URL=" \
                "# Адреса проверок contract-review-site и contract-review-backup в Healthchecks.io: https://hc-ping.com/…" \
                "SITE_PING=" \
                "BACKUP_PING=" >"$CONF"
        )
        fail "создан $CONF: впишите адреса и запустите install ещё раз"
    fi
    chmod 600 "$CONF"
    need_url SITE_URL >/dev/null
    need_url SITE_PING >/dev/null
    need_url BACKUP_PING >/dev/null

    if [[ $(realpath "$0") != "$(realpath -m "$ROOT$BIN")" ]]; then
        install -D -m 755 "$0" "$ROOT$BIN"
    fi
    mkdir -p "$unit_dir" "$(dirname "$CRON_FILE")"
    printf '%s\n' \
        "[Unit]" \
        "Description=contract-review: проверка сайта и сигнал в Healthchecks.io" \
        "Wants=network-online.target" \
        "After=network-online.target" \
        "" \
        "[Service]" \
        "Type=oneshot" \
        "ExecStart=$BIN site" \
        "TimeoutStartSec=120" >"$unit_dir/$UNIT.service"
    # Первая проверка — через 3 минуты после загрузки: если диск закрыт, письмо придёт сразу за ней.
    printf '%s\n' \
        "[Unit]" \
        "Description=contract-review: проверка сайта раз в 5 минут" \
        "" \
        "[Timer]" \
        "OnBootSec=3min" \
        "OnUnitActiveSec=5min" \
        "AccuracySec=30s" \
        "" \
        "[Install]" \
        "WantedBy=timers.target" >"$unit_dir/$UNIT.timer"
    # Ночную копию теперь запускает этот скрипт: он же сообщает её итог (раньше строка cron звала backup.py).
    printf '%s\n' \
        "# Ночная зашифрованная копия contract-review (DEPLOYMENT.md, разделы 5 и 14). Итог — сигналом в Healthchecks.io." \
        "0 3 * * * root $BIN backup" >"$CRON_FILE"
    chmod 644 "$CRON_FILE"
    systemctl daemon-reload
    systemctl enable --now "$UNIT.timer"
    echo "Установлено: $BIN, таймер $UNIT.timer (раз в 5 минут), ночная копия — $CRON_FILE."
    cmd_site
    echo "Отправлен первый сигнал проверки сайта. Проверить, что письма доходят: contract-review-monitor test"
}

main() {
    case ${1:-} in
    install) cmd_install ;;
    site) cmd_site ;;
    backup) cmd_backup ;;
    test) cmd_test ;;
    *)
        sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
    esac
}

# Подключённый через source (проверка скрипта) — только функции, без запуска команды.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
