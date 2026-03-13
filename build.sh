#!/bin/bash
# ──────────────────────────────────────────────
# WhisperMac – App bauen
# Ausführen: ./build.sh
# ──────────────────────────────────────────────
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="WhisperMac"
APP_OUT="$SCRIPT_DIR/dist/$APP_NAME.app"

echo "🔨 $APP_NAME.app wird gebaut..."

# dist-Ordner vorbereiten
rm -rf "$SCRIPT_DIR/dist"
mkdir -p "$APP_OUT/Contents/MacOS"
mkdir -p "$APP_OUT/Contents/Resources"

# ── Info.plist ────────────────────────────────
cat > "$APP_OUT/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>WhisperMac</string>
    <key>CFBundleDisplayName</key>
    <string>WhisperMac</string>
    <key>CFBundleIdentifier</key>
    <string>com.whispermac.app</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>WhisperMac</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>WhisperMac benötigt Mikrofonzugriff für Sprachaufnahmen.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>WhisperMac benötigt diese Berechtigung zum Einfügen von Text.</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

# ── C-Launcher kompilieren (echtes Binary = kein Python-im-Dock Problem) ─
PYTHON_FW="/opt/homebrew/opt/python@3.11/Frameworks/Python.framework/Versions/3.11"
PYTHON_BIN="$PYTHON_FW/bin/python3.11"
SITE_PACKAGES="$SCRIPT_DIR/venv/lib/python3.11/site-packages"
APP_SCRIPT="$SCRIPT_DIR/app.py"

cat > /tmp/whispermac_launcher.c << CSRC
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

int main(void) {
    wchar_t prog[1024];
    mbstowcs(prog, "$PYTHON_BIN", 1024);
    Py_SetProgramName(prog);
    setenv("PYTHONPATH", "$SITE_PACKAGES", 1);

    Py_Initialize();

    /* sys.path erweitern + sys.frozen = True */
    PyRun_SimpleString(
        "import sys; sys.frozen = True; sys.argv = ['WhisperMac']; "
        "sys.path.insert(0, '$SCRIPT_DIR')"
    );

    FILE *fp = fopen("$APP_SCRIPT", "r");
    if (!fp) {
        fprintf(stderr, "WhisperMac: app.py nicht gefunden\\n");
        Py_Finalize();
        return 1;
    }
    int ret = PyRun_SimpleFile(fp, "$APP_SCRIPT");
    fclose(fp);
    if (PyErr_Occurred()) PyErr_Print();
    Py_Finalize();
    return ret;
}
CSRC

echo "🔧 Kompiliere Launcher..."
PY_INCLUDES=$(/opt/homebrew/opt/python@3.11/bin/python3.11-config --includes)
PY_LDFLAGS=$(/opt/homebrew/opt/python@3.11/bin/python3.11-config --ldflags --embed)
PY_RPATH="/opt/homebrew/opt/python@3.11/Frameworks/Python.framework/Versions/3.11/lib"

clang /tmp/whispermac_launcher.c \
    -o "$APP_OUT/Contents/MacOS/$APP_NAME" \
    $PY_INCLUDES $PY_LDFLAGS \
    -Wl,-rpath,"$PY_RPATH" \
    -w

# ── Ad-hoc Signatur (damit macOS die App startet) ─
echo "🔏 Signiere App..."
codesign --force --deep --sign - "$APP_OUT"

echo ""
echo "✅ Fertig! App liegt in: dist/WhisperMac.app"
echo ""
echo "Zum Installieren:"
echo "  cp -r dist/WhisperMac.app /Applications/"
echo ""
echo "Beim ersten Start → Systemeinstellungen:"
echo "  Datenschutz & Sicherheit → Mikrofon → WhisperMac ✓"
echo "  Datenschutz & Sicherheit → Bedienungshilfen → WhisperMac ✓"
