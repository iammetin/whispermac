#!/bin/bash
# Einmalige lokale Signieridentität für WhisperMac. Nicht bei jedem Build ausführen.
set -euo pipefail
umask 077

SIGN_IDENTITY="WhisperMac Local Signing"
KEYCHAIN_PATH=$(security default-keychain -d user)
KEYCHAIN_PATH="${KEYCHAIN_PATH#*\"}"
KEYCHAIN_PATH="${KEYCHAIN_PATH%\"}"
if [ ! -f "$KEYCHAIN_PATH" ]; then
    echo "❌ Benutzer-Schlüsselbund nicht gefunden: $KEYCHAIN_PATH"
    exit 1
fi

SIGN_IDENTITIES=$(security find-identity -v -p codesigning)
if [[ "$SIGN_IDENTITIES" == *"\"$SIGN_IDENTITY\""* ]]; then
    echo "✅ $SIGN_IDENTITY ist bereits eingerichtet."
    exit 0
fi

if security find-certificate -c "$SIGN_IDENTITY" "$KEYCHAIN_PATH" >/dev/null 2>&1; then
    echo "❌ Zertifikat bereits vorhanden, aber nicht als gültige Signieridentität verfügbar."
    echo "   Bitte in der Schlüsselbundverwaltung privaten Schlüssel und Code-Signing-Vertrauen prüfen."
    exit 1
fi

SIGN_TMP=$(mktemp -d "${TMPDIR:-/tmp}/whispermac-signing.XXXXXX")
trap 'rm -rf "$SIGN_TMP"' EXIT

cat > "$SIGN_TMP/openssl.cnf" <<'EOF'
[req]
distinguished_name = subject
x509_extensions = signing
prompt = no
[subject]
CN = WhisperMac Local Signing
[signing]
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyCertSign,cRLSign
extendedKeyUsage = critical,codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
EOF

echo "🔏 Erstelle lokale Signieridentität (10 Jahre gültig)..."
/usr/bin/openssl req -new -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -config "$SIGN_TMP/openssl.cnf" \
    -keyout "$SIGN_TMP/key.pem" -out "$SIGN_TMP/certificate.pem" 2>/dev/null
# macOS benötigt hier einen passwortgeschützten, kompatiblen PKCS#12-Container.
# Das zufällige Transportpasswort wird nach dem Import nicht mehr benötigt.
SIGN_P12_PASSWORD=$(/usr/bin/openssl rand -hex 24)
/usr/bin/openssl pkcs12 -export \
    -inkey "$SIGN_TMP/key.pem" -in "$SIGN_TMP/certificate.pem" \
    -name "$SIGN_IDENTITY" -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 \
    -passout stdin -out "$SIGN_TMP/identity.p12" <<< "$SIGN_P12_PASSWORD"

# Nur codesign darf den Schlüssel verwenden; kein pauschaler App-Zugriff (-A).
security import "$SIGN_TMP/identity.p12" -k "$KEYCHAIN_PATH" -P "$SIGN_P12_PASSWORD" -T /usr/bin/codesign
unset SIGN_P12_PASSWORD
# Benutzer-Vertrauen ausschließlich für Code Signing, nicht für TLS/Webseiten.
security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN_PATH" "$SIGN_TMP/certificate.pem"

SIGN_IDENTITIES=$(security find-identity -v -p codesigning)
if [[ "$SIGN_IDENTITIES" != *"\"$SIGN_IDENTITY\""* ]]; then
    echo "❌ Die Signieridentität ist noch nicht verfügbar. Bitte den Schlüsselbund prüfen."
    exit 1
fi
echo "✅ Feste Signieridentität eingerichtet. Jetzt ./build.sh ausführen."
