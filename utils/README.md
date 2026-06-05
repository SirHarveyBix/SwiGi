# SwiGi — Utils

Scripts de diagnostic et maintenance. Lancer depuis la racine du projet ou depuis `utils/`.

```bash
# Depuis la racine
python3 utils/scan_features.py
python3 utils/add_pids.py

# Ou depuis utils/
cd utils && python3 scan_features.py
```

---

## scan_features.py

Liste toutes les features HID++ 2.0 exposées par les claviers connectés.

**Prérequis :** SwiGi doit être arrêté (un seul process peut tenir le handle HID).

```bash
python3 utils/scan_features.py
```

Utile pour identifier les feature codes non documentés (ex : backlight, profils, etc.).

---

## add_pids.py

Injecte les PIDs des claviers Gen S dans `~/.swigi_prefs.json`.

**Pourquoi :** SwiGi sauvegarde automatiquement les PIDs des claviers connectés au démarrage.
Si un clavier n'a jamais été connecté à un Mac donné pendant que SwiGi tourne, ce Mac
ne le surveillera pas à l'arrivée. Ce script force l'ajout des PIDs sans avoir à connecter
le clavier physiquement.

**Lancer sur chaque Mac :**

```bash
python3 utils/add_pids.py
```

Puis redémarrer SwiGi. Idempotent — peut être relancé sans risque.
