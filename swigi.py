#!/usr/bin/env python3
"""SwiGi — synchronisation Easy-Switch via Bluetooth.

Quand Easy-Switch est pressé sur le clavier Logitech, capture la notification
CHANGE_HOST et envoie la même commande à la souris. Les deux basculent sur le même hôte.

Autonome : tout le code HID++ est inclus dans le package 'swigi'.
Seule dépendance = bibliothèque hidapi.

macOS:  bash install_mac.sh  (installe tout + autostart)
Windows: hidapi.dll dans le dossier de ce fichier + double-cliquer setup_win.bat
Linux:  sudo apt install libhidapi-hidraw0 && python3 swigi.py

Options :
  python3 swigi.py           # mode DEBUG (tous les logs)
  python3 swigi.py -q        # mode quiet (INFO seulement)
  python3 swigi.py --log-file swigi.log
"""

import sys

from swigi.main import main

if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
