# Enregistrer un entretien mené en visio (Google Meet, Teams)

*Réflexion de cadrage — 2026-07-30. Fait suite au diagnostic « no-speech » du même jour
(`CLAUDE.md`, section « Régression transcription par segment »), qui a établi la cause
racine mais laissé l'évolution à arbitrer. Aucun code modifié par ce document.*

## 1. Le problème, tel qu'il s'est manifesté

Mission 16, entretien Google Meet d'environ 1 h 40 : **119 segments de transcription
soumis, 29 aboutis, 90 refusés en 422 « aucune parole détectée »** — et les 29 qui
passent sont exactement les moments où l'intervieweur parle. Ce n'était pas une
régression : le navigateur capte `getUserMedia({ audio: true })`, c'est-à-dire le **micro
physique du poste**, qui n'entend que la voix de la personne assise devant lui. La voix
des participants distants sort dans le casque et **n'atteint jamais le périphérique
capturé**. Les réponses de l'interviewée sont définitivement perdues pour cette session
(fuite de casque mesurée, inexploitable).

Autrement dit : **l'outil enregistre aujourd'hui un entretien en présentiel. En visio, il
n'enregistre que la moitié de l'entretien — et c'est la moins intéressante.**

Le correctif livré le 2026-07-30 est une **bannière d'alerte** (2 segments consécutifs
sans parole → « pour un entretien à distance, le micro ne capte pas le son du casque »).
Elle évite de perdre 1 h 40 sans s'en apercevoir. Elle ne résout rien.

## 2. Ce que fait le code aujourd'hui (vérifié, pas supposé)

