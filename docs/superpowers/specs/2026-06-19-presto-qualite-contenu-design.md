# Presto — correctifs qualité de contenu (tweet du soir + briefing)

*Spec de design — 2026-06-19*

## Contexte

Trois défauts de contenu signalés sur Presto :

1. **Tweet de fin de journée (17h30) inutile** : il rebrasse l'actualité du Presto du matin au lieu d'apporter de l'information neuve ou une continuation du feed.
2. **Signature « à lundi » le vendredi** : le briefing improvise une signature de fin de semaine alors que Presto publie 7 jours sur 7 (éditions du samedi et dimanche existent).
3. **Filler « pas de sources disponibles au moment de la mise en presse »** : le briefing signale l'absence d'information au lieu de simplement l'omettre.

Les fixes 2 et 3 touchent le même fichier (`prompts/system_presto_v1.md`) ; le fix 1 touche le pipeline de tweets.

## Objectifs

- Le tweet de 17h30 apporte du **net-neuf de la journée** (ce qui a cassé après le briefing matinal), ou **rien** les jours creux.
- Le briefing ne suppose jamais de pause de publication (signature toujours « à demain »).
- Le briefing n'évoque jamais ses propres limites de sourcing ; il omet, ou pour un dossier en cours, clôt vers l'avant (« on suit la situation »).

## Non-objectifs

- Ne pas toucher le tweet de **midi** (continuité éditoriale + sondage = comportement voulu).
- Ne pas réécrire la logique de génération du briefing ni l'agrégation RSS.
- DST : les crons sont en UTC avec commentaires EDT (condition préexistante), hors scope.

---

## Fix 1 — Tweet du soir : récap de la journée, généré tard

### Cause racine

`tools/afternoon_tweets.py` génère **midi ET soir en même temps à 11h EDT** (cron `0 15 * * *` de `afternoon.yml`), mais le worker Cloudflare ne poste le soir qu'à **17h30 EDT**. Au moment de la génération (11h), le mini-refetch remonte 8h → fenêtre ~3h–11h EDT, qui chevauche le briefing matinal. Le tweet ne peut donc pas voir l'actu de 11h–17h et rebrasse les mêmes sources. De plus, `SOIR_PROMPT` ne lui interdit pas de répéter le matin.

Preuve (8 juin) : 2 puces sur 3 du tweet soir = mêmes histoires que le thread matinal.

### Approche retenue

Sortir la génération du soir dans une **étape tardive séparée** (~16h30 EDT), avec refetch couvrant la journée + déduplication contre le matin + skip si rien de neuf. Le pipeline Python (prompts, garde anti-opinion `neutralite.py`, `aggregate`) reste réutilisé. Worker inchangé.

Alternatives écartées :
- **Prompt seul, sans changer le timing** : généré à 11h, il ne peut pas voir l'actu du jour → skipperait presque tous les jours.
- **Génération dans le worker à 17h30** : exigerait de réécrire la garde anti-opinion + l'agrégation RSS en JS. Trop coûteux pour zéro gain.

### Changements

**`tools/afternoon_tweets.py`**
- Nouvel argument `--only {midi,soir,both}` (défaut `both`, pour les runs manuels).
- Fenêtre de refetch par type de tweet :
  - midi → `AFTERNOON_REFETCH_HEURES` (défaut 8).
  - soir → `EVENING_REFETCH_HEURES` (défaut 8). Généré à 20h30 UTC, 8h en arrière couvre ~8h30–16h30 EDT (journée de travail).
  - Le refetch est fait par type généré (le `aggregate(...)` migre dans la boucle du plan, avec la fenêtre du type courant).
- `SOIR_PROMPT` réécrit :
  - Les 3 sujets du matin sont passés comme **liste d'exclusion** (« DÉJÀ COUVERT CE MATIN — NE PAS REPRENDRE »), plus seulement comme contexte.
  - Tâche : sélectionner 1 à 3 faits qui ont **cassé ou évolué depuis le matin**. Une histoire du matin ne requalifie que s'il y a un développement substantiel (nouveau chiffre, décision, issue) ; on cadre alors le développement, pas l'histoire d'origine.
  - Accroche recadrée vers « ce que t'as manqué depuis ce matin » plutôt que « ce qui a bougé aujourd'hui ».
  - **Skip** : si aucun fait net-neuf ne qualifie, le modèle renvoie `{"skip": true}` (ou `texte_tweet` vide).
- `construire_soir` : retourne `None` si `skip` vrai ou texte vide (comportement de skip déjà présent pour texte vide).

**`.github/workflows/afternoon.yml`**
- Commande → `python tools/afternoon_tweets.py --date "$DATE" --only midi`.
- L'étape de commit n'ajoute plus que `data/tweets/$DATE-midi.json` (sinon un vieux fichier soir traînerait et serait posté un jour creux).

