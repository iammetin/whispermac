#!/bin/bash
# ══════════════════════════════════════════════════════════════════
#  WhisperMac – Einrichtung & Installation
#  Ausführen: chmod +x setup.sh && ./setup.sh
# ══════════════════════════════════════════════════════════════════
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="WhisperMac"

# ── Farben ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}▶  $1${RESET}"; }
success() { echo -e "${GREEN}✅  $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠️   $1${RESET}"; }
error()   { echo -e "${RED}❌  $1${RESET}"; exit 1; }
header()  { echo -e "\n${BOLD}$1${RESET}\n$(printf '─%.0s' {1..55})"; }

clear
echo -e "${BOLD}"
echo "  ██╗    ██╗██╗  ██╗██╗███████╗██████╗ ███████╗██████╗"
echo "  ██║    ██║██║  ██║██║██╔════╝██╔══██╗██╔════╝██╔══██╗"
echo "  ██║ █╗ ██║███████║██║███████╗██████╔╝█████╗  ██████╔╝"
echo "  ██║███╗██║██╔══██║██║╚════██║██╔═══╝ ██╔══╝  ██╔══██╗"
echo "  ╚███╔███╔╝██║  ██║██║███████║██║     ███████╗██║  ██║"
echo "   ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝╚══════╝╚═╝     ╚══════╝╚═╝  ╚═╝"
echo -e "${RESET}"
echo -e "  ${BOLD}Mac${RESET} – Lokale Sprache-zu-Text App"
echo ""

# ══════════════════════════════════════════════════════════════════
header "1 / 5  Voraussetzungen prüfen"
# ══════════════════════════════════════════════════════════════════

# macOS prüfen
if [[ "$(uname)" != "Darwin" ]]; then
    error "WhisperMac läuft nur auf macOS."
fi
success "macOS erkannt"

# Apple Silicon prüfen
if [[ "$(uname -m)" != "arm64" ]]; then
    error "WhisperMac benötigt einen Mac mit Apple Silicon (M1/M2/M3/M4)."
fi
success "Apple Silicon ($(uname -m)) erkannt"

# Xcode Command Line Tools prüfen
if ! xcode-select -p &>/dev/null; then
    warn "Xcode Command Line Tools nicht gefunden – werden installiert..."
    xcode-select --install
    echo "Bitte Installation abwarten, dann setup.sh erneut ausführen."
    exit 0
fi
success "Xcode Command Line Tools vorhanden"

# Homebrew prüfen
if ! command -v brew &>/dev/null; then
    error "Homebrew nicht gefunden.\nBitte installieren: https://brew.sh"
fi
success "Homebrew gefunden"

# Python 3.11 prüfen
PYTHON_BIN="/opt/homebrew/opt/python@3.11/bin/python3.11"
if [[ ! -f "$PYTHON_BIN" ]]; then
    info "Python 3.11 wird über Homebrew installiert..."
    brew install python@3.11
fi
success "Python 3.11 gefunden ($PYTHON_BIN)"

# ══════════════════════════════════════════════════════════════════
header "2 / 5  Modelle prüfen"
# ══════════════════════════════════════════════════════════════════

WHISPER_MODEL="$SCRIPT_DIR/models/whisper-cpp/ggml-large-v3-turbo.bin"
WHISPER_COREML_DIR="$SCRIPT_DIR/models/whisper-cpp/ggml-large-v3-turbo-encoder.mlmodelc"
WHISPER_SERVER_BIN="$SCRIPT_DIR/vendor/whisper.cpp-runtime/build/bin/whisper-server"
LLM_DIR="$SCRIPT_DIR/models/llm"
WHISPER_OK=false
LLM_OK=false

# whisper.cpp Runtime + Modell prüfen
if [[ -f "$WHISPER_MODEL" && -d "$WHISPER_COREML_DIR" && -x "$WHISPER_SERVER_BIN" ]]; then
    WHISPER_OK=true
    success "whisper.cpp Runtime und Modell gefunden"
else
    echo ""
    warn "whisper.cpp Runtime oder Modell fehlen im Projekt."
    echo ""
    echo -e "  ${BOLD}Erwartete Dateien:${RESET}"
    echo ""
    echo "  • $WHISPER_SERVER_BIN"
    echo "  • $WHISPER_MODEL"
    echo "  • $WHISPER_COREML_DIR"
    echo ""
    read -p "  Ohne whisper.cpp Runtime fortfahren? (App startet dann nicht) [j/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        echo "  Bitte Runtime/Modell bereitstellen und setup.sh erneut ausführen."
        exit 0
    fi
fi

