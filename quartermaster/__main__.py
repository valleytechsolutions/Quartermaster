"""Entry point: `python3 -m quartermaster`, installed as /usr/share/APPLaunch/bin/quartermaster."""

import argparse
import os
import signal
import sys
import threading


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quartermaster", description="Quartermaster wardriver for the CardputerZero")
    parser.add_argument("--no-splash", action="store_true", help="skip the boot animation")
    parser.add_argument("--probe", action="store_true", help="print the adapters and GPS ports found, then exit")
    parser.add_argument("--version", action="store_true", help="print the version, then exit")
    args = parser.parse_args(argv)
    os.umask(0o077)

    from . import __version__, config, hardware, lora
    if args.version:
        print(__version__)
        return 0
    if args.probe:
        for adapter in hardware.wifi_adapters():
            print("wifi  %(interface)s  %(label)s  bands=%(bands)s  usb=%(usb_id)s  driver=%(driver)s" % adapter)
        for adapter in hardware.stalled_usb_adapters():
            print("wifi  (not starting)  %(label)s  usb=%(usb_id)s  port=%(port)s" % adapter)
        for port in hardware.gps_candidates():
            print("gps   %(path)s  %(label)s" % port)
        cap = lora.Cap()
        cap.start()
        cap.wait(10)
        print("lora  Cap LoRa-1262: %s" % ("found" if cap.found else lora.WORDS.get(cap.state, cap.state)))
        return 0

    from . import faults, wigle
    from .engine import DEFAULT_ROOT
    from .gnss import GPS
    from .power import Rails
    from .ui.app import App
    from .ui.display import FramebufferDisplay
    from .ui.keys import EvdevInput

    faults.path = DEFAULT_ROOT / "errors.log"
    cfg = config.load()
    try:
        display = FramebufferDisplay()
    except OSError as exc:
        print("Cannot open the screen (%s). Is this a CardputerZero?" % exc, file=sys.stderr)
        return 2
    keyboard = EvdevInput()
    rails = Rails()
    gps = GPS(cfg.get("gps_source", "auto"), rails=rails, power=cfg.get("gps_power", True))
    stopped = threading.Event()
    threading.Thread(target=gps.run, args=(stopped,), daemon=True, name="gps").start()
    app = App(display, keyboard, gps, cfg, root=DEFAULT_ROOT, splash=not args.no_splash, cap=lora.Cap(rails))
    wigle.prune_empty(app.logs_dir)

    def stop(*_):
        app.running = False
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        app.run()
    finally:
        stopped.set()
        rails.restore_all()
        keyboard.close()
        display.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
