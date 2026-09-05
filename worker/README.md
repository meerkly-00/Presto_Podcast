# Presto Audio Proxy

Worker Cloudflare qui sert les MP3 du podcast via le domaine `prestopodcast.online`
au lieu de l'URL longue github.com/.../releases/...

## URL pattern

- `https://prestopodcast.online/audio/2026-05-28.mp3` → release GitHub `2026-05-28`
- `https://prestopodcast.online/audio/eco-2026-05-28.mp3` → ancienne release Eco

## Déployer

```bash
cd C:\Users\jchal\Podcast\worker
npm install -g wrangler  # si pas déjà installé
wrangler login
wrangler deploy
```

Puis dans le Cloudflare dashboard :
1. `Workers & Pages` → `presto-audio-proxy` → **Triggers**
2. **Add Custom Domain** → ❌ (on veut un Route, pas un Custom Domain)
3. **Routes** → **Add route** → `prestopodcast.online/audio/*` → zone `prestopodcast.online`

Test :
```bash
curl -IL https://prestopodcast.online/audio/2026-05-28.mp3
# Doit retourner 200 (avec content-type: audio/mpeg)
```

## Cron 4h EDT : déclenche le briefing Presto

Le Worker remplace cron-job.org : à `0 8 * * *` (UTC) il appelle
`workflow_dispatch` sur `briefing.yml`. Il lui faut un secret :

1. GitHub → Settings → Developer settings → Fine-grained tokens → repo
   `Presto_Podcast`, permission **Actions: Read and write** (expiration max).
2. `cd worker && wrangler secret put GITHUB_DISPATCH_TOKEN` (coller le token).
3. `wrangler deploy`.

Sans secret, le cron logge « GITHUB_DISPATCH_TOKEN absent » et ne fait rien.
Le job `guard` de briefing.yml évite toute double génération si le filet de
sécurité GitHub (`schedule`) tourne aussi.

## Avantages

- URLs propres au branding (plus de github.com dans le feed)
- Cloudflare cache les MP3 (rapide partout dans le monde)
- Si tu renommes le repo GitHub plus tard, juste changer `REPO` dans le JS
- Si tu migres vers Cloudflare R2 plus tard, juste swap le `proxyTo()` pour pointer R2

## Si tu renommes le repo

Change la constante `REPO` dans `audio-proxy.js` et redéploie.
