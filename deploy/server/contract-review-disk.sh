#!/usr/bin/env bash
# Зашифрованный диск contract-review на сервере: LUKS2 в файле-контейнере (documentation/DEPLOYMENT.md, раздел 11).
#
#   contract-review-disk setup <размер>  один раз: создать диск (например, 40G) и перенести на него данные Docker
#   contract-review-disk unlock          открыть диск и запустить систему — после каждой перезагрузки сервера
#   contract-review-disk lock            остановить систему и закрыть диск
#   contract-review-disk status          открыт ли диск и запущен ли Docker
#
# Ключ — одна строка на стандартном вводе (setup, unlock). На сервере он не сохраняется, его хранит
# владелец. С компьютера владельца:  ssh <сервер> contract-review-disk unlock < contract-review-disk.key
#
# На диске — всё, что пишет система: база, файлы проверок, образы и журналы контейнеров (каталоги Docker
# и containerd подключаются с диска), а ещё код с .env и резервные копии (папка app) и swap — страховка от
# нехватки памяти. Пока диск закрыт, Docker не запускается.
set -euo pipefail

CONTAINER=${CONTRACT_REVIEW_DISK_FILE:-/srv/contract-review.luks}
MAPPER=${CONTRACT_REVIEW_DISK_NAME:-contract-review-data}
MOUNT=${CONTRACT_REVIEW_DISK_MOUNT:-/srv/contract-review}
APP_DIR=$MOUNT/app
# Swap — только внутри диска: страховка от нехватки памяти. Размер в МБ, 0 — без swap.
SWAPFILE=$MOUNT/swapfile
SWAP_MB=${CONTRACT_REVIEW_DISK_SWAP_MB:-2048}
# Каталоги, которые подключаются с диска (через пробел; переменная окружения — для проверки скрипта).
# shellcheck disable=SC2206
STATE_DIRS=(${CONTRACT_REVIEW_DISK_STATE_DIRS:-/var/lib/docker /var/lib/containerd})
SERVICES=(containerd.service docker.socket docker.service)
INSTALL_PATH=/usr/local/sbin/contract-review-disk
SYSCTL_FILE=/etc/sysctl.d/90-contract-review-disk.conf
FSTAB=/etc/fstab
MIN_KEY_LENGTH=32
# Запас свободного места системе сервера после создания диска: 2 ГБ, на маленьком диске — десятая часть.
SYSTEM_RESERVE=$((2 * 1024 ** 3))
KEY=""
LOOP=""

# Что успел сделать setup: при сбое откатывается ровно это.
STEP=""
CREATED_FILE=0
CREATED_MOUNT=0
ATTACHED=0
OPENED=0
MOUNTED=0
DOCKER_CHANGED=0
COMMITTED=0
MOVED_DIRS=()
ENABLED_BEFORE=()
ACTIVE_BEFORE=()

fail() {
    echo "Ошибка: $*" >&2
    exit 1
}

warn() {
    echo "Внимание: $*" >&2
}

need_root() {
    [[ $EUID -eq 0 ]] || fail "запустите от root: sudo contract-review-disk $*"
}

has_systemd() {
    command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]
}

human() {
    awk -v bytes="$1" 'BEGIN {
        if (bytes >= 1024 ^ 3) printf "%.1f ГБ", bytes / 1024 ^ 3; else printf "%.0f МБ", bytes / 1024 ^ 2
    }'
}

size_bytes() {
    # Размер вида 40G в байтах: M, G, T — степени 1024, как у fallocate.
    local number=${1%[MGT]}
    case ${1: -1} in
    M) echo $((number * 1024 ** 2)) ;;
    G) echo $((number * 1024 ** 3)) ;;
    T) echo $((number * 1024 ** 4)) ;;
    esac
}

