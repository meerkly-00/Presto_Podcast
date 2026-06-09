# Spec — Pages par épisode Presto (Phase 2)

**Date** : 2026-06-09
**Portée** : Presto (prestopodcast.online). Port de la Phase 1 Le Buzzer + lien homepage + page archives + automatisation.

## Contexte

Phase 1 (Le Buzzer) est live : `tools/episode_pages.py` génère une page HTML permanente par épisode (résumé + transcript + schema), backfillée et branchée sur le workflow quotidien. Voir le repo LeBuzzer pour le module éprouvé (13 tests, revu spec+qualité, bug d'injection JSON-LD corrigé).

Presto a la MÊME structure de scripts (`output/scripts/*.xml` avec `<intro>`/`<chapitre titre>`/`<outro>`), versionnés dans `C:\Users\jchal\Podcast`. Différences vs Le Buzzer :
- **Service** : Presto = **Cloudflare Pages** (projet « presto », déploy par `wrangler pages deploy <dir> --project-name=presto --branch=main`), PAS un Worker. Source déployée actuelle = `Downloads\presto_deploy` (manuel ; piège : Pages git-connecté au repo Python, builds skippés via `[skip ci]`).
- **Marque** : sombre/or (`--bg #0A0A0A`, `--gold #C9973F`, `--cream #F0EDE6`), fonts Barlow Condensed + Barlow.
- La homepage a un **hero-player JS** qui fetch `feed.xml` et remplit l'épisode du jour.

## Décisions

1. **Lien homepage** : « Lire le verbatim → » dans le hero (posé dynamiquement par le JS feed vers `/episodes/<date-du-jour>/`) **+** une **page archives `/episodes/`** listant tous les épisodes (navigable).
2. **Déploiement** : **tout-auto** — rapatrier le site Presto dans le repo + `briefing.yml` génère et déploie via `wrangler pages deploy` (token Cloudflare en secret GitHub). Règle aussi le piège historique.

## Travail

### A. Module `tools/episode_pages.py` (repo Podcast)
Port du module Le Buzzer, adapté :
- Constantes Presto : `BASE_URL=https://www.prestopodcast.online`, `SERIES_NAME=Presto`, `ARTWORK_URL=…/brand/og-image-1200x630.png`, `GENRE=News`, sameAs = Spotify `033pvJapq7VhGPvCyn5Bhy`, Apple `id1896847376`, YouTube `@PrestoPodcast`, X `@PrestoPodcast`.
- CSS de marque Presto (or/sombre, Barlow).
- MÊME logique : `parse_script`, `french_date`, `is_audio_active`, `iso_duration`, `meta_description`, `episode_metadata`, `render_episode_page` (PodcastEpisode + BreadcrumbList), `build_sitemap`, `build_all`.
- **NOUVEAU** : `build_archive_index(dates, site_dir)` → page `episodes/index.html` listant tous les épisodes (date FR + titre, lien `/episodes/<date>/`), plus récent en premier, marque Presto, `<title>`/meta/canonical propres. `build_all` l'appelle.
- Sitemap inclut aussi `/episodes/` (l'index).
- CLI : génère dans la racine du site fournie.
- Tests pytest (porter les 13 + 1 pour l'archive index).

### B. Lien homepage (`Downloads\presto_deploy\index.html`, puis source repo)
- Ajouter dans le hero un `<a id="hero-verbatim" class="cta-text" href="/episodes/" style="display:none">Lire le verbatim →</a>` (+ un lien discret « toutes les archives » vers `/episodes/`).
- Étendre le JS feed existant (≈ ligne 1371) : extraire la date de l'enclosure (`/(\d{4}-\d{2}-\d{2})/`), poser `hero-verbatim.href = '/episodes/'+date+'/'`, l'afficher.

### C. Déploiement
- **Étape 1 (dé-risque, manuel)** : générer les pages + index dans `Downloads\presto_deploy`, `wrangler pages deploy . --project-name=presto --branch=main`, vérifier live.
- **Étape 2 (auto)** : rapatrier le contenu de `presto_deploy` dans le repo (dossier `site/`), ajouter à `briefing.yml` la génération + `wrangler pages deploy site/ --project-name=presto --branch=main` (avec `CLOUDFLARE_API_TOKEN`). **Action utilisateur requise** : créer un token Cloudflare (Pages:Edit) + l'ajouter en secret GitHub `CLOUDFLARE_API_TOKEN` ; déconnecter l'intégration Git du projet Pages dans le dashboard (sinon double-build).

## Vérification
1. Tests pytest verts.
2. Backfill génère une page par script + `episodes/index.html` + sitemap.
3. Page épisode < 50 Ko, schema PodcastEpisode+BreadcrumbList valide.
4. Live : `curl https://www.prestopodcast.online/episodes/2026-06-08/` → 200 + transcript ; `/episodes/` → 200 + liste ; homepage montre « Lire le verbatim ».
5. (Auto) un run manuel de `briefing.yml` (workflow_dispatch) régénère + redéploie sans intervention.

## Hors-portée
Réécriture des tweets vers les pages (item suivant), page FAQ, allègement homepage Le Buzzer.
