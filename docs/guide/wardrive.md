# The wardrive screen and keys

[Back to the guide](README.md)

## Moving around

| Key | What it does |
| --- | --- |
| **F X Z C** or the arrow keys | Up, down, left, right (F, X, Z and C have arrows printed on them) |
| **OK** (Enter) | Open or confirm |
| **Esc** | Back |
| **Space** | On the menu: start or stop a wardrive |
| **W R L U H S K A** | On the menu: jump to Wardrive, Recon, Logs, Uploads, Hardware, Settings, Keys or About |
| **Esc** or **Q** on the menu | Quit Quartermaster. It asks first, with **Cancel** picked: move to **Quit** with **Z** or **C** and press **OK**, or press **Y** |

In a text box (for example when you type a key):

| Key | What it does |
| --- | --- |
| **Sym**, then a key | Types the symbol printed on that key |
| **Tab** | Opens a list of every symbol to pick from |
| **Del** | Erases |
| **OK** | Saves |
| **Esc** | Cancels |

## The wardrive screen

Start a wardrive with **W** or **Space** on the menu.

At the top are three counters:

- **WI-FI**: Wi-Fi networks heard.
- **BLUETOOTH**: Bluetooth devices heard.
- **LOGGED**: rows written to the log.

Under them:

- **2.4 GHz, 5 GHz, 6 GHz** with a number each: networks per band.
- **!3** (for example): alerts on this drive. Red when one of them is urgent. See [Drive Alerts](alerts.md).
- **Q 9m** (for example): alerts are quiet for 9 more minutes.
- **DEEP**: [Deep scan](deep-scan.md) is listening. **DEEPx3** means three adapters listen. **F2** means it heard Flock frames twice. This counts sightings, not cameras, so one camera can raise it several times.
- On the right: how many radios and nodes scan, and, when you have them, the number of aircraft and mesh nodes.

Below that, one line in amber or red appears when something needs your attention, such as "GPS fix lost. Logging pauses until it is back." See [Troubleshooting](troubleshooting.md).

The list at the bottom shows the newest devices heard. For Wi-Fi each row shows the band, the name ("(hidden)" when the network sends no name), the security, the channel and the signal strength. Bluetooth rows show **BT**. Networks on your ignore list show **IGNORED** and are never written to the log.

## Keys on the wardrive screen

| Key | What it does |
| --- | --- |
| **OK** or **S** | Stop the wardrive. It asks first. Press **OK** again to stop and save. Or choose **Stop, don't save**. |
| **Tab** (or **Z** / **C**) | Show Wi-Fi and Bluetooth, only Wi-Fi, or only Bluetooth |
| **A** | The alerts of this wardrive (when Drive Alerts are on) |
| **Q** | No alert tones or banners for 10 minutes. Press again to end it |
| **Del** | Hide the alert banner on screen. With no banner showing, Del goes back like Esc |
| **Esc** | Back to the menu. The wardrive keeps running. Press **W** to come back to it |

While the wardrive waits for a GPS fix, **OK** shows "Stop" instead of "Stop + save", because there is nothing to save yet.

Settings in the Scanning, Deep scan, Logging and GPS sections cannot be changed during a wardrive. Settings says "Stop the wardrive to change this setting." All other settings, such as Drive Alerts, change at once.

Recon and a wardrive never run together. If you start one while the other runs, Quartermaster asks first and stops the other.

## Stopping without saving

For a test drive you do not want to keep, the stop question has three choices: **Cancel**, **Stop + save** and **Stop, don't save**. "Stop + save" is the one selected, so pressing **OK** twice always saves.

1. Press **OK** on the wardrive screen.
2. Press **C** (or move right) to **Stop, don't save**, then **OK**.
3. It asks "Throw away this wardrive?" and says how many networks will be lost. Choose **Throw away** and press **OK**.

That wardrive's log, summary, aircraft, mesh and alert files are removed from the device, and nothing more of it is uploaded. Files from earlier wardrives are not touched. This cannot be undone.

On a very long drive the log is split into parts of 8 MB, and a finished part may be uploaded while you still drive. Such a part stays on the upload site. The question then says "Parts already uploaded stay on the upload sites", and the notice after it says "Uploaded parts stay on the sites."

If a file could not be removed, the notice says "Some files of this wardrive could not be removed". What stays can come back as a log the next time Quartermaster starts. Delete it then on the Logs screen.

On a Raspberry Pi screen the question shows one choice at a time, with what that choice does. Move the stick right for **Stop, don't save** and left for **Cancel**.

## Low battery

At 5 % battery, off the charger, a running wardrive is saved in full at once and keeps recording. See [Battery](first-start.md#battery).

## When you stop

The summary shows the numbers of the drive and the file it was saved as. It also says what happens next, for example "Uploading to WiGLE, WDGWars." or "Press K to add your upload keys."

| Key | What it does |
| --- | --- |
| **L** | Logs |
| **U** | Uploads |
| **K** | Keys |
| **W** | Start a new wardrive |
| **Esc** or **OK** | Back to the menu |

If Drive Alerts found something, a short note lists the alerts of the drive.

## Logs screen

Each saved log shows its date and size, and one dot per upload site (WiGLE, WDG, DRIFT):

- green dot: sent,
- amber ring: waiting to be sent,
- red dot: refused (open the log to see why),
- dash: this file is not for that site (aircraft and mesh files never go to WiGLE),
- thin empty ring: no key saved for that site.

**AIR** marks an aircraft file, **MESH** a mesh file, **REC** the log being written now.

Keys: **OK** opens a log, **U** uploads now. In a log: **U** sends it again, **D** deletes it.

## Starting a wardrive by itself

Set **Settings > Start wardrive on launch** to **Yes** and Quartermaster skips the menu and starts scanning when it opens.