read_key() {
    # Первая строка стандартного ввода. Файл ключа мог быть сохранён в Windows, поэтому отбрасываются метка
    # UTF-8 (BOM), перевод строки \r и пробелы по краям: в самом ключе их нет.
    local line=""
    if [[ -t 0 ]]; then
        # Запуск в терминале: ключ вставляют вручную, на экран он не выводится.
        IFS= read -rs -p "Ключ диска: " line || true
        echo
    else
        IFS= read -r line || true
    fi
    # Метка UTF-8; у файла в UTF-16 (перенаправление > в Windows PowerShell 5.1) — метка UTF-16, а нулевые
    # байты read пропускает сам.
    line=${line#$'\xEF\xBB\xBF'}
    line=${line#$'\xFF\xFE'}
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [[ ${#line} -ge $MIN_KEY_LENGTH ]] ||
        fail "ключ не получен: передайте файл ключа на стандартный ввод (contract-review-disk $1 < contract-review-disk.key)"
    KEY=$line
}

with_key() {
    # Ключ — через канал, без перевода строки: так он одинаков при создании и при открытии диска.
    # printf — встроенная команда bash, ключ не виден в списке процессов.
    printf '%s' "$KEY" | "$@"
}

is_open() {
    [[ -e /dev/mapper/$MAPPER ]]
}

container_loop() {
    # loop-устройство, через которое подключён файл диска; пусто, если не подключён.
    losetup --list --noheadings --output NAME --associated "$CONTAINER" 2>/dev/null | head -n 1
}

attach_loop() {
    # Файл диска подключается через loop-устройство с прямым вводом-выводом: зашифрованные страницы не
    # кешируются второй раз, а запись в swap на диске не требует памяти под кеш файла диска. Сектор loop — 4096
    #: с ним прямой ввод-вывод работает и на дисках с сектором 512, и на дисках 4Kn.
    LOOP=$(container_loop)
    if [[ -z $LOOP ]]; then
        LOOP=$(losetup --find --show --sector-size 4096 "$CONTAINER")
    fi
    losetup --direct-io=on "$LOOP" 2>/dev/null ||
        warn "у $LOOP не включился прямой ввод-вывод: файловая система под файлом диска его не поддерживает. Swap на диске включаться не будет"
}

loop_dio() {
    # 1 — у loop файла диска включён прямой ввод-вывод.
    losetup --list --noheadings --output DIO --associated "$CONTAINER" 2>/dev/null | head -n 1 | tr -d ' '
}

detach_loop() {
    local loop attempt
    loop=$(container_loop)
    if [[ -z $loop ]]; then
        return
    fi
    losetup --detach "$loop"
    # Если loop ещё держат (udev сразу после закрытия диска), ядро отключит его само чуть позже: дождаться.
    for attempt in {1..50}; do
        if [[ -z $(container_loop) ]]; then
            return
        fi
        sleep 0.1
    done
    warn "$loop ещё занят: ядро отключит его само, когда его отпустят"
}

swap_active() {
    grep -q "^$SWAPFILE[[:space:]]" /proc/swaps 2>/dev/null
}

create_swap() {
    # Файл swap без пропусков (dd, а не fallocate): такой swapon принимает на любом ядре.
    if ((SWAP_MB <= 0)); then
        return
    fi
    echo "Создаю swap на диске: $SWAP_MB МБ."
    : >"$SWAPFILE"
    chmod 600 "$SWAPFILE"
    dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAP_MB" status=none
    mkswap "$SWAPFILE" >/dev/null
}

enable_swap() {
    if [[ ! -f $SWAPFILE ]] || swap_active; then
        return
    fi
    if [[ $(loop_dio) != 1 ]]; then
        # Без прямого ввода-вывода запись в swap сама требует памяти и при её нехватке может зависнуть.
        warn "swap на диске не включён: у loop файла диска нет прямого ввода-вывода"
        return
    fi
    swapon "$SWAPFILE" 2>/dev/null ||
        warn "swap на диске не включился: система работает, но при нехватке памяти процессы будут падать"
}

disable_swap() {
    # До размонтирования: пока swap включён, диск не отключить.
    if [[ ! -f $SWAPFILE ]]; then
        return
    fi
    swapoff "$SWAPFILE" 2>/dev/null || true
    if swap_active; then
        fail "swap на диске не выключился: его страницам не хватает места в памяти. Закройте программы и повторите contract-review-disk lock"
    fi
}

stop_docker() {
    if has_systemd; then
        systemctl stop "${SERVICES[@]}" 2>/dev/null || true
    fi
}

start_docker() {
    if ! has_systemd || ! systemctl cat docker.service >/dev/null 2>&1; then
        echo "Docker не установлен или нет systemd: система не запускается."
        return
    fi
    systemctl start containerd.service docker.service
    if [[ -f $APP_DIR/docker-compose.yml ]]; then
        # Контейнеры с restart: unless-stopped поднимаются сами; up -d — на случай, если их остановили.
        (cd "$APP_DIR" && docker compose up -d)
    fi
}

mount_state_dirs() {
    local dir inner
    for dir in "${STATE_DIRS[@]}"; do
        inner=$MOUNT/$(basename "$dir")
        mkdir -p "$inner" "$dir"
        mountpoint -q "$dir" || mount --bind "$inner" "$dir"
    done
}

busy() {
    fail "$1 занят: закройте программы и терминалы в нём (кто его держит — fuser -vm $1) и повторите contract-review-disk lock"
}

unmount_all() {
    local index dir
    for ((index = ${#STATE_DIRS[@]} - 1; index >= 0; index--)); do
        dir=${STATE_DIRS[index]}
        if mountpoint -q "$dir"; then
            umount "$dir" || busy "$dir"
        fi
    done
    if mountpoint -q "$MOUNT"; then
        umount "$MOUNT" || busy "$MOUNT"
    fi
}

comment_swap_lines() {
    # /etc/fstab без строк swap: они закомментированы. Строка swap — по третьему полю, с отступом и без
    # поля опций тоже.
    awk '!/^[[:space:]]*#/ && $3 == "swap" { print "# выключено contract-review-disk: " $0; next } { print }' "$1"
}

has_swap_lines() {
    awk '!/^[[:space:]]*#/ && $3 == "swap" { found = 1 } END { exit !found }' "$1"
}

harden() {
    # Память процессов не должна попадать на незашифрованную часть диска: ни через swap, ни через дампы
    # памяти (в дампе LibreOffice, worker или всего ядра мог оказаться текст договора, а у ядра — и ключ).
    # Каждый шаг — по возможности: диск к этому времени уже создан, а не сделанное скрипт называет.
    local unit
    if [[ ${CONTRACT_REVIEW_DISK_NO_HARDEN:-} == 1 ]]; then
        # Только для проверки скрипта в контейнере: swap там — у всей виртуальной машины Docker.
        echo "Swap и дампы памяти не тронуты (CONTRACT_REVIEW_DISK_NO_HARDEN=1)."
        return
    fi
    swapoff -a || warn "swap не выключился: выключите его сами (swapoff -a) и проверьте swapon --show"
    # Строки fstab правятся, даже если swap уже выключен: иначе он вернётся после перезагрузки.
    if has_swap_lines "$FSTAB"; then
        if comment_swap_lines "$FSTAB" >"$FSTAB.contract-review-disk.new" && cp -p "$FSTAB" "$FSTAB.contract-review-disk.bak" &&
            mv "$FSTAB.contract-review-disk.new" "$FSTAB"; then
            echo "Строки swap в $FSTAB закомментированы (копия — $FSTAB.contract-review-disk.bak)."
        else
            warn "$FSTAB не изменён: закомментируйте строки swap сами"
        fi
    fi
    if has_systemd; then
        # Swap без fstab (раздел GPT, свои .swap-юниты) systemd включает через swap.target.
        systemctl mask swap.target >/dev/null || warn "swap.target не замаскирован: systemctl mask swap.target"
        # kdump при падении ядра пишет всю память в /var/crash, на незашифрованную часть.
        for unit in kdump-tools.service kdump.service; do
            if systemctl cat "$unit" >/dev/null 2>&1; then
                systemctl disable --now "$unit" >/dev/null 2>&1 && echo "Выключен $unit." ||
                    warn "$unit не выключен: systemctl disable --now $unit"
            fi
        done
        systemctl disable --now apport.service >/dev/null 2>&1 || true
    fi
    if [[ $(cat /sys/kernel/kexec_crash_loaded 2>/dev/null || echo 0) != 0 ]]; then
        warn "загружено ядро для дампа памяти (kdump): выгрузите его (kdump-config unload) и проверьте cat /sys/kernel/kexec_crash_loaded — должно быть 0"
    fi
    if mkdir -p "$(dirname "$SYSCTL_FILE")" &&
        printf '%s\n' "# contract-review-disk: дампы памяти не сохраняются." \
            "kernel.core_pattern=|/bin/false" "fs.suid_dumpable=0" \
            "# Swap на диске — страховка: ядро берётся за него, только когда памяти почти не осталось." \
            "vm.swappiness=10" >"$SYSCTL_FILE"; then
        sysctl -q -p "$SYSCTL_FILE" 2>/dev/null || echo "sysctl не применился сразу, применится после перезагрузки."
    else
        warn "$SYSCTL_FILE не записан: дампы памяти не выключены, swappiness не задан"
    fi
}

check_setup() {
    # Всё, из-за чего setup может не дойти до конца, проверяется до первого изменения.
    local size=$1 need avail total reserve used=0 swap_bytes=$((SWAP_MB * 1024 ** 2)) dir tool
    [[ $size =~ ^[1-9][0-9]*[MGT]$ ]] || fail "укажите размер диска, например: contract-review-disk setup 40G"
    for tool in cryptsetup mkfs.ext4 chattr fallocate; do
        command -v "$tool" >/dev/null || fail "нет команды $tool: apt install cryptsetup e2fsprogs util-linux"
    done
    if [[ -e $CONTAINER ]]; then
        if cryptsetup isLuks "$CONTAINER" 2>/dev/null; then
            fail "$CONTAINER уже есть: диск создан раньше. Открыть его — contract-review-disk unlock"
        fi
        fail "$CONTAINER есть, но это не диск LUKS: прошлый setup, похоже, оборвался. Удалите файл (rm $CONTAINER) и повторите"
    fi
    if [[ -e $MOUNT && -n $(ls -A "$MOUNT" 2>/dev/null) ]]; then
        fail "в $MOUNT уже есть файлы: под диском они остались бы на незашифрованной части. Перенесите их и повторите; код ставится в $APP_DIR после setup"
    fi
    need=$(size_bytes "$size")
    avail=$(df --output=avail -B1 "$(dirname "$CONTAINER")" | tail -1 | tr -d ' ')
    total=$(df --output=size -B1 "$(dirname "$CONTAINER")" | tail -1 | tr -d ' ')
    reserve=$((total / 10 < SYSTEM_RESERVE ? total / 10 : SYSTEM_RESERVE))
    ((avail >= need + reserve)) ||
        fail "свободно $(human "$avail"): диску $size нужно $(human "$need") и ещё $(human "$reserve") системе сервера. Укажите размер меньше"
    for dir in "${STATE_DIRS[@]}"; do
        if [[ -d $dir ]]; then
            used=$((used + $(du -sb "$dir" | cut -f1)))
        fi
    done
    # Часть диска ext4 занимают служебные данные и запас для root: прежним данным и swap — не больше 80 %.
    (((used + swap_bytes) * 10 <= need * 8)) ||
        fail "данные Docker ($(human "$used")) и swap ($(human "$swap_bytes")) не поместятся на диск $size: укажите размер больше"
}

rollback_setup() {
    # Выход из setup. Если он не дошёл до конца — вернуть всё, что успел сделать, как было.
    local status=$? dir inner item name stranded=()
    trap - EXIT
    if ((status == 0)); then
        return
    fi
    if ((COMMITTED)); then
        echo "Диск создан и данные перенесены, но setup не закончен (шаг: $STEP). Исправьте причину и выполните contract-review-disk unlock (если команды ещё нет — bash $0 unlock)." >&2
        exit "$status"
    fi
    [[ -n $STEP ]] || exit "$status"
    set +e
    echo "setup не выполнен (шаг: $STEP). Возвращаю всё как было." >&2
    for dir in "${STATE_DIRS[@]}"; do
        mountpoint -q "$dir" && umount "$dir"
    done
    for dir in "${MOVED_DIRS[@]}"; do
        inner=$MOUNT/$(basename "$dir")
        chattr -i "$dir" 2>/dev/null
        # Перенесённое целиком — обратно. Если оригинал остался на месте, на диске — недописанная копия.
        while IFS= read -r -d '' item; do
            name=$(basename "$item")
            if [[ -e $dir/$name || -L $dir/$name ]]; then
                rm -rf "$item"
            else
                mv "$item" "$dir/"
            fi
        done < <(find "$inner" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
        if [[ -n $(find "$inner" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) ]]; then
            stranded+=("$inner")
        fi
    done
    if ((${#stranded[@]})); then
        # Закрыть и удалить диск сейчас — значит потерять то, что на нём осталось.
        echo "Ошибка: не все данные Docker вернулись на место, часть осталась на диске: ${stranded[*]}." >&2
        echo "Диск оставлен открытым в $MOUNT, Docker не запущен. Перенесите эти данные обратно вручную (mv), затем выполните bash $0 lock и удалите $CONTAINER." >&2
        exit "$status"
    fi
    ((MOUNTED)) && umount "$MOUNT"
    ((OPENED)) && cryptsetup close "$MAPPER"
    ((ATTACHED)) && detach_loop
    ((CREATED_FILE)) && rm -f "$CONTAINER"
    ((CREATED_MOUNT)) && rmdir "$MOUNT" 2>/dev/null
    if ((DOCKER_CHANGED)); then
        ((${#ENABLED_BEFORE[@]})) && systemctl enable "${ENABLED_BEFORE[@]}" >/dev/null 2>&1
        ((${#ACTIVE_BEFORE[@]})) && systemctl start "${ACTIVE_BEFORE[@]}"
    fi
    echo "Готово: файла диска нет, Docker — как до setup. Исправьте причину и запустите setup снова." >&2
    exit "$status"
}

cmd_setup() {
    need_root setup
    local size=${1:-} dir inner unit
    check_setup "$size"
    read_key setup
    trap rollback_setup EXIT

    STEP="создание файла диска"
    CREATED_FILE=1
    fallocate -l "$size" "$CONTAINER"
    chmod 600 "$CONTAINER"
    STEP="подключение файла диска (losetup)"
    attach_loop
    ATTACHED=1
    STEP="шифрование (cryptsetup luksFormat)"
    with_key cryptsetup luksFormat --type luks2 --sector-size 4096 --batch-mode --key-file - "$LOOP"
    STEP="открытие диска"
    with_key cryptsetup open --type luks2 --key-file - "$LOOP" "$MAPPER"
    OPENED=1
    STEP="файловая система (mkfs.ext4)"
    mkfs.ext4 -q -L "$MAPPER" "/dev/mapper/$MAPPER"
    STEP="подключение диска"
    if [[ ! -e $MOUNT ]]; then
        mkdir -p "$MOUNT"
        CREATED_MOUNT=1
    fi
    mount "/dev/mapper/$MAPPER" "$MOUNT"
    MOUNTED=1
    chmod 700 "$MOUNT"
    mkdir -p "$APP_DIR"
    STEP="swap на диске"
    create_swap

    # Docker останавливается только теперь, когда диск готов: сбой до этого места его не трогает.
    # Дальше он не должен писать на незашифрованную часть диска и запускается только после unlock.
    STEP="остановка Docker"
    if has_systemd; then
        for unit in "${SERVICES[@]}"; do
            if systemctl is-enabled -q "$unit" 2>/dev/null; then
                ENABLED_BEFORE+=("$unit")
            fi
            if systemctl is-active -q "$unit" 2>/dev/null; then
                ACTIVE_BEFORE+=("$unit")
            fi
        done
        DOCKER_CHANGED=1
        stop_docker
        systemctl disable "${SERVICES[@]}" >/dev/null 2>&1 || true
    fi

    for dir in "${STATE_DIRS[@]}"; do
        STEP="перенос $dir на диск"
        inner=$MOUNT/$(basename "$dir")
        mkdir -p "$inner" "$dir"
        MOVED_DIRS+=("$dir")
        chattr -i "$dir" 2>/dev/null || true
        # То, что Docker записал при установке, переносится на диск. Каталог на незашифрованной части
        # остаётся пустым и неизменяемым: Docker, запущенный без unlock, не сможет в него писать.
        find "$dir" -mindepth 1 -maxdepth 1 -exec mv -t "$inner" {} +
        chattr +i "$dir" 2>/dev/null || echo "Не удалось сделать $dir неизменяемым (chattr): пропущено."
    done
    STEP="подключение каталогов Docker с диска"
    mount_state_dirs
    COMMITTED=1

    # Данные уже на диске: сначала ставится сам contract-review-disk — он нужен для unlock, — а дальше всё по возможности,
    # чтобы сбой в защите памяти не оставил систему без Docker.
    STEP="установка contract-review-disk"
    if [[ $(realpath "$0") != "$INSTALL_PATH" ]]; then
        install -m 755 "$0" "$INSTALL_PATH"
    fi
    STEP="swap и дампы памяти"
    harden || warn "защита swap и дампов памяти сделана не полностью: проверьте swapon --show и $SYSCTL_FILE"
    enable_swap
    STEP="запуск Docker"
    start_docker || warn "Docker не запустился: смотрите journalctl -u docker, потом contract-review-disk unlock"
    trap - EXIT
    echo "Диск создан и открыт: $MOUNT. Код системы — в $APP_DIR."
    echo "После перезагрузки сервера: ssh <сервер> contract-review-disk unlock < contract-review-disk.key"
}

cmd_unlock() {
    need_root unlock
    [[ -e $CONTAINER ]] || fail "$CONTAINER нет: диск ещё не создан (contract-review-disk setup)"
    if is_open && mountpoint -q "$MOUNT"; then
        echo "Диск уже открыт."
    else
        read_key unlock
        if ! is_open; then
            attach_loop
            if ! with_key cryptsetup open --type luks2 --key-file - "$LOOP" "$MAPPER"; then
                detach_loop
                fail "диск не открылся: ключ не подошёл"
            fi
        fi
        mountpoint -q "$MOUNT" || mount "/dev/mapper/$MAPPER" "$MOUNT"
        echo "Диск открыт: $MOUNT."
    fi
    enable_swap
    mount_state_dirs
    start_docker
}

cmd_lock() {
    need_root lock
    stop_docker
    disable_swap
    unmount_all
    if is_open; then
        cryptsetup close "$MAPPER"
    fi
    detach_loop
    echo "Диск закрыт. Система остановлена до contract-review-disk unlock."
}

cmd_status() {
    if is_open && mountpoint -q "$MOUNT"; then
        echo "Диск открыт: $MOUNT ($(df -h --output=used,size "$MOUNT" | tail -1 | xargs) занято)."
        if [[ $(loop_dio) == 1 ]]; then
            echo "Прямой ввод-вывод loop: включён."
        else
            echo "Прямой ввод-вывод loop: выключен — swap на диске не включается."
        fi
        if swap_active; then
            echo "Swap на диске: включён."
        elif [[ -f $SWAPFILE ]]; then
            echo "Swap на диске: выключен."
        fi
    elif [[ -e $CONTAINER ]]; then
        echo "Диск закрыт. Открыть: ssh <сервер> contract-review-disk unlock < contract-review-disk.key"
    else
        echo "Диск не создан."
    fi
    if has_systemd; then
        echo "Docker: $(systemctl is-active docker.service 2>/dev/null || true)."
    fi
}

main() {
    case ${1:-} in
    setup) cmd_setup "${2:-}" ;;
    unlock) cmd_unlock ;;
    lock) cmd_lock ;;
    status) cmd_status ;;
    *)
        sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
    esac
}

# Подключённый через source (проверка скрипта) — только функции, без запуска команды.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
