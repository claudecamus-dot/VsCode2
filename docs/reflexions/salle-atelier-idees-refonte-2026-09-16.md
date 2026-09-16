# Salle « Atelier d'idées » — refonte design/UX, 2026-09-16 (2e convocation)

Reconvoquée ~1h après une 1re salle sur "revoir" le design (voir
`salle-atelier-idees-design-ux-2026-09-16.md`), cette fois sur "refondre" —
changement d'ambition confirmé par l'utilisateur. Mêmes 5 voix, 2 tours. Ne
modifie aucun fichier — ce document est le livrable de la salle
(question tranchée + recommandation), pas l'exécution.

## Question à trancher

**Une refonte du design/UX du site est-elle justifiée par ce qu'on sait, ou
le signal disponible tient-il dans des correctifs ciblés — et si oui,
qu'est-ce qui produit enfin un résultat qu'on peut REGARDER plutôt qu'un
3ᵉ document ?**

Cette dernière clause a été ajoutée en cours de délibération : Splinter a
fait remarquer que les deux salles précédentes (juillet + ce matin) avaient
toutes deux débouché sur des documents de process, jamais sur un écran
redessiné — et que "refonte" était peut-être la façon dont vous réclamiez
quelque chose à voir, pas un périmètre plus large.

## Verdict — unanime, 5/5

**Pas de refonte.** L'identité visuelle existe (tokens CSS sémantiques dans
`app/static/app.css:root`, certifiés « point fort » par la revue de
2026-07-23) mais elle est **contournée**, pas absente : 87-96 valeurs
hexadécimales en dur (deux comptages indépendants, Sally et Wildcard), 22-23
tailles de police, 8-9 rayons de bordure — presque tout concentré dans **un
seul fichier** (`app.css`), quasi rien dans les templates (Cadreur : 4
occurrences de couleur en dur sur 44 templates). Ce n'est pas un système
incohérent, c'est un système qui a dérivé d'un demi-cran à chaque ajout,
faute d'être écrit.

**Ce qui a aussi changé pendant la délibération** : Wildcard et Sally ont
d'abord proposé, indépendamment, un « design-system minimal » — puis s'y
sont opposés eux-mêmes au round 2, sous le défi de Splinter (« un
design-system rédigé en principes, sans producteur pour l'appliquer — fait
que Wildcard a établi lui-même — c'est exactement zéro pixel, en pire »).
Convergence finale : **redessiner UN écran réel sur les tokens existants**,
le design-system s'en extrayant après coup comme sous-produit, jamais comme
préalable.

## Recommandation nommée

**Redessiner l'écran d'enregistrement (`record.html` et/ou
`record_libre.html` — désaccord mineur ci-dessous, mais déjà réconcilié avec
la décision de la 1re salle) sur les 5-6 tokens sémantiques déjà présents
dans `app.css`** (pas de nouveaux tokens inventés) : une couleur par
sévérité, une échelle typo resserrée, 1-2 rayons. **Dans le même
commit** : corriger `#rec-reset` (« Recommencer ») avec un `confirm()` —
bug trouvé par la 1re salle, confirmé et localisé trois fois cette
séance (Portevoix : muet sur `capture.html`, `record.html` ET
`record_libre.html` ; Sally : ligne 231 de `record_libre.html`) :
il détruit transcription + sauvegarde audio sans confirmation, alors que
« Démarrer », trois lignes plus haut, en a une.

**Méthode** : `run-dev-server` (capture de l'écran AVANT) → redessiner en
CSS (les templates eux-mêmes sont déjà propres — Sally : 1 seule couleur en
dur sur 2615 lignes de `record_libre.html`, le désordre est dans la feuille
de style, pas le HTML) + le `confirm()` sur `#rec-reset` → re-rendre →
comparer. **Gate** : `design-reviewer` + `a11y-auditor` + `revue-ui-web` sur
le résultat — leur usage normal (vérifier un rendu), pas d'exécution
autonome (**aucun producteur n'exécute une refonte visuelle sur ce
projet — vérifié par Wildcard : les 3 outils sont read-only par
construction**, contrairement au deck PPT qui a 5 producteurs).

**Rien de tout cela ne s'exécute sans votre feu vert** — la salle elle-même
le rappelle (Sally : « cette recommandation est une proposition, pas une
exécution »).

## Écarté explicitement

- **Écrire un design-system comme livrable séparé** — proposé puis retiré en
  confrontation (Wildcard : « je le retire de ma liste » ; Sally : « je
  retire mon mot »). Un document de principes sans écran qui le prouve est,
  de l'aveu des deux voix qui l'avaient porté, le même défaut que les deux
  salles précédentes.
- **Le doublon `entretiens/` vs `interviews/`** (trouvé par Cadreur : 252
  occurrences, 28 fichiers, 52 % des templates + 5 modules Python) — réel,
  mais **dette interne sans symptôme utilisateur observable**, vérifié par
  Splinter : `/entretiens/` n'expose que 2 routes POST qui redirigent
  aussitôt (303) vers `/interviews/`, jamais une page ou une URL atteinte
  par l'usager. Cadreur et Sally s'accordent : **chantier séparé** (un jour
  de ménage sur le nommage), pas un sujet de design, à ne pas mélanger dans
  le même commit (R2 — scope discipline).
- **Refonte de l'architecture de l'information (navigation, 107 routes/2
  liens de nav)** — signal réel (Cadreur), mais aucune voix n'a de preuve
  qu'il gêne l'usager identifié (le consultant qui refait le même parcours
  chaque semaine) ; laissé en observation, pas en chantier.

## Désaccord documenté

**Quel écran redessiner en premier : `record.html` (Wildcard — le plus
gros, porte le bug) ou `record_libre.html` (Sally — déjà exploré en salle
1, le plus propre en HTML, porte aussi le bug à la ligne 231) ?** Non
tranché entre les deux voix. Dans les faits, ça ne bloque rien : la 1re
salle avait déjà scopé les DEUX (plus `record_segment_wait.html`) dans sa
propre recommandation — traiter les deux à la suite plutôt que choisir n'est
pas un surcoût, c'était déjà la décision en attente.

## Reste tracé (déjà dans le fichier de la 1re salle, non dupliqué ici)

Le glossaire « Synthèse transverse »/« Synthèse globale » et les boutons
export dupliqués restent dans `salle-atelier-idees-design-ux-2026-09-16.md`
— aucune des deux salles ne les a rouverts.
