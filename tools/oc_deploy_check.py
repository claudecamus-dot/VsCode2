#!/usr/bin/env python3
import argparse
from pathlib import Path
import re
import sys
import difflib

def read_text(p: Path):
    try:
        return p.read_text(encoding='utf-8')
    except Exception:
        return p.read_text(encoding='latin-1')

def extract_id(text: str, path: Path):
    m = re.search(r"^id:\s*(\S+)", text, re.M)
    if m:
        return m.group(1).strip()
    return path.stem

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--canonical', required=True)
    p.add_argument('--deployed', required=True)
    args = p.parse_args()

    canonical = Path(args.canonical)
    deployed = Path(args.deployed)
    if not canonical.exists():
        print('Canonical agents dir not found:', canonical)
        return 2
    if not deployed.exists():
        print('Deployed agents dir not found:', deployed)
        return 2

    total = 0
    missing = []
    modified = []
    same = []

    for md in sorted(canonical.rglob('*.md')):
        total += 1
        text = read_text(md)
        agent_id = extract_id(text, md)
        gen_file = deployed / f"{agent_id}.md"
        if not gen_file.exists():
            missing.append((agent_id, md, gen_file))
            continue
        # compare normalized lines
        gen_text = read_text(gen_file)
        if text == gen_text:
            same.append((agent_id, md, gen_file))
        else:
            # compute diff
            diff = ''.join(difflib.unified_diff(
                gen_text.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=str(gen_file), tofile=str(md), lineterm=''))
            modified.append((agent_id, md, gen_file, diff))

    print(f'Checked {total} canonical agents')
    print(f'  {len(same)} up-to-date | {len(modified)} modified | {len(missing)} missing')
    if missing:
        print('\nMissing generated files:')
        for aid, src, gen in missing:
            print(f' - {aid}  (source: {src}) -> expected {gen}')

    if modified:
        print('\nModified agents (showing diff, canonical -> deployed):')
        for aid, src, gen, diff in modified:
            print(f'\n--- {aid} ---')
            print(diff)

    return 0

if __name__ == '__main__':
    raise SystemExit(main())
