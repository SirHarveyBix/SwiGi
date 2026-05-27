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

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   🔀 SwiGi — Installation macOS     ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""

# ── Vérification de la présence de swigi.py ──────────────────────────────────
if [ ! -f "$SWIGI_PY" ]; then
    echo -e "${RED}✖ swigi.py introuvable dans : $SCRIPT_DIR${RESET}"
    echo ""
    echo -e "  Ce script doit être lancé depuis le dossier SwiGi."
    echo -e "  ${DIM}Si tu as utilisé curl | bash directement, utilise plutôt :${RESET}"
    echo ""
    echo -e "  ${BOLD}curl -fsSL https://raw.githubusercontent.com/SirHarveyBix/SwiGi/main/install_curl.sh | bash${RESET}"
    exit 1
fi
echo -e "${GREEN}✓${RESET} SwiGi trouvé dans : ${BOLD}$SCRIPT_DIR${RESET}"

# ── Python ────────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✖ Python 3 introuvable.${RESET}"
    echo "  Installe Python : https://www.python.org/downloads/"
    exit 1
fi
PYTHON_PATH="$(command -v python3)"
echo -e "${GREEN}✓${RESET} $(python3 --version)"

# ── hidapi ────────────────────────────────────────────────────────────────────
HIDAPI_OK=false
for path in /opt/homebrew/lib/libhidapi.dylib /usr/local/lib/libhidapi.dylib; do
    if [ -f "$path" ]; then
        HIDAPI_OK=true
        break
    fi
done

if $HIDAPI_OK; then
    echo -e "${GREEN}✓${RESET} hidapi trouvé"
else
    echo -e "${BLUE}→${RESET} Installation de hidapi..."
    if command -v brew &>/dev/null; then
        brew install hidapi
    else
        echo ""
        echo -e "${RED}✖ Homebrew introuvable. Installe-le d'abord :${RESET}"
        echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        echo "  Puis relance ce script."
        exit 1
    fi
    echo -e "${GREEN}✓${RESET} hidapi installé"
fi

# ── rumps (icône menu bar) ────────────────────────────────────────────────────
if "$PYTHON_PATH" -c "import rumps" 2>/dev/null; then
    echo -e "${GREEN}✓${RESET} rumps trouvé"
else
    echo -e "${BLUE}→${RESET} Installation de rumps (icône menu bar)..."
    "$PYTHON_PATH" -m pip install --quiet rumps --break-system-packages 2>/dev/null \
        || "$PYTHON_PATH" -m pip install --quiet rumps
    if "$PYTHON_PATH" -c "import rumps" 2>/dev/null; then
        echo -e "${GREEN}✓${RESET} rumps installé"
    else
        echo -e "${YELLOW}⚠${RESET}  rumps introuvable après installation — l'icône menu bar sera absente"
        echo "  SwiGi fonctionnera quand même (sans icône)."
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
echo -e "${GREEN}✓${RESET} Configuration démarrage automatique créée"

# ── Démarrage (bootstrap/bootout remplace load/unload déprécié) ───────────────
launchctl bootout "$LAUNCHD_TARGET" "$PLIST" 2>/dev/null || true
launchctl bootstrap "$LAUNCHD_TARGET" "$PLIST"
echo -e "${GREEN}✓${RESET} SwiGi démarré"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   ${GREEN}✓ SwiGi est installé et actif !${RESET}${BOLD}   ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""
echo -e "  📋 Logs        : ${BOLD}$LOG${RESET}"
echo -e "  🛑 Désactiver  : ${DIM}launchctl bootout $LAUNCHD_TARGET $PLIST${RESET}"
echo -e "  ▶️  Réactiver   : ${DIM}launchctl bootstrap $LAUNCHD_TARGET $PLIST${RESET}"
echo ""
echo -e "${YELLOW}${BOLD}⚠  ACTION REQUISE une seule fois :${RESET}"
echo -e "  Réglages Système → Confidentialité et sécurité"
echo -e "  → Surveillance des entrées → ajouter le binaire Python :"
echo -e "  ${BOLD}$PYTHON_PATH${RESET}"
echo ""
