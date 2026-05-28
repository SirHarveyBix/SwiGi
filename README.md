# 🔀 SwiGi

**Switch your keyboard. Mouse follows. Done.**

SwiGi synchronise le bouton Easy-Switch entre le clavier et la souris Logitech via Bluetooth — sans dongle USB, sans Logi Options+, sans contrainte réseau.

> _Made for people. Enjoy it and stop being a slave to buttons._

<p align="center">
  <i>Ça t'a fait gagner du temps ?</i><br><br>
  <a href="https://github.com/sponsors/LeeHoffka" target="_blank"><img src="https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?style=for-the-badge&logo=github" alt="Sponsor on GitHub" height="40"></a>
</p>

🇫🇷 **Français** | 🇬🇧 [English](#-english)

---

## ✨ Fonctionnalités

| Fonctionnalité                  | Description                                                           |
| ------------------------------- | --------------------------------------------------------------------- |
| 🔀 **Sync Easy-Switch**          | Appuie une fois sur le clavier → la souris suit automatiquement       |
| 🔵 **Bluetooth natif**           | Pas de dongle USB, pas de Logi Options+, pas de réseau                |
| 🔄 **Reconnexion automatique**   | Watchdog : reconnecte clavier et souris en < 15s si déconnexion BT    |
| 🔗 **Sync vérifiée**             | Vérifie et confirme dans les logs que la souris a bien basculé        |
| ⚡ **Faible latence**            | Réponse < 300ms dans des conditions normales                          |
| 🖱️ **Multi-souris**              | Envoie CHANGE_HOST à toutes les souris connectées simultanément       |
| 🍎 **Icône menu bar macOS**      | Statut clavier/souris visible en permanence, compteur de basculements |
| ☑️ **Suivi souris désactivable** | Checkbox dans le menu pour activer/désactiver le suivi de la souris   |
| 🔔 **Notifications système**     | Alerte à la connexion/déconnexion de chaque périphérique (macOS)      |
| 🔁 **Démarrage automatique**     | launchd (macOS), Startup folder (Windows), systemd (Linux)            |
| 📄 **Log rotation**              | `--log-file` : max 4 Mo au total, aucune croissance infinie           |
| 🔒 **Non-intrusif**              | Mode non-exclusif macOS — coexiste avec Logi Options+                 |
| 📦 **Zéro friction**             | Un package Python, une dépendance (hidapi)                            |

---

## 🇫🇷 Français

### Prérequis

- Un clavier **et** une souris Logitech avec Easy-Switch et Bluetooth (série MX, série Ergo, etc.)
- **Pas besoin de savoir coder.**

---

### 🍎 Installation macOS

**Méthode la plus simple — une seule commande (recommandé)**

```bash
curl -fsSL https://raw.githubusercontent.com/SirHarveyBix/SwiGi/main/install_curl.sh | bash
```

C'est tout. SwiGi est cloné, installé, et se relancera automatiquement à chaque démarrage de ton Mac.

---

**Alternative — git clone manuel**

```bash
git clone https://github.com/SirHarveyBix/SwiGi.git
cd SwiGi
bash install_mac.sh
```

---

**Méthode manuelle (si tu préfères tout contrôler)**

**Étape 1 — Installer Python** (si pas déjà installé)

Va sur [python.org/downloads](https://www.python.org/downloads/) et télécharge Python 3. Lance l'installeur, clique « Continue » partout.

**Étape 2 — Installer Homebrew** (si pas déjà installé)

Ouvre le Terminal et colle :

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Étape 3 — Installer hidapi**

```bash
brew install hidapi
```

**Étape 4 — Télécharger SwiGi**

```bash
git clone https://github.com/SirHarveyBix/SwiGi.git
```

**Étape 5 — Lancer SwiGi**

```bash
cd SwiGi
python3 swigi.py
```

---

#### ⚙️ Démarrage automatique macOS

Lance le script d'installation automatique (voir ci-dessus) **ou** fais-le manuellement :

```bash
# Dans le dossier SwiGi :
bash install_mac.sh
```

Pour **désactiver** le démarrage automatique :

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist
```

---

#### 🔐 Permission requise sur macOS

> ⚠️ **Important — à faire une seule fois**

macOS bloque par défaut l'accès aux périphériques d'entrée. Tu dois autoriser SwiGi :

**Si tu as utilisé le script d'installation (launchd) :**

1. Ouvre **Réglages Système** → **Confidentialité et sécurité** → **Surveillance des entrées**
2. Clique sur le **+** et ajoute **python3** — son chemin exact est affiché lors de l'installation, ou retrouve-le avec `which python3` dans le Terminal
3. Relance le service pour qu'il prenne en compte la permission :

   ```bash
   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist
   ```

**Si tu lances manuellement depuis Terminal :**

1. Ouvre **Réglages Système** → **Confidentialité et sécurité** → **Surveillance des entrées**
2. Clique sur le **+** et ajoute **Terminal**
3. Redémarre SwiGi

> ⚠️ **Après chaque rebuild** (build portable PyInstaller), macOS ne reconnaît plus le binaire. Supprime l'ancien SwiGi dans Surveillance des entrées et rajoute le nouveau.

---

### 🪟 Installation Windows

**Tu n'as pas besoin d'installer Python.**

**Étape 1 — Télécharger les fichiers**

Télécharge et place dans un même dossier (ex. `C:\SwiGi\`) :

- Le ZIP SwiGi complet (bouton vert **Code** → **Download ZIP**) — contient `swigi.py`, le dossier `swigi\` et `setup_win.bat`
- `hidapi.dll` depuis [github.com/libusb/hidapi/releases](https://github.com/libusb/hidapi/releases) → Assets → `hidapi-win.zip` → dossier `x64` → `hidapi.dll`
- Python embeddable depuis [python.org/downloads/windows](https://www.python.org/downloads/windows/) → « Windows embeddable package (64-bit) » → dézippe dans un sous-dossier `python-3\`

⚠️ `swigi.py` **seul ne suffit pas** — le dossier `swigi\` doit être présent dans le même répertoire.

**Étape 2 — Lancer le setup**

Double-clique sur **`setup_win.bat`**.

Ce script :

- Copie tout au bon endroit
- Crée un raccourci de démarrage
- **Configure le démarrage automatique** au login Windows
- Ouvre le dossier final

**Étape 3 — Lancer SwiGi**

Double-clique sur **`start.bat`** dans `%USERPROFILE%\SwiGi\`.

---

### 🐧 Installation Linux

**Étape 1 — Installer les dépendances**

```bash
sudo apt install python3 libhidapi-hidraw0
```

**Étape 2 — Règle udev** (accès HID sans root)

```bash
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/42-logitech-hid.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

**Étape 3 — Lancer SwiGi**

```bash
python3 swigi.py
```

**Étape 4 — Démarrage automatique (systemd)**

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/swigi.service << EOF
[Unit]
Description=SwiGi — synchronisation Easy-Switch Logitech

[Service]
ExecStart=$(command -v python3) $(pwd)/swigi.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user enable --now swigi
```

---

### 🖥️ Configuration multi-Mac

> **Important pour 2+ Macs**

SwiGi doit tourner sur les **3 Macs SIMULTANÉMENT**. Chaque instance surveille les périphériques localement connectés via Bluetooth HID++ 2.0 — il n'y a aucune communication réseau entre les instances.

**Pourquoi c'est nécessaire :** SwiGi ne peut envoyer CHANGE_HOST qu'aux périphériques **actuellement connectés au Mac local**. Quand la souris est sur Mac2, Mac1 ne peut pas la commander via Bluetooth HID.

**Exemple avec 3 Macs (Mac1=hôte0, Mac2=hôte1, Mac3=hôte2) :**

- Mac1 → Mac2 : SwiGi sur **Mac1** envoie CHANGE_HOST(1) à la souris ✓
- Mac2 → Mac1 : SwiGi sur **Mac2** envoie CHANGE_HOST(0) à la souris ✓
- Mac1 → Mac3 : SwiGi sur **Mac1** envoie CHANGE_HOST(2) ✓

**Support multi-clavier :** 2 claviers MX Keys connectés au même Mac sont supportés. SwiGi surveille chaque clavier dans un thread dédié.

**Installation sur chaque Mac :**

```bash
git clone https://github.com/SirHarveyBix/SwiGi.git
cd SwiGi
bash install_mac.sh
```

> ⚠️ Si SwiGi est installé sur un seul Mac, la souris ne suivra que dans **une direction** (vers les autres hôtes). Au retour vers ce Mac, il faudra presser manuellement le bouton Easy-Switch de la souris.

---

### 🐭 BetterMouse — synchronisation multi-Mac

> **Optionnel** — uniquement si tu utilises [BetterMouse](https://better-mouse.com) pour configurer ta souris Logitech.

SwiGi peut **exporter et appliquer automatiquement** un profil BetterMouse après chaque basculement. Le profil (scroll, polling, ratchet, DPI) est partagé via un fichier JSON dans `~/.swigi_profiles/`.

**Configuration (une seule fois) :**

1. Configure ta souris exactement comme tu veux dans BetterMouse
2. Exporte le profil courant :

   ```bash
   python3 -c "from swigi.bettermouse import export_current; print(export_current('mon-profil'))"
   ```

3. Active l'application automatique dans le menu SwiGi :
   - Icône menu bar → **BetterMouse** → **Appliquer au switch** ✓
   - Sélectionne le profil `mon-profil`

**Sync entre Macs :**

Copie le fichier `~/.swigi_profiles/mon-profil.json` sur chaque Mac (via iCloud Drive, `scp`, AirDrop, git…). Chaque instance SwiGi appliquera le même profil après chaque switch.

```bash
# Depuis le Mac source :
scp ~/.swigi_profiles/mon-profil.json user@mac2:~/.swigi_profiles/
scp ~/.swigi_profiles/mon-profil.json user@mac3:~/.swigi_profiles/
```

> Le profil contient uniquement les réglages scroll/hardware — jamais de données sensibles (licence, etc.).

---

### ❓ Problèmes fréquents

| Problème                                       | Solution                                                                                                                                                                                          |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| « Clavier introuvable »                        | Vérifie que le clavier est connecté en Bluetooth (pas en USB)                                                                                                                                     |
| « Souris introuvable »                         | Idem pour la souris                                                                                                                                                                               |
| Rien ne se passe sur macOS                     | Ajoute `python3` (launchd) ou Terminal (manuel) dans Surveillance des entrées, puis relance le service                                                                                            |
| L'icône n'apparaît pas (installation)          | Re-exécute `bash install_mac.sh` depuis le dossier SwiGi — le vieux plist peut pointer vers le mauvais Python. Vérifie ensuite : `python3 -c "import rumps"`                                      |
| L'icône n'apparaît pas (général)               | 1) `python3` dans Surveillance des entrées. 2) `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist` |
| Souris ne revient pas après switch             | SwiGi doit tourner sur **les 3 Macs simultanément** (voir ci-dessus). Vérifie aussi les logs : `tail -50 ~/Library/Logs/swigi.log`                                                                |
| 2 Macs OK, 3e Mac instable au retour           | Vérifie les reconnexions clavier répétées dans les logs : c'est un signe de reconnexion BT lente (instabilité radio/Bluetooth locale).                                                            |
| `hidapi introuvable` sur macOS                 | Lance `brew install hidapi`                                                                                                                                                                       |
| `hidapi introuvable` sur Windows               | Vérifie que `hidapi.dll` est dans le même dossier que `swigi.py`                                                                                                                                  |
| SwiGi se lance mais ne fait rien               | Lance avec `-v` pour plus de détails : `python3 swigi.py -v`                                                                                                                                      |
| Permission perdue après update Homebrew Python | Ré-autoriser le nouveau binaire dans Surveillance des entrées. Trouver le chemin avec `which python3`.                                                                                            |
| Logs : où les trouver                          | `tail -50 ~/Library/Logs/swigi.log` ou `cat ~/Library/Logs/swigi.log`                                                                                                                             |

---

### ⚙️ Options

```bash
python3 swigi.py                          # mode normal
python3 swigi.py -v                       # mode verbose (logs détaillés)
python3 swigi.py --log-file swigi.log     # écriture logs dans un fichier (rotation auto)
```

---

### Comment ça marche

1. SwiGi surveille le clavier via Bluetooth HID (ping régulier)
2. Quand tu appuies sur Easy-Switch, le clavier envoie une notification `CHANGE_HOST`
3. SwiGi la capture et envoie **immédiatement** la même commande à toutes les souris connectées
4. Les périphériques basculent sur le même hôte
5. Le probe loop vérifie et confirme dans les logs que la souris est bien sur le bon hôte

Architecture pipe unidirectionnel : clavier notifie → SwiGi envoie CHANGE_HOST → souris bascule → log confirme. Pas de correction agressive, pas de boucle de feedback.

Utilise le protocole HID++ 2.0 (feature CHANGE_HOST `0x1814`). Un package Python modulaire, aucune dépendance sauf hidapi.

---

### ⚡ Performances

SwiGi est extrêmement léger — conçu pour tourner 24h/24 en arrière-plan sans impact visible.

| Ressource | Valeur typique                                                   |
| --------- | ---------------------------------------------------------------- |
| CPU       | < 0,5 % (boucle bloquée en attente kernel BT)                    |
| RAM       | ~10–15 Mo (Python + hidapi)                                      |
| Disque    | 0 écriture en fonctionnement normal (logs uniquement si demandé) |
| Réseau    | 0 octet (100 % Bluetooth local, aucune connexion internet)       |
| Batterie  | Négligeable — équivalent à avoir le Bluetooth activé normalement |

La boucle principale passe la majorité du temps bloquée dans `hid_read_timeout` (appel système kernel). Python ne s'exécute que quelques microsecondes par cycle. La consommation est comparable à un daemon SSH ou à l'agent Bluetooth natif.

---

### Appareils testés

| Appareil                                | OS              | Connexion |
| --------------------------------------- | --------------- | --------- |
| MX Keys S + MX Vertical                 | macOS (Sequoia) | Bluetooth |
| MX Keys S + MX Vertical                 | Windows 11      | Bluetooth |
| 2× MX Keys S (PID=0xB35B) + MX Master 4 | macOS 13+       | Bluetooth |

_Validé en production : 3 Macs simultanément, multi-clavier._

Devrait fonctionner avec toute combinaison d'appareils Logitech supportant HID++ 2.0 et CHANGE_HOST.

---

## 🇬🇧 English

**Press Easy-Switch. Mouse follows. Done.**

SwiGi syncs Easy-Switch between your Logitech keyboard and mouse over Bluetooth — no USB receiver, no Logi Options+, no same-network requirement.

### Features

| Feature                    | Description                                                |
| -------------------------- | ---------------------------------------------------------- |
| 🔀 **Easy-Switch sync**     | Press once on keyboard → mouse follows automatically       |
| 🔵 **Native Bluetooth**     | No USB dongle, no Logi Options+, no network required       |
| 🔄 **Auto-reconnect**       | Watchdog reconnects both devices in < 15s after BT drop    |
| 🔗 **Verified sync**        | Confirms in logs that the mouse actually switched          |
| ⚡ **Low latency**          | < 300ms response under normal conditions                   |
| 🖱️ **Multi-mouse**          | Sends CHANGE_HOST to all connected mice simultaneously     |
| 🍎 **macOS menu bar**       | Live keyboard/mouse status, switch counter                 |
| ☑️ **Mouse follow toggle**  | Checkbox in menu bar to enable/disable mouse following     |
| 🔔 **System notifications** | Alerts on device connect/disconnect (macOS)                |
| 🔁 **Autostart**            | launchd (macOS), Startup folder (Windows), systemd (Linux) |
| 📄 **Log rotation**         | `--log-file`: max 4 MB total, no unbounded growth          |
| 🔒 **Non-intrusive**        | macOS non-exclusive mode — coexists with Logi Options+     |
| 📦 **Zero friction**        | Single Python package, one dependency (hidapi)             |

### Requirements

- Logitech keyboard + mouse with Easy-Switch and Bluetooth (MX series, Ergo series, etc.)
- **No coding knowledge required.** Just follow the steps below.

### 🍎 macOS

**One-liner install (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/SirHarveyBix/SwiGi/main/install_curl.sh | bash
```

This clones the repo, installs hidapi, starts SwiGi, and sets it to launch automatically at login.

**Alternative — manual git clone:**

```bash
git clone https://github.com/SirHarveyBix/SwiGi.git
cd SwiGi
bash install_mac.sh
```

**Manual install:**

1. Install [Python 3](https://www.python.org/downloads/)
2. Install [Homebrew](https://brew.sh/) then run `brew install hidapi`
3. Download this repo (green **Code** button → **Download ZIP**), unzip it
4. In Terminal: `python3 swigi.py`

**macOS Permission (required once):**

- **Script install (launchd):** System Settings → Privacy & Security → Input Monitoring → add **python3** (find its path with `which python3`), then restart the service:

  ```bash
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist
  ```

- **Manual launch from Terminal:** System Settings → Privacy & Security → Input Monitoring → add **Terminal**

> ⚠️ After every PyInstaller rebuild, remove the old SwiGi entry and re-add the new binary.

### 🪟 Windows

1. Download `hidapi.dll` from [libusb/hidapi releases](https://github.com/libusb/hidapi/releases) → Assets → `hidapi-win.zip` → `x64/hidapi.dll`
2. Download the [Python embeddable package](https://www.python.org/downloads/windows/) (64-bit ZIP) → extract to `python-3\`
3. Put `swigi.py`, the `swigi\` folder, `hidapi.dll`, `python-3\`, `setup_win.bat` in one folder
4. Run **`setup_win.bat`** — installs, configures autostart, opens the folder
5. Run **`start.bat`** to launch SwiGi

### 🐧 Linux

```bash
sudo apt install python3 libhidapi-hidraw0
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/42-logitech-hid.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
python3 swigi.py
```

### ⚙️ Options

```bash
python3 swigi.py                          # normal mode
python3 swigi.py -v                       # verbose (detailed logs)
python3 swigi.py --log-file swigi.log     # write logs to file (auto-rotation)
```

### 🖥️ Multi-Mac setup

> **Required if you use 2+ Macs**

SwiGi must run on **all 3 Macs SIMULTANEOUSLY**. Each instance monitors locally connected devices via Bluetooth HID++ 2.0 — there is no network communication between instances.

**Why this matters:** SwiGi can only send CHANGE_HOST to devices **currently connected to the local Mac**. When the mouse is on Mac2, Mac1 cannot reach it over Bluetooth HID.

**Example with 3 Macs (Mac1=host0, Mac2=host1, Mac3=host2):**

- Mac1 → Mac2: SwiGi on **Mac1** sends CHANGE_HOST(1) to mouse ✓
- Mac2 → Mac1: SwiGi on **Mac2** sends CHANGE_HOST(0) to mouse ✓
- Mac1 → Mac3: SwiGi on **Mac1** sends CHANGE_HOST(2) ✓

**Multi-keyboard support:** 2 MX Keys keyboards connected to the same Mac are supported. SwiGi monitors each keyboard in a dedicated thread.

Install on each Mac:

```bash
git clone https://github.com/SirHarveyBix/SwiGi.git
cd SwiGi
bash install_mac.sh
```

> ⚠️ With SwiGi on only one Mac, the mouse will follow in **one direction only**. On the return switch, you'll need to press the mouse's Easy-Switch button manually.

---

### 🐭 BetterMouse — multi-Mac sync

> **Optional** — only if you use [BetterMouse](https://better-mouse.com) to configure your Logitech mouse.

SwiGi can **export and auto-apply** a BetterMouse profile after each switch. The profile (scroll, polling, ratchet, DPI) is shared via a JSON file in `~/.swigi_profiles/`.

**Setup (once):**

1. Configure your mouse exactly as you want in BetterMouse
2. Export the current profile:

   ```bash
   python3 -c "from swigi.bettermouse import export_current; print(export_current('my-profile'))"
   ```

3. Enable auto-apply in SwiGi's menu:
   - Menu bar icon → **BetterMouse** → **Apply on switch** ✓
   - Select profile `my-profile`

**Sync across Macs:**

Copy `~/.swigi_profiles/my-profile.json` to each Mac (via iCloud Drive, `scp`, AirDrop, git…). Each SwiGi instance will apply the same profile after every switch.

```bash
# From the source Mac:
scp ~/.swigi_profiles/my-profile.json user@mac2:~/.swigi_profiles/
scp ~/.swigi_profiles/my-profile.json user@mac3:~/.swigi_profiles/
```

> The profile only contains scroll/hardware settings — never sensitive data (license keys, etc.).

---

### ❓ Troubleshooting

| Problem                                      | Fix                                                                                                                                                                                     |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Keyboard not found"                         | Make sure keyboard is connected via Bluetooth (not USB)                                                                                                                                 |
| "Mouse not found"                            | Same for mouse                                                                                                                                                                          |
| Nothing happens on macOS                     | Add `python3` (launchd) or Terminal (manual) to Input Monitoring, then restart the service                                                                                              |
| Menu bar icon missing (install)              | Re-run `bash install_mac.sh` from the SwiGi folder — the old plist may point to the wrong Python. Then verify: `python3 -c "import rumps"`                                              |
| Menu bar icon missing (general)              | 1) `python3` in Input Monitoring. 2) `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.swigi.plist` |
| Mouse doesn't come back after switch         | SwiGi must run on **all 3 Macs simultaneously** (see above). Check logs: `tail -50 ~/Library/Logs/swigi.log`                                                                            |
| `hidapi not found` on macOS                  | Run `brew install hidapi`                                                                                                                                                               |
| `hidapi not found` on Windows                | Check `hidapi.dll` is in the same folder as `swigi.py`                                                                                                                                  |
| SwiGi starts but does nothing                | Run with `-v` for details: `python3 swigi.py -v`                                                                                                                                        |
| Permission lost after Homebrew Python update | Re-authorize the new binary in Input Monitoring. Find the path with `which python3`.                                                                                                    |
| Where to find logs                           | `tail -50 ~/Library/Logs/swigi.log`                                                                                                                                                     |

### How it works

1. SwiGi monitors the keyboard over Bluetooth HID (regular ping)
2. When you press Easy-Switch, the keyboard sends a `CHANGE_HOST` notification
3. SwiGi captures it and **immediately** sends the same command to all connected mice
4. All devices switch to the same host
5. The probe loop verifies and confirms in logs that the mouse is on the correct host

Unidirectional pipe architecture: keyboard notifies → SwiGi sends CHANGE_HOST → mouse switches → log confirms. No aggressive correction, no feedback loop.

Uses the HID++ 2.0 protocol (CHANGE_HOST feature `0x1814`). Single Python package, one dependency (hidapi).

### ⚡ Performance

SwiGi is extremely lightweight — designed to run 24/7 in the background with no visible impact.

| Resource | Typical value                                                        |
| -------- | -------------------------------------------------------------------- |
| CPU      | < 0.5% (loop blocked waiting in kernel BT syscall)                   |
| RAM      | ~10–15 MB (Python + hidapi)                                          |
| Disk     | 0 writes during normal operation (logs only if `--log-file` is used) |
| Network  | 0 bytes (100% local Bluetooth, no internet connection)               |
| Battery  | Negligible — equivalent to having Bluetooth enabled normally         |

The main loop spends most of its time blocked in `hid_read_timeout` (a kernel syscall). Python only executes for a few microseconds per cycle. Comparable to a background SSH agent or the native Bluetooth daemon.

### Tested

| Device                                  | OS              | Connection |
| --------------------------------------- | --------------- | ---------- |
| MX Keys S + MX Vertical                 | macOS (Sequoia) | Bluetooth  |
| MX Keys S + MX Vertical                 | Windows 11      | Bluetooth  |
| 2× MX Keys S (PID=0xB35B) + MX Master 4 | macOS 13+       | Bluetooth  |

_Validated in production: 3 Macs simultaneously, multi-keyboard._

Should work with any Logitech device combo supporting HID++ 2.0 and CHANGE_HOST.

---

## 🤝 Support

If SwiGi saves you time / Si SwiGi t'économise du temps :

<a href="https://github.com/sponsors/LeeHoffka" target="_blank"><img src="https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?style=for-the-badge&logo=github" alt="Sponsor on GitHub" height="40"></a>

---

## 📋 Historique des correctifs

| Version    | Symptôme                                                             | Fix                                            |
| ---------- | -------------------------------------------------------------------- | ---------------------------------------------- |
| 2026-05-26 | Souris ne suit pas (macOS BT retourne réponses paddées 32 octets)    | MSG_LENGTHS check accepte len >= au lieu de == |
| 2026-05-26 | Delay jusqu'à 500ms lors du switch (mouse_lock tenu pendant HID I/O) | I/O sorti du lock dans probe loop              |

---

## 🛠️ Développement

### Installation de l'environnement

```bash
git clone https://github.com/SirHarveyBix/SwiGi.git
cd SwiGi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Lancer les tests

```bash
python -m pytest tests/ --cov=swigi --cov-report=term-missing -q
```

### Linter

```bash
ruff check swigi/ tests/
```

### Mise à jour des dépendances

Les dépendances Python sont listées dans `requirements.txt`. Pour mettre à jour :

```bash
source .venv/bin/activate
pip install --upgrade -r requirements.txt
```

> **Note :** `hidapi` n'est pas dans `requirements.txt` — c'est une bibliothèque C chargée via ctypes. Sur macOS : `brew install hidapi`. Sur Linux : `sudo apt install libhidapi-hidraw0`.
| 2026-05-26 | CPU spike pendant reconnexion (600 scans HID/60s)                    | Backoff exponentiel 0.5s→5s                              |
| 2026-05-26 | install_mac.sh curl\|bash cassé ($SCRIPT_DIR = bash)                 | Détection + message d'erreur clair, git clone recommandé |
| 2026-05-26 | launchctl load/unload déprécié macOS 13+                             | Remplacé par bootstrap/bootout                           |
| 2026-05-26 | Écriture plist BetterMouse non atomique (corruption si crash)        | tempfile + os.replace()                                  |
| 2026-05-27 | Architecture daemon trop complexe (1200L, pending_host, correction auto) | Réécriture v2 : pipe unidirectionnel ~340L, envoi immédiat |
| 2026-05-27 | Boucle infinie `get_device_name` si device retourne nom tronqué      | Guard `to_read <= 0: break`                              |
| 2026-05-27 | `_build_message` tronque silencieusement paramètres > 16 bytes       | `ValueError` explicite si `len(parameters) > 16`         |
| 2026-05-27 | Profil BetterMouse rejeté si casse du nom souris différente          | Comparaison case-insensitive                             |
| 2026-05-27 | Backups BetterMouse `.swigi_bak_*` jamais nettoyés                   | `os.unlink(backup)` après succès apply_profile           |
| 2026-05-27 | `RuntimeError` itération dict menu bar sous changement concurrent    | `list(menu.items())` avant itération                     |
| 2026-05-27 | Tests flaky : seuil timing `< 0.15s` trop serré (CI échoue)          | Event-based wait + seuil 0.25s                           |

---

## 📜 Licence / License

MIT — fais-en ce que tu veux / do whatever you want with it.

## 🙏 Crédits / Credits

Inspiré par [SwiGi](https://github.com/LeeHoffka/SwiGi) de LeeHoffka
