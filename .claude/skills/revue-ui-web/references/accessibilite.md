# Checklist accessibilite — adaptee de ui-craft `references/accessibility.md`

Source : https://raw.githubusercontent.com/educlopez/ui-craft/main/skills/ui-craft/references/accessibility.md
(MIT), lu le 2026-09-08. Contenu resume ci-dessous ; formulation transcrite,
exemples adaptes au canal Jinja2/HTMX/CSS maison de VSCode2 (pas de composants
React, donc pas de reference a un framework de composants).

## Critique (bloque l'usage clavier/lecteur d'ecran)

- Nom accessible sur tout controle interactif : bouton icone seule ->
  `aria-label` ; `<input>` -> `<label for>` associe ; lien -> texte parlant
  (jamais "cliquez ici" seul).
- Tout ce qui est cliquable doit etre atteignable au Tab : preferer `<button>`
  natif a un `<div>`/`<span>` avec `onclick`. **Point d'attention Jinja2** :
  un `{% if %}` qui genere un `<div onclick=...>` a la place d'un `<button>`
  est le piege le plus frequent dans ce genre de template.
- Focus visible (`:focus-visible`) jamais retire sans remplacement visible
  equivalent en contraste.
- Modales : focus piege pendant l'ouverture, restaure a la fermeture, focus
  initial pose a l'interieur.

## Fort impact

- Semantique HTML native preferee aux roles ARIA de substitution.
- Hierarchie de titres respectee (`h1`..`h6`, pas de saut).
- Lien "aller au contenu" en tete de page si la nav est longue.
- `<th>` sur les en-tetes de tableau (verifie deja present sur
  `missions/list.html` : `<thead><tr><th>Mission</th>...`).
- Formulaires : erreur liee au champ via `aria-describedby`, `aria-invalid`
  sur le champ en erreur, focus sur la premiere erreur a la soumission, jamais
  de blocage du copier-coller, taille de police mobile >= 16px (evite le zoom
  iOS involontaire).

## Priorite moyenne

- `aria-live="polite"` sur les toasts et validations inline — **cas HTMX** :
  toute cible `hx-swap` qui remplace un fragment sans rechargement de page en
  a besoin pour rester perceptible a un lecteur d'ecran (autosave, "Regenerer
  (IA)", panneaux de synthese).
- `aria-expanded` + `aria-controls` sur les controles de type accordeon/repli.
- Contraste APCA prefere a WCAG2 ; les etats d'interaction ont plus de
  contraste que l'etat de repos.
- Etat desactive jamais signale par la seule couleur (verifie : `app.css`
  a bien une regle `.btn:disabled` distincte avec `opacity`/`grayscale`, pas
  juste une couleur — bon signal existant a preserver, pas un defaut).
- Animations respectent `prefers-reduced-motion` ; les animations au survol
  sont conditionnees a `@media (hover: hover) and (pointer: fine)`.

## Quantifie

- Cible tactile minimum 44px (extensible par pseudo-element si le visuel est
  plus petit).
- Police mobile des champs de saisie >= 16px.

## Ce que ce document ne remplace pas

Un contraste APCA se calcule sur les couleurs REELLES du rendu (foreground vs
background), pas en lisant le CSS seul quand des variables sont composees
(ex. superposition de calques). La revue doit soit calculer les paires de
couleurs utilisees a partir de `app.css` (`:root` donne les valeurs hex — table
calculable a la main ou via un outil externe non execute ici), soit noter le
controle comme "a verifier au rendu" plutot que trancher sans le calcul.
