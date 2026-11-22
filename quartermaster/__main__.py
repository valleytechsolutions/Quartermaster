"""Entry point: `python3 -m quartermaster` (installed as /usr/share/APPLaunch/bin/quartermaster)."""

import argparse
import os
import signal
import sys
import threading


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quartermaster", description="Quartermaster wardriver for the CardputerZero")
    parser.add_argument("--no-splash", action="store_true")
    parser.add_argument("--probe", action="store_true", help="print the adapters and GPS ports found, then exit")
    args = parser.parse_args(argv)
    os.umask(0o077)

    from . import config, hardware
    if args.probe:
        for adapter in hardware.wifi_adapters():
            print("wifi  %(interface)s  %(label)s  bands=%(bands)s  usb=%(usb_id)s  driver=%(driver)s" % adapter)
        for port in hardware.gps_candidates():
            print("gps   %(path)s  %(label)s" % port)
        return 0

    from .gnss import GPS
    from .ui.app import App
    from .ui.display import FramebufferDisplay
    from .ui.keys import EvdevInput

    cfg = config.load()
    try:
        display = FramebufferDisplay()
    except OSError as exc:
        print("Cannot open the screen (%s). Is this a CardputerZero?" % exc, file=sys.stderr)
        return 2
    keyboard = EvdevInput()
    gps = GPS(cfg.get("gps_source", "auto"))
    stopped = threading.Event()
    threading.Thread(target=gps.run, args=(stopped,), daemon=True, name="gps").start()
    app = App(display, keyboard, gps, cfg, splash=not args.no_splash)

    def stop(*_):
        app.running = False
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        app.run()
    finally:
        stopped.set()
        keyboard.close()
        display.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
