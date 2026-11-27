# Biscuit node

[Back to the guide](README.md)

Biscuit node mode makes Quartermaster a **6 GHz node** for a Biscuit or Biscuit Pro from [BiscuitShop](https://biscuitshop.us). The Biscuit runs the wardrive and logs everything with its own GPS. Quartermaster adds the 6 GHz band, which most Biscuits cannot hear.

In node mode Quartermaster works exactly like the Biscuit Pager node for the Hak5 Pager by GSHD and CodeHedge, so the Biscuit treats every Quartermaster the same way:

- It reports to the Biscuit as a Pager node (`Pager:v1.1.0`), then tells it that it is a Pager 6 GHz node from Quartermaster.
- It scans **6 GHz only**. Once a second it goes back to 2.4 GHz channel 6 for a moment to talk to the Biscuit, and a network it hears there is never sent.
- The Hak5 Pager version of Quartermaster has the same mode on its **Biscuit** tile.

## What you need

- A **6 GHz USB Wi-Fi adapter**: a Wi-Fi 6E adapter with an MT7921AU or MT7925U chip, such as the ALFA AWUS036AXM. The built-in Wi-Fi has no 6 GHz. See [Wi-Fi adapters](wifi-adapters.md).
- A Wi-Fi country that allows 6 GHz. In most countries it does.
- The Biscuit set to **Extended** node mode. Secure node mode does not take Pager nodes. The Biscuit also needs firmware that takes 6 GHz nodes.
- Deep scan's radio helper, which comes with Quartermaster. Node mode uses it even when Deep scan is off.

## Pair with your Biscuit

1. Plug in the 6 GHz adapter and turn on the Biscuit in Extended node mode.
2. Open **Settings > Biscuit node** and press **OK**.
3. Every Biscuit that answers is listed, with **6 GHz ready**, **Secure mode** or **Old firmware** beside it. Move to yours and press **OK**.
4. Quartermaster remembers it and starts node mode at once.

**P** pairs with another Biscuit and **D** forgets this one.

## Use node mode

1. Open **Settings > Biscuit node** and press **OK** to start.
2. Start a wardrive on the Biscuit. The screen says **Scanning** and counts:
   - **6 GHz heard**: networks the adapter heard on 6 GHz.
   - **Sent**: networks sent to the Biscuit.
   - **Updates**: every send, counting a network again when it is heard much closer or after 30 seconds.
   - **Waiting**: networks waiting for the next moment on channel 6.
3. **Esc** goes back. Node mode keeps running and **Settings > Biscuit node** shows its state.
4. Press **OK** on the Biscuit node screen to stop. The adapter goes back to normal.

While node mode runs, a wardrive and Recon wait: node mode scans 6 GHz and nothing else. Stop it first.

| The screen says | What to do |
| --- | --- |
| Searching | Quartermaster is looking for your Biscuit on channel 6. Turn the Biscuit on in Extended node mode. |
| Joined | Start a wardrive on the Biscuit. |
| Secure mode | Set the Biscuit to Extended node mode. |
| Old firmware | This Biscuit's firmware takes no 6 GHz nodes. Update the Biscuit. |
| Plug in a 6 GHz USB adapter | No USB adapter is plugged in. |
| No 6 GHz channels on the USB adapter | The adapter has no Wi-Fi 6E chip, or the Wi-Fi country allows no 6 GHz. |
| The 6 GHz adapter is in use for a network | Disconnect the adapter from the network first. |

## On a DIY Raspberry Pi

Node mode works the same way on a Pi with a 6 GHz USB adapter. **Settings > Biscuit node** has its own layout on both HATs. **OK** searches, pairs, starts and stops. To pair with another Biscuit or forget this one, press **K3** and choose **Pair again** or **Forget**. **K2** goes back.
