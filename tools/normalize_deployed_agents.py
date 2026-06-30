#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / 'external' / 'openhub_clone' / 'agents'
DEPLOYED = ROOT / '.opencode' / 'agents'

def read(p: Path):
    try:
        return p.read_text(encoding='utf-8')
    except Exception:
        return p.read_text(encoding='latin-1')

def extract_id(text: str, path: Path):
    m = re.search(r"(?m)^\s*id:\s*(\S+)", text)
    if m:
        return m.group(1).strip()
    return path.stem

def normalize_trailing(text: str) -> str:
    # remove trailing blank lines, ensure single trailing newline
    return re.sub(r"\s+\Z", "\n", text)

def main():
    if not CANONICAL.exists():
        print('Canonical agents dir missing:', CANONICAL)
        return 2
    DEPLOYED.mkdir(parents=True, exist_ok=True)

    for src in sorted(CANONICAL.rglob('*.md')):
        text = read(src)
        aid = extract_id(text, src)
        out = DEPLOYED / f"{aid}.md"
        norm = normalize_trailing(text)
        out.write_text(norm, encoding='utf-8')
        print('NORMALIZED', out)

    return 0

if __name__ == '__main__':
    raise SystemExit(main())
