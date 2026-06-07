"""
Orchestrateur principal du briefing matinal.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .aggregate import aggregate
from .generate import generate_script, load_system_prompt, load_recent_context, save_context, _format_date_fr, _MOIS
from .tts import generate_audio
from .feed import add_episode, prune_old_episodes

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
    keep_days   = int(os.getenv("FEED_KEEP_DAYS", 7))

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
    episode_title_pfx   = os.getenv("EPISODE_TITLE_PREFIX", "Presto — édition du")

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
    # Numéro d'épisode stable = jours depuis le lancement (insensible au checkout CI).
    # len(existing_mp3s) donnait toujours "1" en CI (checkout frais).
    from datetime import date as _date
    launch_str = os.getenv("PODCAST_LAUNCH_DATE", "2026-05-24")
    try:
        launch = _date.fromisoformat(launch_str)
        episode_number = (date.date() if hasattr(date, "date") else date).toordinal() - launch.toordinal() + 1
        if episode_number < 1:
            episode_number = None
    except (ValueError, AttributeError):
        episode_number = None

    # Titre en français : "Presto — édition du 28 mai 2026"
    mois_fr = _MOIS[date.month - 1]
    titre_episode = f"{episode_title_pfx} {date.day} {mois_fr} {date.year}"

    add_episode(
        feed_path=feed_path,
        title=titre_episode,
        audio_url=_audio_url(audio_filename),
        audio_size_bytes=audio_size,
        script_xml=script_xml,
        pub_date=date,
        duration_sec=duration_sec,
        episode_number=episode_number,
    )
    result["feed_path"] = feed_path

    # 6. Purge des épisodes plus vieux que keep_days (défaut 7)
    if keep_days > 0:
        logger.info("=== Étape 6 : Purge des épisodes > %d jours ===", keep_days)
        pruned = prune_old_episodes(feed_path, keep_days=keep_days, now=date)
        result["pruned_episodes"] = pruned

    logger.info("=== Briefing terminé. ===")
    return result
