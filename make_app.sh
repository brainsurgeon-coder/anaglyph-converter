#!/bin/bash
# Builds "Anaglyph Converter.app" on your Desktop.
# Run once from Terminal:  bash /path/to/this/folder/make_app.sh

set -e

# The folder this script lives in (works wherever the project is downloaded)
FOLDER="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/anaglyph-env/bin/python"
SCRIPT="$FOLDER/anaglyph_converter.py"
ICON_PNG="$FOLDER/icon.png"
APP="$HOME/Desktop/Anaglyph Converter.app"

# --- Checks ---
if [ ! -f "$SCRIPT" ]; then
  echo "Can't find $SCRIPT"
  echo "Keep anaglyph_converter.py in the same folder as make_app.sh."
  exit 1
fi

if [ ! -x "$PY" ]; then
  echo "Setting up the Python environment (one time)..."
  if [ -x /opt/homebrew/bin/python3 ]; then BASEPY=/opt/homebrew/bin/python3; else BASEPY=python3; fi
  "$BASEPY" -m venv "$HOME/anaglyph-env"
  "$HOME/anaglyph-env/bin/pip" install --quiet tkinterdnd2
fi

# --- Build the app bundle ---
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/AnaglyphConverter" <<EOF
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
exec "$PY" "$SCRIPT"
EOF
chmod +x "$APP/Contents/MacOS/AnaglyphConverter"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Anaglyph Converter</string>
  <key>CFBundleDisplayName</key><string>Anaglyph Converter</string>
  <key>CFBundleIdentifier</key><string>com.getdrsarkar.anaglyphconverter</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>AnaglyphConverter</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

# --- Icon ---
if [ -f "$ICON_PNG" ]; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z $d $d "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
fi

# Remove the old Terminal launcher if it exists
rm -f "$HOME/Desktop/Anaglyph.command"

touch "$APP"
echo "Done! 'Anaglyph Converter' is on your Desktop."
