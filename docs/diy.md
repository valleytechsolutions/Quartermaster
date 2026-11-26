# Build a DIY Quartermaster

Use a Raspberry Pi, either of Raspyjack's joystick HATs, and a GPS to build a handheld Quartermaster. The package installs on Raspberry Pi OS. Keep your current SD card if you already use Raspyjack and want to switch between the two systems.

## Parts

| Part | What to use |
| --- | --- |
| Computer | Raspberry Pi Zero 2 WH, or Zero 2 W with a soldered 40-pin header. Pi 3B, 3B+ and 4B also have the header. The original Zero W/WH requires 32-bit Raspberry Pi OS and has a slower single-core CPU. |
| Display | Pick one of the HATs below. Both plug directly onto the 40-pin header and include the controls. |
| Storage and power | A microSD card for Raspberry Pi OS Lite and logs, and a power supply or USB battery sized for the Pi and its peripherals. |
| Position | A USB GPS that outputs NMEA, or a receiver available through local `gpsd`. Quartermaster waits for a valid fix and clock before it writes WiGLE rows. |
| Optional radios | A supported USB Wi-Fi adapter for additional bands, USB Quartermaster nodes, or an RTL-SDR for aircraft. A Zero has one USB OTG data port. Use an OTG hub when connecting multiple USB peripherals and provide enough power for the radios. |

### Which HAT do you have?

