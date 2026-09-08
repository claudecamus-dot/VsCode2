---
name: revue-ui-web
description: >
  Revue design/accessibilite scoree des pages HTML servies par Interview-to-Deck
  (FastAPI + Jinja2/HTMX). Produit un rapport (constats + score juge, jamais une
  modification automatique). Use when: avant de livrer un ecran nouveau ou modifie
  sous app/templates/, sur demande "revue design", "audit accessibilite", ou comme
  volet UI de revue-increment quand le changement touche un template .html ou
  app/static/app.css.
---

# revue-ui-web — revue design/accessibilite (VSCode2)

Installee le 2026-09-08 sur arbitrage utilisateur (« lance les actions pour les 2
trouvailles de veille », hub de supervision VScode5 — trouvaille de veille
`educlopez/ui-craft` du 2026-09-07). Redigee par le porteur `bmad-recherche` du hub
(skill `bmad-deep-recon`, type technical), relue par l'orchestrateur avant greffe.
Essai encadre : un premier passage reel sur `missions/list.html` + `app/static/app.css`
avant d'etendre la revue aux 43 templates.

## Attribution (licence MIT)

Contenu adapte de **educlopez/ui-craft** (https://github.com/educlopez/ui-craft,
licence MIT, Eduardo Calvo 2026). MIT n'impose que la conservation de la notice de
copyright et de la licence dans les copies substantielles — cette ligne EST cette
notice pour ce fichier et ses references/. Ce qui est repris : la structure de
methode (heuristiques Nielsen scorees, 6 lois de design, checklist a11y, 43 regles
anti-slop) et le format de rapport (Craft Report). Ce qui N'EST PAS repris : le
serveur MCP (`ui-craft-mcp`, npm), le detecteur `scripts/detect.mjs` (script Node a
executer), les 25 commandes slash et les 24 skills de *build* (`/craft`, `/polish`,
`/animate`, etc.) — ce skill ne couvre QUE la revue, jamais l'ecriture de code.
`ui-craft` est vivant (318 etoiles, dernier push 2026-09-03, verifie via l'API
GitHub le 2026-09-08) mais son adoption ici est un sous-ensemble volontairement
reduit — principe R3 du hub : adapter au canal, ne jamais plaquer.

## Ce que ce skill n'est PAS

- Pas d'installation MCP, pas de `npx`, pas de `node scripts/...` — zero execution
  de code tiers telecharge (garde-fou recherche du hub).