# LLM prüfen (optional)
if ls "$LLM_DIR"/*.safetensors &>/dev/null; then
    LLM_OK=true
    success "LLM-Modell (KI-Korrektur) gefunden"
else
    warn "Kein LLM in models/llm/ – KI-Korrektur nicht verfügbar (optional)."
    echo -e "  ${BLUE}Empfehlung: huggingface-cli download NexVeridian/Qwen3.5-2B-8bit --local-dir \"$LLM_DIR\"${RESET}"
fi

# ══════════════════════════════════════════════════════════════════
header "3 / 5  Python-Umgebung einrichten"
# ══════════════════════════════════════════════════════════════════

VENV="$SCRIPT_DIR/venv"

if [[ ! -d "$VENV" ]]; then
    info "Virtuelle Umgebung wird erstellt..."
    "$PYTHON_BIN" -m venv "$VENV"
    success "Virtuelle Umgebung erstellt"
else
    success "Virtuelle Umgebung bereits vorhanden"
fi

# Abhängigkeiten nur installieren wenn noch nicht vorhanden
if ! "$VENV/bin/python" -c "import rumps, sounddevice, deep_translator" &>/dev/null; then
    info "Abhängigkeiten werden installiert..."
    "$VENV/bin/pip" install --upgrade pip --quiet
    "$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet
    success "Abhängigkeiten installiert"
else
    success "Abhängigkeiten bereits installiert"
fi

# mlx-lm nur installieren wenn LLM vorhanden und noch nicht installiert
if $LLM_OK; then
    if ! "$VENV/bin/python" -c "import mlx_lm" &>/dev/null; then
        info "mlx-lm für KI-Korrektur wird installiert..."
        "$VENV/bin/pip" install mlx-lm --quiet
        success "mlx-lm installiert"
    else
        success "mlx-lm bereits installiert"
    fi
fi

# ══════════════════════════════════════════════════════════════════
header "4 / 5  App bauen & installieren"
# ══════════════════════════════════════════════════════════════════

APP_BINARY="/Applications/$APP_NAME.app/Contents/MacOS/$APP_NAME"
BUILD_NEEDED=false

if [[ ! -f "$APP_BINARY" ]]; then
    BUILD_NEEDED=true
    info "App noch nicht vorhanden – wird gebaut..."
elif find "$SCRIPT_DIR" -maxdepth 1 -name "*.py" -newer "$APP_BINARY" | grep -q .; then
    BUILD_NEEDED=true
    info "Quellcode hat sich geändert – App wird neu gebaut..."
elif find "$SCRIPT_DIR/assets" -newer "$APP_BINARY" | grep -q .; then
    BUILD_NEEDED=true
    info "Assets haben sich geändert – App wird neu gebaut..."
else
    success "App ist bereits aktuell – Build übersprungen"
fi

if $BUILD_NEEDED; then
    bash "$SCRIPT_DIR/build.sh"
fi

# ══════════════════════════════════════════════════════════════════
header "5 / 5  Dock-Integration"
# ══════════════════════════════════════════════════════════════════

APP_PATH="/Applications/$APP_NAME.app"

# Prüfen ob schon im Dock
DOCK_PLIST="$HOME/Library/Preferences/com.apple.dock.plist"
if /usr/libexec/PlistBuddy -c "Print persistent-apps" "$DOCK_PLIST" 2>/dev/null \
    | grep -q "WhisperMac"; then
    success "WhisperMac ist bereits im Dock"
else
    info "WhisperMac wird zum Dock hinzugefügt..."
    defaults write com.apple.dock persistent-apps -array-add \
        "<dict>\
            <key>tile-data</key>\
            <dict>\
                <key>file-data</key>\
                <dict>\
                    <key>_CFURLString</key>\
                    <string>$APP_PATH</string>\
                    <key>_CFURLStringType</key>\
                    <integer>0</integer>\
                </dict>\
            </dict>\
        </dict>"
    killall Dock
    success "WhisperMac zum Dock hinzugefügt"
fi

# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  WhisperMac wurde erfolgreich installiert!           ${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Nächste Schritte:${RESET}"
echo ""
if $BUILD_NEEDED; then
echo "  1. App öffnet sich gleich automatisch"
echo "  2. Beim ersten Start Berechtigungen freigeben:"
else
echo "  1. WhisperMac starten: open /Applications/WhisperMac.app"
echo "  2. Berechtigungen freigeben (falls noch nicht geschehen):"
fi
echo "     → Systemeinstellungen → Datenschutz & Sicherheit"
echo "        • Mikrofon         → WhisperMac ✓"
echo "        • Bedienungshilfen → WhisperMac ✓"
echo ""
echo -e "  ${BOLD}Bedienung:${RESET}"
echo "     fn halten          → Aufnahme starten"
echo "     fn loslassen       → Transkribieren & einfügen"
echo "     fn fn (Doppeltipp) → KI-Korrektur an/aus"
echo "     F15                → Textkürzel"
echo "     F15 F15            → Workflows"
echo ""
