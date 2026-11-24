"""Deep scan: one spare USB adapter listens in monitor mode while a wardrive or Recon runs.

Which adapter listens is decided so that no band loses its managed scans: every band of the listening adapter must
still be swept by another adapter, and 6 GHz by another 6 GHz adapter, because the monitor never goes there. The
built-in radio, an adapter on a network and the only 6 GHz adapter always keep scanning normally.

The listen pattern follows flock-you (11, 6 and 1 at 250 ms, downwards, as Flock cameras hop upwards every 125 ms)
and SquachWatch (the other 2.4 GHz channels by how busy they are, and a slice of the busy 5 GHz channels).
"""

import multiprocessing
import os
import queue
import threading
import time

from . import faults, planner, radio_helper

DRIVERS = frozenset({"mt7921u", "mt7925u", "mt76x2u", "mt76x0u", "rtw88_8822bu", "rtw88_8821cu", "ath9k_htc"})
FLOCK_CHANNELS = (11, 6, 1)
FLOCK_DWELL, FILL_DWELL, FILL_BUDGET = 250, 110, 900
SLICE5_MS, LOOK5_BUSY, LOOK5_QUIET = 1500, 250, 150
SLOW_SWITCH_MS, SLOW_DWELL = 40, 150
SWITCH_MS = 30
CPU_LIMIT = 25.0
FORWARD_QUEUE = 64
REASONS = {
    "builtin": "Built-in: always scans normally",
    "only6": "Only 6 GHz radio: scans normally",
    "no_monitor": "No monitor mode in this driver",
    "connected": "Connected to a network: left alone",
    "band": "Needed to keep its bands scanned",
    "other": "Another adapter listens",
    "chosen": "Not the adapter picked in Settings",
}


def _is_alfa(adapter):
    """By the name the adapter gives itself; the label is shared by every adapter with the same chip id."""
    text = str(adapter.get("product") or adapter.get("label") or "").lower()
    return "alfa" in text or "awus036ax" in text.replace(" ", "")


def assign(adapters, nodes=(), helper=None, choice="auto"):
    """Roles for the adapters of a wardrive: {"monitor": adapter or None, "roles": {interface: "monitor" | "managed"},
    "reasons": {interface: why it is not the monitor}, "unavailable": why there is no monitor, or ""}.

    helper: the radio helper's `adapters` answer, which says which adapter is connected or carries the default route.
    nodes: the bands ("wifi24", "wifi5") of the nodes in use; they cover 2.4 and 5 GHz but never 6 GHz."""
    live = {a.get("interface"): a for a in helper or ()}
    roles = {a["interface"]: "managed" for a in adapters}
    reasons = {}
    node_bands = set()
    for band in nodes:
        node_bands.add({"wifi24": "2.4", "wifi5": "5"}.get(band, band))
    node_bands &= {"2.4", "5"}
    candidates = []
    for a in adapters:
        name = a["interface"]
        state = live.get(name, {})
        if not a.get("external"):
            reasons[name] = REASONS["builtin"]
        elif not a.get("monitor") or a.get("driver") not in DRIVERS or state.get("monitor") is False:
            reasons[name] = REASONS["no_monitor"]
        elif state.get("connected") or state.get("default_route"):
            reasons[name] = REASONS["connected"]
        elif not _covered(a, adapters, node_bands):
            reasons[name] = REASONS["only6"] if "6" in a.get("bands", ()) and not any(
                o is not a and "6" in o.get("bands", ()) for o in adapters) else REASONS["band"]
        else:
            candidates.append(a)
    picked = None
    if choice not in (None, "", "auto"):
        picked = next((a for a in candidates if a["interface"] == choice), None)
        if picked is None:
            why = reasons.get(choice) or ("Not plugged in" if choice not in roles else REASONS["band"])
            return {"monitor": None, "roles": roles, "reasons": reasons,
                    "unavailable": "%s: %s" % (choice, why)}
    elif candidates:
        picked = sorted(candidates, key=lambda a: ("6" in a.get("bands", ()), _is_alfa(a),
                                                   adapters.index(a)))[0]
    if picked is None:
        if not any(a.get("external") for a in adapters):
            why = "Needs a USB Wi-Fi adapter with monitor mode"
        elif len([a for a in adapters if a.get("external")]) == 1:
            only = next(a for a in adapters if a.get("external"))
            why = reasons.get(only["interface"]) or "Needs a second USB adapter"
            if why == REASONS["only6"] or why == REASONS["band"]:
                why = "Needs a second USB adapter; %s keeps 6 GHz" % only.get("label", only["interface"])
        else:
            why = "No spare adapter can listen without losing a band"
        return {"monitor": None, "roles": roles, "reasons": reasons, "unavailable": why}
    roles[picked["interface"]] = "monitor"
    for a in candidates:
        if a is not picked:
            reasons[a["interface"]] = REASONS["other"]
    return {"monitor": picked, "roles": roles, "reasons": reasons, "unavailable": ""}


