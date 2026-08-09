"""
Rafraîchit la base de faits du bot media-watch.

Le bot ne connaissait que le briefing du matin, donc en après-midi il n'avait
plus de munitions et le pré-filtre écartait tout. Ce script réagrège les
nouvelles fraîches (RSS, gratuit) et les condense en fiche de faits sourcés
avec Haiku (petit modèle, quelques cents par mois).

Écrit data/fresh_facts.json : {generated_at, faits: [{sujet, fait}]}
Usage : py tools/refresh_facts.py [--hours 8] [--dry-run]
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import anthropic
from dotenv import load_dotenv

from src.aggregate import aggregate

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(ROOT / ".env")

OUT_FILE = ROOT / "data" / "fresh_facts.json"
CONFIG_PATH = os.getenv("SOURCES_FILE", str(ROOT / "config" / "sources.yaml"))
MODEL = os.getenv("REFRESH_MODEL", "claude-haiku-4-5")
MAX_CHARS = int(os.getenv("REFRESH_MAX_CHARS", "30000"))

PROMPT = """Voici des articles d'actualité agrégés dans les dernières heures.

\"\"\"{news}\"\"\"

Extrais les développements factuels notables : ce qui a bougé, avec des faits
concrets (chiffres, décisions, déclarations, issues). Maximum 12 entrées.

RÈGLES :
- Uniquement des faits présents dans les articles. Aucune invention.
- Chaque fait nomme sa source ("Selon Reuters...", "D'après Radio-Canada...").
- Ton neutre et sec. Aucun adjectif évaluatif, aucune opinion.
- Ignore le sport, les faits divers, les drames humains et le divertissement.
- "sujet" = 3 à 6 mots qui identifient le dossier (sert de filtre de pertinence).
- "fait" = une à deux phrases avec le contenu factuel et la source.

Réponds UNIQUEMENT avec un objet JSON, rien d'autre :
{{"faits": [{{"sujet": "...", "fait": "..."}}]}}"""


def load_fresh_facts(max_age_h: int = 12, log=None) -> list[dict]:
    """Lit la fiche de faits du jour (lecteur partagé par les autres outils).

    Retourne [] si le fichier est absent, illisible, ou trop vieux : mieux vaut
    retomber sur le briefing du matin que de citer du vieux comme du frais.
    """
    if not OUT_FILE.exists():
        return []
    try:
        data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(data["generated_at"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        if log:
            log(f"fresh_facts.json illisible ({e}), ignoré.")
        return []

    age_h = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
    if age_h > max_age_h:
        if log:
            log(f"Faits frais périmés ({age_h:.1f} h), ignorés.")
        return []
    return data.get("faits", [])


def _extract_json(raw: str) -> dict:
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        if txt.lstrip().lower().startswith("json"):
            txt = txt.lstrip()[4:]
    start, end = txt.find("{"), txt.rfind("}")
    if start != -1 and end != -1:
        txt = txt[start:end + 1]
    return json.loads(txt)


def _msg_text(msg) -> str:
    """Extrait le bloc texte (ignore les blocs de réflexion)."""
    for block in msg.content:
        if getattr(block, "type", "") == "text":
            return block.text.strip()
    return ""


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Rafraîchit la base de faits du bot.")
    parser.add_argument("--hours", type=int, default=int(os.getenv("REFRESH_HOURS", "8")),
                        help="Fenêtre d'agrégation en heures (défaut 8)")
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans écrire le fichier")
    args = parser.parse_args()

    news = aggregate(CONFIG_PATH, since_hours=args.hours)[:MAX_CHARS]
    if len(news) < 500:
        print("Agrégation vide ou trop mince, rien à rafraîchir.")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT.format(news=news)}],
    )

    try:
        data = _extract_json(_msg_text(msg))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Réponse non parsable, rien écrit : {e}")
        sys.exit(1)

    faits = [
        {"sujet": f.get("sujet", "").strip(), "fait": f.get("fait", "").strip()}
        for f in data.get("faits", [])
        if f.get("sujet", "").strip() and len(f.get("fait", "").strip()) >= 25
    ]
    if not faits:
        print("Aucun fait exploitable extrait, rien écrit.")
        return

    u = msg.usage
    print(f"{len(faits)} fait(s) extrait(s) — {u.input_tokens} tokens in / {u.output_tokens} out")
    for f in faits:
        print(f"  • [{f['sujet']}] {f['fait'][:100]}")

    if args.dry_run:
        print("\n[DRY_RUN] Fichier non écrit.")
        return

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": args.hours,
        "faits": faits,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nÉcrit → {OUT_FILE}")


if __name__ == "__main__":
    main()
