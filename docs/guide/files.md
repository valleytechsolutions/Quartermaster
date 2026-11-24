# Files and logs

[Back to the guide](README.md)

Everything Quartermaster writes is in the `quartermaster` folder in your home folder (`~/quartermaster`). Your settings and keys are in `~/.config/quartermaster`. Removing the app keeps all of them.

## The quartermaster folder

| File | What it is |
| --- | --- |
| `logs/quartermaster_<date>-<time>.wigle.csv` | The wardrive, in the WiGLE format (WigleWifi-1.6). This is what goes to WiGLE, WDGWars and Wardrift. |
| `logs/quartermaster_<date>-<time>.summary.csv` | One line per device: name, security, channel, first and last seen, and the strongest and weakest sighting with position and time. For you, not uploaded. |
| `logs/quartermaster_<date>-<time>.aircraft.json.gz` | The aircraft of the wardrive, for WDGWars and Wardrift. |
| `logs/quartermaster_<date>-<time>.aircraft.csv` | One line per aircraft, for you. |
| `logs/quartermaster_<date>-<time>.mesh.json.gz` | The Meshtastic and MeshCore nodes of the wardrive, for WDGWars and Wardrift. |
| `logs/quartermaster_<date>-<time>.mesh.csv` | One line per mesh node, for you. |
| `recon/recon_<date>-<time>.jsonl` | What Recon flagged. |
| `recon/drive_<date>-<time>.jsonl` | What Drive Alerts found during a wardrive, alerted or not. Never uploaded. |
| `uploads.json` | Which site already has which file. |
| `errors.log` | The reason, when something goes wrong. Attach it when you report a problem. |

Files whose name starts with a dot in `logs` are wardrives still being written. After a crash or a power cut, Quartermaster finishes that log the next time it opens and says "Finished the log of your last wardrive."

A log is split into parts at 8 MB, so that every site accepts it. Each new part is a new file, named after the time of its first row. A `-1`, `-2` and so on is only added when that name is already taken, for example when two files start in the same second.

## How rows are chosen

By default each device has at most two rows in a log: its **strongest** sighting (the closest pass) and its **weakest** (the far edge of its range), each with its own position and time. A very weak sighting (below -92 dBm), or one whose position is less sure than 25 m, is not used as the far edge. With **Settings > Rows per device** on **Full track**, more rows are added as you move.

Every row needs a fresh GPS fix and a set clock, so WiGLE accepts every row.

## The settings folder

| File | What it is |
| --- | --- |
| `~/.config/quartermaster/config.json` | Your settings. |
| `~/.config/quartermaster/accounts.json` | Your upload keys. Only your user can read it. |

## Copying logs off the Cardputer

- Open the [phone setup page](uploads.md#entering-keys-from-your-phone-or-computer-easiest) and tap a log to download it. The page offers the WiGLE logs and summaries.
- Or use SFTP (file transfer over SSH) from a computer, and copy the `~/quartermaster/logs` folder.

## Deleting a log

On the Logs screen, open the log and press **D**. Its summary file is deleted with it.
