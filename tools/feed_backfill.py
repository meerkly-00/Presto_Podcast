"""
Backfill du feed RSS : réinsère tous les épisodes purgés (les MP3 sont toujours
sur les releases GitHub). Durée estimée depuis la taille (débit ~87 kbps constant).
Usage : py tools/feed_backfill.py
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import parse as parse_xml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.feed import add_episode, _load_or_create, _serialize_feed  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEED = PROJECT_ROOT / "feed.xml"
BITRATE_BPS = 86_900  # observé sur les épisodes récents (length / duration)

os.environ.setdefault("PODCAST_ARTWORK_URL", "https://www.prestopodcast.online/artwork-v2.jpg")

MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def french_date(iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.day} {MOIS_FR[d.month]} {d.year}"


def existing_guids():
    root = parse_xml(FEED).getroot()
    return {g.text for g in root.iter("guid")}


def release_asset_size(date):
    try:
        out = subprocess.run(
            ["gh", "release", "view", date, "--json", "assets"],
            capture_output=True, text=True, check=True, cwd=PROJECT_ROOT)
        assets = json.loads(out.stdout)["assets"]
        for a in assets:
            if a["name"] == f"{date}.mp3":
                return a["size"]
    except subprocess.CalledProcessError:
        return None
    return None


def main():
    guids = existing_guids()
    scripts = sorted((PROJECT_ROOT / "output" / "scripts").glob("*.xml"))
    added = skipped = missing = 0

    for script in scripts:
        date = script.stem
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        url = f"https://github.com/meerkly-00/Presto_Podcast/releases/download/{date}/{date}.mp3"
        if url in guids:
            skipped += 1
            continue
        size = release_asset_size(date)
        if not size:
            print(f"{date} : pas de release/MP3, skip")
            missing += 1
            continue
        duration = round(size * 8 / BITRATE_BPS)
        pub = datetime.strptime(date, "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc)
        add_episode(
            feed_path=FEED,
            title=f"Presto — édition du {french_date(date)}",
            audio_url=url,
            audio_size_bytes=size,
            script_xml=script.read_text(encoding="utf-8"),
            pub_date=pub,
            duration_sec=duration,
        )
        added += 1
        print(f"{date} : ajouté ({size} bytes, ~{duration}s)")

    # Retrie tous les items par pubDate décroissante
    rss = _load_or_create(FEED)
    channel = rss.find("channel")
    items = channel.findall("item")
    def pub_key(it):
        import email.utils
        return email.utils.parsedate_to_datetime(it.find("pubDate").text)
    for it in items:
        channel.remove(it)
    for it in sorted(items, key=pub_key, reverse=True):
        channel.append(it)
    _serialize_feed(rss, FEED)

    print(f"\nTerminé : {added} ajoutés, {skipped} déjà présents, {missing} sans MP3.")


if __name__ == "__main__":
    main()
