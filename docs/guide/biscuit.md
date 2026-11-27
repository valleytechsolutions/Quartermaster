# Biscuit node

[Back to the guide](README.md)

Biscuit node mode makes Quartermaster a **6 GHz node** for a Biscuit or Biscuit Pro from [BiscuitShop](https://biscuitshop.us). The Biscuit runs the wardrive and logs everything with its own GPS. Quartermaster adds the 6 GHz band, which most Biscuits cannot hear.

In node mode Quartermaster works exactly like the Biscuit Pager node for the Hak5 Pager by GSHD and CodeHedge, so the Biscuit treats every Quartermaster the same way:

- It reports to the Biscuit as a Pager node (`Pager:v1.1.0`) and sends only what the Pager node sends. It says `Pager:v1.1.0` again when the Biscuit adds it, gives it a job or answers it again, and every 30 seconds, so the Biscuit always shows it as a Pager.
- The Biscuit finds it and pairs with it from a node search, as it does the Pager.
- It scans **6 GHz only**. Once a second it goes back to 2.4 GHz channel 6 for a moment to talk to the Biscuit, and a network it hears there is never sent.
- The Hak5 Pager version of Quartermaster has the same mode on its **Biscuit** tile.

## What you need

- A **6 GHz USB Wi-Fi adapter**: a Wi-Fi 6E adapter with an MT7921AU or MT7925U chip, such as the ALFA AWUS036AXM. The built-in Wi-Fi has no 6 GHz. See [Wi-Fi adapters](wifi-adapters.md).
- A Wi-Fi country that allows 6 GHz. In most countries it does.
- The Biscuit set to **Extended** node mode. Secure node mode does not take Pager nodes. The Biscuit also needs firmware v1.7.0 or newer, the release that added Pager nodes.
- Deep scan's radio helper, which comes with Quartermaster. Node mode uses it even when Deep scan is off.

## Add it to your Biscuit

As with the Pager node, pairing is done on the Biscuit, not on Quartermaster.

1. Plug in the 6 GHz adapter and turn on the Biscuit in Extended node mode.
2. Open **Settings > Biscuit node** and press **OK**. The screen says **Waiting**.
3. In the Biscuit manager app, search for nodes. Quartermaster shows up as a Pager node. Add it.
4. The screen says **Joined** and names the Biscuit.

## Use node mode

1. Open **Settings > Biscuit node** and press **OK** to start, if it is not running yet.
2. Start a wardrive on the Biscuit. The screen says **Scanning** and counts:
   - **6 GHz heard**: networks the adapter heard on 6 GHz.
   - **Sent**: networks sent to the Biscuit.
   - **Updates**: every send, counting a network again each time it is sent, at most every 5 seconds.
   - **Waiting**: networks waiting for the next moment on channel 6.
3. **Esc** goes back. Node mode keeps running and **Settings > Biscuit node** shows its state.
4. Press **OK** on the Biscuit node screen to stop. The adapter goes back to normal.

While node mode runs, a wardrive and Recon wait: node mode scans 6 GHz and nothing else. Stop it first.

| The screen says | What to do |
| --- | --- |
| Waiting | Search for nodes in the Biscuit manager app and add this one. The Biscuit must be on, in Extended node mode. |
| Joined | Start a wardrive on the Biscuit. |
| Secure mode | Set the Biscuit to Extended node mode. |
| Old firmware | This Biscuit's firmware takes no 6 GHz nodes. Update it to v1.7.0 or newer. |
| Plug in a 6 GHz USB adapter | No USB adapter is plugged in. |
| No 6 GHz channels on the USB adapter | The adapter has no Wi-Fi 6E chip, or the Wi-Fi country allows no 6 GHz. |
| The 6 GHz adapter is in use for a network | Disconnect the adapter from the network first. |

## On a DIY Raspberry Pi

Node mode works the same way on a Pi with a 6 GHz USB adapter. **Settings > Biscuit node** has its own layout on both HATs. **OK** starts and stops it. **K2** goes back.
