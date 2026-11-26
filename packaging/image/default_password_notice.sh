#!/bin/sh
shadow=${1:-/etc/shadow}
record=${2:-/usr/local/lib/quartermaster-image/default-login}
[ -r "$shadow" ] && [ -r "$record" ] || exit 0
users=$(awk -F: 'NR == FNR { if (hash == "") hash = $0; next } hash != "" && $2 == hash { print $1 }' "$record" "$shadow" | xargs)
[ -n "$users" ] || exit 0
printf '\nThe login %s still has the default password. Type passwd to set your own.\n\n' "$users"
