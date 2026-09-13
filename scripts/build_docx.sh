#!/usr/bin/env bash
# Render REPORT.docx with pandoc. Uses REPORT.filled.md if it exists (results
# filled in), otherwise REPORT.md (placeholders shown as PENDING markers).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v pandoc >/dev/null 2>&1; then
  echo "pandoc not found. Install it (macOS: brew install pandoc) and re-run." >&2
  exit 1
fi

SRC=REPORT.md
if [ -f REPORT.filled.md ]; then SRC=REPORT.filled.md; fi

# Turn any unfilled {{RESULT:key}} into a visible marker so the docx never
# ships a raw template token.
TMP="$(mktemp -t c4report.XXXXXX).md"
python3 - "$SRC" "$TMP" <<'EOF'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
text = re.sub(r"\{\{RESULT:([a-zA-Z0-9_]+)\}\}", r"**[PENDING RESULT: \1]**", text)
open(dst, "w", encoding="utf-8").write(text)
EOF

pandoc "$TMP" -f markdown -t docx -o REPORT.docx --toc --toc-depth=2
rm -f "$TMP"
echo "wrote REPORT.docx from $SRC"