| Waveshare 1.44inch LCD HAT | Waveshare 1.3inch LCD HAT |
| --- | --- |
| [![Front of the 1.44-inch Waveshare LCD HAT, with the joystick to the left of the display and three buttons to the right](https://www.waveshare.com/media/catalog/product/1/_/1.44inch-lcd-hat-1.jpg)](https://www.waveshare.com/1.44inch-lcd-hat.htm) | [![Front of the 1.3-inch Waveshare IPS LCD HAT, with three buttons to the left of the display and the joystick to the right](https://www.waveshare.com/media/catalog/product/1/_/1.3inch-lcd-hat-1.jpg)](https://www.waveshare.com/1.3inch-lcd-hat.htm) |
| **128×128 · ST7735S** · `st7735-128` (default) | **240×240 · ST7789** · `st7789-240` |
| [1.44″ product gallery](https://www.waveshare.com/1.44inch-lcd-hat.htm) | [1.3″ product gallery](https://www.waveshare.com/1.3inch-lcd-hat.htm) · [1.3″ hardware guide](https://www.waveshare.com/wiki/1.3inch_LCD_HAT) |

Photos by Waveshare show the bare HATs. See [First drive](#first-drive) for what the Quartermaster interface looks like on each screen size.

The Zero 2 W's onboard Wi-Fi covers 2.4 GHz. A 3B+ or 4B adds onboard 5 GHz. For 6 GHz, add a Linux-supported tri-band adapter and check the bands shown under **Hardware**. The Cardputer's Cap LoRa-1262, Grove port and power rails are specific to the M5Stack hardware. Use USB GPS or gpsd on a Pi. Battery percentage requires a power supply that reports a system battery to Linux. [Pi Zero specifications](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) · [Quartermaster adapter list](https://github.com/valleytechsolutions/Quartermaster#wi-fi-adapters)

## Easiest way: flash a ready image

Each Raspberry Pi release has two ready SD card images, one for each HAT. They boot straight into Quartermaster. You do not need to type any commands.

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer. Use the newest version.
2. Download [quartermaster.rpi-imager-manifest](https://github.com/valleytechsolutions/Quartermaster/releases/download/pi-v1.2.9/quartermaster.rpi-imager-manifest) and open it. It is a small file that tells Imager where the two images are. Imager starts with the Quartermaster images in its list.

   If opening the file does not start Imager, open Imager yourself. Click **App Options**, then **Edit** beside **Content Repository**. Choose **Use custom file**, pick the file you downloaded and click **Apply & Restart**.
3. Choose your Pi model and click **Next**.
4. Click **Quartermaster 1.2.9**, choose the image for your HAT and click **Next**. Imager downloads the image for you.

| Your HAT | Image to choose |
| --- | --- |
| 1.44″ HAT (128×128) | Quartermaster 1.2.9 for the Waveshare 1.44inch LCD HAT (128×128) |
| 1.3″ HAT (240×240) | Quartermaster 1.2.9 for the Waveshare 1.3inch LCD HAT (240×240) |

5. Choose your microSD card and click **Next**. Use one of 8 GB or more.
6. Imager shows its set-up pages one after another. Go through them with **Next**. You can leave a page empty, except **Localisation**: there **Next** works only after you choose a city. We recommend filling in two pages.
   - **Localisation**: choose your capital city. This sets your Wi-Fi country, so the Pi scans every Wi-Fi channel your country allows.
   - **Wi-Fi**: enter your network if you want to log in to the Pi over your network. Imager may fill in the network your computer uses. A Zero W, Zero 2 W or 3B can only join a 2.4 GHz network, so if your router has a separate 5 GHz name, type the 2.4 GHz name and its password instead.

   A username and password set on the **User** page replace the default login below. On the last page, **Remote access**, you can click **Next** without changing anything, because SSH is already on. If you choose public-key authentication there, fill in the **User** page too. Otherwise the login is named after your computer account, has no password and cannot use `sudo`.

   Do not use **Skip customisation** unless you want none of this. It throws away what you entered on every page, your Wi-Fi network included, and writes the card with the default login.
7. Write the card. With the Pi powered off, seat the HAT on the GPIO header with pin 1 lined up, and plug in the GPS. On a Zero use the micro-USB port marked **USB** for data and **PWR IN** for power.
8. Put the card in the Pi and power it on. The first start takes a few minutes, and the Pi may restart once. Then the Quartermaster menu appears on the HAT.

### Log in

- On Windows, open **Terminal** or **PowerShell** from the Start menu and type the commands below there. Nothing shows on the screen while you type a password. That is normal.
- The default login is user `pi` with password `pi`. SSH is on.
- Connect from a computer on the same network with `ssh pi@quartermaster.local`. If that name is not found, use the Pi's address from your router: `ssh pi@<address>`.
- **Change the password right away.** Log in, type `passwd` and follow the prompts. Anyone on the same network can log in with the default password, so do this first on shared or public Wi-Fi. The Pi reminds you each time you log in until you have changed it.
- A username and password set in Raspberry Pi Imager replace the default: `pi` with password `pi` then no longer works. Log in with `ssh <your user>@quartermaster.local`. A hostname set in Imager replaces `quartermaster` too.
- The Pi needs a network for SSH: set your Wi-Fi in Imager, or plug in an Ethernet cable on a 3B, 3B+ or 4B. If a Zero W, Zero 2 W or 3B never shows up, check that you gave Imager your 2.4 GHz network, and write the card again if you did not.
- Each Pi makes its own SSH host keys on its first start. Your computer asks you to trust the new key the first time you connect. Type `yes` to go on.
- Saved drives are in `~/quartermaster/logs`, Recon and Drive Alert logs in `~/quartermaster/recon` and aircraft captures in `~/quartermaster/aircraft`. Copy the drives to your computer with `scp "pi@quartermaster.local:quartermaster/logs/*" .`, and the other two folders the same way. The real folders are in `/var/lib/quartermaster/quartermaster/`. Any user in the `_quartermaster` group can read them. Add a user with `sudo usermod -aG _quartermaster <user>`.

### Good to know

- **Wrong HAT?** Put the card in your computer and open the drive called **bootfs**. Open `quartermaster.txt` in a text editor. Change the last line to `display=st7735-128` for the 1.44″ HAT or `display=st7789-240` for the 1.3″ HAT. Save it, then put the card back in the Pi. Windows may also show another drive from the card and offer to format it. Click **Cancel**, because formatting it erases Quartermaster.
- **Wi-Fi country.** Wi-Fi and Bluetooth are switched on from the first start, so scanning works right away. The image does not choose a country for you. Until you set yours, Wi-Fi keeps to the channels that are allowed everywhere, which leaves out 6 GHz and a few other channels. Set it on Imager's **Localisation** page in step 6, or log in and run `sudo raspi-config nonint do_wifi_country US` with your own two-letter country code in place of `US`. The Pi reminds you at each login until a country is set.
- Quartermaster runs under its own built-in `_quartermaster` account, which has no password and cannot log in. It starts the same way whichever login user you have.
- **Flashing an image file yourself.** Each [Pi release](https://github.com/valleytechsolutions/Quartermaster/releases/tag/pi-v1.2.9) also has the two image files under **Assets**, each named after its HAT: `quartermaster-pi-1.2.9-waveshare-1.44inch-lcd-hat.img.xz` and `quartermaster-pi-1.2.9-waveshare-1.3inch-lcd-hat.img.xz`. Write one with **Use custom** in Imager or with balenaEtcher. Neither needs the file unpacked. Some other tools, such as Win32 Disk Imager, need it unpacked first. Raspberry Pi Imager 2 shows no set-up pages for a file chosen this way, so the card starts with the default login and without your Wi-Fi network or country. Set the country as shown above, and join a network from the Pi with `sudo nmtui`. To reach the Pi for that, use an Ethernet cable on a 3B, 3B+ or 4B, or plug in a screen and a keyboard.
- The images work on the Zero W, Zero 2 W, 3B, 3B+ and 4B. The images of the newest Pi release are rebuilt every month with the latest Raspberry Pi OS updates.
- **Updating.** To update only Quartermaster, log in and run the commands of step 3 under [Assemble and install manually](#assemble-and-install-manually), then restart the Pi with `sudo reboot`. Skip steps 4 and 5 there: the image has already done them. You can also flash the newest image. Flashing erases the card, so copy your saved drives to your computer first. After flashing, the Pi has new SSH keys and your computer may refuse to connect. Run `ssh-keygen -R quartermaster.local` on the computer once, then connect again.
- To start faster, the image does not check for system updates in the background. Run `sudo apt update && sudo apt full-upgrade` when you want them.
- The first start grows the card's file system to the full size of the card. You do not need to do anything.
- The app's own log is `sudo journalctl -b -u quartermaster@_quartermaster.service`.

Use the steps below instead if you want to install Quartermaster on a card you already use.

## Assemble and install manually

1. With the Pi powered off, align pin 1 and seat the HAT on the GPIO header. Plug in the GPS. On a Zero use the micro-USB port marked **USB** for data and **PWR IN** for power.
2. For a new build, write Raspberry Pi OS Lite (Trixie) to the microSD. Zero 2 W, 3B, 3B+ and 4B can use 32-bit or 64-bit. The original Zero W needs 32-bit. RaspyJack's DIY guide uses the 32-bit edition. In Raspberry Pi Imager configure a username, network and SSH. If your existing build runs Raspberry Pi OS Bookworm with the standard Raspberry Pi repositories, you can install on that card starting at step 3.
3. Boot the Pi and connect over SSH. Download the installer and checksum from the [Quartermaster Pi 1.2.9 release](https://github.com/valleytechsolutions/Quartermaster/releases/tag/pi-v1.2.9). Run these commands in a new directory on the Pi.

```sh
release=https://github.com/valleytechsolutions/Quartermaster/releases/download/pi-v1.2.9
wget "$release/quartermaster-pi_1.2.9_all.deb" "$release/SHA256SUMS"
sudo apt update
sudo raspi-config nonint do_spi 0
sha256sum --check SHA256SUMS && sudo apt install ./quartermaster-pi_1.2.9_all.deb
```

Check that the checksum command reports `OK` before installing. To build the same version from source, check out `pi-v1.2.9` and run `python3 tools/build_deb.py --target pi` on Raspberry Pi OS or Debian Trixie. The Pi package is written to `build/`. Use that path for the install command.

4. The installed display setting defaults to the 1.44″ HAT. For the **1.3″ HAT**, select its profile before rebooting.

```sh
echo st7789-240 | sudo tee /etc/quartermaster/display
```

5. For a self-contained handheld, enable the display service for your login user and reboot. The reboot also applies SPI and device group permissions.

```sh
sudo systemctl enable "quartermaster@$USER.service"
sudo reboot
```

The Pi now opens Quartermaster on the HAT. To launch it manually instead, leave the service disabled, reboot once, and run `quartermaster` as your normal user. Setup does not require the Raspyjack software. If using the same OS installation for both apps, stop the other display service before starting Quartermaster so only one app owns the SPI screen and buttons.

## First drive

**Quartermaster screen previews:** 1.44″ (128×128) on top, 1.3″ (240×240) on the bottom. These are software-rendered previews with sample data, not photos of a running build.

![Menu, scanning settings, wardrive, stop confirmation and hardware on both Waveshare HATs](screens/waveshare-preview.png)

1. Move the joystick through the menu. Click the stick or press **K1** to select. **K2** goes back. **K3** opens the actions for the current screen. It opens an on-screen keyboard inside a text field.
2. Open **Hardware**. Check that your GPS has a fix and that the expected Wi-Fi adapters and bands appear. A GPS may need a clear view of the sky.
3. Choose **Wardrive**. The square dashboard shows GPS and radio counts. Press **OK** to stop. The question shows one choice at a time, with what it does: **Stop + save** comes first, so **OK** again saves the drive. Move right for **Stop, don't save**, which asks once more and then keeps nothing of that drive, or left for **Cancel**. Open **Logs** to see the saved drive. Logs live under `~/quartermaster/logs/` for the user running the app.
4. Open **Keys** for phone setup or use a USB keyboard for longer text. On detailed screens, choose **K3 → Pan details** and move the stick to read across the original layout. Press **K2** to return to normal navigation.

## Checking a build

- `quartermaster --probe` reports the selected profile and detected adapters and GPS ports. The profile is the configured choice. To verify the panel, look for readable text, correct colors, and all four edges of the menu.
- If the screen stays blank, check the profile in `/etc/quartermaster/display`, verify `/dev/spidev0.0` exists, and read `journalctl -b -u "quartermaster@$USER.service"`. On a ready image the service is `quartermaster@_quartermaster.service`, and `quartermaster.txt` on the boot partition sets the profile at every start. Both HATs use SPI0 CE0. The backlight, display and joystick need access to the Pi's GPIO.
- A physical acceptance check for each HAT includes every joystick direction, the center press, K1–K3, a GPS fix, a saved drive and a reboot back to the menu. Frame rendering and simulated I/O tests cover both profiles. Panel orientation, radio reception and runtime on battery require an assembled unit.
