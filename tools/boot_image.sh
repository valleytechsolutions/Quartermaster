#!/usr/bin/env bash
# Start a Quartermaster SD card image in an emulated Raspberry Pi 3 and check its first start over SSH.
#
#   tools/boot_image.sh --image build/images/quartermaster-pi-<version>-<hat>.img.xz
#
# Options: --imager (first set the card up the way Raspberry Pi Imager does: its own user, password, name and
# Wi-Fi country), --work <dir> (default: a temporary folder), --port <local port for SSH, default 5555>,
# --wait <minutes to wait for a start, default 30>.
# Needs sudo for the loop device, and qemu-system-aarch64, qemu-img, sshpass, xz and openssl.
# The emulated Pi has no HAT, no Wi-Fi and no Bluetooth, so the app itself cannot run there. What is checked: the
# card starts, grows to the card size, takes the login (the default one or Imager's), has SSH on, opens newly saved
# logs to the login, and still does all of that after a restart.
# The emulator restarts the moment the Pi's watchdog is switched on, so the emulated Pi is started without one.
set -euo pipefail

image='' work='' port=5555 wait_minutes=30 imager=no
service_user=_quartermaster
data=/var/lib/quartermaster/quartermaster
user=pi password=pi name=quartermaster
imager_user=kal-test imager_password=Imager-set-1 imager_name=qm-imager imager_country=US

die() { echo "boot_image: $*" >&2; exit 1; }
log() { echo "==> $*"; }
usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

while [ $# -gt 0 ]; do
    case $1 in
        --image) image=${2:-}; shift 2 ;;
        --work) work=${2:-}; shift 2 ;;
        --port) port=${2:-}; shift 2 ;;
        --wait) wait_minutes=${2:-}; shift 2 ;;
        --imager) imager=yes; shift ;;
        *) usage ;;
    esac
done

[ -f "$image" ] || die "no image at '$image'"
[[ $port =~ ^[0-9]+$ && $wait_minutes =~ ^[0-9]+$ ]] || die "--port and --wait take whole numbers"
for tool in sudo losetup mount umount qemu-system-aarch64 qemu-img sshpass ssh xz openssl timeout; do
    command -v "$tool" >/dev/null || die "missing $tool"
done

made=''
if [ -z "$work" ]; then
    work=$(mktemp -d)
    made=$work
fi
mkdir -p "$work/boot"
work=$(cd "$work" && pwd)
disk=$work/card.img
loop='' mounted='' failed=0 booted=no starts=0 console=$work/console-0.log

running() { [ -f "$work/qemu.pid" ] && kill -0 "$(cat "$work/qemu.pid")" 2>/dev/null; }
halted() { grep -aqE 'reboot: (Power down|System halted|Power off not available)' "$console" 2>/dev/null; }

stop_emulator() {
    local patience=${1:-0}
    for _ in $(seq 1 "$patience"); do
        if ! running || halted; then break; fi
        sleep 1
    done
    if running; then
        kill "$(cat "$work/qemu.pid")" 2>/dev/null || true
        for _ in $(seq 1 20); do
            running || break
            sleep 0.5
        done
    fi
    rm -f "$work/qemu.pid"
}

show_card_journal() {
    local part
    loop=$(sudo losetup --find --show --partscan --read-only "$disk") || return 0
    sudo udevadm settle 2>/dev/null || true
    part=${loop}p2
    mkdir -p "$work/root"
    if sudo mount -o ro,noload "$part" "$work/root" 2>/dev/null; then
        mounted=$work/root
        log "The card's own journal"
        sudo journalctl --no-pager -o short-monotonic -D "$work/root/var/log/journal" 2>/dev/null | tail -n 200 || true
        sudo tail -n 60 "$work/root/var/log/cloud-init-output.log" 2>/dev/null || true
    fi
}

cleanup() {
    local rc=$?
    set +e
    stop_emulator
    if [ "$rc" != 0 ] || [ "$failed" != 0 ]; then
        [ -s "$console" ] && { log "Last lines of the console of start $starts"; tail -n 150 "$console"; }
        [ "$booted" = no ] && [ -f "$disk" ] && show_card_journal
    fi
    [ -n "$mounted" ] && sudo umount "$mounted"
    [ -n "$loop" ] && sudo losetup -d "$loop" 2>/dev/null
    [ -n "$made" ] && rm -rf "$made"
    exit "$rc"
}
trap cleanup EXIT

attach() {
    loop=$(sudo losetup --find --show --partscan "$@" "$disk")
    sudo udevadm settle 2>/dev/null || true
    for _ in 1 2 3 4 5; do [ -b "${loop}p1" ] && break; sleep 1; done
}

detach() {
    [ -z "$mounted" ] || sudo umount "$mounted"
    mounted=''
    [ -z "$loop" ] || sudo losetup -d "$loop"
    loop=''
}

