#!/usr/bin/env bash
# Re-vendors EmulatorJS and the NDS cores from npm into ./emulator.
set -euo pipefail
VERSION="${1:-4.2.3}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"
npm pack "@emulatorjs/emulatorjs@$VERSION" "@emulatorjs/core-melonds@$VERSION" "@emulatorjs/core-desmume@$VERSION" >/dev/null
rm -rf "$ROOT/emulator"
mkdir -p "$ROOT/emulator"
tar xzf "emulatorjs-emulatorjs-$VERSION.tgz"
cp -r package/data/. "$ROOT/emulator/"
cp package/LICENSE "$ROOT/emulator/LICENSE-EmulatorJS"
rm -f "$ROOT/emulator/cores/package.json"
mkdir -p "$ROOT/emulator/cores/reports"
for core in melonds desmume; do
  rm -rf package && tar xzf "emulatorjs-core-$core-$VERSION.tgz"
  cp package/*.data "$ROOT/emulator/cores/"
  cp package/reports/*.json "$ROOT/emulator/cores/reports/"
done
"$ROOT/tools/build-emulatorjs-bundle.sh"
echo "EmulatorJS $VERSION vendored into $ROOT/emulator"
