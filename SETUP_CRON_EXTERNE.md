# Déclenchement fiable à 6h EDT via cron-job.org

GitHub retarde ses crons de 0 à 4+ heures. Ce setup utilise cron-job.org (gratuit, fiable à la minute) pour déclencher le workflow exactement à 5h50 EDT, avec le cron GitHub comme filet de sécurité.

## Étape 1 — Créer un PAT GitHub

1. Aller sur https://github.com/settings/tokens → **Generate new token (classic)**
2. Cocher le scope **`workflow`**
3. Copier le token généré (visible une seule fois)

## Étape 2 — Créer le job sur cron-job.org

1. Créer un compte gratuit sur https://cron-job.org
2. **Create cronjob** avec ces paramètres :

| Champ | Valeur |
|-------|--------|
| URL | `https://api.github.com/repos/meerkly-00/briefing-matinal/actions/workflows/briefing.yml/dispatches` |
| Méthode | `POST` |
| Schedule | tous les jours à **09:50 UTC** (= 5h50 EDT) |

**Headers à ajouter :**
```
Authorization: Bearer TON_PAT_ICI
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

**Body (Request body) :**
```json
{"ref":"main"}
```

3. Sauvegarder → le job se déclenche chaque matin à 9h50 UTC pile.

## Étape 3 — Vérifier

Le lendemain matin, vérifier dans https://github.com/meerkly-00/briefing-matinal/actions que le workflow a démarré vers 9h50 UTC. Si oui, tout fonctionne.

> Le cron GitHub reste actif comme backup en cas de panne de cron-job.org.

---

**Obsolète depuis le passage au Worker Cloudflare.** Presto ne dépend plus de
cron-job.org : `worker/audio-proxy.js` déclenche `briefing.yml` à 6h heure du
Québec, et le `schedule` de `briefing.yml` sert de filet. Voir `worker/README.md`.