take_boot_files() {
    local file
    for file in kernel8.img initramfs8 bcm2710-rpi-3-b.dtb cmdline.txt; do
        [ -f "$work/boot/$file" ] || die "the card has no $file"
    done
    cp "$work/boot/kernel8.img" "$work/boot/initramfs8" "$work/boot/bcm2710-rpi-3-b.dtb" "$work/"
    cmdline=$(tr -d '\n' < "$work/boot/cmdline.txt" | sed -E 's/(^| )console=[^ ]*//g')
    cmdline="console=tty1 console=ttyAMA0,115200 console=ttyAMA1,115200 ${cmdline# } dwc_otg.lpm_enable=0"
    cmdline="$cmdline earlycon=pl011,mmio32,0x3f201000"
    cmdline="$cmdline initcall_blacklist=bcm2835_wdt_driver_init module_blacklist=bcm2835_wdt"
}

log "Unpacking $(basename "$image")"
case $image in
    *.xz) xz -dc "$image" > "$disk" ;;
    *) cp --sparse=always "$image" "$disk" ;;
esac

attach
sudo mount -o "uid=$(id -u),gid=$(id -g)" "${loop}p1" "$work/boot"
mounted=$work/boot
if [ "$imager" = yes ]; then
    log "Setting the card up as Raspberry Pi Imager does: user $imager_user, name $imager_name, country $imager_country"
    user=$imager_user password=$imager_password name=$imager_name
    cat > "$work/boot/user-data" <<EOF
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
    printf '%s\n' "$(tr -d '\n' < "$work/boot/cmdline.txt") cfg80211.ieee80211_regdom=$imager_country" \
        > "$work/cmdline.txt"
    cp "$work/cmdline.txt" "$work/boot/cmdline.txt"
fi
take_boot_files
detach
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
    local began=$SECONDS state
    wait_for_login
    while [ $((SECONDS - began)) -lt $((wait_minutes * 60)) ]; do
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
    fi
}

checks() {
    local when=$1 unit
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
    as_root "-u $service_user sh -c 'umask 077 && echo saved > $data/logs/boot-check.wigle.csv'" || true
    sleep 8
    check "$when: a newly saved log opens to the login" \
        "$(on_pi cat quartermaster/logs/boot-check.wigle.csv 2>&1)" saved
    as_root "rm -f $data/logs/boot-check.wigle.csv" || true
}

notice() {
    SSHPASS=$2 timeout 120 sshpass -e ssh -tt "${ssh_options[@]}" "$1@127.0.0.1" <<< exit 2>&1 |
        grep -c 'still has the default password' || true
}

report() {
    log "$1"
    on_pi 'systemctl --failed --plain --no-legend; systemd-analyze 2>&1 | head -n 2
        cloud-init status --long 2>&1 | head -n 12; cat /proc/cmdline; ip -brief address' || true
    on_pi "systemctl status --no-pager -n 12 quartermaster@$service_user.service quartermaster-first-login.service \
        quartermaster-logs.service quartermaster-boot-config.service 2>&1 | cut -c1-200" || true
    as_root "journalctl -b --no-pager -p warning -n 60 2>&1 | cut -c1-220" || true
}

start_emulator
settle
booted=yes
report "First start"
checks "first start"

if [ "$imager" = yes ]; then
    check "the default login is switched off" \
        "$(as_login pi pi true >/dev/null 2>&1 && echo open || echo refused)" refused
    check "no default password notice" "$(notice "$user" "$password")" 0
    check "the Wi-Fi country reached the kernel command line" \
        "$(on_pi "grep -c 'cfg80211.ieee80211_regdom=$imager_country' /proc/cmdline")" 1
else
    check "the login says the password is still the default" "$(notice "$user" "$password")" 1
    as_root "sh -c 'echo $user:Changed-by-test-1 | chpasswd'"
    password=Changed-by-test-1
    check "the new password works" "$(on_pi id -un)" "$user"
    check "the old password is refused" \
        "$(as_login "$user" pi true >/dev/null 2>&1 && echo open || echo refused)" refused
    check "no notice after the password change" "$(notice "$user" "$password")" 0
fi

log "Switching off and starting again"
before=$(on_pi cat /proc/sys/kernel/random/boot_id) || true
as_root "systemctl poweroff" >/dev/null 2>&1 || true
stop_emulator 600
check "the first start switches off by itself" "$(halted && echo yes || echo no)" yes
attach --read-only
sudo mount -o ro "${loop}p1" "$work/boot"
mounted=$work/boot
take_boot_files
detach
start_emulator
settle
check "second start is a new start" \
    "$([ "$(on_pi cat /proc/sys/kernel/random/boot_id)" != "$before" ] && echo yes || echo no)" yes
report "Second start"
checks "second start"
check "first login is not set up again" \
    "$(on_pi systemctl show -p ConditionResult --value quartermaster-first-login.service)" no

as_root "systemctl poweroff" >/dev/null 2>&1 || true
stop_emulator 600
check "the second start switches off by itself" "$(halted && echo yes || echo no)" yes
[ "$failed" = 0 ] || die "the image did not pass every check"
log "The image starts, takes its login over SSH and keeps it after a restart"
