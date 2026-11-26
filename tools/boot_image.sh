#!/usr/bin/env bash
# Start a Quartermaster SD card image in an emulated Raspberry Pi 3 and check its first start over SSH.
#
#   tools/boot_image.sh --image build/images/quartermaster-pi-<version>-<hat>.img.xz
#
# Options: --imager (first set the card up the way Raspberry Pi Imager does: its own user, password, name and
# Wi-Fi country), --hat <the HAT the card must be set for>, --work <dir> (default: a temporary folder),
# --port <local port for SSH, default 5555>, --wait <minutes to wait for a start, default 30>.
# Needs qemu-system-aarch64, qemu-img, mtools, partx, sshpass, xz and openssl. Root is not needed.
# The emulated Pi has no HAT, no Wi-Fi and no Bluetooth, so the app stops as soon as it looks for its screen. What is
# checked: the card starts, grows to the card size, takes the login (the default one or Imager's), has SSH on, starts
# the app for the HAT named in quartermaster.txt, opens newly saved logs to the login, and still does all of that
# after a restart.
# The emulator restarts the moment the Pi's watchdog is switched on, so the emulated Pi is started without one.
set -euo pipefail

image='' work='' port=5555 wait_minutes=30 imager=no wanted_hat=''
service_user=_quartermaster
data=/var/lib/quartermaster/quartermaster
log_folders=(logs recon aircraft)
user=pi password=pi name=quartermaster
imager_user=kal-test imager_password=Imager-set-1 imager_name=qm-imager imager_country=US

die() { echo "boot_image: $*" >&2; exit 1; }
log() { echo "==> $*"; }
usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

while [ $# -gt 0 ]; do
    case $1 in
        --imager) imager=yes; shift; continue ;;
        --image|--hat|--work|--port|--wait) [ $# -ge 2 ] || usage ;;
        *) usage ;;
    esac
    case $1 in
        --image) image=$2 ;;
        --hat) wanted_hat=$2 ;;
        --work) work=$2 ;;
        --port) port=$2 ;;
        --wait) wait_minutes=$2 ;;
    esac
    shift 2
done

[ -f "$image" ] || die "no image at '$image'"
[[ $port =~ ^[0-9]+$ && $wait_minutes =~ ^[0-9]+$ ]] || die "--port and --wait take whole numbers"
for tool in qemu-system-aarch64 qemu-img mcopy partx sshpass ssh xz openssl timeout; do
    command -v "$tool" >/dev/null || die "missing $tool"
done
export MTOOLS_SKIP_CHECK=1

made=''
if [ -z "$work" ]; then
    work=$(mktemp -d)
    made=$work
fi
mkdir -p "$work"
work=$(cd "$work" && pwd)
disk=$work/card.img
loop='' mounted='' failed=0 booted=no starts=0 console=$work/console-0.log
failures=()

