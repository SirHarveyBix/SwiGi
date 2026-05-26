#!/bin/bash
# SwiGi — Installation automatique macOS
# Lance ce script DEPUIS le dossier SwiGi (git clone ou zip extrait).
# Usage : bash install_mac.sh
#
# ⚠️  NE PAS utiliser curl | bash : le script a besoin d'être dans le dossier SwiGi.
#     Méthode recommandée :
#       git clone https://github.com/SirHarveyBix/SwiGi.git && cd SwiGi && bash install_mac.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SWIGI_PY="$SCRIPT_DIR/swigi.py"
PLIST="$HOME/Library/LaunchAgents/com.swigi.plist"
LOG="$HOME/Library/Logs/swigi.log"
LAUNCHD_TARGET="gui/$(id -u)"

echo "=== SwiGi — Installation macOS ==="
echo ""

# ── Vérification de la présence de swigi.py ──────────────────────────────────
if [ ! -f "$SWIGI_PY" ]; then
    echo "❌ swigi.py introuvable dans : $SCRIPT_DIR"
    echo ""
    echo "   Ce script doit être lancé depuis le dossier SwiGi."
    echo "   Si tu as utilisé curl | bash, ça ne fonctionne pas — le script"
    echo "   ne peut pas trouver les fichiers SwiGi sans le dossier."
    echo ""
    echo "   Solution :"
    echo "     git clone https://github.com/SirHarveyBix/SwiGi.git"
    echo "     cd SwiGi"
    echo "     bash install_mac.sh"
    exit 1
fi
echo "✅ SwiGi trouvé dans : $SCRIPT_DIR"

# ── Python ────────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 introuvable."
    echo "   Installe Python : https://www.python.org/downloads/"
    exit 1
fi
PYTHON_PATH="$(command -v python3)"
echo "✅ $(python3 --version)"

# ── hidapi ────────────────────────────────────────────────────────────────────
HIDAPI_OK=false
for path in /opt/homebrew/lib/libhidapi.dylib /usr/local/lib/libhidapi.dylib; do
    if [ -f "$path" ]; then
        HIDAPI_OK=true
        break
    fi
done

if $HIDAPI_OK; then
    echo "✅ hidapi trouvé"
else
    echo "📦 Installation de hidapi..."
    if command -v brew &>/dev/null; then
        brew install hidapi
    else
        echo ""
        echo "❌ Homebrew introuvable. Installe-le d'abord :"
        echo '   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        echo "   Puis relance ce script."
        exit 1
    fi
    echo "✅ hidapi installé"
fi

# ── rumps (icône menu bar) ────────────────────────────────────────────────────
if "$PYTHON_PATH" -c "import rumps" 2>/dev/null; then
    echo "✅ rumps trouvé"
else
    echo "📦 Installation de rumps (icône menu bar)..."
    "$PYTHON_PATH" -m pip install --quiet rumps --break-system-packages 2>/dev/null \
        || "$PYTHON_PATH" -m pip install --quiet rumps
    if "$PYTHON_PATH" -c "import rumps" 2>/dev/null; then
        echo "✅ rumps installé"
    else
        echo "⚠️  rumps introuvable après installation — l'icône menu bar sera absente"
        echo "   SwiGi fonctionnera quand même (sans icône)."
    fi
fi

# ── LaunchAgent plist ─────────────────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.swigi</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$SWIGI_PY</string>
        <string>--log-file</string>
        <string>$LOG</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
EOF
echo "✅ Configuration démarrage automatique créée"

# ── Démarrage (bootstrap/bootout remplace load/unload déprécié) ───────────────
launchctl bootout "$LAUNCHD_TARGET" "$PLIST" 2>/dev/null || true
launchctl bootstrap "$LAUNCHD_TARGET" "$PLIST"
echo "✅ SwiGi démarré"

echo ""
echo "════════════════════════════════════════"
echo " SwiGi est installé et actif !"
echo "════════════════════════════════════════"
echo ""
echo "  📋 Logs        : $LOG"
echo "  🛑 Désactiver  : launchctl bootout  $LAUNCHD_TARGET $PLIST"
echo "  ▶️  Réactiver   : launchctl bootstrap $LAUNCHD_TARGET $PLIST"
echo ""
echo "⚠️  ACTION REQUISE une seule fois :"
echo "   Réglages Système → Confidentialité et sécurité"
echo "   → Surveillance des entrées → ajouter le binaire Python :"
echo "   $PYTHON_PATH"
echo ""
