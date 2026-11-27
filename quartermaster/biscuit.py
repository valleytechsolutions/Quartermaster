"""Biscuit node mode: a spare USB adapter with 6 GHz scans 6 GHz for a Biscuit or Biscuit Pro, which logs it all
with its own GPS.

The radio helper runs the node on a monitor interface it owns. It reports to the Biscuit as a Biscuit Pager node
(`Pager:v1.1.0`) and sends what CodeHedge's Pager node sends. As with that node, there is no pairing here: the node
asks on channel 6 until the Biscuit adds it from a node search in the Biscuit manager app. It scans 6 GHz only and
goes to 2.4 GHz channel 6 only to talk to the Biscuit. Closing the helper connection stops the node and puts the
adapter back.
"""

import threading
import time

from . import faults, radio_helper

OFF, STARTING, SEARCHING, JOINED, SCANNING, SECURE, OLD, STOPPING, FAILED = (
    "off", "starting", "searching", "joined", "scanning", "secure", "old", "stopping", "failed")
FIRMWARE = "Pager:v1.1.0"
SIX_GHZ = range(5955, 7116)
HOME = 2437
DRIVERS = ("mt7921u", "mt7925u")


def pick_adapter(adapters):
    """The USB adapter node mode can use: monitor mode, 6 GHz and channel 6 allowed, not in use for a network."""
    usable = [a for a in adapters if a.get("usb") and a.get("monitor") and not a.get("connected") and
              not a.get("default_route") and a.get("driver") in DRIVERS and HOME in a.get("freqs", ()) and
              any(f in SIX_GHZ for f in a.get("freqs", ()))]
    return min(usable, key=lambda a: a["interface"]) if usable else None


def why_none(adapters):
    six = [a for a in adapters if a.get("usb") and any(f in SIX_GHZ for f in a.get("freqs", ()))]
    if not [a for a in adapters if a.get("usb")]:
        return "Plug in a 6 GHz USB adapter, such as an ALFA AWUS036AXM."
    if not six:
        return ("No 6 GHz channels on the USB adapter. It needs a Wi-Fi 6E chip (MT7921AU or MT7925U) and a Wi-Fi "
                "country that allows 6 GHz.")
    return "The 6 GHz adapter is in use for a network. Disconnect it first."


class BiscuitLink:
    """Node mode as the screens see it. One thread owns each run from the helper connection to its close."""

    def __init__(self, device="cardputer", connect=radio_helper.connect, diy=False):
        self.device, self.connect, self.diy = device, connect, diy
        self.state, self.error = OFF, ""
        self.live = {}
        self.adapter = ""
        self.started = None
        self._client = None
        self._thread = None
        self._wanted = False
        self._lock = threading.Lock()

    @property
    def name(self):
        return self.live.get("name") or ""

    @property
    def running(self):
        return self.state not in (OFF, FAILED)

    def start(self):
        with self._lock:
            if self.running:
                return False
            self.state, self.error = STARTING, ""
            self.live, self.adapter, self.started = {}, "", time.monotonic()
            self._wanted = True
            self._thread = threading.Thread(target=self._run, daemon=True, name="biscuit")
            self._thread.start()
        return True

    def _run(self):
        client = None
        try:
            client, state, detail = self.connect()
            if client is None:
                raise radio_helper.HelperError(radio_helper.words(state, detail, diy=self.diy))
            self._client = client
            adapter = pick_adapter(client.request("adapters")["adapters"])
            if adapter is None:
                raise radio_helper.HelperError(why_none(client.request("adapters")["adapters"]))
            self.adapter = adapter["interface"]
            vif = client.request("monitor_start", iface=adapter["interface"])["vif"]
            client.biscuit = self._message
            client.request("biscuit_node", vif=vif, device=self.device)
            if self._wanted:
                self.state = SEARCHING
            while self._wanted and not client.closed.wait(0.5):
                pass
            if self._wanted:
                self.error = self.error or "The radio helper stopped."
        except radio_helper.HelperError as exc:
            self.error = str(exc)[:120] or "Node mode could not start."
        except Exception:
            faults.note("Biscuit node")
            self.error = "Node mode hit a problem. See errors.log."
        finally:
            self._client = None
            if client is not None:
                client.close()
            with self._lock:
                self.state = FAILED if self._wanted else OFF
                self._wanted = False

    def _message(self, message):
        kind = message.get("kind")
        if kind == "status":
            self.live = message
            if self._wanted and message.get("state") in (
                    SEARCHING, JOINED, SCANNING, SECURE, OLD):
                self.state = message["state"]
        elif kind == "error":
            self.error = str(message.get("error", ""))[:120]
            client = self._client
            if client is not None:
                client.close()

    def stop(self, wait=False):
        with self._lock:
            thread = self._thread
            if self.running and self.state != STOPPING:
                self._wanted = False
                self.state = STOPPING
                client = self._client
                if client is not None:
                    client.close()
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(10)

    def status(self):
        return dict(self.live, state=self.state, error=self.error, adapter=self.adapter,
                    seconds=int(time.monotonic() - self.started) if self.started and self.running else 0)