running() {
    local pid
    pid=$(cat "$work/qemu.pid" 2>/dev/null) && [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}
partition() { echo $(($(partx -g -r -o START -n "$1" "$disk") * 512)); }
halted() { grep -aqE 'reboot: (Power down|System halted|Power off not available)' "$console" 2>/dev/null; }

stop_emulator() {
    local patience=${1:-0}
    for _ in $(seq 1 "$patience"); do
        if ! running || halted; then break; fi
        sleep 1
    done
    if running; then
        kill "$(cat "$work/qemu.pid" 2>/dev/null)" 2>/dev/null || true
        for _ in $(seq 1 20); do
            running || break
            sleep 0.5
        done
    fi
    rm -f "$work/qemu.pid"
}

show_card_journal() {
    command -v sudo >/dev/null || return 0
    loop=$(sudo -n losetup --find --show --read-only --offset "$(partition 2)" "$disk") || return 0
    mkdir -p "$work/root"
    if sudo -n mount -o ro,noload "$loop" "$work/root" 2>/dev/null; then
        mounted=$work/root
        log "The card's own journal"
        sudo -n journalctl --no-pager -o short-monotonic -D "$work/root/var/log/journal" 2>/dev/null |
            tail -n 200 || true
        sudo -n tail -n 60 "$work/root/var/log/cloud-init-output.log" 2>/dev/null || true
    fi
}

show_console() {
    log "Last lines of the console of start $starts"
    if halted; then
        sed -nE '1,/reboot: (Power down|System halted|Power off not available)/p' "$console" | tail -n 150
        echo "(The emulated Pi switched off here. It always prints a kernel panic next, which is normal.)"
    else
        tail -n 150 "$console"
    fi
}

cleanup() {
    local rc=$?
    set +e
    stop_emulator
    if [ "$rc" != 0 ] || [ "$failed" != 0 ]; then
        [ -s "$console" ] && show_console
        [ "$starts" -gt 0 ] && [ "$booted" = no ] && [ -f "$disk" ] && show_card_journal
        [ "${#failures[@]}" = 0 ] || { log "Failed checks"; printf 'FAIL  %s\n' "${failures[@]}"; }
    fi
    [ -n "$mounted" ] && sudo -n umount "$mounted"
    [ -n "$loop" ] && sudo -n losetup -d "$loop" 2>/dev/null
    [ -n "$made" ] && rm -rf "$made"
    exit "$rc"
}
trap cleanup EXIT

take_boot_files() {
    local file
    for file in kernel8.img initramfs8 bcm2710-rpi-3-b.dtb cmdline.txt; do
        mcopy -n -i "$disk@@$(partition 1)" "::$file" "$work/$file" || die "the card has no $file"
    done
    cmdline=$(tr -d '\n' < "$work/cmdline.txt" | sed -E 's/(^| )console=[^ ]*//g')
    cmdline="console=tty1 console=ttyAMA0,115200 console=ttyAMA1,115200 ${cmdline# } dwc_otg.lpm_enable=0"
    cmdline="$cmdline earlycon=pl011,mmio32,0x3f201000"
    cmdline="$cmdline initcall_blacklist=bcm2835_wdt_driver_init module_blacklist=bcm2835_wdt"
}

log "Unpacking $(basename "$image")"
case $image in
    *.xz) xz -dc "$image" > "$disk" ;;
    *) cp --sparse=always "$image" "$disk" ;;
esac

if [ "$imager" = yes ]; then
    log "Setting the card up as Raspberry Pi Imager does: user $imager_user, name $imager_name, country $imager_country"
    user=$imager_user password=$imager_password name=$imager_name
    cat > "$work/user-data" <<EOF
#cloud-config
manage_resolv_conf: false

hostname: "$imager_name"
manage_etc_hosts: true
packages:
- avahi-daemon

apt:
  preserve_sources_list: true
  conf: |
    Acquire {
      Check-Date "false";
    };

timezone: "America/New_York"
keyboard:
  model: pc105
  layout: "us"
user:
  name: "$imager_user"
  shell: /bin/bash
  lock_passwd: false
  passwd: "$(openssl passwd -6 "$imager_password")"
  sudo: null

ssh_pwauth: true

runcmd:
  - [ systemctl, enable, --now, ssh ]
  - [ rfkill, unblock, wifi ]
  - [ sh, -c, "for f in /var/lib/systemd/rfkill/*:wlan; do echo 0 > \"\$f\"; done" ]
EOF
    mcopy -n -i "$disk@@$(partition 1)" ::cmdline.txt "$work/cmdline.txt"
    printf '%s\n' "$(tr -d '\n' < "$work/cmdline.txt") cfg80211.ieee80211_regdom=$imager_country" \
        > "$work/cmdline.new"
    mcopy -o -i "$disk@@$(partition 1)" "$work/user-data" ::user-data
    mcopy -o -i "$disk@@$(partition 1)" "$work/cmdline.new" ::cmdline.txt
fi
take_boot_files
hat=$(mcopy -n -i "$disk@@$(partition 1)" ::quartermaster.txt - | tr -d '\r' | sed -n 's/^display=//p' | tail -n 1)
[ -n "$hat" ] || die "the card's quartermaster.txt names no HAT"
[ -z "$wanted_hat" ] || [ "$hat" = "$wanted_hat" ] || die "the card is set for $hat, not for $wanted_hat"
qemu-img resize -q -f raw "$disk" 8G

