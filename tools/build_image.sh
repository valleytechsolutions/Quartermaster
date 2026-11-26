#!/usr/bin/env bash
# Build a ready-to-flash Raspberry Pi OS Lite (Trixie, armhf) SD card image with Quartermaster for one Waveshare HAT.
#
#   sudo tools/build_image.sh --hat st7735-128 --deb build/pi/quartermaster-pi_<version>_all.deb
#
# Options: --base <raspios .img.xz or .img> (default: download the latest Lite armhf image and check its sha256),
# --out <dir> (default build/images), --grow-mb <MiB added to the root partition, default 1536>.
# Needs root, util-linux, e2fsprogs, xz, curl, python3 and qemu-user-static registered for ARM binaries.
# Writes quartermaster-pi-<version>-<hat>.img.xz, its Imager metadata (.json) and SHA256SUMS to the out directory.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
base_url=${QM_BASE_URL:-https://downloads.raspberrypi.com/raspios_lite_armhf_latest}
hat='' deb='' base='' out=$here/build/images grow_mb=1536
service_id=59999
groups_wanted=(video input render dialout gpio spi i2c bluetooth netdev plugdev audio quartermaster-radio)
units=(quartermaster-radio.socket quartermaster-boot-config.service quartermaster@quartermaster.service)

die() { echo "build_image: $*" >&2; exit 1; }
log() { echo "==> $*"; }
usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

while [ $# -gt 0 ]; do
    case $1 in
        --hat) hat=${2:-}; shift 2 ;;
        --deb) deb=${2:-}; shift 2 ;;
        --base) base=${2:-}; shift 2 ;;
        --out) out=${2:-}; shift 2 ;;
        --grow-mb) grow_mb=${2:-}; shift 2 ;;
        -h|--help) usage ;;
        *) usage ;;
    esac
done

case $hat in st7735-128|st7789-240) ;; *) usage ;; esac
[ -f "$deb" ] || die "no package at '$deb'"
[[ $grow_mb =~ ^[0-9]+$ ]] || die "--grow-mb takes a whole number"
[ "$(id -u)" = 0 ] || die "run as root (loop devices, mounts and chroot)"
for tool in losetup sfdisk e2fsck resize2fs mount umount chroot xz sha256sum dpkg-deb curl python3 truncate; do
    command -v "$tool" >/dev/null || die "missing $tool"
done
[ -e /proc/sys/fs/binfmt_misc/qemu-arm ] || die "ARM binaries cannot run here: install qemu-user-static and binfmt-support"
[ "$(dpkg-deb -f "$deb" Package)" = quartermaster-pi ] || die "$deb is not the quartermaster-pi package"
version=$(dpkg-deb -f "$deb" Version)
[[ $version =~ ^[0-9A-Za-z.+~-]+$ ]] || die "unexpected package version '$version'"
name=quartermaster-pi-$version-$hat

mkdir -p "$out"
out=$(cd "$out" && pwd)
deb=$(cd "$(dirname "$deb")" && pwd)/$(basename "$deb")
work=$(mktemp -d "$out/.work-$hat.XXXXXX")
img=$work/$name.img
mnt=$work/root
mounts=()
loops=()

