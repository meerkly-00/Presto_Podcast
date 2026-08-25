"""
Génération du script de briefing via l'API Claude.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _format_date_fr(dt: datetime) -> str:
    jour = _JOURS[dt.weekday()]
    mois = _MOIS[dt.month - 1]
    return f"{jour} {dt.day} {mois} {dt.year}"


def load_system_prompt(prompt_path: str) -> str:
    with open(prompt_path, encoding="utf-8") as f:
        return f.read()


def load_recent_context(data_dir: str, n: int = 3, context_file: str = "context.json") -> str:
    path = Path(data_dir) / context_file
    if not path.exists():
        return ""
    with open(path, encoding="utf-8") as f:
        entries: list[dict] = json.load(f)
    entries = entries[-n:]
    if not entries:
        return ""
    lines = []
    for e in entries:
        lines.append(f"=== Briefing du {e['date']} ===\n{e['summary']}")
    return "\n\n".join(lines)


def _extract_chapter_summaries(script_xml: str) -> str:
    chapters = re.findall(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', script_xml, re.DOTALL)
    summaries = []
    for title, body in chapters:
        sentences = [s.strip() for s in body.strip().split(".") if s.strip()]
        excerpt = ". ".join(sentences[:3]) + "."
        if len(excerpt) > 300:
            excerpt = excerpt[:297] + "..."
        summaries.append(f"- {title} : {excerpt}")
    return "\n".join(summaries)


def save_context(script_xml: str, date_fr: str, data_dir: str, context_file: str = "context.json") -> None:
    path = Path(data_dir) / context_file
    entries: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    summary = _extract_chapter_summaries(script_xml)
    entries.append({"date": date_fr, "summary": summary})
    entries = entries[-10:]  # garde 10 briefings max
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


# Filet de sécurité : supprime tout méta-commentaire sur les sources/le
# processus que le LLM aurait laissé passer malgré la règle 7 du prompt.
# Cible uniquement le méta-process (≠ un vrai manque attribué à un acteur du
# monde, ex. « la police n'a pas dévoilé le nom » qui reste une nouvelle).
_META_SOURCE = (
    r"(?:"
    r"sources?[^.!?<>]{0,40}?(?:disponibl\w*|fournies)"
    r"|je n'ai pas pu (?:vérifier|confirmer)\w*"
    r"|aucune source[^.!?<>]{0,30}?(?:confirm\w*|disponibl\w*)"
    r"|informations? non (?:confirmé\w*|vérifié\w*)"
    r"|au moment de (?:la production|la mise en presse|la publication|produire ce briefing|écrire ces lignes)"
    r"|il n'a pas été possible de (?:vérifier|confirmer|obtenir)"
    r"|les détails (?:ne sont pas (?:disponibles|précisés|connus)|manquent|restent imprécis)"
    r")"
)
# Une « phrase » sans balise ni ponctuation interne, contenant le motif méta,
# se terminant par . ! ? — on l'efface en entier. [^.!?<>] garantit qu'on ne
# traverse jamais une frontière de phrase ni une balise XML.
_META_SENTENCE_RE = re.compile(
    r"\s*[^.!?<>]*?" + _META_SOURCE + r"[^.!?<>]*[.!?]",
    re.IGNORECASE,
)


def strip_meta_source_commentary(script_xml: str) -> tuple[str, int]:
    cleaned, n = _META_SENTENCE_RE.subn("", script_xml)
    if n:
        # Nettoie les espaces doublés laissés par la suppression.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n[ \t]+\n", "\n\n", cleaned)
    return cleaned, n


# Cadence de planification. La cadence réelle du TTS mesurée sur les épisodes
# publiés est un peu plus lente (2026-08-25 : 1861 mots lus pour 13 min, soit
# ~143 mots/minute) ; garder 150 ici fait viser un peu long plutôt qu'un peu
# court, ce qui est le bon sens de l'erreur pour un briefing de 15 à 18 min.
_WPM = 150

# Plancher accepté avant relance : sous ce ratio de la cible, on redemande au
# modèle d'étoffer. 0.88 de 17 min = 15 min, la durée annoncée aux auditeurs.
_LENGTH_FLOOR_RATIO = 0.88


def spoken_word_count(script_xml: str) -> int:
    """Nombre de mots réellement prononcés, balises XML exclues.

    len(script_xml.split()) comptait aussi les balises et les attributs : il
    surestimait la durée. Seul le texte de <intro>, <chapitre> et <outro> est
    envoyé au TTS, c'est donc lui seul qui fait la durée de l'épisode.
    """
    bodies = re.findall(
        r"<(?:intro|chapitre[^>]*|outro)>(.*?)</(?:intro|chapitre|outro)>",
        script_xml,
        re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", " ".join(bodies))
    return len(text.split())


def _msg_text(msg) -> str:
    """Extrait le bloc texte (les modèles à réflexion renvoient d'abord un ThinkingBlock)."""
    for block in msg.content:
        if getattr(block, "type", "") == "text":
            return block.text
    return ""


def _call_claude(client, model: str, system_prompt: str, messages: list[dict]) -> str:
    """Un appel Claude qui renvoie le script nettoyé de ses méta-commentaires.

    max_tokens plafonne la réflexion ET le texte. claude-sonnet-5 active la
    réflexion adaptative par défaut : le 2026-08-09, elle a consommé les 8192
    tokens sans laisser de place au script (stop_reason=max_tokens, zéro bloc
    texte). On la désactive, le briefing n'en a pas besoin, et on garde de la
    marge sur max_tokens.
    """
    message = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "disabled"},
        system=system_prompt,
        messages=messages,
    )

    script = _msg_text(message)
    if not script.strip():
        raise RuntimeError(
            f"Claude {model} n'a renvoyé aucun bloc texte "
            f"(stop_reason={getattr(message, 'stop_reason', '?')})."
        )

    script, n_meta = strip_meta_source_commentary(script)
    if n_meta:
        logger.warning("Filtre méta-sources : %d phrase(s) supprimée(s) du script.", n_meta)
    return script


