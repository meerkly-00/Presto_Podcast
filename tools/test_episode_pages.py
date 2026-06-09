import json
import re
import textwrap
from datetime import date
from pathlib import Path

import tools.episode_pages as ep


SAMPLE = textwrap.dedent('''\
    ```xml
    <script>
      <intro>Bonjour, voici votre briefing matinal. Les faits, pas le sermon.</intro>

      <chapitre titre="Top international">
        Premier paragraphe du top international.

        Deuxième paragraphe.
      </chapitre>

      <chapitre titre="Actualité nationale">
        L'actualité nationale ici.
      </chapitre>

      <outro>À demain.</outro>
    </script>
    ```
    ''')


def test_parse_script(tmp_path):
    p = tmp_path / "2026-06-08.xml"
    p.write_text(SAMPLE, encoding="utf-8")
    data = ep.parse_script(p)
    assert data["date"] == "2026-06-08"
    assert data["intro"] == "Bonjour, voici votre briefing matinal. Les faits, pas le sermon."
    assert data["outro"] == "À demain."
    assert len(data["chapters"]) == 2
    assert data["chapters"][0]["titre"] == "Top international"
    assert data["chapters"][0]["paragraphs"] == [
        "Premier paragraphe du top international.",
        "Deuxième paragraphe.",
    ]
    assert data["chapters"][1]["titre"] == "Actualité nationale"


def test_french_date():
    assert ep.french_date("2026-06-08") == "8 juin 2026"
    assert ep.french_date("2026-12-01") == "1 décembre 2026"


def test_is_audio_active():
    today = date(2026, 6, 8)
    assert ep.is_audio_active("2026-06-08", today=today) is True
    assert ep.is_audio_active("2026-06-02", today=today) is True   # 6 jours
    # Boundary is inclusive: exactly keep_days (7) days old is still active
    assert ep.is_audio_active("2026-06-01", today=today) is True   # 7 jours — limite inclusive
    assert ep.is_audio_active("2026-05-31", today=today) is False  # 8 jours — hors fenêtre
    assert ep.is_audio_active("2026-05-20", today=today) is False


def test_iso_duration():
    assert ep.iso_duration(330) == "PT5M30S"
    assert ep.iso_duration(3661) == "PT1H1M1S"
    assert ep.iso_duration(60) == "PT1M"
    assert ep.iso_duration(0) == "PT0S"


def test_meta_description():
    assert ep.meta_description("Court résumé.") == "Court résumé."
    long = "mot " * 60
    out = ep.meta_description(long)
    assert len(out) <= 156
    assert out.endswith("…")


FEED_SAMPLE = '''<?xml version="1.0" ?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>Presto</title>
    <item>
      <title>Presto — édition du 8 juin 2026</title>
      <guid isPermaLink="false">https://github.com/x/releases/download/2026-06-08/2026-06-08.mp3</guid>
      <enclosure url="https://github.com/x/releases/download/2026-06-08/2026-06-08.mp3" type="audio/mpeg" length="5714749"/>
      <itunes:duration>330</itunes:duration>
    </item>
  </channel>
</rss>'''