- Pas de score deterministe "UICraftScore" au sens strict de ui-craft (celui-ci
  vient d'un scanner Node execute en CI) : le score produit ici est **juge par le
  modele**, comme le `UsabilityScore (judged)` de ui-craft lui-meme le fait pour
  ses heuristiques — meme etiquette, meme honnetete sur la nature du chiffre.
- Ne modifie JAMAIS un template ni `app.css`. Produit un rapport ; l'application
  d'un correctif est une decision separee, arbitree par l'utilisateur (R4).

## Etat reel du projet au moment de l'ecriture (verifie, pas suppose)

- 43 templates `.html` sous `app/templates/` (compte reel : `Glob`, 2026-09-08).
- Une seule feuille de style : `app/static/app.css` — source de verite CSS unique
  (pas de duplication `<style>` par page, contrairement a VSCode1).
- `app/templates/base.html` : tokens CSS charges via `<link rel="stylesheet"
  href="/static/app.css">`, HTMX charge en LOCAL (`/static/htmx.min.js`, jamais un
  CDN — l'app est offline-first).
- Aucune skill ni agent de revue design/accessibilite existant avant celle-ci :
  `.claude/skills/` a `deck-design-review` (revue du **deck PPTX exporte**, hors-sujet
  ici — contrat slide par slide, pas page web) et pas d'equivalent pour les pages
  servies.
- `app.css` declare un token `--radius: 10px` (`:root`, ligne 10) mais `.btn`
  (ligne 99) et `.mission-name-input` (ligne 84) codent en dur `border-radius: 8px`
  et `6px` — cas reel du finding "token existe, pas utilise partout" (voir
  `references/anti-slop-43.md` regle 16).
- `.mission-name-input:focus` (ligne 87) fait `outline: none` avec `border-color:
  var(--accent)` comme remplacement — a verifier au rendu reel (contraste du
  remplacement, pas garanti suffisant) : candidat pour `references/accessibilite.md`,
  pas un defaut confirme sans mesure APCA reelle.
- `base.html` marque la section active par une classe CSS (`class="active"`) sur
  les liens de nav, sans `aria-current="page"` — candidat de revue, pas un defaut
  bloquant (le contenu textuel du lien reste lisible au lecteur d'ecran).

## Point d'attention specifique HTMX (le plus utile de cette adoption)

HTMX remplace des fragments de DOM sans rechargement de page (`hx-post`,
`hx-swap`, autosave, "Regenerer (IA)"). C'est exactement le cas que
`references/anti-slop-43.md` regle 28 (`a11y/streaming-no-live-region`) couvre :
un contenu qui change sans `aria-live` est invisible a un lecteur d'ecran. Aucun
`aria-live` trouve dans `base.html` ni dans les fragments verifies
(`_saved.html`, `_verbatims.html`, `_global_panel.html`) — a confirmer template
par template a l'usage du skill, pas affirme ici comme defaut generalise (seuls
`base.html` et `missions/list.html` ont ete lus en entier).

## Processus (une revue, jamais une correction automatique)

1. **Cadrer le perimetre** — un template modifie, un ecran, ou l'app entiere.
   Ne jamais faire une revue "app entiere" par defaut (cout).
2. **Voir le rendu reel** — lancer via la skill `run-dev-server` existante du
   projet (port vierge, jamais `--reload` seul) et ouvrir la page concernee.
   Si aucune capture visuelle n'est possible dans la session, le dire
   explicitement dans le rapport ("passe code-seul, non verifie au rendu") —
   ne jamais le taire (adaptation de la regle "halte tant que le visuel n'est
   pas securise" de la commande `/audit` de ui-craft, qui suppose Playwright MCP
   — absent ici, donc pas de blocage dur, mais pas de silence non plus).
3. **Anti-slop** — passer `references/anti-slop-43.md` sur le template et sur
   les regles `app.css` qu'il utilise.
4. **Accessibilite** — passer `references/accessibilite.md` (clavier, APCA,
   ARIA, cibles tactiles, `prefers-reduced-motion`) ; pour HTMX, verifier
   systematiquement le point "streaming sans aria-live" ci-dessus sur toute
   cible `hx-swap`.
5. **Heuristiques** — noter avec `references/heuristiques-nielsen.md` (Nielsen
   10 + 6 lois), produire le `UsabilityScore (judged)`.
6. **Rapport** — format Craft Report (Verifie / Passe / Recommande — jamais
   "Change", puisque rien n'est modifie / Verdict en une phrase). Chaque
   constat cite fichier:ligne. Un constat sans localisation n'est pas retenu
   (meme discipline que `audit-technique/SKILL.md` du hub).

## References (fichiers de ce dossier) et sous-agents

- `references/anti-slop-43.md` — les 43 regles, transcrites depuis
  `scripts/detect/rules.mjs` de ui-craft (lu, pas execute), annotees
  applicable/adaptee pour Jinja2/HTMX.
- `references/accessibilite.md` — checklist clavier/APCA/ARIA, adaptee de
  `skills/ui-craft/references/accessibility.md`.
- `references/heuristiques-nielsen.md` — les 10 heuristiques + 6 lois de
  design scorees, adaptees de `skills/ui-craft/references/heuristics.md`.
- `.claude/agents/design-reviewer.md` et `.claude/agents/a11y-auditor.md` — deux
  sous-agents read-only (Read/Grep/Glob uniquement), copies quasi telles-quelles
  des agents ui-craft du meme nom (deja au format Claude Code natif, aucun MCP),
  adaptes pour pointer vers les references ci-dessus. Ils vivent dans
  `.claude/agents/` (et non dans ce dossier) parce que c'est la seule
  arborescence que l'outil `Agent` charge ; a lancer en parallele sur un meme
  perimetre, leurs angles sont disjoints.
