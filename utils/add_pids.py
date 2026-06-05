#!/usr/bin/env python3
"""Ajoute les PIDs des claviers Gen S dans ~/.swigi_prefs.json.

Lancer sur CHAQUE Mac pour que _keyboard_probe_loop fonctionne.
"""
import json, os

PIDS = [0xB369, 0xB378]  # MX Keys Mini, MX Keys S
f = os.path.expanduser("~/.swigi_prefs.json")
try:
    d = json.load(open(f))
except Exception:
    d = {}
pids = set(d.get("keyboard_pids_gen_s", []))
pids.update(PIDS)
d["keyboard_pids_gen_s"] = sorted(pids)
json.dump(d, open(f, "w"), indent=2)
print(f"OK → {f}")
print(f"keyboard_pids_gen_s = {[hex(p) for p in d['keyboard_pids_gen_s']]}")
