# Extraction IA sur entretiens longs (37min → 3h) — constat et options

Contexte : demande explicite du 2026-07-19, suite à un timeout réel sur
`split_03.weba` (37min réelles, confirmé via `av.open().duration`). Besoin
exprimé : la répartition question/réponse doit fonctionner pour des
entretiens de **3h**.

## 1. Ce qui a été corrigé le jour même (voir CLAUDE.md)

Le défaut `OLLAMA_CHUNK_MAX_WORDS` (1800 mots) produisait des tronçons dont
la génération seule — modèle **chaud**, mesuré en réel — prenait ~570s,
quasiment le double d'`OLLAMA_TIMEOUT` (300s). Ce n'était donc pas (ou pas
seulement) un problème de démarrage à froid : la génération elle-même est le
coût dominant sur ce type de matériel (CPU, pas de GPU dédié). Nouveau
défaut : 400 mots (~150s mesuré à chaud, marge confortable). Une relance
ciblée (une seule) a aussi été ajoutée sur un timeout Ollama isolé.

**Effet** : un entretien de 37min ne devrait plus *échouer*. Il devient
lent, pas cassé.

## 2. Ce que ce correctif NE résout PAS — le mur structurel

Mesures réelles (`ai_common._call_ollama`, modèle chaud, llama3.1:8b CPU) :

| Mots/tronçon | Temps mesuré |
| --- | --- |
| 400 | 149.5s |
| 600 | 207.3s |
| 1800 | 572.7s |

Modèle linéaire ajusté : `temps ≈ 32.7 + 0.291 × mots`. Le tronçonnage
réduit le risque *par appel*, mais ne change pas le **temps total** — celui-ci
dépend du volume de mots à traiter, pas de la taille des tronçons.
Extrapolation à `OLLAMA_CHUNK_MAX_WORDS=400` (~135 mots/minute de parole) :

| Durée entretien | Mots (approx.) | Tronçons | Temps étape 1 seule (transcript→tours) |
| --- | --- | --- | --- |
| 37min (constat réel) | ~5 000 | 13 | **~32 min** |
| 1h | ~8 100 | 21 | ~52 min |
| 2h | ~16 200 | 41 | ~102 min |
| **3h** | **~24 300** | **61** | **~152 min** |

Et ce n'est que l'étape 1 (transcript → tours). L'étape 2 (tours →
répartition, `_CHUNK_MAX_TURNS=40` + un appel de fusion) ajoute un temps
comparable pour un entretien aussi long (beaucoup plus de tours à
regrouper).

**Conclusion sans détour** : pour 3h, l'opération réussirait désormais
(plus d'erreur de timeout par tronçon) mais **occuperait une seule requête
HTTP synchrone bloquée pendant 2h30 à 3h+**. Ce n'est pas praticable :
l'onglet navigateur doit rester ouvert tout ce temps, le moindre incident
réseau perd toute la progression, aucune visibilité sur l'avancement. Ce
n'est pas un problème de réglage — c'est une limite de l'architecture
synchrone actuelle face à de l'inférence locale CPU sur un tel volume.

## 3. Options techniques

### A. Matériel/modèle plus rapide
GPU dédié, ou modèle plus petit (`qwen2.5:1.5b-instruct`, ~4x plus rapide
par les repères déjà notés dans CLAUDE.md, qualité d'extraction non
validée). Réduit le temps par mot mais ne change pas la nature du problème
(toujours une requête synchrone géante) — à combiner avec B, pas un
substitut.

### B. Traitement asynchrone en arrière-plan (recommandé)
Découpler soumission et résultat : l'utilisateur soumet → le serveur
répond immédiatement (« traitement en cours ») → un job en arrière-plan
traite les tronçons séquentiellement en écrivant sa progression en base →
un écran de statut interroge périodiquement (HTMX polling, quelques
secondes) et affiche « X/Y tronçons traités » → à la fin, redirection vers
l'écran de revue habituel. C'est le pattern standard pour de l'inférence
locale longue.

Points de conception à trancher avant de coder :
- Modèle de données : nouveau statut sur `Interview` (pending/running/
  done/failed + compteur de progression), ou table dédiée
  `ExtractionJob` si plusieurs jobs doivent coexister par entretien
  (transcription puis répartition, potentiellement en parallèle sur
  plusieurs entretiens).
- Exécution : `BackgroundTasks` de FastAPI (simple, suffisant ici — les
  appels Ollama sont de l'attente réseau, pas du calcul CPU Python, donc
  ne bloquent pas le event loop de façon aussi grave que du vrai calcul).
- **Reprise sur échec** : sauvegarder les tronçons déjà traités au fur et
  à mesure plutôt que tout perdre si un tronçon échoue après 61 appels
  réussis — cohérent avec la philosophie « relance ciblée » déjà en place
  ailleurs dans le projet.
- Écran de statut : polling HTMX toutes les 5-10s sur une route dédiée,
  pas de WebSocket nécessaire pour ce besoin.

Effort : fonctionnalité substantielle (nouveau modèle de données,
exécution en arrière-plan, écran de statut, reprise sur échec) — mérite un
incrément dédié, pas un correctif ponctuel.

### C. Mitigation immédiate sans code (disponible dès aujourd'hui)
Rien n'empêche de saisir un entretien de 3h comme **plusieurs entretiens
libres successifs** plutôt qu'un seul (ex. par pause naturelle de
l'entretien) — chacun de 45-60min reste dans un temps de traitement
raisonnable (~50min max pour l'étape 1) avec l'architecture actuelle. La
synthèse globale de mission agrège de toute façon tous les entretiens
libres d'une mission, donc le résultat final (répartition dans les 5
catégories) n'est pas affecté par ce découpage — seul le résumé par
« entretien » est fragmenté. Coût : zéro développement, change juste
l'usage. À proposer comme solution de contournement en attendant B si le
besoin de 3h est urgent.

### D. Pipeline hétérogène (non recommandé en l'état)
Modèle plus petit/rapide pour le « map », modèle plus gros seulement pour
le « reduce » final. Complexité ajoutée, gain net incertain, ne résout pas
le passage à l'échelle linéaire — écarté sauf signal contraire.

## 4. Recommandation

1. Correctif du jour (chunk size + relance) suffit pour ~37-45min
   d'entretien — accepté comme livré.
2. Pour 3h : **construire B (traitement asynchrone)** — c'est la seule
   option qui passe réellement à l'échelle et donne de la visibilité/
   résilience. À planifier comme un incrément dédié.
3. En attendant B : **C (découper en plusieurs entretiens libres)** comme
   solution de contournement immédiate, sans code, si le besoin de 3h est
   urgent.
4. A (modèle plus rapide) en complément de B une fois B en place, pas
   avant — sinon on optimise une architecture qui restera de toute façon
   inadaptée à 3h.
