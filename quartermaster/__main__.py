"""Entry point for the headless Linux wardriving application."""

import argparse
import json
import os
import signal
import sys
import threading


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quartermaster", description="Quartermaster wardriver")
    parser.add_argument("--no-splash", action="store_true", help="skip the boot animation")
    parser.add_argument("--probe", action="store_true", help="print the adapters and GPS ports found, then exit")
    parser.add_argument("--headless-wardrive", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dashboard", action="store_true",
                        help="run in the foreground with live counts; Ctrl-C stops and saves logs")
    parser.add_argument("--stop", action="store_true", help="stop the background wardrive and finalize logs")
    parser.add_argument("--status", action="store_true", help="show whether a background wardrive is running")
    parser.add_argument("--_background-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--configure-uploads", action="store_true", help="securely enter or update upload keys")
    parser.add_argument("--upload", action="store_true", help="upload finished logs now using saved keys")
    parser.add_argument("--upload-status", action="store_true", help="show the finished-log upload queue without sending")
    parser.add_argument("--export", metavar="DIRECTORY", help="copy finished logs to an external directory")
    parser.add_argument("--show-config", action="store_true", help="show settings; secret values are redacted")
    parser.add_argument("--set", dest="settings", nargs=2, action="append", metavar=("KEY", "VALUE"),
                        help="set a validated scalar setting, for example --set adsb off")
    parser.add_argument("--cardputer-ui", action="store_true",
                        help="launch the legacy Cardputer framebuffer UI instead of the Linux dashboard")
    parser.add_argument("--version", action="store_true", help="print the version, then exit")
    args = parser.parse_args(argv)
    os.umask(0o077)

    from . import __version__, background, config, hardware, lora
    from .engine import DEFAULT_ROOT
    if args.version:
        print(__version__)
        return 0
    if args.configure_uploads:
        return configure_uploads()
    if args.show_config:
        return show_config(config)
    if args.settings:
        return set_config(config, args.settings)
    if args.upload_status:
        return background.run_if_idle(DEFAULT_ROOT, lambda: upload_status(DEFAULT_ROOT),
                                      "Stop the wardrive before checking the upload queue.")
    if args.upload:
        return background.run_if_idle(DEFAULT_ROOT, lambda: upload_finished(DEFAULT_ROOT),
                                      "Stop the wardrive before uploading finished logs.")
    if args.export:
        return background.run_if_idle(DEFAULT_ROOT, lambda: export_finished(DEFAULT_ROOT, args.export),
                                      "Stop the wardrive before exporting finalized logs.")
    if args.stop:
        return background.stop(DEFAULT_ROOT)
    if args.status:
        return background.status(DEFAULT_ROOT)
    if args.probe:
        from . import adsb, ble

        for adapter in hardware.wifi_adapters():
            print("wifi  %(interface)s  %(label)s  bands=%(bands)s  usb=%(usb_id)s  driver=%(driver)s" % adapter)
        for adapter in hardware.stalled_usb_adapters():
            title, detail = hardware.stall_words(hardware.explain(adapter))
            print("wifi  (not starting)  %s  usb=%s  port=%s  %s" % (title, adapter["usb_id"], adapter["port"], detail))
        for port in hardware.gps_candidates():
            print("gps   %(path)s  %(label)s" % port)
        dongles = hardware.rtl_sdr_dongles()
        decoder = adsb.find_decoder()
        print("adsb  %d RTL-SDR device(s); decoder %s" %
              (len(dongles), decoder or "rtl_adsb/readsb not found"))
        print("ble   %s" % ble.probe())
        hat = lora.WaveshareHat()
        hat.start()
        print("lora  Waveshare SX1262 HAT: %s" % ("SPI device present" if hat.found else "not found"))
        return 0

    if args._background_worker:
        from . import headless

        lock_fd = background.worker_root(DEFAULT_ROOT)
        if lock_fd is None:
            return 2
        stopped = threading.Event()
        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

        def stop_worker(*_):
            stopped.set()

        for sig in signals:
            signal.signal(sig, stop_worker)
        try:
            return headless.run(stopped, on_started=background.worker_ready)
        finally:
            background.finish_worker(DEFAULT_ROOT, lock_fd)

    if args.headless_wardrive or args.dashboard:
        from . import headless

        lock_fd = background.acquire_foreground(DEFAULT_ROOT)
        if lock_fd is None:
            return 1
        stopped = threading.Event()
        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        previous = {sig: signal.getsignal(sig) for sig in signals}

        def stop(*_):
            stopped.set()

        try:
            for sig in signals:
                signal.signal(sig, stop)
            return headless.run(stopped)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            background.release_foreground(DEFAULT_ROOT, lock_fd)

    if not args.cardputer_ui:
        return background.start(DEFAULT_ROOT)

    from . import faults, wigle
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
        print("Cannot open the framebuffer display (%s). Use --headless-wardrive on Linux." % exc, file=sys.stderr)
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


