# Publication ponctuelle : comment l'épisode arrive avant 6h EDT

Le `pubDate` d'un épisode est l'heure réelle d'exécution du workflow
(`src/pipeline.py`). « Publier à la bonne heure » revient donc à « démarrer
`briefing.yml` à la bonne heure ».

**GitHub ne garantit pas l'heure de ses crons planifiés** : il les met en file
d'attente et les livre avec 20 min à plusieurs heures de retard. Exemples réels
sur ce dépôt : 09h31, 09h36, puis **19h24** le 27 août 2026 (au lieu de 09h00).

Trois niveaux, du plus ponctuel au plus tolérant :

| Niveau | Déclencheur | Heure | Fiabilité |
|---|---|---|---|
| 1 | Cron du Worker Cloudflare | 08:00 UTC (4h EDT) | à la minute |
| 2 | 4 crons GitHub de secours | 07:28 → 09:08 UTC | 20 min à plusieurs heures de retard |
| 3 | cron-job.org (optionnel) | au choix | à la minute |

Le job `garde` de `briefing.yml` garantit **un seul épisode par jour** : le
premier déclencheur qui démarre produit l'épisode, les suivants font no-op (pas
de double facture Anthropic/OpenAI).

---

## Niveau 1 — Cron du Worker Cloudflare (recommandé)

Déjà codé dans `worker/audio-proxy.js` (`dispatchBriefing`) et
`worker/wrangler.toml` (cron `0 8 * * *`). Il ne manque que le jeton.

### Étape 1 — Créer un token GitHub

1. https://github.com/settings/personal-access-tokens → **Generate new token**
   (fine-grained)
2. Repository access : **Only select repositories** → `meerkly-00/Presto_Podcast`
3. Repository permissions : **Actions = Read and write**
4. Copier le token (visible une seule fois)

### Étape 2 — L'enregistrer comme secret du Worker

```bash
cd worker
npx wrangler secret put GH_DISPATCH_TOKEN   # coller le token
npx wrangler deploy
```

Sans ce secret, le cron se contente d'un log `GH_DISPATCH_TOKEN absent` : rien
ne casse, les crons GitHub prennent le relais.

### Étape 3 — Vérifier

Le lendemain, un run `workflow_dispatch` doit apparaître vers 08:00 UTC dans
https://github.com/meerkly-00/Presto_Podcast/actions

---

## Niveau 2 — Crons GitHub (actif, aucune config)

`briefing.yml` programme quatre tentatives à des minutes hors des pics
(07:28, 07:58, 08:38, 09:08 UTC). La première qui démarre produit l'épisode.
Même avec une heure de retard, la dernière passe encore avant 6h EDT (10:00 UTC).

---

## Niveau 3 — cron-job.org (optionnel, si le Worker ne suffit pas)

1. Compte gratuit sur https://cron-job.org → **Create cronjob**

| Champ | Valeur |
|-------|--------|
| URL | `https://api.github.com/repos/meerkly-00/Presto_Podcast/actions/workflows/briefing.yml/dispatches` |
| Méthode | `POST` |
| Schedule | tous les jours à **08:00 UTC** |

**Headers :**
```
Authorization: Bearer TON_PAT_ICI
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

**Body :** `{"ref":"main"}`

> ⚠️ C'est ce job qui a cessé de tirer le 25 août 2026 (PAT expiré, job
> désactivé après échecs, ou URL pointant encore vers l'ancien nom du dépôt
> `briefing-matinal`). Un PAT classique expire : surveiller sa date.

---

## Forcer une régénération

```
Actions → Presto — briefing matinal → Run workflow → force = 1
```

Sans `force=1`, un run manuel lancé après l'épisode du jour ne fait rien.
