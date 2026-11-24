# Install and update

[Back to the guide](README.md)

## From the CardputerZero AppStore (easiest)

1. Open the **AppStore** app on the Cardputer.
2. Search for **Quartermaster**, or enter the share code **WARD**.
3. Install it.

To update later, open the AppStore app again. It lists the apps that have a newer version. Press **7** to update them.

**"Not found" (404) right after a new release.** This has been seen on a CardputerZero when a new version had just come out. Wait a few minutes and try again. (This is the AppStore, not Quartermaster. Quartermaster's own update check says "Could not check. Try again later." when it gets no proper answer.)

## With one command (newest version first)

New versions reach GitHub first and the AppStore after a review. To get the newest one now, open a terminal on the Cardputer, or connect to it over SSH (a way to type commands on it from another computer), and paste:

```sh
wget -qO /tmp/quartermaster.deb https://github.com/valleytechsolutions/Quartermaster/releases/latest/download/quartermaster.deb && sudo apt install -y /tmp/quartermaster.deb
```

The same command also updates Quartermaster.

### Use apt, not dpkg

Quartermaster needs a few other packages, for example `rtl-sdr` for aircraft and `usb-modeswitch` for some Wi-Fi adapters. `sudo apt install -y /tmp/quartermaster.deb` fetches those for you. The older command `sudo dpkg -i` does not, so parts of Quartermaster may not work. If you already used `dpkg -i`, run `sudo apt install -f` to fetch what is missing.

## After the first install: restart once

Restart the Cardputer once after the first install. The install gives your user new permissions (for the RTL-SDR stick, the Cap LoRa-1262, Bluetooth, the speaker and the radio helper). They only apply after a restart. Until then you may see messages such as "No permission yet. Restart the Cardputer once."

Then open **Quartermaster** from the launcher.

## Checking for updates

**Settings > Check for updates** asks the AppStore and GitHub if there is a newer version. Nothing is downloaded. You see one of these:

| Message | What it means |
| --- | --- |
| "X is ready. Open the AppStore and press 7 to update it." | The AppStore has a newer version. |
| "X is on GitHub. It reaches the AppStore after review." | Only GitHub has it so far. Use the one command above, or wait. |
| "You have the latest version (X)." | Nothing to do. |
| "Could not check. Is Wi-Fi connected?" | The Cardputer is not online. |
| "Could not check. Try again later." | A site did not answer properly. |

Press **OK** on that screen to check again.

## Removing Quartermaster

```sh
sudo apt remove quartermaster
```

Your logs and settings stay where they are. See [Files and logs](files.md).
