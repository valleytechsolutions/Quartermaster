# Biscuit 6 GHz node

A [Biscuit](https://biscuitshop.us) is an ESP32-C5 wardriver. It scans 2.4 and 5 GHz by itself and takes extra radios, called nodes, over ESP-NOW. With **Biscuit 6 GHz node** on, Quartermaster becomes one of those nodes: a Wi-Fi 6E adapter on your Cardputer listens on 6 GHz and hands every 6 GHz network it hears to the Biscuit, which logs it with its own GPS.

Quartermaster logs the same networks too, with the Cardputer's GPS, so your own log keeps them as well.

## What you need

- A Biscuit with firmware that takes 6 GHz nodes (v1.7.0 or newer), set to **Extended** node mode. **Secure** mode does not work with this node yet.
- A USB Wi-Fi 6E adapter with an **MT7921AU** or **MT7925U** chip, such as the ALFA AWUS036AXM or AXML. See [Wi-Fi adapters](wifi-adapters.md#6-ghz).
- The adapter must not be connected to a network.
- The Wi-Fi country of the Cardputer must allow 6 GHz. Without it there are no 6 GHz channels to listen on.
- The [radio helper](deep-scan.md#the-radio-helper), which the install adds.

## How to use it

1. Turn on **Settings > Deep scan > Biscuit 6 GHz node**.
2. Start a wardrive on the Cardputer. The adapter leaves normal scans and the Wardrive screen shows **BSC**.
3. Start a wardrive on the Biscuit. Quartermaster joins it, and **BSC** turns gold with the number of 6 GHz networks sent.

The **Hardware** screen has a **BSC** line with what the node is doing, how many 6 GHz networks it heard and how many it sent.

## What it does on air

- It listens on the 15 main 6 GHz channels on every sweep, and on a sixth of the other 6 GHz channels each time, so it hears every 6 GHz channel every few sweeps. 6 GHz is only listened to.
- About once a second it goes back to 2.4 GHz channel 6, talks to the Biscuit there and sends what it heard.
- The only frames it sends are the Biscuit's own short ESP-NOW messages, from the adapter's own address, on channel 6. The radio helper refuses to send anything else.
- While node mode runs, the adapter does not scan 2.4 and 5 GHz for Quartermaster. The built-in Wi-Fi, other adapters, nodes and Bluetooth keep scanning as before.

## Messages

| The node says | What it means |
| --- | --- |
| Looking for a Biscuit | No Biscuit answered yet. Keep it close and in Extended mode. |
| Joined. Start a wardrive on the Biscuit. | The Biscuit took the node, but its wardrive has not started. |
| Sending 6 GHz to the Biscuit | Everything works. |
| Biscuit is in Secure mode: set it to Extended | Change the Biscuit's node mode. |
| Biscuit firmware has no 6 GHz nodes: update it | Update the Biscuit's firmware. |
| Needs a USB Wi-Fi 6E adapter (MT7921AU or MT7925U) | Plug in such an adapter before you start the wardrive. |
| No 6 GHz channels: set the Wi-Fi country | The Cardputer's Wi-Fi country does not allow 6 GHz. |

The message format follows the Biscuit Pager Node payload by GSHD and Hedge for the Hak5 WiFi Pineapple Pager. Quartermaster's node is written separately from it.

[Back to the guide](README.md)