cleanup() {
    local rc=$? i
    set +e
    for ((i = ${#mounts[@]} - 1; i >= 0; i--)); do
        umount "${mounts[i]}" 2>/dev/null || umount -l "${mounts[i]}"
    done
    for i in "${loops[@]}"; do
        losetup -d "$i"
    done
    rm -rf "$work"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

mount_at() {
    mount "$@"
    mounts+=("${!#}")
}

unmount_all() {
    local i
    sync
    for ((i = ${#mounts[@]} - 1; i >= 0; i--)); do
        umount "${mounts[i]}"
    done
    mounts=()
}

attach() {
    attached=$(losetup --find --show --offset "$(($1 * 512))" --sizelimit "$(($2 * 512))" "$img")
    loops+=("$attached")
}

in_chroot() {
    local env=(HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C.UTF-8
               DEBIAN_FRONTEND=noninteractive)
    local var
    for var in http_proxy https_proxy no_proxy; do
        [ -n "${!var:-}" ] && env+=("$var=${!var}")
    done
    chroot "$mnt" /usr/bin/env -i "${env[@]}" "$@"
}

partitions() {
    sfdisk -J "$img" | python3 -I -c '
import json, sys
table = json.load(sys.stdin)["partitiontable"]
assert table.get("sectorsize", 512) == 512, "unexpected sector size"
parts = table["partitions"]
assert len(parts) == 2, "expected a boot and a root partition"
print(parts[0]["start"], parts[0]["size"], parts[1]["start"], parts[1]["size"])'
}

fetch_base() {
    local url file
    url=$(curl -fsSIL --retry 3 -o /dev/null -w '%{url_effective}' "$base_url")
    file=${url##*/}
    [[ $file =~ ^[0-9-]+raspios-trixie-armhf-lite\.img\.xz$ ]] || die "unexpected base image $url"
    log "Downloading $file"
    mkdir "$work/base"
    curl -fsSL --retry 3 -o "$work/base/$file" "$url"
    curl -fsSL --retry 3 -o "$work/base/$file.sha256" "$url.sha256"
    (cd "$work/base" && sha256sum --check --strict "$file.sha256")
    base=$work/base/$file
}

if [ -z "$base" ]; then
    fetch_base
else
    [ -f "$base" ] || die "no base image at '$base'"
    if [ -f "$base.sha256" ]; then
        (cd "$(dirname "$base")" && sha256sum --check --strict "$(basename "$base").sha256")
    fi
fi
base_name=$(basename "$base")

log "Unpacking $base_name"
case $base in
    *.xz) xz -dc "$base" > "$img" ;;
    *) cp --sparse=always "$base" "$img" ;;
esac

if [ "$grow_mb" -gt 0 ]; then
    log "Adding $grow_mb MiB to the root partition"
    truncate -s "+${grow_mb}M" "$img"
    echo ', +' | sfdisk --quiet --no-reread --no-tell-kernel -N 2 "$img"
fi
read -r boot_start boot_size root_start root_size < <(partitions)
attach "$boot_start" "$boot_size"
boot_dev=$attached
attach "$root_start" "$root_size"
root_dev=$attached
e2fsck -fy "$root_dev" >/dev/null || [ $? -le 1 ] || die "the root file system has errors"
resize2fs "$root_dev" >/dev/null

mkdir "$mnt"
mount_at "$root_dev" "$mnt"
[ -d "$mnt/boot/firmware" ] || die "no /boot/firmware in the base image"
mount_at "$boot_dev" "$mnt/boot/firmware"
config=$mnt/boot/firmware/config.txt
if [ ! -f "$config" ] || [ ! -f "$mnt/boot/firmware/cmdline.txt" ]; then
    die "the base image has no config.txt or cmdline.txt"
fi
mount_at -t proc proc "$mnt/proc"
mount_at -t sysfs sysfs "$mnt/sys"
mount_at --bind /dev "$mnt/dev"
mount_at --bind /dev/pts "$mnt/dev/pts"
mount_at -t tmpfs tmpfs "$mnt/run"
mount_at -t tmpfs tmpfs "$mnt/tmp"

log "Preparing the chroot"
keep=$work/keep
mkdir "$keep"
for path in etc/resolv.conf etc/ld.so.preload etc/machine-id; do
    if [ -e "$mnt/$path" ] || [ -L "$mnt/$path" ]; then
        mkdir -p "$keep/$(dirname "$path")"
        cp -a "$mnt/$path" "$keep/$path"
    fi
done
host_keys_before=$(find "$mnt/etc/ssh" -maxdepth 1 -name 'ssh_host_*' -printf '%f\n' 2>/dev/null || true)
cloud_state_before=$([ -e "$mnt/var/lib/cloud" ] && echo yes || echo no)
seeds() {
    (cd "$mnt/boot/firmware" && { sha256sum cmdline.txt user-data network-config meta-data 2>/dev/null; true; })
}
seed_before=$(seeds)
rm -f "$mnt/etc/resolv.conf" "$mnt/etc/ld.so.preload"
cp -L /etc/resolv.conf "$mnt/etc/resolv.conf"
[ ! -e "$mnt/usr/sbin/policy-rc.d" ] || die "the base image already has a policy-rc.d"
printf '#!/bin/sh\nexit 101\n' > "$mnt/usr/sbin/policy-rc.d"
chmod 755 "$mnt/usr/sbin/policy-rc.d"

log "Updating Raspberry Pi OS"
apt_options=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
in_chroot apt-get update
in_chroot apt-get "${apt_options[@]}" full-upgrade

log "Installing $(basename "$deb")"
cp "$deb" "$mnt/tmp/quartermaster-pi.deb"
in_chroot apt-get "${apt_options[@]}" --no-install-recommends install /tmp/quartermaster-pi.deb
in_chroot apt-get -y autoremove --purge

log "Setting up the quartermaster service user"
in_chroot getent passwd "$service_id" >/dev/null && die "uid $service_id is taken in the base image"
in_chroot getent group "$service_id" >/dev/null && die "gid $service_id is taken in the base image"
in_chroot getent passwd quartermaster >/dev/null && die "the base image already has a quartermaster user"
in_chroot groupadd --gid "$service_id" quartermaster
in_chroot useradd --uid "$service_id" --gid quartermaster --home-dir /var/lib/quartermaster --create-home \
    --shell /usr/sbin/nologin --comment Quartermaster quartermaster
sed -i -E "s/^([[:space:]]*[UG]ID_MAX[[:space:]]+).*/\1$((service_id - 1))/" "$mnt/etc/login.defs"
if [ -f "$mnt/etc/adduser.conf" ]; then
    sed -i -E "s/^#?[[:space:]]*(LAST_[UG]ID)=.*/\1=$((service_id - 1))/" "$mnt/etc/adduser.conf"
fi
for group in "${groups_wanted[@]}"; do
    if in_chroot getent group "$group" >/dev/null; then
        in_chroot usermod -aG "$group" quartermaster
    fi
done
chmod 750 "$mnt/var/lib/quartermaster"

log "Configuring the $hat display and autostart"
printf '%s\n' "$hat" > "$mnt/etc/quartermaster/display"
sed "s/@HAT@/$hat/" "$here/packaging/image/quartermaster.txt" > "$mnt/boot/firmware/quartermaster.txt"
install -D -m 644 "$here/packaging/image/boot_config.py" "$mnt/usr/local/lib/quartermaster-image/boot_config.py"
install -D -m 644 "$here/packaging/image/quartermaster-boot-config.service" \
    "$mnt/etc/systemd/system/quartermaster-boot-config.service"
install -D -m 644 "$here/packaging/image/50-quartermaster-wifi-scan.rules" \
    "$mnt/etc/polkit-1/rules.d/50-quartermaster-wifi-scan.rules"
in_chroot systemctl enable "${units[@]}"
printf 'VERSION=%s\nHAT=%s\nBASE_IMAGE=%s\nBUILT=%s\n' "$version" "$hat" "$base_name" "$(date -u +%Y-%m-%d)" \
    > "$mnt/etc/quartermaster/image-release"

for state in "$mnt"/var/lib/systemd/rfkill/*:wlan; do
    [ -e "$state" ] || continue
    echo 0 > "$state"
done

if grep -Eq '^[[:space:]]*dtparam=spi=on' "$config"; then
    :
elif grep -Eq '^[[:space:]]*#[[:space:]]*dtparam=spi=on' "$config"; then
    sed -i -E '0,/^[[:space:]]*#[[:space:]]*dtparam=spi=on/s//dtparam=spi=on/' "$config"
else
    printf '\n[all]\ndtparam=spi=on\n' >> "$config"
fi

log "Cleaning up"
in_chroot apt-get clean
rm -rf "$mnt"/var/lib/apt/lists/* "$mnt"/var/cache/apt/*.bin "$mnt/usr/sbin/policy-rc.d"
rm -f "$mnt/etc/resolv.conf" "$mnt/etc/machine-id"
for path in etc/resolv.conf etc/ld.so.preload etc/machine-id; do
    if [ -e "$keep/$path" ] || [ -L "$keep/$path" ]; then
        cp -a "$keep/$path" "$mnt/$path"
    fi
done
for key in "$mnt"/etc/ssh/ssh_host_*; do
    [ -e "$key" ] || continue
    grep -qxF "$(basename "$key")" <<< "$host_keys_before" || rm -f "$key"
done
[ "$cloud_state_before" = yes ] || rm -rf "$mnt/var/lib/cloud"
find "$mnt/var/log" -type f \( -name '*.gz' -o -name '*.[0-9]' \) -delete

log "Checking the image"
check() {
    if [ "$2" = "$3" ]; then
        echo "ok    $1: $3"
    else
        die "$1: expected '$3', got '$2'"
    fi
}
# shellcheck disable=SC2016
check "package" "$(in_chroot dpkg-query -W -f '${Status} ${Version}' quartermaster-pi)" "install ok installed $version"
check "display" "$(cat "$mnt/etc/quartermaster/display")" "$hat"
check "boot file" "$(in_chroot python3 -I /usr/local/lib/quartermaster-image/boot_config.py)" "display is $hat"
check "SPI" "$(grep -Ec '^[[:space:]]*dtparam=spi=on' "$config")" 1
for unit in "${units[@]}"; do
    check "$unit" "$(in_chroot systemctl is-enabled "$unit" 2>/dev/null || true)" enabled
done
check "app import as quartermaster" "$(in_chroot runuser -u quartermaster -- /bin/sh -c \
    'cd /usr/lib/quartermaster/app && exec python3 -c "import quartermaster; print(quartermaster.__version__)"')" \
    "$version"
check "quartermaster uid" "$(in_chroot id -u quartermaster)" "$service_id"
check "radio helper accepts quartermaster" "$(in_chroot python3 -I -c '
import pwd, sys
sys.path.insert(0, "/usr/lib/quartermaster")
import radiod
user = pwd.getpwnam("quartermaster")
print(radiod.peer_allowed(user.pw_uid, user.pw_gid))')" True
probe=$(in_chroot sh -c 'useradd -M -N -s /usr/sbin/nologin qm-probe && id -u qm-probe; userdel qm-probe' 2>/dev/null)
check "next login user below the service uid" "$([ "${probe:-0}" -ge 1000 ] && [ "$probe" -lt "$service_id" ] && echo yes || echo "no ($probe)")" yes
check "Wi-Fi rfkill saved state" "$(cat "$mnt"/var/lib/systemd/rfkill/*:wlan 2>/dev/null | grep -vx 0 | xargs)" ""
member_of=" $(in_chroot id -nG quartermaster) "
for group in video gpio spi dialout netdev bluetooth quartermaster-radio; do
    if in_chroot getent group "$group" >/dev/null; then
        check "quartermaster in $group" "$([[ $member_of == *" $group "* ]] && echo yes || echo no)" yes
    fi
done
check "accounts with a password" "$(awk -F: '$2 !~ /^[!*]/ {print $1}' "$mnt/etc/shadow" | xargs)" ""
for unit in ssh.service ssh.socket; do
    state=$(in_chroot systemctl is-enabled "$unit" 2>/dev/null || true)
    check "$unit off" "$([ "$state" = enabled ] && echo "$state" || echo off)" off
done
check "ssh switch files" "$(find "$mnt/boot/firmware" -maxdepth 1 \( -iname ssh -o -iname ssh.txt \) -printf '%f ')" ""
init_format=systemd
if [ -x "$mnt/usr/bin/cloud-init" ]; then
    init_format=cloudinit-rpi
fi
check "cmdline.txt and cloud-init seed" "$(seeds)" "$seed_before"
echo "ok    Imager customisation: $init_format"

fstrim "$mnt" 2>/dev/null || true
unmount_all
e2fsck -fy "$root_dev" >/dev/null || [ $? -le 1 ] || die "the root file system has errors"
for loop in "${loops[@]}"; do
    losetup -d "$loop"
done
loops=()

log "Compressing $name.img.xz"
extract_size=$(stat -c %s "$img")
extract_sha256=$(sha256sum "$img" | cut -d' ' -f1)
xz -T0 -6 -c "$img" > "$work/$name.img.xz"
rm -f "$img"
download_size=$(stat -c %s "$work/$name.img.xz")
download_sha256=$(sha256sum "$work/$name.img.xz" | cut -d' ' -f1)
python3 -I -c '
import json, sys
keys = ("name", "version", "hat", "base_image", "init_format", "extract_size", "extract_sha256",
        "image_download_size", "image_download_sha256", "release_date")
meta = dict(zip(keys, sys.argv[1:]))
for key in ("extract_size", "image_download_size"):
    meta[key] = int(meta[key])
meta["file"] = meta["name"] + ".img.xz"
print(json.dumps(meta, indent=2))' "$name" "$version" "$hat" "$base_name" "$init_format" "$extract_size" \
    "$extract_sha256" "$download_size" "$download_sha256" "$(date -u +%Y-%m-%d)" > "$work/$name.json"
mv "$work/$name.img.xz" "$work/$name.json" "$out/"
(cd "$out" && sha256sum -- *.img.xz > SHA256SUMS)
log "Wrote $out/$name.img.xz ($((download_size / 1048576)) MiB, $((extract_size / 1048576)) MiB unpacked)"