def test_episode_metadata_in_feed(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(FEED_SAMPLE, encoding="utf-8")
    meta = ep.episode_metadata("2026-06-08", feed, today=date(2026, 6, 8))
    assert meta["title"] == "Presto — édition du 8 juin 2026"
    assert meta["audio_url"].endswith("2026-06-08.mp3")
    assert meta["duration_sec"] == 330
    assert meta["audio_active"] is True


def test_episode_metadata_not_in_feed(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text(FEED_SAMPLE, encoding="utf-8")
    meta = ep.episode_metadata("2026-05-20", feed, today=date(2026, 6, 8))
    assert meta["title"] == "Presto — édition du 20 mai 2026"
    assert meta["audio_url"] is None
    assert meta["audio_active"] is False


def _render(date_iso, audio_active):
    content = {
        "date": date_iso,
        "intro": "Résumé de l'épisode.",
        "chapters": [{"titre": "Top international", "paragraphs": ["Para un.", "Para deux."]}],
        "outro": "À demain.",
    }
    meta = {
        "title": f"Presto — édition du {ep.french_date(date_iso)}",
        "audio_url": "https://github.com/x/2026-06-08.mp3" if audio_active else None,
        "duration_sec": 330 if audio_active else None,
        "audio_active": audio_active,
    }
    return ep.render_episode_page(content, meta)


def test_render_active_episode():
    out = _render("2026-06-08", True)
    assert '<html lang="fr-CA">' in out
    assert "<h1" in out and "8 juin 2026" in out
    assert "Top international" in out and "Para un." in out and "Para deux." in out
    assert "<audio" in out and "2026-06-08.mp3" in out
    assert 'rel="canonical" href="https://www.prestopodcast.online/episodes/2026-06-08/"' in out
    # JSON-LD valide contenant PodcastEpisode + BreadcrumbList
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', out, re.DOTALL)
    assert blocks, "aucun bloc JSON-LD"
    types = [json.loads(b)["@type"] for b in blocks]
    assert "PodcastEpisode" in types
    assert "BreadcrumbList" in types


def test_render_episode_genre_in_schema():
    """Le JSON-LD PodcastEpisode doit inclure genre dans partOfSeries."""
    out = _render("2026-06-08", True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', out, re.DOTALL)
    podcast_block = next(b for b in blocks if "PodcastEpisode" in b)
    data = json.loads(podcast_block)
    assert data["partOfSeries"].get("genre") == "News"


def test_render_archived_episode():
    out = _render("2026-05-20", False)
    assert "<audio" not in out
    assert "archivé" in out.lower()


def test_render_listen_links():
    """La section Écouter doit inclure Spotify, Apple Podcasts et YouTube."""
    out = _render("2026-06-08", True)
    assert "Spotify" in out
    assert "Apple Podcasts" in out
    assert "YouTube" in out


def test_build_sitemap(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    ep.build_sitemap(["2026-06-08", "2026-06-07"], site)
    xml = (site / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://www.prestopodcast.online/" in xml
    assert "https://www.prestopodcast.online/feed.xml" in xml
    assert "https://www.prestopodcast.online/episodes/" in xml
    assert "https://www.prestopodcast.online/episodes/2026-06-08/" in xml
    assert "https://www.prestopodcast.online/episodes/2026-06-07/" in xml
    assert xml.count("<url>") == 5  # home + feed + archive index + 2 épisodes


def test_build_archive_index(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    dates = ["2026-06-08", "2026-06-07", "2026-06-06"]
    ep.build_archive_index(dates, site)
    index = site / "episodes" / "index.html"
    assert index.exists(), "episodes/index.html doit exister"
    content = index.read_text(encoding="utf-8")
    assert "Tous les épisodes" in content
    assert "fr-CA" in content
    assert "/episodes/2026-06-08/" in content
    assert "/episodes/2026-06-07/" in content
    assert "/episodes/2026-06-06/" in content
    # Ordre : plus récent en premier
    pos_08 = content.index("2026-06-08")
    pos_06 = content.index("2026-06-06")
    assert pos_08 < pos_06, "Les épisodes doivent être listés du plus récent au plus ancien"


def test_build_all(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "2026-06-08.xml").write_text(SAMPLE, encoding="utf-8")
    (scripts / "2026-06-07.xml").write_text(SAMPLE.replace("06-08", "06-07"), encoding="utf-8")
    feed = tmp_path / "feed.xml"
    feed.write_text(FEED_SAMPLE, encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    n = ep.build_all(scripts, site, feed, today=date(2026, 6, 8))
    assert n == 2
    page = site / "episodes" / "2026-06-08" / "index.html"
    assert page.exists()
    assert "Top international" in page.read_text(encoding="utf-8")
    assert (site / "sitemap.xml").exists()
    assert (site / "episodes" / "index.html").exists()


def test_json_ld_injection_safety():
    """Un audio_url contenant </script> ne doit pas s'échapper du bloc ld+json."""
    malicious_url = "https://example.com/ep.mp3?x=</script><script>alert(1)</script>"
    content = {
        "date": "2026-06-08",
        "intro": "Intro sans danger.",
        "chapters": [{"titre": "Top", "paragraphs": ["Para."]}],
        "outro": "À demain.",
    }
    meta = {
        "title": "Presto — titre </script> dangereux",
        "audio_url": malicious_url,
        "duration_sec": 330,
        "audio_active": True,
    }
    out = ep.render_episode_page(content, meta)
    # The raw </script> from data must NOT appear unescaped inside a ld+json block
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', out, re.DOTALL)
    assert len(blocks) == 2, f"Expected 2 ld+json blocks, got {len(blocks)}"
    for block in blocks:
        assert "</script>" not in block, "Raw </script> found unescaped in a ld+json block"
        # The escaped form should be present instead
        assert "<\\/" in block or "</" not in block


def test_transcript_html_escaping():
    """Les caractères HTML dans les paragraphes doivent être échappés dans le rendu."""
    content = {
        "date": "2026-06-08",
        "intro": "Intro.",
        "chapters": [{"titre": "Top", "paragraphs": ['<b>x</b> & "q"']}],
        "outro": "À demain.",
    }
    meta = {
        "title": "Presto — édition du 8 juin 2026",
        "audio_url": None,
        "duration_sec": None,
        "audio_active": False,
    }
    out = ep.render_episode_page(content, meta)
    assert "&lt;b&gt;" in out, "Les balises <b> doivent être échappées en &lt;b&gt;"
    assert "&amp;" in out, "& doit être échappé en &amp;"
    assert "<b>x</b>" not in out, "Les balises HTML brutes ne doivent pas apparaître dans le transcript"
