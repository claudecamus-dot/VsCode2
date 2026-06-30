#!/usr/bin/env python3
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / '.opencode' / 'agents'
SKILLS_DIR = ROOT / '.opencode' / 'skills'

def extract_frontmatter(md_text):
    m = re.match(r"^---\n(.*?)\n---\n", md_text, re.S)
    return m.group(1) if m else ''

def parse_list_value(fm, key):
    # find key: [a, b, c]
    m = re.search(rf"^{key}:\s*\[(.*?)\]", fm, re.M | re.S)
    if not m:
        return []
    inner = m.group(1)
    items = [i.strip().strip('"\' "') for i in inner.split(',') if i.strip()]
    return items

def skill_exists(skill_name):
    # skill_name like 'developer/dev-standards-universal'
    p1 = SKILLS_DIR / (skill_name + '.md')
    p2 = SKILLS_DIR / skill_name / 'SKILL.md'
    return p1.exists() or p2.exists()

def main():
    if not AGENTS_DIR.exists():
        print('No agents directory found at', AGENTS_DIR)
        sys.exit(2)

    missing = {}
    total_skills = 0
    for md in AGENTS_DIR.rglob('*.md'):
        text = md.read_text(encoding='utf-8')
        fm = extract_frontmatter(text)
        skills = parse_list_value(fm, 'skills') + parse_list_value(fm, 'native_skills')
        skills = [s for s in skills if s]
        if not skills:
            continue
        total_skills += len(skills)
        for s in skills:
            if not skill_exists(s):
                missing.setdefault(str(md.relative_to(AGENTS_DIR)), []).append(s)

    print(f'Validated agents in {AGENTS_DIR}\nTotal skills referenced: {total_skills}')
    if not missing:
        print('All referenced skills are present.')
        return 0

    print('\nMissing skills:')
    for agent, skills in missing.items():
        print(f'- {agent}:')
        for s in skills:
            print(f'    - {s}')
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
