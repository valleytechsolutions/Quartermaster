#!/usr/bin/env python3
"""Time Deep scan's frame reader on this machine: python3 tools/bench_monitor.py [--frames 60000] [--aps 120]

It replays a dense street, mostly beacons from many access points with a few probe requests, through the same reader
the monitor process runs, and prints the cost per frame and the share of one core at a given frame rate. On the
CardputerZero, expect about seven times the figures of a desktop.
"""

import argparse
from pathlib import Path
import random
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quartermaster import monitor  # noqa: E402


def radiotap(freq, rssi):
    return struct.pack("<BBHIBBHHb", 0, 0, 15, (1 << 1) | (1 << 3) | (1 << 5), 0, 0, freq, 0x00a0, rssi)


def beacon(bssid, ssid, channel):
    body = struct.pack("<QHH", 0, 100, 0x0411) + bytes((0, len(ssid))) + ssid + bytes((3, 1, channel))
    body += bytes((48, 20)) + struct.pack("<H", 1) + b"\x00\x0f\xac\x04" + struct.pack("<H", 1) + \
        b"\x00\x0f\xac\x04" + struct.pack("<H", 1) + b"\x00\x0f\xac\x02" + b"\x00\x00"
    return bytes((0x80, 0, 0, 0)) + b"\xff" * 6 + bssid + bssid + b"\x10\x00" + body


def probe(source):
    ies = bytes((0, 0, 1, 4, 2, 4, 11, 22))
    return bytes((0x40, 0, 0, 0)) + b"\xff" * 6 + source + b"\xff" * 6 + b"\x10\x00" + ies


def street(count, aps, seed=5):
    rnd = random.Random(seed)
    stations = [bytes((0xac, 0x84, 0xc6, rnd.randrange(256), rnd.randrange(256), i & 255)) for i in range(aps)]
    frames = []
    for i in range(count):
        channel = rnd.choice((1, 6, 11, 3, 9))
        freq = 2407 + 5 * channel
        if rnd.random() < 0.9:
            bssid = rnd.choice(stations)
            frames.append(radiotap(freq, rnd.randrange(-90, -40)) + beacon(bssid, b"net-%d" % bssid[5], channel))
        else:
            frames.append(radiotap(freq, -70) + probe(bytes((0x02, 0, 0, 0, 0, i & 255))))
    return frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=60000)
    parser.add_argument("--aps", type=int, default=120)
    parser.add_argument("--rate", type=int, default=1500, help="frames a second to work out the share of a core")
    args = parser.parse_args()
    frames = street(args.frames, args.aps)
    worker = monitor.Worker()
    started = time.process_time()
    clock = 0.0
    for raw in frames:
        worker.feed(raw, clock)
        clock += 1.0 / args.rate
        worker.tick(clock)
    spent = time.process_time() - started
    per_frame = spent / len(frames) * 1e6
    print("%d frames, %.1f µs a frame, %.1f%% of one core at %d frames a second (desktop figure)" % (
        len(frames), per_frame, per_frame * args.rate / 1e4, args.rate))


if __name__ == "__main__":
    main()
