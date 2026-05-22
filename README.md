# 🔀 SwiGi

**Switch your keyboard. Mouse follows. Done.**

SwiGi synchronise le bouton Easy-Switch entre le clavier et la souris Logitech via Bluetooth — sans dongle USB, sans Logi Options+, sans contrainte réseau.

> _Made for people. Enjoy it and stop being a slave to buttons._

<p align="center">
  <i>Ça t'a fait gagner du temps ?</i><br><br>
  <a href="https://github.com/sponsors/SirHarveyBix" target="_blank"><img src="https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?style=for-the-badge&logo=github" alt="Sponsor on GitHub" height="40"></a>
</p>

🇫🇷 **Français** — tu lis ça | 🇬🇧 [English](#-english) — scroll down

---

## 🇫🇷 Français

### Requirements

- Un clavier **et** une souris Logitech avec Easy-Switch et Bluetooth (série MX, série Ergo, etc.)
- **pas besoin de savoir coder.**

---

### 🍎 Installation macOS

**Méthode la plus simple — script automatique (recommandé)**

1. Ouvre le Terminal (`Cmd+Espace`)
2. Colle cette commande et appuie sur Entrée :

```bash
curl -fsSL https://raw.githubusercontent.com/SirHarveyBix/SwiGi/main/install_mac.sh | bash
```

C'est tout. SwiGi démarre et se relancera automatiquement à chaque démarrage de ton Mac.

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
cd swigi
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
launchctl unload ~/Library/LaunchAgents/com.swigi.plist
```

---

#### 🔐 Permission requise sur macOS

> ⚠️ **Important — à faire une seule fois**

macOS bloque par défaut l'accès aux périphériques d'entrée. Tu dois autoriser SwiGi :

1. Ouvre **Réglages Système** → **Confidentialité et sécurité** → **Surveillance des entrées**
2. Clique sur le **+** et ajoute **Terminal** (ou **SwiGi** si tu utilises le build portable)
3. Redémarre SwiGi

> ⚠️ **Après chaque rebuild** (build portable PyInstaller), macOS ne reconnaît plus le binaire. Supprime l'ancien SwiGi dans Surveillance des entrées et rajoute le nouveau.

---

### 🪟 Installation Windows

**Tu n'as pas besoin d'installer Python.**

**Étape 1 — Télécharger les fichiers**

Télécharge et place dans un même dossier (ex. `C:\SwiGi\`) :

- `swigi.py` (depuis cette page — bouton vert **Code** → **Download ZIP**)
- `hidapi.dll` depuis [github.com/libusb/hidapi/releases](https://github.com/libusb/hidapi/releases) → Assets → `hidapi-win.zip` → dossier `x64` → `hidapi.dll`
- Python embeddable depuis [python.org/downloads/windows](https://www.python.org/downloads/windows/) → « Windows embeddable package (64-bit) » → dézippe dans un sous-dossier `python-3\`
- `setup_win.bat` (inclus dans le ZIP SwiGi)

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

### ❓ Problèmes fréquents

| Problème                         | Solution                                                         |
| -------------------------------- | ---------------------------------------------------------------- |
| « Clavier introuvable »          | Vérifie que le clavier est connecté en Bluetooth (pas en USB)    |
| « Souris introuvable »           | Idem pour la souris                                              |
| Rien ne se passe sur macOS       | Ajoute Terminal dans Surveillance des entrées (voir ci-dessus)   |
| `hidapi introuvable` sur macOS   | Lance `brew install hidapi`                                      |
| `hidapi introuvable` sur Windows | Vérifie que `hidapi.dll` est dans le même dossier que `swigi.py` |
| SwiGi se lance mais ne fait rien | Lance avec `-v` pour plus de détails : `python3 swigi.py -v`     |

---

### ⚙️ Options

```bash
python3 swigi.py       # mode normal
python3 swigi.py -v    # mode verbose (logs détaillés)
```

---

### Comment ça marche

1. SwiGi envoie un « ping » régulier au clavier via Bluetooth (~100ms)
2. Quand tu appuies sur Easy-Switch, le clavier envoie une notification `CHANGE_HOST`
3. SwiGi la capture et envoie la même commande à la souris
4. Les deux périphériques basculent sur le même hôte

Utilise le protocole HID++ 2.0 (feature CHANGE_HOST `0x1814`). Un seul fichier Python, aucune dépendance sauf hidapi.

---

### Appareils testés

| Appareil                | OS              | Connexion |
| ----------------------- | --------------- | --------- |
| MX Keys S + MX Vertical | macOS (Sequoia) | Bluetooth |
| MX Keys S + MX Vertical | Windows 11      | Bluetooth |

Devrait fonctionner avec toute combinaison d'appareils Logitech supportant HID++ 2.0 et CHANGE_HOST.

---

## 🇬🇧 English

**Press Easy-Switch. Mouse follows. Done.**

SwiGi syncs Easy-Switch between your Logitech keyboard and mouse over Bluetooth — no USB receiver, no Logi Options+, no same-network requirement.

### Requirements

- Logitech keyboard + mouse with Easy-Switch and Bluetooth (MX series, Ergo series, etc.)
- **No coding knowledge required.** Just follow the steps below.

### 🍎 macOS

**Automatic install (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/SirHarveyBix/SwiGi/main/install_mac.sh | bash
```

This installs hidapi, starts SwiGi, and sets it to launch automatically at login.

**Manual install:**

1. Install [Python 3](https://www.python.org/downloads/)
2. Install [Homebrew](https://brew.sh/) then run `brew install hidapi`
3. Download this repo (green **Code** button → **Download ZIP**), unzip it
4. In Terminal: `python3 swigi.py`

**macOS Permission (required once):**
System Settings → Privacy & Security → Input Monitoring → add Terminal (or SwiGi)

> ⚠️ After every PyInstaller rebuild, remove the old SwiGi entry and re-add the new binary.

### 🪟 Windows

1. Download `hidapi.dll` from [libusb/hidapi releases](https://github.com/libusb/hidapi/releases) → Assets → `hidapi-win.zip` → `x64/hidapi.dll`
2. Download the [Python embeddable package](https://www.python.org/downloads/windows/) (64-bit ZIP) → extract to `python-3\`
3. Put `swigi.py`, `hidapi.dll`, `python-3\`, `setup_win.bat` in one folder
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

### Tested

| Device                  | OS              | Connection |
| ----------------------- | --------------- | ---------- |
| MX Keys S + MX Vertical | macOS (Sequoia) | Bluetooth  |
| MX Keys S + MX Vertical | Windows 11      | Bluetooth  |

Should work with any Logitech device combo supporting HID++ 2.0 and CHANGE_HOST.

---

## 🤝 Support

If SwiGi saves you time / Si SwiGi t'économise du temps :

<a href="https://github.com/sponsors/SirHarveyBix" target="_blank"><img src="https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?style=for-the-badge&logo=github" alt="Sponsor on GitHub" height="40"></a>

---

## 📜 Licence / License

MIT — fais-en ce que tu veux / do whatever you want with it.

## 🙏 Crédits / Credits

Inspiré par [CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch) de MikalaiBarysevich et la doc protocole de [Solaar](https://github.com/pwr-Solaar/Solaar).
