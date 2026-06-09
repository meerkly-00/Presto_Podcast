"""Génère une page HTML par épisode de Presto (archive permanente + SEO/AEO)."""

import html
import json
import re
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from xml.etree.ElementTree import parse as parse_xml

BASE_URL = "https://www.prestopodcast.online"
SERIES_NAME = "Presto"
ARTWORK_URL = "https://www.prestopodcast.online/brand/og-image-1200x630.png"
GENRE = "News"
SPOTIFY_URL = "https://open.spotify.com/show/033pvJapq7VhGPvCyn5Bhy"
APPLE_URL = "https://podcasts.apple.com/us/podcast/presto/id1896847376"
YOUTUBE_URL = "https://www.youtube.com/@PrestoPodcast"
X_URL = "https://x.com/PrestoPodcast"
KEEP_DAYS = 7
MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def parse_script(path):
    """Parse un script .xml en dict {date, intro, chapters:[{titre,paragraphs}], outro}."""
    raw = Path(path).read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", raw, re.DOTALL)
    if not m:
        raise ValueError(f"Aucun bloc <script> trouvé dans {path}")
    body = m.group(1)

    def _tag(name):
        t = re.search(rf"<{name}>(.*?)</{name}>", body, re.DOTALL)
        return html.unescape(t.group(1).strip()) if t else ""

    chapters = []
    for cm in re.finditer(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', body, re.DOTALL):
        text = cm.group(2).strip()
        paragraphs = [html.unescape(p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]
        chapters.append({"titre": html.unescape(cm.group(1).strip()), "paragraphs": paragraphs})

    return {
        "date": Path(path).stem,
        "intro": _tag("intro"),
        "chapters": chapters,
        "outro": _tag("outro"),
    }


def french_date(iso):
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return f"{d.day} {MOIS_FR[d.month]} {d.year}"


def is_audio_active(iso, today=None, keep_days=KEEP_DAYS):
    today = today or date_cls.today()
    ep_date = datetime.strptime(iso, "%Y-%m-%d").date()
    # Inclusive: an episode exactly keep_days old is still "en ligne 7 jours"
    return ep_date >= today - timedelta(days=keep_days)


def iso_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    out = "PT"
    if h:
        out += f"{h}H"
    if m:
        out += f"{m}M"
    if s or out == "PT":
        out += f"{s}S"
    return out


def meta_description(intro, limit=155):
    text = " ".join(intro.split())
    if len(text) <= limit:
        return text
    truncated = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return truncated + "…"


def _json_ld(obj):
    """Sérialise en JSON sûr pour insertion dans <script> (neutralise </script>)."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


_ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def episode_metadata(iso, feed_path, today=None):
    """Métadonnées d'un épisode. Lit feed.xml si l'item y est ; sinon mode archivé."""
    title = f"{SERIES_NAME} — édition du {french_date(iso)}"
    meta = {
        "title": title,
        "audio_url": None,
        "duration_sec": None,
        "audio_active": is_audio_active(iso, today=today),
    }
    feed_path = Path(feed_path)
    if not feed_path.exists():
        return meta
    root = parse_xml(feed_path).getroot()
    for item in root.iter("item"):
        enc = item.find("enclosure")
        url = enc.get("url") if enc is not None else ""
        if iso in url:
            t = item.find("title")
            if t is not None and t.text:
                meta["title"] = t.text
            meta["audio_url"] = url
            dur = item.find(f"{_ITUNES}duration")
            if dur is not None and dur.text and dur.text.isdigit():
                meta["duration_sec"] = int(dur.text)
            meta["audio_active"] = True
            break
    return meta


_CSS = """
:root{--bg:#0A0A0A;--bg-elev:#111111;--gold:#C9973F;--cream:#F0EDE6;
--cream-dim:rgba(240,237,230,0.72);--hairline:rgba(201,151,63,0.28)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--cream);
font-family:'Barlow',system-ui,sans-serif;font-weight:300;line-height:1.65;
-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:32px 20px 80px}
a{color:var(--gold)}
.crumb{font:600 13px/1.4 'Barlow Condensed',sans-serif;text-transform:uppercase;
letter-spacing:.06em;color:var(--cream-dim);margin-bottom:24px}
.crumb a{color:var(--cream-dim);text-decoration:none}
h1{font-family:'Barlow Condensed',sans-serif;font-weight:800;
font-size:clamp(28px,6vw,48px);line-height:1.05;margin:0 0 8px;color:var(--cream)}
.date{font:700 13px 'Barlow Condensed',sans-serif;text-transform:uppercase;
letter-spacing:.08em;color:var(--gold);margin-bottom:24px}
.lede{font-size:1.1rem;font-weight:400;color:var(--cream);
border-left:3px solid var(--gold);padding-left:16px;margin:0 0 28px}
audio{width:100%;margin:0 0 28px}
.archived{background:var(--bg-elev);border:1px solid var(--hairline);border-radius:10px;
padding:16px 18px;margin:0 0 28px;color:var(--cream-dim);font-size:.95rem}
h2{font-family:'Barlow Condensed',sans-serif;font-weight:700;text-transform:uppercase;
letter-spacing:.04em;font-size:1.3rem;margin:36px 0 10px;
padding-top:18px;border-top:1px solid var(--hairline);color:var(--cream)}
p{margin:0 0 16px}
.listen{margin-top:40px;padding-top:24px;border-top:1px solid var(--hairline);
font:700 13px 'Barlow Condensed',sans-serif;text-transform:uppercase;letter-spacing:.06em;
color:var(--cream-dim)}
.listen a{margin-right:18px;color:var(--gold)}
"""


def render_episode_page(content, meta):
    iso = content["date"]
    fdate = french_date(iso)
    url = f"{BASE_URL}/episodes/{iso}/"
    desc = meta_description(content["intro"])
    title_tag = f"{meta['title']} — transcript & résumé"

    if meta["audio_active"] and meta["audio_url"]:
        player = f'<audio controls preload="none" src="{html.escape(meta["audio_url"])}"></audio>'
    else:
        player = ('<div class="archived">Épisode archivé — l\'audio n\'est plus '
                  'disponible (les épisodes restent en ligne 7 jours). '
                  f'<a href="{BASE_URL}/">Écouter l\'édition du jour →</a></div>')

    chapters_html = ""
    for ch in content["chapters"]:
        paras = "\n".join(f"<p>{html.escape(p)}</p>" for p in ch["paragraphs"])
        chapters_html += f'<h2>{html.escape(ch["titre"])}</h2>\n{paras}\n'

    podcast_ld = {
        "@context": "https://schema.org",
        "@type": "PodcastEpisode",
        "name": meta["title"],
        "datePublished": iso,
        "url": url,
        "description": desc,
        "inLanguage": "fr-CA",
        "partOfSeries": {
            "@type": "PodcastSeries",
            "name": SERIES_NAME,
            "url": BASE_URL + "/",
            "genre": GENRE,
        },
    }
    if meta["duration_sec"]:
        podcast_ld["timeRequired"] = iso_duration(meta["duration_sec"])
    if meta["audio_active"] and meta["audio_url"]:
        podcast_ld["associatedMedia"] = {"@type": "AudioObject", "contentUrl": meta["audio_url"]}

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Accueil", "item": BASE_URL + "/"},
            {"@type": "ListItem", "position": 2, "name": "Épisodes", "item": BASE_URL + "/episodes/"},
            {"@type": "ListItem", "position": 3, "name": fdate, "item": url},
        ],
    }
    ld = (f'<script type="application/ld+json">{_json_ld(podcast_ld)}</script>\n'
          f'<script type="application/ld+json">{_json_ld(breadcrumb_ld)}</script>')

    return f"""<!DOCTYPE html>
<html lang="fr-CA"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title_tag)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow">
<meta name="theme-color" content="#0A0A0A">
<meta property="og:type" content="article">
<meta property="og:title" content="{html.escape(meta['title'])}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{ARTWORK_URL}">
<meta property="og:locale" content="fr_CA">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Barlow:wght@300;400;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
{ld}
</head><body>
<div class="wrap">
<nav class="crumb"><a href="{BASE_URL}/">Accueil</a> › <a href="{BASE_URL}/episodes/">Épisodes</a> › {fdate}</nav>
<h1>{html.escape(meta['title'])}</h1>
<div class="date">{fdate}</div>
<p class="lede">{html.escape(content['intro'])}</p>
{player}
{chapters_html}<div class="listen">Écouter : <a href="{SPOTIFY_URL}">Spotify</a><a href="{APPLE_URL}">Apple Podcasts</a><a href="{YOUTUBE_URL}">YouTube</a></div>
</div>
</body></html>
"""


def build_archive_index(dates, site_dir):
    """Génère episodes/index.html listant tous les épisodes (plus récent en premier)."""
    sorted_dates = sorted(set(dates), reverse=True)
    url = f"{BASE_URL}/episodes/"

    items_html = ""
    for d in sorted_dates:
        fdate = french_date(d)
        items_html += f'  <li><a href="/episodes/{d}/">{fdate}</a></li>\n'

    page = f"""<!DOCTYPE html>