def configure_uploads():
    from getpass import getpass

    from . import accounts

    values = {}
    labels = {field: label for field, label, _ in accounts.FIELDS}
    print("Leave a key blank to keep its current value.")
    for field, _, _ in accounts.FIELDS:
        prompt = labels[field] + ": "
        value = input(prompt) if field == "wigle_name" else getpass(prompt)
        if value.strip():
            values[field] = value
    saved = accounts.update(values)
    ready = accounts.ready(saved)
    print("Upload keys saved securely. %d upload service(s) configured." % len(ready))
    return 0


def show_config(config_module):
    cfg = config_module.load(config_module.PATH)
    if cfg.get("cluster_key"):
        cfg["cluster_key"] = "[configured]"
    print(json.dumps(cfg, indent=2, sort_keys=True))
    return 0


def set_config(config_module, assignments):
    cfg = config_module.load()
    settings = {key: (choices or []) for key, _, choices, _ in config_module.SETTINGS}
    updates = {}
    for key, raw in assignments:
        choices = settings.get(key)
        if not choices:
            print("Unknown or non-scalar setting: %s" % key, file=sys.stderr)
            return 2
        match = next(((value, label) for value, label in choices
                      if raw.casefold() in (str(value).casefold(), label.casefold())), None)
        if match is None:
            allowed = ", ".join("%s (%s)" % (value, label) for value, label in choices)
            print("Invalid value for %s. Choose: %s" % (key, allowed), file=sys.stderr)
            return 2
        updates[key] = match[0]
    cfg.update(updates)
    if "adsb" in updates:
        cfg["adsb_set"] = True
    if "country" in updates:
        cfg["country_set"] = True
    config_module.save(cfg, config_module.PATH)
    print("Updated %s: %s" % (config_module.PATH, ", ".join(sorted(updates))))
    return 0


def upload_status(root):
    from . import config, uploads

    status = uploads.Uploader(root, config.load()).status()
    print("Upload queue: %d finished file(s), %d pending upload(s); automatic %s." %
          (status["files"], status["waiting"], "on" if status["auto"] else "off"))
    for service, detail in status["services"].items():
        if detail["configured"] or detail["waiting"] or detail["sent"] or detail["refused"]:
            print("%s: %d pending, %d sent, %d refused%s" %
                  (detail["name"], detail["waiting"], detail["sent"], detail["refused"],
                   " (key configured)" if detail["configured"] else ""))
        else:
            print("%s: no key configured" % detail["name"])
    return 0


def upload_finished(root):
    from . import accounts, config, uploads

    cfg = config.load()
    saved = accounts.load()
    if not accounts.ready(saved):
        print("No upload keys are configured. Run `quartermaster --configure-uploads` first.")
        return 2
    uploader = uploads.Uploader(root, cfg)
    retry = uploader.run_pass()
    status = uploader.status()
    print("Checked %d finished log file(s); %d upload(s) remain pending." %
          (status["files"], status["waiting"]))
    for detail in status["services"].values():
        if detail["last"]:
            print("%s: %s" % (detail["name"], detail["last"]))
    return 1 if retry else 0


def export_finished(root, destination):
    from . import export_logs

    try:
        copied = export_logs.export(root, destination)
    except (OSError, ValueError) as exc:
        print("Could not export logs: %s" % (getattr(exc, "strerror", None) or exc))
        return 1
    print("Exported %d finished log file(s) to %s." % (len(copied), destination))
    if not copied:
        print("No finished log files were found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
