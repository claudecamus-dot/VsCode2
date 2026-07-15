"""Lint slide copy for the quality issues that make decks read as amateurish.

Operates on plain data — a list of ``{"title": str, "bullets": [str, ...]}`` —
so it works whatever built the deck (python-pptx, Markdown, etc.). Each finding
is ``(slide_index, code, message)``. Rules are tuned for French REX/restitution
decks but the codes are generic.

Codes
-----
LONG_BULLET   a bullet is too long to read on one/two lines
TOO_MANY      more than MAX_BULLETS bullets on a slide
FILLER        empty connective / hedging phrase that adds no information
MIXED_PUNCT   bullets mix "ends with period" and "no period" on the same slide
ABBREV        an unexplained cryptic uppercase abbreviation (expand on first use)
WEAK_TITLE    the title is a bare label, not a claim the audience can take away
DUP_LEAD      several bullets start with the same word (no parallel variety)
"""
import re

MAX_BULLET_CHARS = 200
MAX_BULLETS = 6

FILLER = [
    "afin de", "dans le but de", "il est important de", "il convient de",
    "force est de constater", "un certain nombre de", "au niveau de",
    "en termes de", "de manière à", "il faut noter que", "à noter que",
    "comme on peut le voir", "bien évidemment", "tout simplement",
]

# Abbreviations that are common/self-explanatory enough to leave as-is.
ABBREV_OK = {
    "PPT", "OCTO", "SVG", "PNG", "PDF", "API", "US", "DEV", "IA", "AI", "REX",
    "BMAD", "MCP", "HTML", "CSS", "JSON", "DB", "COM", "CLI", "UI", "UX", "RH",
    "CACL", "URL", "HTTP", "CSV", "SQL", "MVP", "KPI", "OK",
}
_ABBREV_RE = re.compile(r"\b[A-ZÀ-Ý]{2,}(?:\.[A-ZÀ-Ý]{2,})*\b")
_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÿ'’]+")


def _ends_with_period(s):
    return s.rstrip().endswith((".", " :", ":"))


def _has_verb_ish(title):
    # crude: a claim usually has a conjugated verb or ':' introducing one.
    t = title.lower()
    if ":" in t or "?" in t:
        return True
    verbs = (" est ", " sont ", " a ", " ont ", " fait ", " tient ", " produit ",
             " permet ", " reste ", " passe ", " marche ", " dépend ", " doit ",
             " fabriqué", " corrig", " génér", " retenir", " change")
    return any(v in f" {t} " for v in verbs)


def lint_slide(title, bullets, idx=0):
    out = []
    title = (title or "").strip()
    bullets = [b.strip() for b in (bullets or []) if b and b.strip()]

    if title and len(_WORD_RE.findall(title)) <= 3 and not _has_verb_ish(title):
        out.append((idx, "WEAK_TITLE",
                    f"titre « {title} » : libellé plutôt qu'une idée à retenir"))

    if len(bullets) > MAX_BULLETS:
        out.append((idx, "TOO_MANY", f"{len(bullets)} puces (> {MAX_BULLETS}) : scinder la slide"))

    ends = [_ends_with_period(b) for b in bullets]
    if len(set(ends)) > 1:
        out.append((idx, "MIXED_PUNCT", "ponctuation finale des puces incohérente"))

    leads = [(_WORD_RE.findall(b) or [""])[0].lower() for b in bullets]
    for w in set(leads):
        if w and len(w) > 3 and leads.count(w) >= 3:
            out.append((idx, "DUP_LEAD", f"{leads.count(w)} puces commencent par « {w} »"))

    seen_abbr = set()
    for b in bullets:
        if len(b) > MAX_BULLET_CHARS:
            out.append((idx, "LONG_BULLET", f"puce de {len(b)} caractères (> {MAX_BULLET_CHARS})"))
        low = b.lower()
        for f in FILLER:
            if f in low:
                out.append((idx, "FILLER", f"tournure creuse « {f} »"))
        for m in _ABBREV_RE.findall(b):
            tok = m.replace(".", "")
            if tok in ABBREV_OK or tok in seen_abbr:
                continue
            # expanded on first use if a '(' follows nearby → treat as explained
            after = b[b.find(m) + len(m): b.find(m) + len(m) + 2]
            if after.strip().startswith("("):
                seen_abbr.add(tok)
                continue
            out.append((idx, "ABBREV", f"abréviation « {m} » non explicitée"))
            seen_abbr.add(tok)
    return out


def lint_deck(slides):
    findings = []
    for i, s in enumerate(slides):
        findings += lint_slide(s.get("title"), s.get("bullets"), idx=i)
    return findings


def format_findings(findings):
    if not findings:
        return "OK — aucun problème rédactionnel détecté."
    return "\n".join(f"  slide {i} [{code}] {msg}" for i, code, msg in findings)


if __name__ == "__main__":
    import json, sys
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    f = lint_deck(data)
    print(format_findings(f))
    sys.exit(1 if f else 0)
