---
name: run-dev-server
description: Launch Interview-to-Deck's FastAPI dev server, bootstrap a mission with real content via HTTP (trame/theme/question/interview/answer, or the faster import/analyse shortcut), and screenshot a page with headless Edge to verify a UI change. Use whenever asked to run/start/preview the app, or to verify a template/CSS/JS change actually renders correctly — not just that tests pass.
---

# Run Interview-to-Deck's dev server

This project is a server-rendered FastAPI + Jinja2 + HTMX app (see root
`CLAUDE.md` for architecture). There is no frontend build step. This skill
is the verified path for getting it running and actually looking at a page
— don't rediscover the bootstrap/screenshot steps from scratch each time.

## 1. Launch

```bash
cd <repo root>
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8010 --reload > /tmp/server.log 2>&1 &
until curl -sf -o /dev/null http://127.0.0.1:8010/missions; do sleep 1; done
```

**`--reload` n'est pas optionnel** (surtout si l'utilisateur va s'en servir) : un
uvicorn sans `--reload` **fige le code importé au démarrage** — après tout commit, l'app
(donc l'export/écran que l'utilisateur consomme) reflète l'ANCIEN code. Piège réel
2026-07-22 : ~15 tours de « tu dis OK, je vois KO » car le serveur servait un deck périmé
(l'export montrait l'ancien format quand mon build frais montrait le nouveau). **Si
l'utilisateur dit « l'export/l'app est toujours faux » alors que tes rendus frais ont l'air
bons : exporte depuis l'app en marche et compare à un build frais — s'ils diffèrent, le
serveur est périmé, redémarre-le.** cf. [[feedback-stale-dev-server-root-cause]].

**ET `--reload` ne SUFFIT pas** (2026-07-22, observé 2× le même jour) : le watcher
WatchFiles a raté des modifications réelles — un serveur `--reload` a servi du code
vieux de plusieurs heures d'édits. Après toute modif de code destinée au serveur :
**redémarrer le serveur, puis RE-VÉRIFIER le contenu servi** (exporter/`curl` et
contrôler un marqueur du changement) — la vérification du contenu est le gate, jamais
le flag. Pièges de nettoyage associés (même journée) : (1) deux serveurs peuvent être
liés au MÊME port via `SO_REUSEADDR` (netstat montre 2 listeners, les requêtes vont au
vieux) — vérifier `netstat -ano | grep :PORT` après tout kill ; (2) un process spawné
depuis l'outil Bash survit à `Stop-Process`/`taskkill` PowerShell (split de namespace,
cf. [[feedback-powershell-bash-process-namespace-split]]) — le tuer depuis le shell
d'origine ; en dernier recours, repartir sur un port vierge.

Use a port other than 8000 (e.g. 8010) to avoid colliding with a server the
user may already have running for manual testing. This uses `data/app.db`
(the real dev DB, gitignored) — not the disposable test DB pytest uses — so
**always delete any mission you create for testing** (`POST
/missions/{id}/delete`) before finishing, the same way you'd clean up any
other side effect.

Stop it when done:
```powershell
Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%uvicorn%8010%'" |
  Where-Object { $_.CommandLine -notlike '*Get-CimInstance*' } |
  Select-Object -ExpandProperty ProcessId | ForEach-Object { Stop-Process -Id $_ -Force }
```
(The `-notlike` filter excludes the query command itself, which otherwise
self-matches since its own command line contains the search string.)

## 2. Bootstrap content

The app enforces a real sequence — a mission needs a non-empty trame before
most other screens are reachable (`{% if not themes %}` guards throughout).
Fastest path to a mission with full synthesis + recommendations content for
UI testing (e.g. the PPT export/apercu editor):

