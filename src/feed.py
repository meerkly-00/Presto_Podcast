"""
Gestion du flux RSS podcast.
Produit un feed.xml compatible iTunes/Spotify.
"""

import email.utils
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, parse as parse_xml, ElementTree
from xml.dom import minidom

logger = logging.getLogger(__name__)

_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


def _register_namespaces():
    import xml.etree.ElementTree as ET
    ET.register_namespace("itunes", _ITUNES_NS)
    ET.register_namespace("content", _CONTENT_NS)


def _create_channel(title: str, description: str, base_url: str, author: str) -> Element:
    _register_namespaces()
    rss = Element("rss", {
        "version": "2.0",
        "xmlns:itunes": _ITUNES_NS,
        "xmlns:content": _CONTENT_NS,
    })
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "description").text = description
    SubElement(channel, "language").text = "fr-CA"
    SubElement(channel, "link").text = base_url
    SubElement(channel, f"{{{_ITUNES_NS}}}author").text = author
    SubElement(channel, f"{{{_ITUNES_NS}}}explicit").text = "no"
    SubElement(channel, f"{{{_ITUNES_NS}}}category", {"text": "News"})
    return rss


def _load_or_create(feed_path: str) -> Element:
    if Path(feed_path).exists():
        _register_namespaces()
        return parse_xml(feed_path).getroot()
    return _create_channel(
        title=os.getenv("PODCAST_TITLE", "Briefing matinal"),
        description=os.getenv("PODCAST_DESCRIPTION", "Briefing radio matinal québécois généré par IA."),
        base_url=os.getenv("PODCAST_BASE_URL", "http://localhost:8000"),
        author=os.getenv("PODCAST_AUTHOR", ""),
    )


def _extract_chapter_list(script_xml: str) -> str:
    titles = re.findall(r'<chapitre titre="([^"]+)">', script_xml)
    return ", ".join(titles) if titles else ""


def add_episode(
    feed_path: str,
    title: str,
    audio_url: str,
    audio_size_bytes: int,
    script_xml: str,
    pub_date: datetime,
    duration_sec: int,
    episode_number: int | None = None,
) -> None:
    rss = _load_or_create(feed_path)
    channel = rss.find("channel")
    if channel is None:
        raise ValueError("Flux RSS corrompu : élément <channel> introuvable.")

    chapters = _extract_chapter_list(script_xml)
    description = f"Au menu : {chapters}" if chapters else title

    item = Element("item")
    SubElement(item, "title").text = title
    SubElement(item, "description").text = description
    SubElement(item, "pubDate").text = email.utils.format_datetime(pub_date)
    SubElement(item, "guid", {"isPermaLink": "false"}).text = audio_url
    SubElement(item, "enclosure", {
        "url": audio_url,
        "type": "audio/mpeg",
        "length": str(audio_size_bytes),
    })
    SubElement(item, f"{{{_ITUNES_NS}}}duration").text = str(duration_sec)
    SubElement(item, f"{{{_ITUNES_NS}}}summary").text = description
    if episode_number is not None:
        SubElement(item, f"{{{_ITUNES_NS}}}episode").text = str(episode_number)

    # Insère en tête de channel (épisode le plus récent en premier)
    channel.insert(list(channel).index(channel.find("item") or item), item) if channel.find("item") is not None else channel.append(item)

    # Sérialise proprement
    raw = minidom.parseString(
        __import__("xml.etree.ElementTree", fromlist=["tostring"]).tostring(rss, encoding="unicode")
    ).toprettyxml(indent="  ", encoding=None)

    # minidom ajoute un <?xml ...?> en doublon parfois — on le garde propre
    lines = [l for l in raw.splitlines() if l.strip()]
    with open(feed_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info("Feed mis à jour : %s", feed_path)
