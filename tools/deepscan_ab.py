#!/usr/bin/env python3
"""Compare WiGLE logs of laps with Deep scan off (A) and on (B), band by band.

    python3 tools/deepscan_ab.py --a lap1.wigle.csv lap4.wigle.csv --b lap2.wigle.csv lap3.wigle.csv

Laps go in ABBA order over the same route. For each band it prints unique networks, new networks a minute, the
median first-seen delay of networks in both, and the share of A's networks that B found too. It passes only when B
finds at least as many networks as A on every band, and 6 GHz is never zero in B when A has 6 GHz.
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import statistics
import sys

BANDS = ("2.4", "5", "6")


def band(freq):
    try:
        freq = int(freq)
    except (TypeError, ValueError):
        return None
    return "2.4" if freq < 3000 else "5" if freq < 5925 else "6" if freq <= 7125 else None


def read(path):
    """{band: {mac: first seen as seconds into the lap}} of one WiGLE log."""
    seen = {b: {} for b in BANDS}
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.reader(handle))
    start = None
    for row in rows[2:]:
        if len(row) < 14 or row[13] != "WIFI":
            continue
        where = band(row[5])
        if where is None:
            continue
        try:
            when = datetime.strptime(row[3], "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        start = when if start is None else min(start, when)
        mac = row[0].lower()
        if mac not in seen[where] or when < seen[where][mac]:
            seen[where][mac] = when
    for where in BANDS:
        seen[where] = {mac: when - (start or when) for mac, when in seen[where].items()}
    return seen


def compare(a_laps, b_laps):
    """Per band: counts, rates, delays and overlap, and whether B passes."""
    a = [read(p) for p in a_laps]
    b = [read(p) for p in b_laps]
    out = {}
    for where in BANDS:
        a_all = {m: min(lap[where][m] for lap in a if m in lap[where]) for lap in a for m in lap[where]}
        b_all = {m: min(lap[where][m] for lap in b if m in lap[where]) for lap in b for m in lap[where]}
        both = set(a_all) & set(b_all)
        minutes = lambda laps: max([max(lap[where].values(), default=0) for lap in laps] + [60]) / 60.0
        a_sum, b_sum = sum(len(lap[where]) for lap in a), sum(len(lap[where]) for lap in b)
        out[where] = {"a": a_sum, "b": b_sum, "a_unique": len(a_all), "b_unique": len(b_all),
                      "a_per_min": round(len(a_all) / minutes(a), 1), "b_per_min": round(len(b_all) / minutes(b), 1),
                      "a_delay": statistics.median([a_all[m] for m in both]) if both else None,
                      "b_delay": statistics.median([b_all[m] for m in both]) if both else None,
                      "share": round(len(both) / len(a_all), 3) if a_all else None,
                      "pass": b_sum >= a_sum and not (where == "6" and a_sum and not b_sum)}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", nargs="+", required=True, help="laps with Deep scan off")
    parser.add_argument("--b", nargs="+", required=True, help="laps with Deep scan on")
    args = parser.parse_args()
    result = compare([Path(p) for p in args.a], [Path(p) for p in args.b])
    for where, row in result.items():
        print("%-7s A %5d  B %5d  unique %5d/%5d  per min %6.1f/%6.1f  share %s  %s" % (
            where + " GHz", row["a"], row["b"], row["a_unique"], row["b_unique"], row["a_per_min"],
            row["b_per_min"], row["share"], "pass" if row["pass"] else "FAIL"))
    return 0 if all(row["pass"] for row in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
