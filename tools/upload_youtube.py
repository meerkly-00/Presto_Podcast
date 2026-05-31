#!/usr/bin/env python3
"""
upload_youtube.py — Generate SRT + MP4 and upload to YouTube for the Presto podcast.

Usage:
    python tools/upload_youtube.py <script.xml> <audio.mp3> [--date YYYY-MM-DD] [--artwork path]

Env vars required:
    YOUTUBE_CLIENT_ID
    YOUTUBE_CLIENT_SECRET
    YOUTUBE_REFRESH_TOKEN
"""

import argparse
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# Make tools/ importable as a module directory.
_TOOLS_DIR = Path(__file__).parent.resolve()
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import generate_srt
import generate_video as gen_video_module

# ---------------------------------------------------------------------------
# French locale helpers (no locale module needed)
# ---------------------------------------------------------------------------

_WEEKDAYS_FR = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"
]

_MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"
]


def date_to_french(d: date) -> str:
    """Return a French long-form date string, e.g. 'dimanche 31 mai 2026'."""
    weekday = _WEEKDAYS_FR[d.weekday()]
    month = _MONTHS_FR[d.month]
    return f"{weekday} {d.day} {month} {d.year}"


# ---------------------------------------------------------------------------
# XML parsing (handles markdown fences)
# ---------------------------------------------------------------------------

def _read_xml_root(xml_path: str):
    """Read the script XML, stripping ```xml ... ``` fences if present."""
    raw = Path(xml_path).read_text(encoding="utf-8").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return ET.fromstring(raw)


# ---------------------------------------------------------------------------
# Audio duration
# ---------------------------------------------------------------------------

def get_audio_duration(audio_path: str) -> float:
    """Return duration in seconds using mutagen."""
    from mutagen.mp3 import MP3
    audio = MP3(audio_path)
    return audio.info.length


# ---------------------------------------------------------------------------
# Title / description generation
# ---------------------------------------------------------------------------

def build_title(episode_date: date) -> str:
    return f"Presto — édition du {date_to_french(episode_date)}"


def build_description(xml_path: str) -> str:
    root = _read_xml_root(xml_path)

    # Intro text (first 400 chars)
    intro_el = root.find("intro")
    intro_text = ""
    if intro_el is not None and intro_el.text:
        intro_text = intro_el.text.strip()[:400]

    # Chapter titles
    chapter_lines = []
    for ch in root.findall("chapitre"):
        titre = ch.get("titre", "").strip()
        if titre:
            chapter_lines.append(f"• {titre}")

    chapters_block = "\n".join(chapter_lines)

    footer = (
        "\n\nNouvel épisode chaque matin. "
        "Disponible aussi sur Spotify, Apple Podcasts et prestopodcast.online"
    )

    parts = [intro_text]
    if chapters_block:
        parts.append("\n\n" + chapters_block)
    parts.append(footer)

    return "".join(parts)


# ---------------------------------------------------------------------------
# Artwork resolution
# ---------------------------------------------------------------------------

def _is_real_image(p: Path) -> bool:
    """Real JPEGs start with FF D8; a Git LFS pointer / HTML is text."""
    try:
        with open(p, "rb") as fh:
            return fh.read(2) == b"\xff\xd8"
    except OSError:
        return False


def resolve_artwork(arg_artwork: str | None) -> Path:
    """Pick a valid local JPEG background.

    Priority: --artwork, then youtube-bg.jpg (Presto-branded 16:9), then
    artwork.jpg (square fallback).
    """
    repo_root = Path(__file__).parent.parent
    candidates = []
    if arg_artwork:
        candidates.append(Path(arg_artwork))
    candidates.append(repo_root / "youtube-bg.jpg")
    candidates.append(repo_root / "artwork.jpg")

    for cand in candidates:
        cand = cand.resolve()
        if cand.exists() and _is_real_image(cand):
            return cand

    tried = ", ".join(str(c) for c in candidates)
    print(f"Error: no valid artwork found (tried: {tried})", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# YouTube auth
# ---------------------------------------------------------------------------

def build_youtube_client():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    missing = [
        name for name, val in [
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        ]
        if not val
    ]
    if missing:
        print(f"Error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_video(youtube, video_path: str, title: str, description: str) -> str:
    """Upload the MP4 to YouTube and return the video ID."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [
                "presto", "podcast", "actualité", "québec",
                "francophone", "briefing", "nouvelles",
            ],
            "categoryId": "25",
            "defaultLanguage": "fr",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media,
    )

    print("Uploading to YouTube...", flush=True)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  Upload progress: {int(status.progress() * 100)}%", flush=True)

    return response["id"]


# ---------------------------------------------------------------------------
# Save result
# ---------------------------------------------------------------------------

def save_youtube_record(episode_date: date, video_id: str) -> None:
    out_dir = Path(__file__).parent.parent / "data" / "youtube"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{episode_date.isoformat()}.json"
    record = {
        "date": episode_date.isoformat(),
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "uploaded_at": datetime.utcnow().isoformat() + "Z",
    }
    out_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved record to {out_file}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate SRT + MP4 and upload to YouTube for Presto."
    )
    parser.add_argument("script_xml", help="Path to the episode XML script")
    parser.add_argument("audio_mp3", help="Path to the episode MP3 audio")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Episode date (defaults to today)")
    parser.add_argument("--artwork", metavar="PATH", help="Path to background image")
    return parser.parse_args()


def main():
    args = parse_args()

    script_xml = Path(args.script_xml).resolve()
    audio_mp3 = Path(args.audio_mp3).resolve()

    for path, label in [(script_xml, "script XML"), (audio_mp3, "audio MP3")]:
        if not path.exists():
            print(f"Error: {label} not found: {path}", file=sys.stderr)
            sys.exit(1)

    if args.date:
        try:
            episode_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: invalid date '{args.date}', expected YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        episode_date = date.today()

    artwork = resolve_artwork(args.artwork)
    print(f"Using artwork: {artwork}", flush=True)

    print("Reading audio duration...", flush=True)
    duration = get_audio_duration(str(audio_mp3))
    print(f"  Duration: {duration:.1f}s ({duration/60:.1f} min)", flush=True)

    with tempfile.TemporaryDirectory(prefix="presto_yt_") as tmpdir:
        srt_path = Path(tmpdir) / "subtitles.srt"
        mp4_path = Path(tmpdir) / "episode.mp4"

        print("Generating SRT subtitles...", flush=True)
        blocks = generate_srt.parse_script(str(script_xml))
        if not blocks:
            print("Error: no content found in XML script.", file=sys.stderr)
            sys.exit(1)
        timed_blocks = generate_srt.assign_timings(blocks, duration)
        generate_srt.write_srt(timed_blocks, str(srt_path))
        print(f"  Generated {len(timed_blocks)} subtitle entries", flush=True)

        print("Generating MP4 video...", flush=True)
        gen_video_module.generate_video(
            artwork=artwork, audio=audio_mp3, subtitles=srt_path, output=mp4_path,
        )

        title = build_title(episode_date)
        description = build_description(str(script_xml))
        print(f"Title: {title}", flush=True)

        youtube = build_youtube_client()
        video_id = upload_video(youtube, str(mp4_path), title, description)

    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"\nUploaded successfully!\nYouTube URL: {url}")
    save_youtube_record(episode_date, video_id)


if __name__ == "__main__":
    main()