**`.github/workflows/evening.yml`** (nouveau)
- Cron `30 20 * * *` (16h30 EDT) + `workflow_dispatch`.
- Garde le même squelette qu'`afternoon.yml` (checkout, setup-python, pip, alerte si échec).
- Génère `--only soir`, commit+push `data/tweets/$DATE-soir.json` **si présent** (skip propre sinon).
- ~1h de marge avant le post de 17h30, absorbe les délais de cron GitHub.

### Comportement jour creux

Aucun fait net-neuf → le modèle skip → `construire_soir` retourne `None` → aucun fichier écrit → le worker fait `fetch` live, reçoit 404, `postSingleFile` skippe (`!data || !data.text`). Zéro tweet, zéro bruit.

### Timing après changement

```
06h00 EDT  briefing + thread matin
11h00 EDT  afternoon.yml → midi seulement
12h00 EDT  worker poste le poll de midi
16h30 EDT  evening.yml → soir (récap du jour, ou skip)   ← NOUVEAU
17h30 EDT  worker poste le soir (fichier frais)
```

Le worker (`worker/audio-proxy.js`) lit le fichier en direct (`fetch` avec `cacheEverything: false`) : un commit à 16h30 est bien visible au post de 17h30. **`wrangler.toml` et `audio-proxy.js` restent inchangés.**

---

## Fix 2 — Signature de l'outro toujours « à demain »

### Cause

Gabarit d'outro (`system_presto_v1.md` ligne 54) : « ...bonne journée, et à demain. » Aucune consigne n'empêche le modèle de varier la signature selon le jour. Presto publie 7/7 (éditions weekend confirmées) → « à lundi » un vendredi est factuellement faux.

### Changement

Ajout d'un bloc **Signature de l'outro** près du gabarit de sortie :

> **Signature de l'outro** : Presto publie 7 jours sur 7. Termine TOUJOURS par « bonne journée, et à demain », peu importe le jour de la semaine. N'écris jamais « à lundi », « bonne fin de semaine », « on se revoit lundi » ni aucune variante supposant une pause : il y a une édition demain, samedi et dimanche compris.

---

## Fix 3 — Plus de filler « pas de sources disponibles »

### Cause

La règle #7 (`system_presto_v1.md` ligne 111) interdit déjà le méta-commentaire sur les sources, mais sa liste de formulations bannies ne couvre pas les tournures réellement employées : « dans les sources disponibles », « au moment de la production de ce briefing », « les détails ne sont pas précisés ». Le modèle contourne la règle.

### Changement (version chirurgicale)

Étendre la liste bannie de la règle #7 et durcir la consigne :

> Ajout à la liste bannie : « dans les sources disponibles », « les sources disponibles ce matin », « au moment de la production de ce briefing », « au moment de la mise en presse », « au moment de la publication » (en parlant de Presto), « les détails ne sont pas précisés / pas disponibles », « restent imprécis ».
>
> **Consigne** : si un détail manque, énonce seulement ce qui est connu et passe à la suite ; ne signale jamais l'absence ni les limites de tes sources. Pour un dossier majeur **encore en cours**, tu peux clore vers l'avant — « on continue de suivre la situation », « Presto y reviendra », « la situation évolue » — sans jamais référer à tes sources ni au moment de production de Presto.

On garde donc le droit de décrire une situation qui évolue (clôture « on suit la situation »), mais jamais en pointant un manque de sources ou la mise en presse.

---

## Vérification

- **Fix 1** : `python tools/afternoon_tweets.py --date <date_récente> --only soir --dry-run` → vérifier que le texte est net-neuf vs le thread matinal et que le skip se déclenche sur une journée mince. Idem `--only midi` pour confirmer la non-régression de midi.
- **Fix 2 / 3** : édits de prompt validés sur la prochaine édition générée ; possibilité d'un test ciblé en régénérant un script sur une date passée.

## Risques

- Cron GitHub Actions parfois retardé : marge de 1h (16h30 → 17h30) la couvre.
- Si `evening.yml` échoue, pas de tweet soir ce jour-là (alerte issue déjà câblée comme les autres workflows). Acceptable.
- Surcoût LLM : un appel Haiku supplémentaire par jour (soir séparé de midi). Négligeable.

## Fichiers touchés

- `tools/afternoon_tweets.py` (flag, fenêtres de refetch, `SOIR_PROMPT`, `construire_soir`)
- `.github/workflows/afternoon.yml` (1 ligne + commit midi seul)
- `.github/workflows/evening.yml` (nouveau)
- `prompts/system_presto_v1.md` (outro + règle #7)
