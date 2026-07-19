# Enregistrement auto-segmenté en tranches de 30min + traitement associé

Suite de `extraction-longue-duree.md` (constat : le synchrone actuel ne
passe pas à l'échelle pour 3h, recommandation d'un traitement asynchrone en
arrière-plan). Demande du 2026-07-19 : étudier une approche concrète bâtie
sur un découpage de l'enregistrement en tranches de 30min max, **sans
interrompre l'enregistrement**, avec traitement par séquence et
téléchargement de chaque tranche.

## 0. Ce qui existe déjà et sur quoi s'appuyer

`record_libre.html`/`record.html` ont **déjà** un mécanisme de rotation en
tâche de fond, à l'identique de ce qui est demandé ici mais à une autre
échelle : `startSegment()` fait tourner un `MediaRecorder` toutes les 60s
(`SEGMENT_MS`), l'event `stop` uploade le segment puis relance
immédiatement un nouveau segment *si l'enregistrement est toujours actif*
(`if (recordingActive) startSegment();`) — le flux micro (`stream`) n'est
jamais interrompu, seul le `MediaRecorder` qui l'enveloppe tourne. C'est
exactement le mécanisme qu'il faut pour la demande « sans arrêter
l'enregistrement de façon automatique » — à appliquer au **second**
`MediaRecorder` déjà présent (`backupRecorder`), qui aujourd'hui n'upload
qu'une seule fois, à l'arrêt final (`uploadBackup()` appelé depuis l'event
`stop` du bouton « Arrêter »).

Autre acquis à réutiliser tel quel : `save_record_backup()`
(`app/routers/interviews.py:728`) génère déjà un nom de fichier unique par
appel (`{mission_id}_{timestamp}.webm`) et écrit sur disque sans toucher la
base — l'appeler plusieurs fois par entretien fonctionne **déjà** sans
changement. `get_record_backup()` sert **déjà** n'importe quel fichier par
nom (garde-fou anti path-traversal en place) — aucun changement de route
nécessaire pour servir plusieurs segments.

## 1. Rotation audio 30min sans interruption (client, JS)

Dupliquer le pattern de `startSegment()`/`rotationTimer` pour
`backupRecorder`, à un intervalle différent :

```js
var BACKUP_SEGMENT_MS = 30 * 60 * 1000;  // 30 minutes — configurable, cf. §5
var backupSegmentPaths = [];             // rempli au fil des uploads

function startBackupSegment() {
  backupChunks = [];
  backupRecorder = new MediaRecorder(stream);
  backupRecorder.addEventListener('dataavailable', function (e) {
    if (e.data && e.data.size > 0) backupChunks.push(e.data);
  });
  backupRecorder.addEventListener('stop', function () {
    var blob = new Blob(backupChunks, { type: backupRecorder.mimeType || 'audio/webm' });
    uploadBackupSegment(blob);            // POST existant, juste renommé/appelé en boucle
    if (recordingActive) startBackupSegment();  // reprise immédiate, même pattern que startSegment()
  });
  backupRecorder.start();
}

var backupRotationTimer;
// dans startBtn.addEventListener('click', ...), à côté de rotationTimer :
backupRotationTimer = setInterval(function () {
  if (backupRecorder && backupRecorder.state === 'recording') backupRecorder.stop();
}, BACKUP_SEGMENT_MS);

// dans stopBtn.addEventListener('click', ...), à côté de clearInterval(rotationTimer) :
clearInterval(backupRotationTimer);
```

`uploadBackupSegment()` = `uploadBackup()` existant, adapté pour
**accumuler** dans `backupSegmentPaths` (champ caché JSON, même mécanique
que `rec-transcript-hidden` déjà utilisée pour le transcript) plutôt que
d'écraser un seul `backupPathHidden.value`.

Effet : le micro n'est jamais coupé, l'utilisateur ne fait rien de plus
qu'aujourd'hui — la segmentation est invisible pour lui, seulement visible
dans les données produites.

## 2. Modèle de données

`Interview.audio_backup_path` (un seul chemin) reste tel quel pour la
compatibilité (entretiens courts à un seul segment, ou anciens entretiens).
Ajout d'un champ JSON — même pattern que `Interview.repartition`, pas de
nouvelle table pour une simple liste de noms de fichiers sans métadonnée
propre à chaque ligne (pas de sur-ingénierie) :

```python
# app/models.py, classe Interview
audio_segments: Mapped[list] = mapped_column(JSON, default=list)
# Liste de {"filename": str, "position": int} — ordonnée, un par tranche de
# 30min. audio_backup_path reste renseigné avec le DERNIER segment pour la
# lecture audio existante (retrocompatible avec le lecteur déjà en place).
```

```python
# app/db.py::_add_missing_columns()
"interviews": {..., "audio_segments": "JSON"},
```

Comme pour `raw_transcript` (2026-07-19, session précédente), la liste de
segments n'existe qu'en JS/formulaire tant que l'entretien n'est pas
confirmé — même fil : champ caché JSON sur les 3 écrans du wizard libre,
lu par `record_libre_confirm()` à l'enregistrement final.

## 3. Téléchargement de tous les segments

Aucune route nouvelle : la boucle Jinja sur `interview.audio_segments`
suffit, avec le lien existant `GET /missions/{id}/interviews/record/backup/{filename}` :

