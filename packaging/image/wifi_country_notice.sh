#!/bin/sh
rules=${1-$(iw reg get 2>/dev/null)}
country=$(printf '%s\n' "$rules" | sed -n 's/^country \([A-Z0-9][A-Z0-9]\):.*/\1/p' | head -n 1)
[ "$country" = 00 ] || exit 0
printf '\nNo Wi-Fi country is set, so Wi-Fi keeps to the channels that are allowed everywhere.\n'
printf 'Set yours to scan every channel your country allows, 6 GHz included.\n'
printf 'Put your own two-letter country code in place of US:\n  sudo raspi-config nonint do_wifi_country US\n\n'
