# Heuristiques Nielsen + 6 lois de design — adapte de ui-craft `references/heuristics.md`

Source : https://raw.githubusercontent.com/educlopez/ui-craft/main/skills/ui-craft/references/heuristics.md
(MIT), lu le 2026-09-08. Methode reprise a l'identique (elle ne depend d'aucun
outillage — c'est un protocole de jugement humain/LLM) ; seul le libelle du
score est renomme pour ne pas laisser croire a un score deterministe.

## Notation des 10 heuristiques de Nielsen (1 a 5 chacune)

1 = bloque la tache · 2 = friction severe, abandon frequent · 3 = fonctionne
mais deroute, hesitation · 4 = polish mineur seulement, aucun impact usager ·
5 = excellent, rien a ameliorer.

Les 10 heuristiques : visibilite du statut systeme, adequation au modele
mental de l'utilisateur, controle et liberte utilisateur, coherence et
standards, prevention des erreurs, reconnaissance plutot que rappel,
flexibilite/efficacite (usage expert), design minimaliste, aide a la
recuperation d'erreur, aide et documentation.

## 6 lois de design (PASS/FAIL, pas une note)

1. **Loi de Fitts** — cibles tactiles dimensionnees, CTA a portee naturelle.
2. **Loi de Hick** — temps de decision croit avec le nombre de choix ; capper
   la navigation a ~7 items par niveau (verifie : `base.html` n'a que 2 items
   de nav — large marge).
3. **Seuil de Doherty** — reponse percue < 400ms, UI optimiste requise.
   Point d'attention HTMX : un `hx-post` sans indicateur de chargement rend
   ce seuil invisible a mesurer sans le voir au rendu — verifier `busy.js`
   (deja present dans le projet) est bien branche sur la cible concernee.
4. **Hierarchie Cleveland-McGill** — precision d'encodage visuel :
   position/longueur > angle > aire > couleur.
5. **Loi de Miller** — memoire de travail ~7 elements ; formulaires en <=5
   sections, assistants en <=5 etapes.
6. **Loi de Tesler** — la complexite est conservee ; l'absorber dans les
   valeurs par defaut/l'automatisation plutot que l'exposer dans chaque
   reglage.

Chaque loi : PASS ou FAIL avec un detail mesurable, jamais une impression.

## Classement des constats

Par impact, pas par ordre d'heuristique :
`bloque-la-conversion` > `ajoute-de-la-friction` > `reduit-la-confiance` >
`polish-mineur`.

## UsabilityScore (judged) — pas un score deterministe

`heuristic_base = ((moyenne(scores) - 1) / 4) * 100` puis -5 par loi en FAIL,
borne [0,100]. Grades A>=90 / B>=80 / C>=70 / D>=60 / F<60, **toujours suffixe
"(judge)"** : la notation vient d'un jugement du modele au moment de la revue,
elle peut varier d'une passe a l'autre — ui-craft porte deja cette nuance pour
ce meme score (son "UICraftScore" deterministe, lui, vient d'un scanner Node
non adopte ici).
