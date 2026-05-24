"""
Conversion du script XML en MP3 avec marqueurs de chapitre ID3.
Provider : OpenAI TTS (tts-1 ou tts-1-hd) si OPENAI_API_KEY défini,
           sinon edge-tts (gratuit, Microsoft Edge).
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

from mutagen.id3 import ID3, CHAP, CTOC, CTOCFlags, TIT2, ID3NoHeaderError

logger = logging.getLogger(__name__)


def _strip_xml_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_script(script_xml: str) -> list[dict]:
    segments = []
    intro_m = re.search(r"<intro>(.*?)</intro>", script_xml, re.DOTALL)
    if intro_m:
        segments.append({"label": "intro", "title": "Introduction", "text": _strip_xml_tags(intro_m.group(1))})
    for m in re.finditer(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', script_xml, re.DOTALL):
        segments.append({"label": "chapitre", "title": m.group(1), "text": _strip_xml_tags(m.group(2))})
    outro_m = re.search(r"<outro>(.*?)</outro>", script_xml, re.DOTALL)
    if outro_m:
        segments.append({"label": "outro", "title": "Conclusion", "text": _strip_xml_tags(outro_m.group(1))})
    return segments


def _tts_openai(text: str, path: str, voice: str, model: str) -> None:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.audio.speech.create(model=model, voice=voice, input=text, response_format="mp3")
    response.stream_to_file(path)


async def _tts_edge_async(text: str, path: str, voice: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(path)


def _tts_edge(text: str, path: str, voice: str) -> None:
    asyncio.run(_tts_edge_async(text, path, voice))


def _tts_segment(text: str, path: str) -> None:
    provider = os.getenv("TTS_PROVIDER", "openai" if os.getenv("OPENAI_API_KEY") else "edge")
    if provider == "openai":
        model = os.getenv("TTS_MODEL", "tts-1")
        voice = os.getenv("TTS_VOICE", "onyx")
        _tts_openai(text, path, voice, model)
    else:
        voice = os.getenv("TTS_VOICE", "fr-CA-AntoineNeural")
        _tts_edge(text, path, voice)


def _get_duration_ms(audio_path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
        capture_output=True, text=True, check=True,
    )
    return int(float(json.loads(result.stdout)["format"]["duration"]) * 1000)


def _concat_audio(segment_paths: list[str], output_path: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        concat_file = f.name
        for p in segment_paths:
            f.write(f"file '{Path(p).as_posix()}'\n")
    try:
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", output_path],
                       check=True, capture_output=True)
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
        tags.add(CHAP(element_id=cid, start_time=ch["start_ms"], end_time=ch["end_ms"],
                      start_offset=0xFFFFFFFF, end_offset=0xFFFFFFFF,
                      sub_frames=[TIT2(encoding=3, text=[ch["title"]])]))
    tags.add(CTOC(element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                  child_element_ids=chapter_ids, sub_frames=[TIT2(encoding=3, text=["Table des matières"])]))
    tags.save(mp3_path, v2_version=3)


def generate_audio(script_xml: str, output_path: str) -> str:
    segments = parse_script(script_xml)
    if not segments:
        raise ValueError("Aucun segment trouvé dans le script XML.")

    with tempfile.TemporaryDirectory() as tmpdir:
        segment_paths = []
        for i, seg in enumerate(segments):
            seg_path = str(Path(tmpdir) / f"seg_{i:03d}.mp3")
            logger.info("TTS %d/%d : %s ...", i + 1, len(segments), seg["title"])
            _tts_segment(seg["text"], seg_path)
            segment_paths.append(seg_path)
            if i < len(segments) - 1:
                time.sleep(1)

        chapter_data, cumulative_ms = [], 0
        for seg, seg_path in zip(segments, segment_paths):
            dur = _get_duration_ms(seg_path)
            chapter_data.append({"title": seg["title"], "start_ms": cumulative_ms, "end_ms": cumulative_ms + dur})
            cumulative_ms += dur

        logger.info("Durée totale : %.0f min", cumulative_ms / 60000)
        _concat_audio(segment_paths, output_path)

    _add_chapter_markers(output_path, chapter_data)
    return output_path