start_emulator() {
    starts=$((starts + 1))
    console=$work/console-$starts.log
    log "Starting the emulated Raspberry Pi 3"
    qemu-system-aarch64 -machine raspi3b -cpu cortex-a53 -smp 4 -m 1G \
        -kernel "$work/kernel8.img" -initrd "$work/initramfs8" -dtb "$work/bcm2710-rpi-3-b.dtb" \
        -append "$cmdline" -drive "file=$disk,format=raw,if=sd" \
        -device usb-net,netdev=net0 -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$port-:22" \
        -display none -monitor none -serial "file:$console" \
        -no-reboot -daemonize -pidfile "$work/qemu.pid"
}

ssh_options=(-p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
             -o ConnectTimeout=20 -o PreferredAuthentications=password -o PubkeyAuthentication=no
             -o NumberOfPasswordPrompts=1 -o ServerAliveInterval=15)
as_login() {
    local who=$1 secret=$2
    shift 2
    SSHPASS=$secret timeout 900 sshpass -e ssh "${ssh_options[@]}" "$who@127.0.0.1" "$@"
}
on_pi() { as_login "$user" "$password" "$@"; }
as_root() { on_pi "sudo -S -p '' $*" <<< "$password"; }

wait_for_login() {
    local began=$SECONDS told=0
    until on_pi true 2>/dev/null; do
        running || die "the emulator stopped"
        [ $((SECONDS - began)) -lt $((wait_minutes * 60)) ] ||
            die "no SSH login as $user after $wait_minutes minutes"
        sleep 10
        if [ $((SECONDS - began - told)) -ge 120 ]; then
            told=$((SECONDS - began))
            echo "    waiting for SSH, $((told / 60)) min"
        fi
    done
    echo "    SSH answered after $((SECONDS - began)) s"
}

settle() {
    local began=$SECONDS state=''
    wait_for_login
    while [ $((SECONDS - began)) -lt $((wait_minutes * 60 + 300)) ]; do
        state=$(on_pi 'cloud-init status 2>&1 | head -n 1; systemctl is-system-running 2>&1' 2>/dev/null |
            xargs) || true
        case $state in
            "status: done running"|"status: done degraded"|"status: error "*|"status: disabled "*) break ;;
        esac
        sleep 15
    done
    echo "    settled after $((SECONDS - began)) s: $state"
}

check() {
    if [ "$2" = "$3" ]; then
        echo "ok    $1: $3"
    else
        echo "FAIL  $1: expected '$3', got '$2'"
        failed=1
        failures+=("$1: expected '$3', got '$2'")
    fi
}

checks() {
    local when=$1 unit app
    check "$when: SSH login" "$(on_pi id -un)" "$user"
    check "$when: cloud-init" "$(on_pi 'cloud-init status' | head -n 1)" "status: done"
    check "$when: name" "$(on_pi hostname)" "$name"
    check "$when: card grown to its full size" \
        "$(on_pi 'df --output=size -BG / | tail -n 1' | tr -dc 0-9 |
            awk '{print ($1 >= 7) ? "yes" : "no (" $1 " GB)"}')" yes
    check "$when: login is in the $service_user group" "$(on_pi id -Gn | tr ' ' '\n' | grep -cx "$service_user")" 1
    check "$when: logs link" "$(on_pi readlink quartermaster)" "$data"
    for unit in ssh.service quartermaster-logs.path quartermaster-radio.socket getty@tty1.service; do
        check "$when: $unit" "$(on_pi systemctl is-active "$unit")" active
    done
    check "$when: userconfig.service" "$(on_pi systemctl is-active userconfig.service)" inactive
    check "$when: app unit enabled" "$(on_pi systemctl is-enabled "quartermaster@$service_user.service")" enabled
    check "$when: first login set up once" \
        "$(on_pi 'test -e /var/lib/quartermaster-image/first-login.done && echo yes')" yes
    check "$when: failed Quartermaster set-up units" "$(on_pi 'systemctl --failed --plain --no-legend' |
        awk '{print $1}' | grep '^quartermaster-' | xargs)" ""
    app=$(as_root "journalctl -b -u quartermaster@$service_user.service -o cat --no-pager" 2>/dev/null |
        grep '^Cannot open' | tail -n 1) || true
    check "$when: the app is started for the $hat HAT" \
        "$(grep -c "^Cannot open $hat display or controls: " <<< "$app" || true)" 1
    check "$when: the app reports no missing software" \
        "$(grep -ciE 'no module named|cannot import|shared object' <<< "$app" || true)" 0
    for folder in "${log_folders[@]}"; do
        as_root "-u $service_user sh -c 'umask 077 && echo saved > $data/$folder/boot-check.log'" || true
    done
    for folder in "${log_folders[@]}"; do
        for _ in $(seq 1 12); do
            [ "$(on_pi cat "quartermaster/$folder/boot-check.log" 2>/dev/null)" = saved ] && break
            sleep 5
        done
        check "$when: a newly saved log in $folder opens to the login" \
            "$(on_pi cat "quartermaster/$folder/boot-check.log" 2>&1)" saved
    done
    sleep 6
    check "$when: saving a log leaves no warning in the journal" "$(as_root "journalctl -b -q -p warning -o cat \
        --no-pager -u quartermaster-logs.service -u quartermaster-logs-changed.service && echo none")" none
    for folder in "${log_folders[@]}"; do
        as_root "rm -f $data/$folder/boot-check.log" || true
    done
}

