#!/usr/bin/env bash
# Builds emulator/emulator.min.js and emulator.min.css from the vendored EmulatorJS
# sources (npm only ships unminified files). Loading the bundle keeps EmulatorJS out of
# its debug mode, which EJS_DEBUG_XX would otherwise enable.
#
# Patch applied: the ROM is handed to the in-memory filesystem with canOwn, so it is
# not copied a second time (matters for 128-512 MB DS ROMs on phones).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../emulator" && pwd)"
OUT="$DIR/emulator.min.js"
# Same order as data/loader.js loads the sources in debug mode.
for f in emulator.js nipplejs.js shaders.js storage.js gamepad.js GameManager.js socket.io.min.js compression.js; do
  cat "$DIR/src/$f"
  printf '\n;\n'
done > "$OUT"
before='this.gameManager.FS.writeFile(altName, fileData);'
after='this.gameManager.FS.writeFile(altName, fileData, { canOwn: true });'
if [ "$(grep -cF "$before" "$OUT")" != 1 ]; then
  echo "canOwn patch target not found exactly once in $OUT; EmulatorJS changed, update this script" >&2
  exit 1
fi
python3 - "$OUT" "$before" "$after" <<'PY'
import sys
path, before, after = sys.argv[1:]
src = open(path).read()
open(path, "w").write(src.replace(before, after))
PY
cp "$DIR/emulator.css" "$DIR/emulator.min.css"
echo "Built $OUT"
