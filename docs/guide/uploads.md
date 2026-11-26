# Uploads

[Back to the guide](README.md)

Quartermaster can send your logs to three sites by itself: **WiGLE**, **WDGWars** and **Wardrift**. Use any one of them, or all three.

| What | WiGLE | WDGWars | Wardrift |
| --- | --- | --- | --- |
| Wi-Fi and Bluetooth logs | Yes | Yes | Yes |
| Aircraft | No | Yes | Yes |
| Mesh nodes | No | Yes | Yes |

## Getting your keys

Each site gives you a key on its own website. Quartermaster never asks for your password.

| Site | Where to find the key |
| --- | --- |
| [WiGLE](https://wigle.net) | Account page, then **Show My Token**. You need both the **API Name** and the **API Token**. On the phone page you can paste the "Encoded for use" line instead. |
| [WDGWars](https://wdgwars.pl) | Profile, then **Generate API key**. |
| [Wardrift](https://wardrift.net) | Dashboard, then **API Keys**. Create a key for a wardriving device. |

## Entering keys from your phone or computer (easiest)

1. Join the Cardputer to Wi-Fi in the CardputerZero settings. Your phone or computer must be on the **same** Wi-Fi.
2. On the menu, press **K**, then **OK** on the first row, "Set up from a phone or PC".
3. Scan the QR code with your phone. The link in the code already holds the PIN, so the page fills it in by itself.
4. Or type the address on the screen into a browser. Then enter the 6-digit PIN shown on the Cardputer.
5. Paste your keys and press Save. The Cardputer says "Keys saved from the setup page".

The page only exists while that screen is open. It closes when you leave the screen. A new PIN is made each time. After 5 wrong PINs it locks; close the screen and open it again for a new PIN. The page never shows a saved key in full, only its last four characters.

The page also lets you:

- download your Wi-Fi and Bluetooth logs (tap a log),
- paste a list of networks to leave out of your logs (the same list as **Settings > Ignored networks**).

The page uses plain HTTP on your local network, protected by the PIN. Only open it on a network you trust.

## Entering keys on the Cardputer

On the menu press **K**, pick a key and press **OK**, then type it. In a text box, **Sym** and a key type the symbol printed on that key, and **Tab** opens a list of every symbol.

| Key on the Keys screen | What it does |
| --- | --- |
| **OK** | Type the key |
| **T** | Test it with the site. Nothing is uploaded |
| **D** | Remove it |

When you save a key, Quartermaster checks it right away. For WiGLE, add both the API name and the API token.

Keys are stored on the Cardputer in a file only your user can read.

## When logs are sent

With **Settings > Send logs** on **Automatic** (the default), finished logs are sent whenever the Cardputer is online. A wardrive's log is sent once you stop the wardrive, never while it is still being written. With **Only when asked**, press **U** on the Logs screen or **OK** on the Uploads screen.

If a site cannot be reached, Quartermaster tries again later, waiting longer each time (1, 2, 5, 10 and 15 minutes).

## The Uploads screen

Press **U** on the menu. The top line says how things stand, for example "Every log has been sent", "2 uploads waiting" or "Add a key to start uploading". Each site shows:

- **NO KEY**, **KEY SAVED** or **KEY REFUSED**,
- how many files were sent, are waiting, or were refused,
- the last thing that happened.

| Key | What it does |
| --- | --- |
| **OK** | Upload now |
| **T** | Test your keys |
| **K** | Go to Keys |

Open a log on the Logs screen to see what each site did with it, and press **U** to send it again.

## Common upload messages

| Message | Meaning |
| --- | --- |
| No internet connection | The Cardputer is offline. It tries again later. |
| The clock is not set yet. Will try again | The Cardputer's clock is not set yet. It tries again later. |
| WiGLE did not accept the name and token | Check your WiGLE API name and token. |
| WiGLE already had it / Wardrift already had it | Nothing to do. It counts as sent. |
| Too large for Wardrift (10 MB limit) | Wardrift does not take files this large. |
| Nothing to send. No node gave its own position | A mesh file with nothing WDGWars can take. |
| Wardrift takes mesh nodes only from a node key | Wardrift refused mesh nodes with this kind of key. |
| Wardrift is busy with another upload. Will try again | It retries by itself. |