```bash
BASE=http://127.0.0.1:8010
COOKIES=/tmp/cj.txt

# 1. Mission
curl -s -c $COOKIES -X POST "$BASE/missions" -d "name=Smoke Test" -o /dev/null
MID=$(curl -s -b $COOKIES "$BASE/missions" | grep -o '/missions/[0-9]*">Smoke Test' | grep -o '[0-9]*')

# 2. One theme + one question (required before anything else works)
curl -s -b $COOKIES -X POST "$BASE/missions/$MID/trame/themes" -d "title=Theme Test" -o /dev/null
TID=$(curl -s -b $COOKIES "$BASE/missions/$MID/trame" | grep -o 'themes/[0-9]*/questions' | head -1 | grep -o '[0-9]*')
curl -s -b $COOKIES -X POST "$BASE/missions/$MID/trame/themes/$TID/questions" -d "label=Question test&qtype=open" -o /dev/null

# 3. One interview with one answered question (unlocks export/synthesis screens)
IVID=$(curl -s -b $COOKIES -X POST "$BASE/missions/$MID/interviews" -d "interviewee=Testeur" -D - -o /dev/null | grep -i location | grep -o '[0-9]*')
QID=$(curl -s -b $COOKIES "$BASE/interviews/$IVID" | grep -o 'answers/[0-9]*' | head -1 | grep -o '[0-9]*')
curl -s -b $COOKIES -X POST "$BASE/interviews/$IVID/answers/$QID" -d "text=Reponse de test." -o /dev/null

# 4. Shortcut to real global-synthesis + recommendations content: import a
#    pre-filled analysis markdown instead of generating/typing it by hand.
#    See _FILLED_ANALYSIS in tests/test_mission_trame_flow.py for the exact
#    expected format (## SYNTHÈSE GLOBALE / ### <catégorie>, ## RECOMMANDATIONS
#    / #### Axe N / ##### Recommandation N.M).
curl -s -b $COOKIES -X POST "$BASE/missions/$MID/import/analyse" -F "file=@analyse.md;type=text/markdown" -o /dev/null
```

Now `$BASE/missions/$MID/synthese/apercu` (and every other synthesis screen)
has real content to render.

**Cleanup**: `curl -s -b $COOKIES -X POST "$BASE/missions/$MID/delete"`.

## 3. Screenshot a page (visual verification)

No Playwright/Selenium in this project's venv. Headless Edge (bundled with
Windows) works, but needs `--no-sandbox --disable-dev-shm-usage` — without
them it fails with `LoadEnclaveImageW failed, error code 577` in this
environment:

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --headless=new --disable-gpu --no-sandbox --disable-dev-shm-usage `
  --window-size=1400,1600 --screenshot="<scratchpad>\page.png" `
  "http://127.0.0.1:8010/missions/<id>/synthese/apercu"
```

Then read the PNG. This only captures the page's initial state — it does
not click or execute interactions.

**Si le screenshot échoue quand même (`error code 577` MALGRÉ les flags, ou Edge
qui hang sur un profil neuf — vu le 2026-07-22)** : ne pas s'acharner. Replier sur
une **vérif de structure servie** — `curl` la page et vérifier que les éléments
attendus + le `/static/app.css` sont bien servis (classes, libellés, gating cookie…) —
et **dire explicitement que le rendu pixel n'a pas pu être vu** (la 1re page démo P5a-1
a été livrée ainsi). Ne jamais prétendre avoir vu un rendu qu'on n'a pas obtenu. For verifying client-side JS behavior
(e.g. the apercu editor's tab-switching/live-preview logic in
`apercu.html`), extract the relevant `<script>` block and exercise it under
Node with a minimal stubbed `document` object (`getElementById`,
`querySelectorAll`, `classList.toggle`, `addEventListener`, `dataset`) rather
than installing a browser-automation dependency for a one-off check.

## 4. HTMX partial-response endpoints

Autosave/partial-update endpoints (anything with `hx-post` in a template)
return an HTML fragment, not a full page — curl them directly to verify
server-side behavior (e.g. a computed hint, a status badge) without needing
a browser at all:

```bash
curl -s -b $COOKIES -X POST "$BASE/recommandations/$RECO_ID/field" \
  --data-urlencode "field=objectif" --data-urlencode "value=..."
```
