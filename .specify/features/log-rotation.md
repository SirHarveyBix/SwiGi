# Spec : Log rotation

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

Avec launchd (`KeepAlive = true`), SwiGi tourne indéfiniment. `StandardOutPath` dans le plist écrit dans un fichier sans limite de taille — peut atteindre plusieurs centaines de Mo après quelques semaines.

## 2. Solution

`--log-file FILE` active un `RotatingFileHandler` (stdlib `logging.handlers`) : max 1 Mo par fichier, 3 fichiers de sauvegarde = maximum ~4 Mo total. `install_mac.sh` passe ce flag au plist launchd à la place de `StandardOutPath`.

## 3. Exigences fonctionnelles

| #   | Exigence                                                                | Priorité |
| --- | ----------------------------------------------------------------------- | -------- |
| F1  | `--log-file FILE` active la rotation automatique                        | MUST     |
| F2  | Taille max totale ≤ 4 Mo (1 Mo × 3 backups + courant)                   | MUST     |
| F3  | Sans `--log-file` : logs stdout uniquement (comportement actuel)        | MUST     |
| F4  | Console (stdout) et fichier actifs simultanément si `--log-file` fourni | SHOULD   |

## 4. Implémentation

```python
if args.log_file:
    fh = logging.handlers.RotatingFileHandler(
        args.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)
```

`install_mac.sh` génère le plist avec :

```xml
<string>--log-file</string>
<string>~/Library/Logs/swigi.log</string>
```

(remplace `StandardOutPath` qui n'a pas de rotation)

## 5. Conformité constitution

| Principe        | Impact     | Mesure                                     |
| --------------- | ---------- | ------------------------------------------ |
| Simplicité      | ✅ Neutre  | `logging.handlers` stdlib, zéro dépendance |
| Portabilité     | ✅ Positif | Fonctionne sur les 3 OS                    |
| Robustesse      | ✅ Positif | Pas de disque plein après usage long terme |
| Non-intrusivité | ✅ Neutre  | Opt-in via flag CLI                        |
| Réactivité      | ✅ Neutre  | Écriture asynchrone bufferisée par le OS   |
