#!/usr/bin/env python3
from pathlib import Path
import re
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / 'external' / 'openhub_clone' / 'agents'
SKILLS_DIR = ROOT / 'external' / 'openhub_clone' / 'skills'

if not AGENTS_DIR.exists():
    raise SystemExit(f'Agents directory not found: {AGENTS_DIR}')

if not SKILLS_DIR.exists():
    raise SystemExit(f'Skills directory not found: {SKILLS_DIR}')


def parse_frontmatter(text):
    if not text.startswith('---'):
        return {}
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}
    fm = parts[1]
    data = {}
    for line in fm.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()
        data[key] = value
    return data


def parse_list(value):
    if not value:
        return []
    value = value.strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    items = []
    current = ''
    in_quote = False
    quote_char = ''
    for ch in value:
        if ch in "'\"" and not in_quote:
            in_quote = True
            quote_char = ch
            continue
        if ch == quote_char and in_quote:
            in_quote = False
            continue
        if ch == ',' and not in_quote:
            item = current.strip().strip('"\'' )
            if item:
                items.append(item)
            current = ''
            continue
        current += ch
    if current.strip():
        item = current.strip().strip('"\'' )
        if item:
            items.append(item)
    return [i for i in items if i]


agents = []
for md in sorted(AGENTS_DIR.rglob('*.md')):
    text = md.read_text(encoding='utf-8')
    fm = parse_frontmatter(text)
    agent_id = fm.get('id', md.stem)
    label = fm.get('label', '')
    desc = fm.get('description', '')
    skills = parse_list(fm.get('skills', ''))
    native_skills = parse_list(fm.get('native_skills', ''))
    agents.append({
        'id': agent_id,
        'label': label,
        'desc': desc,
        'skills': skills,
        'native': native_skills,
        'path': md.relative_to(ROOT)
    })

skill_counter = Counter()
skill_sources = defaultdict(list)
for agent in agents:
    for skill in agent['skills'] + agent['native']:
        skill_counter[skill] += 1
        skill_sources[skill].append(agent['id'])

unique_skills = sorted(skill_counter)

print('OpenHub Gains Evaluation')
print('========================')
print(f'Agents importés : {len(agents)}')
print(f'Skills référencées (total) : {sum(skill_counter.values())}')
print(f'Skills uniques : {len(unique_skills)}')
print('')
print('Agent portfolio:')
for agent in agents:
    print(f'- {agent["id"]} ({agent["label"]})')
    if agent['skills'] or agent['native']:
        print(f'  skills: {len(agent["skills"])} native: {len(agent["native"])}')
    if agent['desc']:
        print(f'  desc: {agent["desc"][:120]}')
print('')
print('Top 20 most referenced skills:')
for skill, count in skill_counter.most_common(20):
    print(f'- {skill}: {count}')
print('')
print('Skill categories summary:')
categories = defaultdict(int)
for skill in unique_skills:
    cat = skill.split('/', 1)[0] if '/' in skill else skill
    categories[cat] += 1
for cat, count in sorted(categories.items(), key=lambda x: (-x[1], x[0])):
    print(f'- {cat}: {count}')
print('')
print('Total skill categories:', len(categories))

print('\nSkills not present in skills dir:')
missing_skills = []
for skill in unique_skills:
    if (SKILLS_DIR / f'{skill}.md').exists():
        continue
    if (SKILLS_DIR / skill / 'SKILL.md').exists():
        continue
    missing_skills.append(skill)
if not missing_skills:
    print('- None')
else:
    for skill in missing_skills:
        print(f'- {skill}')