notice() {
    local text
    if text=$(SSHPASS=$2 timeout 120 sshpass -e ssh -tt "${ssh_options[@]}" "$1@127.0.0.1" <<< 'exit 0' 2>&1); then
        grep -c 'still has the default password' <<< "$text" || true
    else
        echo "no login"
    fi
}

refused() {
    local answer
    if answer=$(as_login "$1" "$2" true 2>&1); then
        echo open
    elif grep -q 'Permission denied' <<< "$answer"; then
        echo refused
    else
        echo "no answer: $(head -n 1 <<< "$answer")"
    fi
}

report() {
    log "$1"
    on_pi 'systemctl --failed --plain --no-legend; systemd-analyze 2>&1 | head -n 2
        cloud-init status --long 2>&1 | head -n 12; cat /proc/cmdline; ip -brief address' || true
    on_pi "systemctl status --no-pager -n 12 quartermaster@$service_user.service quartermaster-first-login.service \
        quartermaster-logs.service quartermaster-logs-changed.service quartermaster-boot-config.service 2>&1 |
        cut -c1-200" || true
    as_root "journalctl -b -u quartermaster@$service_user.service -o cat --no-pager -n 4 2>&1 | cut -c1-220" || true
    as_root "journalctl -b --no-pager -p warning -n 60 2>&1 | cut -c1-220" || true
}

start_emulator
settle
booted=yes
report "First start"
checks "first start"

if [ "$imager" = yes ]; then
    check "the default login is switched off" "$(refused pi pi)" refused
    check "no default password notice" "$(notice "$user" "$password")" 0
else
    check "the login says the password is still the default" "$(notice "$user" "$password")" 1
    as_root "sh -c 'echo $user:Changed-by-test-1 | chpasswd'"
    password=Changed-by-test-1
    check "the new password works" "$(on_pi id -un)" "$user"
    check "the old password is refused" "$(refused "$user" pi)" refused
    check "no notice after the password change" "$(notice "$user" "$password")" 0
fi

log "Switching off and starting again"
before=$(on_pi cat /proc/sys/kernel/random/boot_id) || true
as_root "systemctl poweroff" >/dev/null 2>&1 || true
stop_emulator 600
check "the first start switches off by itself" "$(halted && echo yes || echo no)" yes
take_boot_files
start_emulator
settle
after=$(on_pi cat /proc/sys/kernel/random/boot_id) || true
check "second start is a new start" \
    "$([ -n "$before" ] && [ -n "$after" ] && [ "$after" != "$before" ] && echo yes || echo no)" yes
report "Second start"
checks "second start"
if [ "$imager" = yes ]; then
    check "the default login is still switched off" "$(refused pi pi)" refused
    check "the Wi-Fi country set in Imager is still on the card after the first start" \
        "$(on_pi "grep -c 'cfg80211.ieee80211_regdom=$imager_country' /proc/cmdline")" 1
fi
check "first login is not set up again" \
    "$(on_pi systemctl show -p ConditionResult --value quartermaster-first-login.service)" no

as_root "systemctl poweroff" >/dev/null 2>&1 || true
stop_emulator 600
check "the second start switches off by itself" "$(halted && echo yes || echo no)" yes
[ "$failed" = 0 ] || die "the image did not pass every check"
log "The image starts, takes its login over SSH and keeps it after a restart"
