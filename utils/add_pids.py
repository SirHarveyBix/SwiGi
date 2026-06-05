#!/usr/bin/env python3
"""Ajoute les product IDs des claviers Gen S dans ~/.swigi_prefs.json.

Lancer sur CHAQUE Mac pour que _keyboard_probe_loop fonctionne.
"""
import json
import os

KEYBOARD_PRODUCT_IDS = [0xB369, 0xB378]  # MX Keys Mini, MX Keys S

prefs_file_path = os.path.expanduser("~/.swigi_prefs.json")
try:
    with open(prefs_file_path) as prefs_file:
        prefs_data = json.load(prefs_file)
except Exception:
    prefs_data = {}

known_product_ids = set(prefs_data.get("keyboard_pids_gen_s", []))
known_product_ids.update(KEYBOARD_PRODUCT_IDS)
prefs_data["keyboard_pids_gen_s"] = sorted(known_product_ids)

with open(prefs_file_path, "w") as prefs_file:
    json.dump(prefs_data, prefs_file, indent=2)

print(f"OK → {prefs_file_path}")
print(f"keyboard_pids_gen_s = {[hex(product_id) for product_id in prefs_data['keyboard_pids_gen_s']]}")