```html
{% if interview.audio_segments %}
<div class="card">
  <h3>Enregistrements (par tranche de 30min)</h3>
  <ul>
    {% for seg in interview.audio_segments %}
    <li><a href="/missions/{{ mission.id }}/interviews/record/backup/{{ seg.filename }}" download>
      Tranche {{ seg.position + 1 }}</a></li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```

Sur `libre_detail.html`/`preview.html`, à côté du lecteur audio déjà en
place. Un lien « tout télécharger » (zip) est une amélioration confort
possible mais non nécessaire au besoin exprimé — à ajouter seulement si
demandé.

## 4. Traitement de la répartition par séquence — lien avec l'asynchrone

C'est ici que ce découpage rejoint directement `extraction-longue-duree.md`
§3 option B (traitement asynchrone recommandé) : **la tranche de 30min
devient la granularité naturelle du job en arrière-plan**, plutôt qu'une
notion abstraite à inventer séparément.

Constat de taille : 30min de parole ≈ 4 000-4 500 mots — largement
au-dessus du budget `OLLAMA_CHUNK_MAX_WORDS` (400, cf. correctif du jour) :
une tranche de 30min n'est donc **pas** elle-même l'unité d'appel IA (elle
serait aussi lente que l'ancien défaut à 1800 mots, en pire) — elle reste
découpée en ~10 sous-tronçons de 400 mots par le map-reduce déjà en place.
La tranche de 30min est l'unité de **job**, pas l'unité d'**appel IA** :

1. Chaque tranche audio, une fois uploadée (§1), a un texte accumulé
   correspondant (le transcript déjà assemblé au fil de l'eau par les
   segments de 60s — capturer sa longueur au moment de la rotation 30min
   pour en faire une « tranche de texte », sans nouveau mécanisme de
   transcription).
2. Dès qu'une tranche de texte est disponible, elle peut être soumise à
   l'extraction (tours) **en tâche de fond**, pendant que l'utilisateur
   continue d'enregistrer la tranche suivante — chevauchement traitement/
   enregistrement plutôt que tout traiter après coup.
3. À l'arrêt final, les tours déjà extraits des tranches précédentes sont
   déjà en base (ou en attente) ; seule la dernière tranche (partielle)
   reste à traiter — le temps d'attente perçu par l'utilisateur après avoir
   cliqué « Arrêter » se limite à la dernière tranche, pas à l'entretien
   entier.
4. La répartition (étape 2) s'exécute une fois tous les tours disponibles
   (toutes tranches traitées) — inchangée sinon (map-reduce déjà en place
   sur les tours).

Modèle de données pour le job en arrière-plan (ébauche, à affiner si ce
chantier est lancé) :

```python
class InterviewSegmentJob(Base):
    __tablename__ = "interview_segment_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Tant que l'entretien n'existe pas encore (avant confirmation), rattaché
    # à un jeton de session temporaire plutôt qu'à interview_id (NULL le temps
    # du wizard, complété à la confirmation — même défi que audio_segments).
    session_token: Mapped[str] = mapped_column(String(64))
    interview_id: Mapped[int | None] = mapped_column(ForeignKey("interviews.id"), default=None)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|done|failed
    turns_result: Mapped[dict | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
```

Exécution : `BackgroundTasks` de FastAPI suffit (les appels Ollama sont de
l'attente réseau, pas du calcul Python bloquant). Écran de statut : simple
polling HTMX (`hx-trigger="every 5s"`) sur une route qui renvoie l'état des
jobs — pas de WebSocket nécessaire.

## 5. Le nombre « 30 minutes » est-il le bon réglage ?

30min ≈ 4 000-4 500 mots ≈ ~27min de temps de traitement (10 sous-tronçons
× ~150s, mesure réelle du jour) — le traitement d'une tranche est donc
sensiblement plus court que sa propre durée d'enregistrement : si le
traitement démarre dès la fin de la tranche 1 pendant que la tranche 2
s'enregistre, il a le temps de finir avant que la tranche 2 soit prête, et
ainsi de suite — **le traitement ne prend jamais de retard sur
l'enregistrement**, exactement la propriété recherchée. Une tranche plus
longue (ex. 45-60min) romprait cet équilibre sur un poste plus lent ; une
tranche plus courte (15min) multiplierait les allers-retours réseau sans
gain net. 30min est un choix raisonnable, à confirmer en usage réel une
fois construit plutôt qu'à sur-optimiser sur papier — prévoir une constante
`BACKUP_SEGMENT_MS` facilement ajustable (déjà le cas dans l'ébauche §1).

## 6. Phasage proposé (si ce chantier est lancé)

- **Palier 1 — segmentation + téléchargement (§1-3)** : risque faible,
  s'appuie entièrement sur des mécanismes déjà éprouvés (rotation JS,
  upload/service de fichiers), valeur immédiate même sans le palier 2
  (résilience : un crash navigateur après 2h ne perd plus que la dernière
  tranche de 30min, pas tout l'entretien). Estimation : quelques heures.
- **Palier 2 — traitement asynchrone par tranche (§4)** : nouveau modèle
  de job, exécution en tâche de fond, écran de statut/polling — le
  chantier substantiel déjà identifié dans `extraction-longue-duree.md`,
  maintenant concrétisé sur la granularité « tranche de 30min » plutôt que
  laissé abstrait. Mérite un incrément dédié.

Le palier 1 seul répond déjà à une partie réelle du besoin (plus de perte
totale sur un entretien long, possibilité de retraiter une tranche
isolément a posteriori même sans automatisation complète) — livrable
indépendamment du palier 2 si le temps manque pour tout construire d'un
coup.