<html lang="fr-CA"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tous les épisodes — Presto</title>
<meta name="description" content="Retrouvez tous les épisodes du podcast Presto — briefing matinal d'actualité québécois.">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow">
<meta name="theme-color" content="#0A0A0A">
<meta property="og:type" content="website">
<meta property="og:title" content="Tous les épisodes — Presto">
<meta property="og:description" content="Retrouvez tous les épisodes du podcast Presto.">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{ARTWORK_URL}">
<meta property="og:locale" content="fr_CA">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Barlow:wght@300;400;600&display=swap" rel="stylesheet">
<style>{_CSS}
.ep-list{{list-style:none;padding:0;margin:0}}
.ep-list li{{border-bottom:1px solid var(--hairline);padding:12px 0}}
.ep-list li:last-child{{border-bottom:none}}
.ep-list a{{font:600 1rem 'Barlow',sans-serif;color:var(--gold);text-decoration:none}}
.ep-list a:hover{{text-decoration:underline}}
</style>
</head><body>
<div class="wrap">
<nav class="crumb"><a href="{BASE_URL}/">Accueil</a> › Épisodes</nav>
<h1>Tous les épisodes</h1>
<p style="color:var(--cream-dim);margin-bottom:28px">Chaque matin, un briefing d'actualité — les faits, pas le sermon.</p>
<ul class="ep-list">
{items_html}</ul>
</div>
</body></html>
"""
    out_dir = Path(site_dir) / "episodes"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page, encoding="utf-8")


def build_sitemap(dates, site_dir):
    """Écrit sitemap.xml : home + feed + archive index + une URL par épisode (dates triées desc)."""
    urls = [f"{BASE_URL}/", f"{BASE_URL}/feed.xml", f"{BASE_URL}/episodes/"]
    urls += [f"{BASE_URL}/episodes/{d}/" for d in sorted(set(dates), reverse=True)]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        changefreq = "daily" if u.endswith("/") and "/episodes/" not in u else "monthly"
        lines.append(f"  <url>\n    <loc>{u}</loc>\n    <changefreq>{changefreq}</changefreq>\n  </url>")
    lines.append("</urlset>\n")
    (Path(site_dir) / "sitemap.xml").write_text("\n".join(lines), encoding="utf-8")


def build_all(scripts_dir, site_dir, feed_path, today=None):
    """Génère une page par script + l'index archives + le sitemap. Retourne le nombre de pages."""
    scripts_dir, site_dir = Path(scripts_dir), Path(site_dir)
    dates = []
    for script in sorted(scripts_dir.glob("*.xml")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", script.stem):
            continue
        content = parse_script(script)
        if not content["chapters"]:
            continue
        meta = episode_metadata(content["date"], feed_path, today=today)
        html_out = render_episode_page(content, meta)
        out_dir = site_dir / "episodes" / content["date"]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(html_out, encoding="utf-8")
        dates.append(content["date"])
    build_archive_index(dates, site_dir)
    build_sitemap(dates, site_dir)
    return len(dates)


if __name__ == "__main__":
    repo = Path(__file__).resolve().parent.parent
    scripts_dir = repo / "output" / "scripts"
    site_dir = Path(r"C:\Users\jchal\Downloads\presto_deploy")
    # Prefer the deployed feed.xml; fall back to repo feed.xml
    feed_path = site_dir / "feed.xml"
    if not feed_path.exists():
        feed_path = repo / "feed.xml"
    count = build_all(scripts_dir, site_dir, feed_path)
    print(f"{count} page(s) d'épisode générée(s) dans {site_dir}/episodes/ + sitemap.xml + index")
