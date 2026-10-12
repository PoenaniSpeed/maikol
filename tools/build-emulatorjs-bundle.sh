#!/usr/bin/env bash
# Builds emulator/emulator.min.js and emulator.min.css from the vendored EmulatorJS
# sources (npm only ships unminified files). Loading the bundle keeps EmulatorJS out of
# its debug mode, which EJS_DEBUG_XX would otherwise enable.
#
# Two patches keep the ROM from being copied while it is loaded (matters for 128-512 MB
# DS ROMs on phones, where iOS kills tabs that use too much memory).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../emulator" && pwd)"
OUT="$DIR/emulator.min.js"
# Same order as data/loader.js loads the sources in debug mode.
for f in emulator.js nipplejs.js shaders.js storage.js gamepad.js GameManager.js socket.io.min.js compression.js; do
  cat "$DIR/src/$f"
  printf '\n;\n'
done > "$OUT"
patch() {
  if [ "$(grep -cF "$1" "$OUT")" != 1 ]; then
    echo "Patch target not found exactly once in $OUT; EmulatorJS changed, update this script: $1" >&2
    exit 1
  fi
  python3 - "$OUT" "$1" "$2" <<'PY'
import sys
path, before, after = sys.argv[1:]
src = open(path).read()
open(path, "w").write(src.replace(before, after))
PY
}
# The ROM read from a File is already a Uint8Array; wrapping it again copies all of it.
patch 'this.checkCompression(new Uint8Array(data), this.localization("Decompress Game Data")' \
      'this.checkCompression(data instanceof Uint8Array ? data : new Uint8Array(data), this.localization("Decompress Game Data")'
# Let the in-memory filesystem keep that array instead of copying it once more.
patch 'this.gameManager.FS.writeFile(altName, fileData);' \
      'this.gameManager.FS.writeFile(altName, fileData, { canOwn: true });' 
cp "$DIR/emulator.css" "$DIR/emulator.min.css"
echo "Built $OUT"
