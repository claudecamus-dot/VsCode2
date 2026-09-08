# Les 43 regles anti-slop — transcrites depuis ui-craft, annotees pour VSCode2

Source : `scripts/detect/rules.mjs` de https://github.com/educlopez/ui-craft
(MIT, Eduardo Calvo), lu via raw.githubusercontent.com le 2026-09-08 — LU, pas
execute (le script Node n'est pas installe ici, cf. garde-fou recherche).

Colonne "Ici" : `telle-quelle` (s'applique sans changement a du Jinja2/HTMX +
CSS maison), `adaptee` (le principe tient, l'exemple d'origine suppose un autre
outillage), `contexte` (ne s'applique que si le patron concerne apparait dans le
template revu — pas un defaut a chercher partout).

Aucune des 43 regles ne suppose React ni Tailwind au niveau du principe : ce
sont des regles CSS/HTML/ARIA. C'est confirme en lisant `rules.mjs` (les
messages parlent de proprietes CSS, d'attributs HTML, d'ARIA — jamais de JSX ni
d'utilitaires Tailwind).

| # | id (ui-craft) | severite | regle | Ici |
|---|---|---|---|---|
| 1 | transition-all | critical | lister les proprietes animees (transform, opacity, background-color), jamais `transition: all` | telle-quelle |
| 2 | bounce-elastic-easing | critical | `ease-out` ou `cubic-bezier(0.22,1,0.36,1)`, jamais bounce/elastic | telle-quelle |
| 3 | animate-bounce | critical | pas d'animation de rebond | telle-quelle |
| 4 | purple-cyan-gradient | critical | un seul accent de marque, pas de gradient violet-cyan | telle-quelle |
| 5 | uppercase-heading | critical | sentence case ; majuscules reservees aux labels <=13px avec tracking | telle-quelle |
| 6 | gradient-text-metric | major | couleur pleine sur les chiffres/metriques | telle-quelle |
| 7 | emoji-feature-icon | major | vraie icone SVG (Lucide/Phosphor/Heroicons), pas d'emoji | telle-quelle |
| 8 | pure-black-text | major | pas de `#000` pur pour le texte, prefer un neutre fonce | telle-quelle |
| 9 | generic-cta | major | CTA specifique, jamais "En savoir plus" seul | telle-quelle |
| 10 | left-top-animation | critical | animer `transform`/`opacity`, jamais `left`/`top`/`width` | telle-quelle |
| 11 | absolute-zindex | major | echelle de z-index petite (10/20/30…), pas de 9999+ | telle-quelle |
| 12 | setTimeout-animation | major | transitions CSS ou `requestAnimationFrame`, pas `setTimeout` pour animer | telle-quelle |
| 13 | inline-any-style | warn | extraire en classe plutot qu'un `style=""` inline | telle-quelle — a verifier, Jinja2 genere parfois du style inline conditionnel |
| 14 | aria-label-emoji | major | `aria-label` decrit l'action, pas le glyphe | telle-quelle |
| 15 | no-focus-visible | major | chaque `:hover` a son `:focus-visible` | telle-quelle — `app.css` a verifier regle par regle |
| 16 | pixel-radius-inconsistency | major | une seule source de verite pour les radius (token OU pixels, pas les deux) | telle-quelle — **deja trouve** : `--radius:10px` non utilise par `.btn`/`.mission-name-input` |
| 17 | unit-mixing | warn | une unite par bloc (rem pour layout, px pour bordures) | telle-quelle |
| 18 | dark-pattern/confirmshaming | critical | option de refus neutre, sans culpabilisation | telle-quelle |
| 19 | dark-pattern/destructive-no-confirm | critical | confirmation avant action destructive, nom de l'element dans le libelle | adaptee — VSCode2 utilise `onsubmit="return confirm(...)"` (natif, accessible clavier/lecteur d'ecran) plutot qu'une modale ; le principe (nommer l'element supprime) est a verifier texte par texte |
| 20 | a11y/icon-only-button-no-label | critical | `aria-label` sur bouton icone seule | telle-quelle |
| 21 | dataviz/categorical-rainbow | major | palette nommee et distinguable (viridis, Okabe-Ito, Tableau10) | contexte — pertinent seulement si un ecran affiche un graphique |
| 22 | state/missing-empty-or-error | major | etat vide/erreur explicite sur les composants qui chargent des donnees | telle-quelle — HTMX charge des fragments : verifier l'etat vide de chaque cible `hx-swap` |
| 23 | copy/placeholder-shipped | critical | pas de texte placeholder livre en prod | telle-quelle |
| 24 | a11y/modal-without-dialog | critical | `<dialog>`/`[popover]` natif plutot que div custom pour le piegeage de focus | telle-quelle |
| 25 | forms/placeholder-as-label | critical | un `<label>`/`aria-label` reel, le placeholder n'est pas un label | telle-quelle |
| 26 | a11y/outline-none-no-replacement | critical | jamais `outline:none` sans remplacement visible | telle-quelle — **candidat trouve** : `.mission-name-input:focus` (voir SKILL.md) |
| 27 | tables/no-overflow-handling | major | overflow horizontal + en-tete sticky sur mobile pour les tableaux longs | telle-quelle — `missions/list.html` a un `<table class="table">` a verifier en mobile |
| 28 | a11y/streaming-no-live-region | critical | contenu qui change sans rechargement -> `aria-live` | telle-quelle — **le plus pertinent ici, HTMX** (voir SKILL.md) |
| 29 | forms/autocomplete-missing | major | `autocomplete` sur email/tel/password | telle-quelle |
| 30 | a11y/heading-order-skip | major | pas de saut de niveau de titre (h1->h3) | telle-quelle |
| 31 | layout/image-height-from-attribute | major | `height:auto` si un seul axe est contraint | telle-quelle |
| 32 | type/crowded-ladder | major | 4 a 6 paliers de taille de police max par ecran | telle-quelle |
| 33 | type/display-not-separated | minor | ecart de 2.7x a 4x entre le display et le palier suivant | telle-quelle |
| 34 | type/bold-display | minor | pas de gras a la taille display | telle-quelle |
| 35 | css/duplicate-declaration | major | pas de propriete declaree deux fois dans un meme bloc | telle-quelle |
| 36 | perf/image-no-dimensions | major | `width`/`height` ou `aspect-ratio` sur `<img>` | telle-quelle |
| 37 | copy/or-divider-caps | major | "ou" en minuscule entre deux options d'auth | contexte — l'app n'a pas d'ecran d'auth identifie |
| 38 | auth/brand-flood-panel | major | pas de panneau de marque plein-bleed sur l'auth | contexte — idem |
| 39 | layout/eyebrow-flood | major | un "eyebrow" (label au-dessus d'une section) toutes les 3 sections max | telle-quelle |
| 40 | copy/scroll-cue | major | pas d'indice de scroll decoratif | telle-quelle |
| 41 | copy/section-number-eyebrow | major | pas de "01 · Section" decoratif | telle-quelle |
| 42 | copy/duplicate-cta-intent | major | un seul libelle par intention d'action | telle-quelle |
| 43 | copy/em-dash-flood | major | pas plus d'un ou deux tirets cadratins dans un texte d'UI | telle-quelle |

Regles 37-38 marquees "contexte" : aucun ecran d'authentification identifie a
ce jour dans les 43 templates listes (missions/interviews/synthese/trames) —
a re-verifier si un ecran de login est ajoute.