def generate_script(
    articles_xml: str,
    system_prompt: str,
    date: datetime | None = None,
    duree_cible: int = 12,
    context_recent: str = "",
    model: str | None = None,
) -> str:
    if date is None:
        date = datetime.now()
    date_fr = _format_date_fr(date)
    model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

    user_parts = [
        f"Date : {date_fr}",
        f"Durée cible : {duree_cible} minutes",
    ]
    if context_recent:
        user_parts.append(f"Contexte récent (résumé des derniers briefings) :\n{context_recent}")

    user_parts.append(articles_xml)
    user_message = "\n\n".join(user_parts)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    logger.info("Appel Claude %s ...", model)
    script = _call_claude(client, model, system_prompt, [
        {"role": "user", "content": user_message},
    ])

    # Le modèle livre régulièrement bien en deçà de la cible du prompt système
    # (2026-08-23 : 1367 mots lus, soit 9 min au lieu de 15). On mesure et on
    # relance une fois pour étoffer à partir du même dump d'articles.
    target_words = duree_cible * _WPM
    floor_words = int(target_words * _LENGTH_FLOOR_RATIO)
    spoken = spoken_word_count(script)

    if spoken < floor_words:
        logger.warning(
            "Script court : %d mots lus (~%.1f min) pour une cible de %d mots "
            "(%d min, plancher %d mots). Relance pour étoffer.",
            spoken, spoken / _WPM, target_words, duree_cible, floor_words,
        )
        rallonge = (
            f"Ce script fait {spoken} mots réellement lus, soit environ "
            f"{spoken / _WPM:.0f} minutes. La cible est de {duree_cible} minutes, "
            f"soit environ {target_words} mots lus, et le plancher absolu est de "
            f"{floor_words} mots.\n\n"
            "Réécris le script AU COMPLET pour atteindre la cible, en puisant "
            "uniquement dans le XML d'articles déjà fourni ci-dessus :\n"
            "- traite les articles importants que tu as laissés de côté ;\n"
            "- ajoute les chapitres pertinents qui manquent ;\n"
            "- approfondis les dossiers majeurs avec le contexte, les chiffres "
            "et les positions des acteurs qui sont dans les articles.\n\n"
            "La règle 7 reste absolue : aucun fait, chiffre, nom ou citation qui "
            "ne vient pas du XML. Pas de remplissage, pas de redites, pas de "
            "formules creuses : de la matière factuelle en plus. Toutes les "
            "autres règles du prompt système s'appliquent telles quelles.\n\n"
            "Renvoie uniquement le script XML complet, rien d'autre."
        )
        try:
            rallonge_script = _call_claude(client, model, system_prompt, [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": script},
                {"role": "user", "content": rallonge},
            ])
        except Exception as e:
            logger.error("Relance d'étoffement échouée (%s) : on garde le script court.", e)
        else:
            rallonge_spoken = spoken_word_count(rallonge_script)
            if rallonge_spoken > spoken:
                script, spoken = rallonge_script, rallonge_spoken
            else:
                logger.warning(
                    "La relance n'a pas allongé le script (%d mots lus) : "
                    "on garde la version initiale.", rallonge_spoken,
                )

    if spoken < floor_words:
        logger.warning(
            "Script toujours sous le plancher : %d mots lus (~%.1f min).",
            spoken, spoken / _WPM,
        )

    logger.info(
        "Script généré : %d mots lus, ~%.1f min de lecture (cible %d min)",
        spoken, spoken / _WPM, duree_cible,
    )
    return script
