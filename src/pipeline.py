"""
Orchestrateur principal du briefing matinal.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .aggregate import aggregate
from .generate import generate_script, load_system_prompt, load_recent_context, save_context
from .tts import generate_audio
from .feed import add_episode

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


def _audio_url(filename: str) -> str:
    # AUDIO_BASE_URL est défini par le workflow GitHub Actions
    # ex: https://github.com/user/repo/releases/download/2026-05-24
    audio_base = os.getenv("AUDIO_BASE_URL")
    if audio_base:
        return f"{audio_base.rstrip('/')}/{filename}"
    base = os.getenv("PODCAST_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/audio/{filename}"


def run(
    date: datetime | None = None,
    duree_cible: int | None = None,
    since_hours: int | None = None,
    skip_tts: bool = False,
    skip_feed: bool = False,
    dry_run: bool = False,
) -> dict:
    load_dotenv(PROJECT_ROOT / ".env")

    if date is None:
        date = datetime.now(timezone.utc)

    duree_cible = duree_cible or int(os.getenv("BRIEFING_DUREE_CIBLE", 12))
    since_hours = since_hours or int(os.getenv("BRIEFING_FENETRE_HEURES", 24))

    date_slug = date.strftime("%Y-%m-%d")
    scripts_dir = PROJECT_ROOT / "output" / "scripts"
    audio_dir = PROJECT_ROOT / "output" / "audio"
    data_dir = PROJECT_ROOT / "data"

    # Chemins configurables via env — permet d'exécuter plusieurs profils de podcast
    feed_path   = os.getenv("FEED_FILE",          str(PROJECT_ROOT / "feed.xml"))
    config_path = os.getenv("SOURCES_FILE",       str(PROJECT_ROOT / "config" / "sources.yaml"))
    prompt_path = os.getenv("SYSTEM_PROMPT_FILE", str(PROJECT_ROOT / "prompts" / "system_briefing_v1.md"))
    audio_prefix        = os.getenv("AUDIO_PREFIX", "")
    context_file        = os.getenv("CONTEXT_FILE", "context.json")
    episode_title_pfx   = os.getenv("EPISODE_TITLE_PREFIX", "Briefing du")

    result: dict = {"date": date_slug}

    # 1. Agrégation
    logger.info("=== Étape 1 : Agrégation des articles ===")
    articles_xml = aggregate(config_path, since_hours=since_hours)
    result["articles_xml_len"] = len(articles_xml)

    if dry_run:
        logger.info("Mode dry-run : arrêt après agrégation.")
        result["articles_xml"] = articles_xml
        return result

    # 2. Contexte récent
    logger.info("=== Étape 2 : Chargement du contexte ===")
    context_recent = load_recent_context(str(data_dir), context_file=context_file)
    system_prompt = load_system_prompt(prompt_path)

    # 3. Génération du script
    logger.info("=== Étape 3 : Génération du script ===")
    script_xml = generate_script(
        articles_xml=articles_xml,
        system_prompt=system_prompt,
        date=date,
        duree_cible=duree_cible,
        context_recent=context_recent,
    )

    script_path = scripts_dir / f"{date_slug}.xml"
    script_path.write_text(script_xml, encoding="utf-8")
    result["script_path"] = str(script_path)

    from .generate import _format_date_fr
    save_context(script_xml, _format_date_fr(date), str(data_dir), context_file=context_file)

    if skip_tts:
        return result

    # 4. Génération audio
    logger.info("=== Étape 4 : Génération audio TTS ===")
    audio_filename = f"{audio_prefix}{date_slug}.mp3"
    audio_path = str(audio_dir / audio_filename)
    generate_audio(script_xml, audio_path)
    result["audio_path"] = audio_path

    if skip_feed:
        return result

    # 5. Mise à jour du flux RSS
    logger.info("=== Étape 5 : Mise à jour du flux RSS ===")
    audio_size = Path(audio_path).stat().st_size
    word_count = len(script_xml.split())
    duration_sec = int(word_count / 150 * 60)
    existing_mp3s = sorted(audio_dir.glob("*.mp3"))

    add_episode(
        feed_path=feed_path,
        title=f"{episode_title_pfx} {date.strftime('%d %B %Y')}",
        audio_url=_audio_url(audio_filename),
        audio_size_bytes=audio_size,
        script_xml=script_xml,
        pub_date=date,
        duration_sec=duration_sec,
        episode_number=len(existing_mp3s),
    )
    result["feed_path"] = feed_path

    logger.info("=== Briefing terminé. ===")
    return result
