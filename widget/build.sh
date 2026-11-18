#!/bin/zsh
# Build widget/InternRadar.app from InternRadar.swift.
set -euo pipefail
cd "$(dirname "$0")"
APP=InternRadar.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
swiftc -O -swift-version 5 -target arm64-apple-macos13.0 -o "$APP/Contents/MacOS/InternRadar" InternRadar.swift \
  -framework Cocoa -framework WebKit -framework Network -framework UserNotifications -framework EventKit
cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>local.internradar.widget</string>
  <key>CFBundleName</key><string>Intern Radar</string>
  <key>CFBundleExecutable</key><string>InternRadar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSRemindersUsageDescription</key><string>Intern Radar adds a reminder when a strong internship match has been waiting two days without an application.</string>
  <key>NSRemindersFullAccessUsageDescription</key><string>Intern Radar adds a reminder when a strong internship match has been waiting two days without an application.</string>
  <key>NSDesktopFolderUsageDescription</key><string>Intern Radar reads its dashboard and runs the internship sweep in your Automation folder on the Desktop.</string>
</dict></plist>
EOF
codesign --force --sign - "$APP"
echo "built $PWD/$APP"
