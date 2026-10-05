#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release --scratch-path .build --jobs "${SWIFT_JOBS:-4}"
mkdir -p dist
STAGE="$(mktemp -d "dist/.build-app.XXXXXX")"
APP="$STAGE/续言 ReThread.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/Backend"
cp .build/release/CodexConversations "$APP/Contents/MacOS/CodexConversations"
cp Sources/Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp manager.py merge_media.py stream_json.py merge_stream.py project_assignment.py desktop_sync.py project_catalog.py project_maintenance.py history_maintenance.py history_index.py server.py agent_repair.py LICENSE "$APP/Contents/Resources/Backend/"
cp -R vendor "$APP/Contents/Resources/Backend/"
find "$APP/Contents/Resources/Backend/vendor" -type d -name __pycache__ -prune -exec rm -rf {} +
cp -R ThirdPartyNotices "$APP/Contents/Resources/"
for resource in .build/release/*.bundle; do
    cp -R "$resource" "$APP/Contents/Resources/"
done
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>CodexConversations</string>
<key>CFBundleIdentifier</key><string>local.zijiu.codex-conversations</string>
<key>CFBundleName</key><string>续言 ReThread</string>
<key>CFBundleDisplayName</key><string>续言 ReThread</string>
<key>CFBundleVersion</key><string>17</string>
<key>CFBundleShortVersionString</key><string>0.2.0</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
DEST="dist/续言 ReThread.app"
if [ -d "$DEST" ]; then
    BACKUP="dist/.previous-ReThread-$(date +%Y%m%d-%H%M%S).app"
    mv "$DEST" "$BACKUP"
    printf 'Previous bundle preserved: %s/%s\n' "$PWD" "$BACKUP"
fi
mv "$APP" "$DEST"
rmdir "$STAGE"
printf '\nBuilt: %s/%s\n' "$PWD" "$DEST"
