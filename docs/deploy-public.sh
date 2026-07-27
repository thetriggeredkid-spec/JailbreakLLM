#!/usr/bin/env bash
# Build a static snapshot of the dashboard and deploy it to Vercel (prod).
# The live dashboard stays local (127.0.0.1:8737); this publishes a mirror.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=docs/public
# clean the artifacts but PRESERVE $OUT/.vercel (the project link)
mkdir -p "$OUT/vendor"
rm -rf "$OUT/vendor" "$OUT/index.html" "$OUT/about.html" "$OUT/state.json" "$OUT/vercel.json"
mkdir -p "$OUT/vendor"

# regenerate index.html + about.html from the latest transcripts
.venv/bin/python docs/render.py >/dev/null
cp docs/index.html "$OUT/index.html"
cp docs/about.html "$OUT/about.html"
cp docs/vendor/three.module.js "$OUT/vendor/three.module.js"
printf '{"cleanUrls": true}\n' > "$OUT/vercel.json"

# snapshot the battlespace state as a static /state.json
.venv/bin/python - <<'PY'
import json, sys
sys.path.insert(0, "docs")
import render
with open("docs/public/state.json", "w", encoding="utf-8") as fh:
    json.dump(render.state_json(None), fh)
PY

(cd "$OUT" && vercel deploy --yes --prod)