def _covered(adapter, adapters, node_bands):
    others = [o for o in adapters if o is not adapter and o.get("bands")]
    for band in adapter.get("bands", ()):
        managed = any(band in o.get("bands", ()) for o in others)
        if band == "6":
            if not managed:
                return False
        elif not managed and band not in node_bands:
            return False
    return True


def keeps_six(adapters, roles):
    """Whether some managed adapter still sweeps 6 GHz, when any adapter can."""
    if not any("6" in a.get("bands", ()) for a in adapters):
        return True
    return any("6" in a.get("bands", ()) and roles.get(a["interface"]) == "managed" for a in adapters)


def freq_of(band, channel):
    return (2484 if channel == 14 else 2407 + 5 * channel) if band == "2.4" else 5000 + 5 * channel


class HopPlan:
    """The monitor's listen pattern, rebuilt each cycle from how busy each channel was."""

    def __init__(self, country="US", mode="balanced", allowed=None):
        self.mode = mode
        found = planner.channels(country)
        allowed = set(allowed) if allowed else None
        ok = lambda band, ch: allowed is None or freq_of(band, ch) in allowed
        self.ghz2 = [ch for band, ch in found if band == "2.4" and ok(band, ch)]
        self.ghz5 = [ch for band, ch in found if band == "5" and ok(band, ch)]
        self.flock = [ch for ch in FLOCK_CHANNELS if ch in self.ghz2]
        self.fill = [ch for ch in self.ghz2 if ch not in self.flock]
        self.min_dwell = FILL_DWELL
        self.rates2, self.rates5 = {}, {}
        self.quiet_next = 0

    def slower(self):
        self.min_dwell = max(self.min_dwell, SLOW_DWELL)

    def heard(self, per_freq):
        """Frames a second per frequency in the last stretch, folded in as SquachWatch does (3/4 old, 1/4 new). A
        channel missing from the stretch was silent, so it cools down too."""
        fresh = {}
        for freq, rate in per_freq.items():
            freq = int(freq)
            band = "2.4" if freq < 3000 else "5"
            ch = (freq - 2407) // 5 if band == "2.4" else (freq - 5000) // 5
            fresh[(band, ch)] = rate
        for band, table in (("2.4", self.rates2), ("5", self.rates5)):
            for ch in set(table) | {c for b, c in fresh if b == band}:
                table[ch] = (table.get(ch, 0.0) * 3 + fresh.get((band, ch), 0.0)) / 4
                if band == "5" and table[ch] < 0.25:
                    table[ch] = 0.0

    def _fill(self, dwell=None):
        if dwell is not None:
            return [(freq_of("2.4", ch), max(dwell, self.min_dwell)) for ch in self.fill]
        top = max([self.rates2.get(ch, 0.0) for ch in self.fill] or [0.0])
        shares = {ch: 2 if not top else (4 if self.rates2.get(ch, 0.0) * 4 >= top else
                                         2 if self.rates2.get(ch, 0.0) else 1) for ch in self.fill}
        total = sum(shares.values()) or 1
        return [(freq_of("2.4", ch), max(self.min_dwell, FILL_BUDGET * shares[ch] // total)) for ch in self.fill]

    def _slice5(self):
        out, budget = [], SLICE5_MS
        busy = sorted((ch for ch in self.ghz5 if self.rates5.get(ch)), key=lambda ch: -self.rates5[ch])
        for ch in busy:
            if budget < LOOK5_BUSY:
                break
            out.append((freq_of("5", ch), LOOK5_BUSY))
            budget -= LOOK5_BUSY
        quiet = [ch for ch in self.ghz5 if not self.rates5.get(ch)]
        taken = 0
        while quiet and taken < 2 and budget >= LOOK5_QUIET:
            ch = quiet[self.quiet_next % len(quiet)]
            self.quiet_next += 1
            out.append((freq_of("5", ch), max(LOOK5_QUIET, self.min_dwell)))
            budget -= LOOK5_QUIET
            taken += 1
        return out

    def build(self):
        """[(MHz, dwell ms)] for the helper's hop_plan."""
        flock = [(freq_of("2.4", ch), FLOCK_DWELL) for ch in self.flock]
        if self.mode == "flock":
            return flock * 4 + self._fill(FILL_DWELL)
        return flock + self._fill() + self._slice5()

    @staticmethod
    def cycle_seconds(plan):
        """How long the helper takes for one round of the plan: the dwells plus each channel change."""
        return sum(dwell + SWITCH_MS for _, dwell in plan) / 1000.0


def wanted(cfg):
    return radio_helper.wanted(cfg)


def _node_bands(nodes):
    return {{"wifi24": "2.4", "wifi5": "5"}.get(n, n) for n in nodes} & {"2.4", "5"}


def _process_cpu(pid, proc="/proc"):
    """Seconds of CPU a process has used, or 0 when it cannot be read."""
    if not pid:
        return 0.0
    try:
        with open(os.path.join(proc, str(pid), "stat"), encoding="ascii", errors="replace") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return 0.0


class DeepScan:
    """The radio helper session of one wardrive or Recon: the monitor, direct scans and tuning, all put back at stop.

    Nothing here runs unless one of the three settings is on."""

    def __init__(self, cfg, connect=radio_helper.connect, process=None):
        self.cfg = cfg
        self._connect = connect
        self._process = process
        self.client = None
        self.state, self.detail = "off", ""
        self.roles = {"monitor": None, "roles": {}, "reasons": {}, "unavailable": ""}
        self.vif = None
        self.monitor = None
        self.tuned = []
        self.tuning = {}
        self.stats = {}
        self.counts = {"flock": 0, "deauth": 0, "networks": 0}
        self.error = ""
        self.level = 0
        self.plan = None
        self.sinks = {}
        self.helper_adapters = []
        self.node_bands = set()
        self._plan_at, self._cycle = 0.0, 3.0
        self._inbox = self._outbox = self._worker = None
        self._forward = queue.Queue(maxsize=FORWARD_QUEUE)
        self.forward_dropped = 0
        self._stop = threading.Event()
        self._threads = []
        self._cpu = {}
        self._cpu_at = None
        self._helper_pid = None

    def open(self):
        if not wanted(self.cfg):
            return False
        self.client, self.state, self.detail = self._connect()
        if self.client is not None and hasattr(self.client, "peer_pid"):
            self._helper_pid = self.client.peer_pid()
        return self.client is not None

    def plan_roles(self, adapters, nodes=()):
        """The adapters that keep scanning normally. The monitor, if any, is left out of them."""
        if self.client is None or not self.cfg.get("deep_scan"):
            return list(adapters)
        try:
            helper = self.client.request("adapters")["adapters"]
        except radio_helper.HelperError as exc:
            self.error = str(exc)[:80]
            return list(adapters)
        self.helper_adapters = helper
        self.node_bands = _node_bands(nodes)
        self.roles = assign(adapters, nodes, helper, self.cfg.get("deep_scan_adapter", "auto"))
        if self.roles["monitor"] is None or not keeps_six(adapters, self.roles["roles"]):
            self.roles["monitor"] = None
            return list(adapters)
        return [a for a in adapters if a is not self.roles["monitor"]]

    def start(self, wifi_sink, frame_sink=None, deauth_sink=None, clash_sink=None):
        """Put the chosen adapter in monitor mode and start listening. False when it could not, and then the adapter
        must go back to managed scans."""
        adapter = self.roles.get("monitor")
        if self.client is None or adapter is None:
            return False
        self.sinks = {"networks": wifi_sink, "frame": frame_sink, "deauth": deauth_sink, "clash": clash_sink}
        try:
            self.vif = self.client.request("monitor_start", iface=adapter["interface"])["vif"]
            freqs = next((h.get("freqs") for h in self.helper_adapters
                          if h.get("interface") == adapter["interface"]), None)
            self.plan = HopPlan(self.cfg.get("country", "US"), self.cfg.get("deep_scan_plan", "balanced"), freqs)
            self._send_plan()
            self._start_worker()
            self.client.frames = self._receive
            self.client.request("capture", vif=self.vif)
        except (radio_helper.HelperError, OSError) as exc:
            self.error = "Monitor failed: %s. Scanning normally." % str(exc)[:60]
            self._stop_monitor()
            self.roles = dict(self.roles, monitor=None)
            return False
        self.monitor = dict(adapter, interface=self.vif, role="monitor", original=adapter["interface"])
        self._threads = [threading.Thread(target=self._dispatch, daemon=True, name="deepscan"),
                         threading.Thread(target=self._pump, daemon=True, name="deepscan-feed")]
        for thread in self._threads:
            thread.start()
        self._total_cpu(0.0)
        return True

    def _send_plan(self):
        steps = [[freq, 20, dwell] for freq, dwell in self.plan.build()][:64]
        reply = self.client.request("hop_plan", vif=self.vif, plan=steps)
        self._plan_at = time.monotonic()
        measured = reply.get("cycle_ms") if isinstance(reply, dict) else None
        self._cycle = max(HopPlan.cycle_seconds([(f, d) for f, _, d in steps]),
                          measured / 1000.0 if isinstance(measured, (int, float)) else 0.0)

    def _start_worker(self):
        if self._process is not None:
            self._inbox, self._outbox, self._worker = self._process(self.vif, self.cfg.get("rssi_step", 4))
            return
        from . import monitor
        context = multiprocessing.get_context("spawn")
        reader, self._inbox = context.Pipe(duplex=False)
        self._outbox, writer = context.Pipe(duplex=False)
        self._worker = context.Process(target=monitor.run, args=(reader, writer, self.vif, self.cfg.get("rssi_step", 4)),
                                       daemon=True, name="deepscan-worker")
        self._worker.start()
        reader.close()
        writer.close()

    def _receive(self, body):
        try:
            self._forward.put_nowait(bytes(body))
        except queue.Full:
            self.forward_dropped += 1

    def _pump(self):
        while not self._stop.is_set():
            try:
                body = self._forward.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._inbox.send_bytes(body)
            except (OSError, ValueError):
                return
            self._cpu["pump"] = time.thread_time()

    def _dispatch(self):
        while not self._stop.is_set():
            try:
                if not self._outbox.poll(0.5):
                    self._upkeep()
                    continue
                kind, payload = self._outbox.recv()
            except (EOFError, OSError, ValueError):
                if not self._stop.is_set():
                    self.error = "Deep scan reader stopped. Scanning normally."
                return
            try:
                self._handle(kind, payload)
            except Exception:
                faults.note("Deep scan")
            self._upkeep()
            self._cpu["dispatch"] = time.thread_time()

    def _handle(self, kind, payload):
        if kind == "stats":
            payload = dict(payload, worker_cpu=payload.get("cpu"), cpu=self._total_cpu(payload.get("cpu")))
            self.stats = payload
            self._degrade(payload)
            if self.plan is not None:
                self.plan.heard(payload.get("channels", {}))
            return
        if kind == "frame":
            self.counts["flock"] += 1
        elif kind == "deauth":
            self.counts["deauth"] += 1
        elif kind == "networks":
            self.counts["networks"] += len(payload)
        sink = self.sinks.get(kind)
        if sink is None:
            return
        if kind == "networks":
            sink(payload, self.monitor)
        else:
            sink(payload)

    def _total_cpu(self, worker):
        """The share of one core Deep scan costs: the frame reader, the helper, and its threads in this process."""
        now = time.monotonic()
        spent = sum(self._cpu.values()) + getattr(self.client, "cpu", 0.0) + _process_cpu(self._helper_pid)
        last, self._cpu_at = self._cpu_at, (now, spent)
        if last is None or now <= last[0]:
            return worker
        return round((worker or 0.0) + 100.0 * max(0.0, spent - last[1]) / (now - last[0]), 1)

    def _helper_gone(self):
        closed = getattr(self.client, "closed", None)
        return isinstance(closed, threading.Event) and closed.is_set()

    def _upkeep(self):
        if self.vif is not None and self._helper_gone():
            self.release("The radio helper stopped. Scanning normally.")
            return
        if self.plan is None or self.client is None or self.vif is None or self.plan.mode != "balanced":
            return
        if time.monotonic() - self._plan_at >= max(3.0, self._cycle):
            try:
                self._send_plan()
            except radio_helper.HelperError as exc:
                self.error = str(exc)[:80]

    def _degrade(self, found):
        gap = found.get("switch_gap_ms")
        if gap is not None and gap > SLOW_SWITCH_MS and self.plan is not None:
            self.plan.slower()
        if (found.get("cpu") or 0) <= CPU_LIMIT:
            return
        self.level += 1
        try:
            if self.level == 1:
                self.client.request("capture", vif=self.vif, mgmt_only=True)
            elif self.level == 2:
                self._forward.put_nowait(b"S")
            elif self.level == 3 and self.plan is not None:
                self.plan.mode = "flock"
                self._send_plan()
            elif self.level >= 4:
                self.release("Busy CPU: Deep scan stopped. Scanning normally.")
        except (radio_helper.HelperError, queue.Full):
            pass

    def tune(self, adapters):
        if self.client is None or not self.cfg.get("radio_tuning"):
            return
        for a in adapters:
            if not a.get("external") or a.get("interface") == self.vif:
                continue
            try:
                self.tuning[a["interface"]] = self.client.request("tune", iface=a["interface"])
                self.tuned.append(a["interface"])
            except radio_helper.HelperError as exc:
                self.tuning[a["interface"]] = {"error": str(exc)[:60]}

    def scanners(self, adapters):
        """The adapters as the Wi-Fi fleet should get them: with the helper for direct scans when that is on."""
        if self.client is None or not self.cfg.get("direct_scan"):
            return adapters
        return [dict(a, helper=self.client) if a.get("external") else a for a in adapters]

    def follow(self, adapters, present=None, nodes=None):
        """Adapters plugged in or pulled out during the drive: the monitor stays out of the managed scans, a new
        adapter gets tuned like the others. A monitor that went away, or whose helper stopped, is let go, so its
        adapter scans again once it is back. present: every Wi-Fi interface there is now; nodes: the bands of the
        nodes in use now."""
        if nodes is not None:
            self.node_bands = _node_bands(nodes)
        if self.vif is not None:
            names = {a["interface"] for a in (adapters if present is None else present)}
            if self.vif not in names or self._helper_gone():
                self.release("The monitor adapter went away. Scanning normally.")
        managed = [a for a in adapters if a["interface"] != self.vif and a.get("type") != "803"]
        chosen = self.roles.get("monitor")
        if self.vif is not None and chosen is not None and not _covered(chosen, managed + [chosen], self.node_bands):
            self.release("Monitor handed back: another adapter left, so it scans its bands again.")
        self.tune([a for a in managed if a["interface"] not in self.tuning])
        return self.scanners(managed)

    def radio_status(self):
        if self.monitor is None:
            return []
        stats = self.stats
        return [{"interface": self.vif, "method": "monitor", "sweeps": 0, "sweep_seconds": None,
                 "error": self.error, "label": self.monitor.get("label", ""), "bands": [],
                 "frames_per_s": stats.get("frames_per_s"), "flock": self.counts["flock"],
                 "original": self.monitor.get("original")}]

    def release(self, why):
        """Hand the monitor adapter back to normal scans; the next look at the adapters picks it up."""
        self.error = why
        self._stop_monitor()
        self.monitor = None
        self.plan = None
        self.roles = dict(self.roles, monitor=None)

    def _stop_monitor(self):
        if self.client is not None and self.vif is not None:
            self.client.frames = None
            try:
                self.client.request("monitor_stop", vif=self.vif)
            except radio_helper.HelperError:
                pass
        self.vif = None

    def stop(self):
        """Put everything back: monitor to managed, tuning undone, then close. The helper also does this by itself
        when the connection closes, so a crash leaves nothing behind."""
        self._stop.set()
        self._stop_monitor()
        for name in self.tuned[::-1]:
            try:
                self.client.request("untune", iface=name)
            except (radio_helper.HelperError, AttributeError):
                pass
        self.tuned = []
        if self._inbox is not None:
            try:
                self._inbox.send_bytes(b"")
            except (OSError, ValueError):
                pass
        if self._worker is not None and hasattr(self._worker, "join"):
            self._worker.join(timeout=3)
            if self._worker.is_alive():
                self._worker.terminate()
                self._worker.join(timeout=1)
        for end in (self._inbox, self._outbox):
            if end is not None and hasattr(end, "close"):
                end.close()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=3)
        if self.client is not None:
            self.client.close()
            self.client = None
        self.monitor = None

    def status(self):
        return {"state": self.state, "detail": self.detail, "monitor": self.monitor, "vif": self.vif,
                "reasons": dict(self.roles.get("reasons", {})), "unavailable": self.roles.get("unavailable", ""),
                "stats": dict(self.stats), "counts": dict(self.counts), "error": self.error,
                "tuning": dict(self.tuning), "plan": self.cfg.get("deep_scan_plan", "balanced"),
                "level": self.level}
