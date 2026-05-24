"""
Conversion du script XML en MP3 avec marqueurs de chapitre ID3.
TTS : edge-tts (Microsoft Edge, gratuit, sans compte).
Dépendances système : ffmpeg et ffprobe dans le PATH.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import edge_tts
from mutagen.id3 import ID3, CHAP, CTOC, CTOCFlags, TIT2, ID3NoHeaderError

logger = logging.getLogger(__name__)


def _strip_xml_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_script(script_xml: str) -> list[dict]:
    """Retourne une liste de segments [{label, title, text}, ...]."""
    segments = []

    intro_m = re.search(r"<intro>(.*?)</intro>", script_xml, re.DOTALL)
    if intro_m:
        segments.append({
            "label": "intro",
            "title": "Introduction",
            "text": _strip_xml_tags(intro_m.group(1)),
        })

    for m in re.finditer(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', script_xml, re.DOTALL):
        segments.append({
            "label": "chapitre",
            "title": m.group(1),
            "text": _strip_xml_tags(m.group(2)),
        })

    outro_m = re.search(r"<outro>(.*?)</outro>", script_xml, re.DOTALL)
    if outro_m:
        segments.append({
            "label": "outro",
            "title": "Conclusion",
            "text": _strip_xml_tags(outro_m.group(1)),
        })

    return segments


async def _tts_segment_async(text: str, path: str, voice: str) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(path)


def _tts_segment(text: str, path: str, voice: str) -> None:
    asyncio.run(_tts_segment_async(text, path, voice))


def _get_duration_ms(audio_path: str) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            audio_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return int(float(data["format"]["duration"]) * 1000)


def _concat_audio(segment_paths: list[str], output_path: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        concat_file = f.name
        for p in segment_paths:
            f.write(f"file '{Path(p).as_posix()}'\n")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                output_path,
            ],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(concat_file)


def _add_chapter_markers(mp3_path: str, chapters: list[dict]) -> None:
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()

    chapter_ids = []
    for i, ch in enumerate(chapters):
        cid = f"chp{i}"
        chapter_ids.append(cid)
        tags.add(CHAP(
            element_id=cid,
            start_time=ch["start_ms"],
            end_time=ch["end_ms"],
            start_offset=0xFFFFFFFF,
            end_offset=0xFFFFFFFF,
            sub_frames=[TIT2(encoding=3, text=[ch["title"]])],
        ))

    tags.add(CTOC(
        element_id="toc",
        flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
        child_element_ids=chapter_ids,
        sub_frames=[TIT2(encoding=3, text=["Table des matières"])],
    ))

    tags.save(mp3_path, v2_version=3)
    logger.info("%d marqueurs de chapitre insérés", len(chapters))


def generate_audio(script_xml: str, output_path: str) -> str:
    # fr-CA-AntoineNeural : voix masculine québécoise
    # fr-CA-SylvieNeural  : voix féminine québécoise
    voice = os.getenv("TTS_VOICE", "fr-CA-AntoineNeural")

    segments = parse_script(script_xml)
    if not segments:
        raise ValueError("Aucun segment trouvé dans le script XML.")

    with tempfile.TemporaryDirectory() as tmpdir:
        segment_paths: list[str] = []
        for i, seg in enumerate(segments):
            seg_path = str(Path(tmpdir) / f"seg_{i:03d}.mp3")
            logger.info("TTS segment %d/%d : %s ...", i + 1, len(segments), seg["title"])
            _tts_segment(seg["text"], seg_path, voice)
            segment_paths.append(seg_path)
            if i < len(segments) - 1:
                time.sleep(2)

        chapter_data: list[dict] = []
        cumulative_ms = 0
        for seg, seg_path in zip(segments, segment_paths):
            dur = _get_duration_ms(seg_path)
            chapter_data.append({
                "title": seg["title"],
                "start_ms": cumulative_ms,
                "end_ms": cumulative_ms + dur,
            })
            cumulative_ms += dur

        logger.info("Durée totale : %.0f min", cumulative_ms / 60000)
        _concat_audio(segment_paths, output_path)

    _add_chapter_markers(output_path, chapter_data)
    logger.info("Audio généré : %s", output_path)
    return output_path
