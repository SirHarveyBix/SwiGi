#!/bin/bash
# SwiGi — Installation en une commande (curl)
# Usage : curl -fsSL https://raw.githubusercontent.com/SirHarveyBix/SwiGi/main/install_curl.sh | bash
#
# Ce script :
#   1. Clone le dépôt SwiGi dans ~/SwiGi
#   2. Lance install_mac.sh depuis le dossier cloné
#   3. Résultat : SwiGi installé, actif, et configuré en autostart

set -e

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

INSTALL_DIR="$HOME/SwiGi"

echo ""
echo -e "${BOLD}╔═════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   🔀 SwiGi — Installation automatique   ║${RESET}"
echo -e "${BOLD}╚═════════════════════════════════════════╝${RESET}"
echo ""

# ── Vérification OS ──────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}✖ Ce script est pour macOS uniquement.${RESET}"
    echo "  Pour Linux : sudo apt install python3 libhidapi-hidraw0 && python3 swigi.py"
    exit 1
fi

# ── Vérification Python ──────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✖ Python 3 introuvable.${RESET}"
    echo "  Installe Python : https://www.python.org/downloads/"
    exit 1
fi
echo -e "${GREEN}✓${RESET} Python $(python3 --version | cut -d' ' -f2)"

# ── Vérification git ─────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    echo -e "${RED}✖ git introuvable.${RESET}"
    echo "  Installe Xcode Command Line Tools : xcode-select --install"
    exit 1
fi
echo -e "${GREEN}✓${RESET} git disponible"

# ── Clone / mise à jour ──────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${BLUE}→${RESET} Mise à jour du dépôt existant..."
    cd "$INSTALL_DIR"
    git pull --quiet
    echo -e "${GREEN}✓${RESET} SwiGi mis à jour dans $INSTALL_DIR"
else
    if [ -d "$INSTALL_DIR" ]; then
        echo -e "${YELLOW}⚠${RESET} $INSTALL_DIR existe déjà (pas un dépôt git)"
        echo -e "  ${DIM}Suppression et re-clone...${RESET}"
        rm -rf "$INSTALL_DIR"
    fi
    echo -e "${BLUE}→${RESET} Clonage de SwiGi..."
    git clone --quiet https://github.com/SirHarveyBix/SwiGi.git "$INSTALL_DIR"
    echo -e "${GREEN}✓${RESET} SwiGi cloné dans $INSTALL_DIR"
fi

# ── Lancement de l'installeur ────────────────────────────────────────────────
echo ""
echo -e "${BLUE}→${RESET} Lancement de l'installation..."
echo -e "${DIM}─────────────────────────────────────────────${RESET}"
echo ""

cd "$INSTALL_DIR"
bash install_mac.sh

echo ""
echo -e "${DIM}─────────────────────────────────────────────${RESET}"
echo ""
echo -e "${GREEN}${BOLD}✓ Installation complète !${RESET}"
echo ""
echo -e "  📁 Dossier   : ${BOLD}$INSTALL_DIR${RESET}"
echo -e "  📋 Logs      : ${BOLD}~/Library/Logs/swigi.log${RESET}"
echo -e "  🔄 Mettre à jour : ${DIM}cd ~/SwiGi && git pull${RESET}"
echo ""
