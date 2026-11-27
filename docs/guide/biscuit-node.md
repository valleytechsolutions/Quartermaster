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

- It works the way CodeHedge's Biscuit Pager Node payload for the Hak5 WiFi Pineapple Pager does, and it comes up on the Biscuit as a **Pager** (firmware `Pager:v1.1.0`). Every Quartermaster version does the same, so you only ever see one kind of 6 GHz node.
- It listens 110 ms on each channel: the 15 main 6 GHz channels on three sweeps, and all 59 6 GHz channels on every fourth. 6 GHz is only listened to.
- After about 0.9 s of listening it goes back to 2.4 GHz channel 6 for 150 ms. There it talks to the Biscuit and sends what it heard. Each network is sent again after 5 s at the soonest.
- If the Biscuit stops answering for 20 s, the node looks for it again.
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

The reference for this node is CodeHedge's [Biscuit Pager Node payload](https://github.com/hak5/wifipineapplepager-payloads/pull/396) by GSHD and Hedge, for the Hak5 WiFi Pineapple Pager. Quartermaster's node is written separately but behaves the same way.

[Back to the guide](README.md)