| Élément | État réel | Où |
| --- | --- | --- |
| Acquisition du flux | `navigator.mediaDevices.getUserMedia({ audio: true })`, **un seul appel**, aucun `deviceId` demandé → périphérique d'entrée par défaut de l'OS | [record_libre.html:1078](../../app/templates/interviews/record_libre.html#L1078), [record.html:957](../../app/templates/interviews/record.html#L957) |
| Consommateurs du flux | Deux `MediaRecorder` sur **le même objet `stream`** : transcription (rotation 60 s) et sauvegarde audio (rotation 30 min) | `startSegment()` / `startBackupRecorder()` |
| Mixage audio | Aucun — pas une seule occurrence d'`AudioContext` dans le projet | grep |
| Choix du périphérique | Aucun sélecteur dans l'UI | grep |
| Décodage serveur | `av.open(...).streams.audio[0]` → PCM 16 kHz mono | [audio_transcribe.py:172](../../app/services/audio_transcribe.py#L172) |
| Import d'un fichier existant | Route `POST /audio/transcribe-file`, **sans aucun filtre de type côté serveur** | [interviews.py:1426](../../app/routers/interviews.py#L1426) |
| Filtre de type côté client | `accept="audio/*"` sur le champ d'import — **rejette un `.mp4`**, sur les deux écrans | [record_libre.html:73](../../app/templates/interviews/record_libre.html#L73), [record.html:65](../../app/templates/interviews/record.html#L65) |

**Point capital pour la suite, mesuré et non supposé** : le décodeur avale déjà un `.mp4`.
Un `.mp4` AAC de 30 s fabriqué à partir d'un échantillon du repo passe dans
`_decode_to_pcm16k` et rend **30,0 s de PCM**. Le format natif d'un enregistrement Meet ou
Teams est donc déjà lisible de bout en bout par le pipeline — **le seul obstacle est
l'attribut `accept="audio/*"` d'un `<input type="file">`.**

Conséquence d'architecture, à garder en tête pour évaluer les options : tout ce qui vient
après l'acquisition (rotation des segments, jobs d'extraction, reprise au bloc échoué,
tranches de sauvegarde) travaille sur un `MediaStream` **quelconque**. Changer la façon
d'obtenir ce flux est un changement **localisé à une fonction** ; rien en aval ne suppose
qu'il vient d'un micro.

## 3. Les options

### Option A — Capture de l'audio de l'onglet (`getDisplayMedia`), mixée au micro

Le navigateur sait capturer le son d'un **autre onglet**. On demande donc deux flux —
le micro (intervieweur) et l'onglet Meet/Teams (personnes distantes) — et on les mélange
en un seul flux via l'API WebAudio, que les deux `MediaRecorder` existants consomment
sans le savoir :

```js
const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
const tab = await navigator.mediaDevices.getDisplayMedia({
  video: true,                 // OBLIGATOIRE : video:false rejette avec TypeError
  audio: { suppressLocalAudioPlayback: false },  // l'intervieweur continue d'entendre
  systemAudio: 'include',      // permet le partage « écran entier » avec son système
});
const ctx = new AudioContext();
const dest = ctx.createMediaStreamDestination();
ctx.createMediaStreamSource(mic).connect(dest);
ctx.createMediaStreamSource(tab).connect(dest);
stream = dest.stream;          // ← seule ligne qui change en aval
```

**Support navigateur** (source : tableau de compatibilité MDN/caniuse, consulté le
2026-07-30) : **Chrome ≥ 74 et Edge ≥ 79 oui ; Firefox non ; Safari non.** Le poste de
travail visé est Windows 11 + Edge, donc c'est couvert — mais la fonctionnalité serait
**inutilisable pour un consultant sous Firefox ou sur Mac/Safari**, et doit donc dégrader
proprement, pas planter.

**Contraintes de l'API, vérifiées dans la spécification :**

- La piste **vidéo est obligatoire** — `video: false` rejette avec `TypeError`. On la
  demande, on ne l'enregistre pas (seul le flux mixé audio part au `MediaRecorder`), mais
  le bandeau « vous partagez votre écran » reste affiché pendant tout l'entretien.
- **Geste utilisateur obligatoire et permission non mémorisable** : le sélecteur de source
  s'ouvre à chaque démarrage d'enregistrement. Impossible de « se souvenir » du choix.
  Deux clics de plus au début de chaque entretien, et une case **« partager aussi l'audio
  de l'onglet »** que l'utilisateur doit penser à cocher — c'est le vrai risque
  d'exploitation : décochée, on retombe silencieusement sur le bug d'aujourd'hui.
- **Teams en application de bureau n'est pas un onglet** : il faut alors partager
  « l'écran entier » avec le son système (supporté par Chrome/Edge sous Windows). Teams
  dans le navigateur (`teams.microsoft.com`), lui, se comporte comme Meet.
- **Écho si l'intervieweur n'a pas de casque** : le son distant sort des haut-parleurs, le
  micro le re-capte, la voix distante est transcrite deux fois. Le casque devient une
  consigne d'usage, pas un confort.

**Ce que ça coûte** : une fonction d'acquisition, un sélecteur de mode d'enregistrement
(présentiel / visio) dans les deux écrans d'enregistrement, et une garde qui **vérifie
que la piste audio partagée existe réellement** (`tab.getAudioTracks().length`) pour
refuser de démarrer plutôt que d'enregistrer un entretien à moitié muet.

**Ce que ça rapporte** : l'entretien complet, en direct, sans dépendre d'un enregistrement
a posteriori ni de l'accord de l'hôte de la réunion.

### Option B — Deux pistes séparées plutôt qu'un mixage (variante de A)

Même acquisition, mais **on n'additionne pas** : on enregistre et on transcrit le micro et
l'onglet **séparément**, puis on entrelace les deux transcriptions par horodatage.

L'intérêt n'est pas cosmétique. Aujourd'hui, l'identification des locuteurs est
**devinée par le modèle** à partir du texte seul — c'est précisément là que le projet a
payé son défaut le plus coûteux (garde anti-hallucination du 2026-07-28 : sur un monologue
sans étiquette, le modèle *inventait* une question de consultant absente du texte). Deux
pistes séparées donnent **l'étiquette de locuteur gratuitement et sans erreur possible** :
ce qui vient du micro est l'intervieweur, ce qui vient de l'onglet est la personne
interrogée. Le tour de table deviendrait structurellement fiable au lieu d'être une
inférence.

**Ce que ça coûte** : le temps de calcul Whisper **double** (deux flux à transcrire) — or
c'est déjà le goulot d'étranglement mesuré du projet, sur un CPU sans GPU. Et il faut une
logique d'entrelacement par horodatage qui n'existe pas.

À ne pas lancer avant A : c'est la même acquisition, avec une consommation différente. A
livre la valeur immédiate (ne plus perdre la moitié de l'entretien) ; B est une évolution
qualitative à évaluer une fois A en service, en la confrontant au vrai coût CPU.

### Option C — Périphérique audio virtuel de l'OS (VB-CABLE, « Stereo Mix »)

Un câble audio virtuel installé sur le poste expose le son de sortie comme un
**périphérique d'entrée**. `getUserMedia` peut alors le capter comme n'importe quel micro,
via un `deviceId`.

**Ce que ça coûte côté code** : presque rien — un sélecteur de périphérique
(`enumerateDevices()`), inexistant aujourd'hui mais trivial.

**Ce que ça coûte côté poste** : l'installation d'un pilote tiers sur chaque machine de
consultant, et une configuration audio Windows que l'utilisateur doit maintenir (le
mixage micro + sortie dans un même périphérique virtuel n'est pas immédiat). C'est
déplacer la complexité du code vers l'exploitation, sur un parc que ce projet ne
provisionne pas — le même argument qui a déjà été opposé à Ollama.

**Verdict** : repli utile à documenter pour un cas particulier (Firefox, Safari, poste où
le partage d'onglet est bloqué par politique), pas une solution par défaut.

### Option D — Importer après coup l'enregistrement de la réunion

Meet et Teams savent enregistrer la réunion et déposer un `.mp4` (Drive / SharePoint).
L'application sait déjà transcrire un fichier importé, bloc par bloc, avec reprise au bloc
échoué.

**Coût réel : un attribut.** Passer `accept="audio/*"` à `accept="audio/*,video/*"` sur le
champ d'import — le serveur ne filtre rien et le décodeur avale le `.mp4` (mesuré, §2).
Vaut aussi la peine de le nommer explicitement dans l'UI (« enregistrement Meet/Teams
(.mp4) »), sans quoi personne ne devinera que c'est possible.

**Limites** : dépend du droit d'enregistrer la réunion (souvent réservé à l'hôte, parfois
désactivé par la politique du client), du délai de mise à disposition du fichier, et **ne
donne rien en direct** — l'onglet Répartition (Q/R) reste vide pendant l'entretien.

C'est le meilleur rapport valeur/effort du lot, et il est **complémentaire** de A, pas
concurrent : A pour le direct, D pour le rattrapage.

### Option E — Récupérer la transcription native de la plateforme

Meet et Teams produisent leurs propres transcriptions (`.vtt`, `.docx`), avec des
étiquettes de locuteur exactes issues des identités de la réunion. Le projet sait déjà
importer un `.docx` d'entretien (mode structuré).

**Ce que ça rapporte** : zéro seconde de Whisper (le goulot du projet disparaît) et une
attribution des locuteurs parfaite.

**Ce que ça coûte** : un parseur par format et par plateforme, et une dépendance à des
sorties dont le format n'est pas sous notre contrôle. La qualité de la transcription
automatique de Teams/Meet en français, sur du vocabulaire métier, **n'a pas été mesurée**
— l'expérience du projet sur Whisper `small` (« Nakache » → « Lacache ») invite à ne rien
présumer avant d'avoir comparé sur un vrai entretien.

Piste à garder ouverte, à ne pas lancer sans cette mesure préalable.

## 4. Recommandation

Un phasage en trois temps, du moins cher au plus structurant :

1. **Palier 0 — Import des enregistrements de visio (option D).** Un attribut, un libellé,
   un test. Débloque immédiatement le cas « la réunion a été enregistrée » sans toucher à
   la chaîne de capture. À faire quoi qu'il arrive.
2. **Palier 1 — Mode « entretien à distance » (option A).** Un sélecteur présentiel/visio
   sur les deux écrans d'enregistrement, acquisition mixée micro + onglet, **garde dure
   sur la présence de la piste audio partagée** (refuser de démarrer plutôt que
   d'enregistrer à moitié), repli explicite et message clair sur Firefox/Safari. C'est le
   palier qui répond au problème signalé.
3. **Palier 2 — Pistes séparées et locuteurs fiables (option B).** À arbitrer *après* le
   palier 1, à la lumière du coût CPU réel mesuré sur un entretien complet.

Les options C et E restent documentées comme replis, sans travail engagé.

## 5. Points à arbitrer par l'utilisateur

- **Le palier 1 vaut-il ses deux clics par entretien ?** Le sélecteur de partage
  d'onglet ne peut pas être mémorisé (contrainte de l'API, pas un choix d'implémentation).
- **Consentement.** Enregistrer des participants distants doit être annoncé et accepté par
  eux ; ce n'est pas une question technique mais elle conditionne le mode opératoire, et
  l'écran d'enregistrement est l'endroit naturel pour le rappeler. À trancher : simple
  mention, ou case à cocher explicite avant de démarrer ?
- **Firefox / macOS.** Y a-t-il des postes concernés ? Si non, le palier 1 se contente
  d'un message ; si oui, l'option C redevient un sujet.
- **Palier 2 maintenant ou plus tard ?** Il supprimerait la principale source d'erreur
  restante de l'extraction (l'attribution des locuteurs devinée), au prix d'un doublement
  du temps de transcription sur un CPU déjà saturé.

## 6. Ce que ce document n'a PAS vérifié

Par honnêteté sur le niveau de preuve, ces points restent à confirmer au moment de
l'implémentation et ne doivent pas être tenus pour acquis :

- Le comportement réel du sélecteur de partage d'Edge sous Windows 11 avec un onglet Meet
  (libellé exact de la case audio, présence du son système en partage d'écran entier) —
  non testé, il faut un navigateur non-headless et une vraie réunion.
- La capture du son système sous macOS, réputée plus restreinte que sous Windows.
- La qualité de transcription native de Meet/Teams en français métier (option E).
- L'ampleur réelle de l'écho sans casque, et si la suppression d'écho du navigateur suffit
  à l'absorber.
