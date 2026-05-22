# Spec : Démarrage automatique — toutes plateformes

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

SwiGi doit être utile sans intervention après l'installation initiale. Un utilisateur non-développeur ne doit pas avoir à ouvrir un Terminal à chaque démarrage.

## 2. Périmètre

**Inclus :**

- macOS : LaunchAgent via plist launchd (`install_mac.sh` automatise tout)
- Windows : VBScript dans le dossier Startup (`setup_win.bat` automatise tout)
- Linux : service systemd user (`--user enable --now`)

**Exclus :**

- macOS Login Item (API différente, requiert bundle .app signé)
- Windows Service système (requiert admin, trop intrusif)

## 3. Exigences fonctionnelles

| #   | Exigence                                                        | Priorité |
| --- | --------------------------------------------------------------- | -------- |
| F1  | SwiGi démarre sans intervention après login                     | MUST     |
| F2  | SwiGi se relance automatiquement si crash (KeepAlive / Restart) | MUST     |
| F3  | Logs accessibles sans Terminal ouvert                           | MUST     |
| F4  | Désactivation simple en une commande / suppression d'un fichier | MUST     |
| F5  | Pas de fenêtre visible sur Windows (pythonw.exe / VBScript)     | MUST     |

## 4. Implémentation par plateforme

### macOS — `install_mac.sh`

- Détecte Python et hidapi, les installe si manquants (via Homebrew)
- Crée `~/Library/LaunchAgents/com.swigi.plist` pointant vers `python3 swigi.py`
- `python3 -m swigi` est aussi supporté comme alternative (même comportement)
- `KeepAlive = true` → launchd redémarre si crash
- Logs dans `~/Library/Logs/swigi.log`
- `launchctl load` démarre immédiatement

Désactivation : `launchctl unload ~/Library/LaunchAgents/com.swigi.plist`

### Windows — `setup_win.bat`

- Copie Python embeddable + hidapi.dll + `swigi.py` + dossier `swigi/` (récursivement) dans `%USERPROFILE%\SwiGi\`
- Crée `%APPDATA%\...\Startup\SwiGi.vbs` qui lance `pythonw.exe swigi.py` sans fenêtre
- VBScript exécuté à chaque login utilisateur

Désactivation : supprimer `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SwiGi.vbs`

### Linux — systemd user

```ini
[Service]
ExecStart=/usr/bin/python3 /path/to/swigi.py
Restart=always
RestartSec=5
```

`systemctl --user enable swigi` → démarre au login utilisateur (pas root)

Désactivation : `systemctl --user disable swigi`

## 5. Conformité constitution

| Principe        | Impact     | Mesure                                              |
| --------------- | ---------- | --------------------------------------------------- |
| Simplicité      | ✅ Positif | Scripts d'install = zéro effort utilisateur         |
| Portabilité     | ✅ Positif | Solution native à chaque OS                         |
| Robustesse      | ✅ Positif | Restart automatique sur crash                       |
| Non-intrusivité | ✅ Neutre  | User-level (pas system-level), révocable facilement |
| Réactivité      | ✅ Neutre  | Démarre avant utilisation, pas d'impact runtime     |
