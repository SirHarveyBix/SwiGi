#!/bin/bash
# SwiGi — Build portable macOS (PyInstaller)
# Produit dist/SwiGi/ — copie ce dossier n'importe où, aucune installation requise.
set -e

echo "=== SwiGi — Build portable macOS ==="

python3 --version || { echo "Python3 introuvable !"; exit 1; }

pip3 install pyinstaller --break-system-packages 2>/dev/null || pip3 install pyinstaller

mkdir -p lib
if [ ! -f lib/libhidapi.dylib ]; then
    if [ -f /opt/homebrew/lib/libhidapi.dylib ]; then
        cp /opt/homebrew/lib/libhidapi.dylib lib/
    elif [ -f /usr/local/lib/libhidapi.dylib ]; then
        cp /usr/local/lib/libhidapi.dylib lib/
    else
        echo "libhidapi.dylib introuvable ! Lance : brew install hidapi"
        exit 1
    fi
fi

echo "Construction en cours..."
pyinstaller \
  --name SwiGi \
  --onedir \
  --console \
  --clean \
  --noconfirm \
  --add-binary "lib/libhidapi.dylib:." \
  --exclude-module tkinter \
  --exclude-module unittest \
  swigi.py

if [ -f dist/SwiGi/SwiGi ]; then
    echo ""
    echo "=== Build terminé ! ==="
    echo "Dossier : dist/SwiGi/"
    echo "Lancement : ./dist/SwiGi/SwiGi"
    echo ""
    echo "⚠️  Après chaque build, mets à jour Surveillance des entrées dans"
    echo "   Réglages Système → Confidentialité et sécurité."
else
    echo "Build échoué !"
    exit 1
fi
