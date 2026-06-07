"""
Génère les 2 tweets auto de l'après-midi pour Presto, à partir du thread du matin.

  - midi : poll de suivi (continuité éditoriale sur un sujet du matin + nouveauté)
  - soir : contre-programme (faits secs, posté ~17h30 avant les bulletins d'opinion)

MINI-REFETCH : on re-agrège les feeds RSS sur une fenêtre courte (8h par défaut)
pour capter les développements survenus depuis le briefing du matin — sans
nouvelle API, juste les feeds existants. Le LLM relie ces news fraîches aux
3 sujets déjà tweetés le matin.

GARDE ANTI-OPINION : chaque texte généré est validé par tools/neutralite.py.
Si un mot évaluatif passe, on régénère ; après 2 essais ratés, on SKIP le tweet
plutôt que de publier quelque chose hors-marque.

Usage :
  python tools/afternoon_tweets.py --date 2026-06-07
    → lit data/tweets/2026-06-07.json
    → écrit data/tweets/2026-06-07-midi.json et 2026-06-07-soir.json
"""

import argparse
import json
import os
import sys
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))                    # importer src.*
sys.path.insert(0, str(Path(__file__).parent))   # importer neutralite

import anthropic
from dotenv import load_dotenv

from src.aggregate import aggregate
from neutralite import mots_evaluatifs

# Force UTF-8 sur la sortie console (Windows cp1252 plante sur emojis/accents)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(ROOT / ".env")

REFETCH_HEURES = int(os.getenv("AFTERNOON_REFETCH_HEURES", "8"))
CONFIG_PATH = os.getenv("SOURCES_FILE", str(ROOT / "config" / "sources.yaml"))
MODEL = os.getenv("CLAUDE_MODEL_AFTERNOON", os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"))
MAX_ESSAIS = 2

_REGLES_NEUTRALITE = """RÈGLES DE NEUTRALITÉ (NON NÉGOCIABLES) :
- AUCUN adjectif ou adverbe évaluatif (pire, mieux, inquiétant, rassurant,
  alarmant, encourageant, choquant, scandaleux, catastrophique, heureusement,
  malheureusement, enfin, hélas, prometteur, dramatique...).
- Décris ce qui s'est passé, JAMAIS ce qu'il faut en penser.
- Français québécois, ton sobre, tutoiement.
- Si tu hésites sur un mot, retire-le."""

POLL_PROMPT = """Tu génères le tweet-sondage de MIDI pour Presto, un briefing d'actualité
québécois 100 % factuel et non-partisan. Ton seul rôle : la CONTINUITÉ
ÉDITORIALE — reprendre un des sujets déjà tweetés ce matin et y ajouter le
développement le plus récent trouvé dans les news fraîches, puis ouvrir un
sondage neutre.

SUJETS DÉJÀ TWEETÉS CE MATIN :
{sujets_matin}

NEWS FRAÎCHES (agrégées il y a quelques heures) :
{news_fraiches}

TÂCHE :
1. Choisis le sujet du matin pour lequel les news fraîches contiennent le
   développement le plus marquant (nouveau fait, chiffre, décision, réaction).
   Si rien n'a évolué, prends le sujet au plus fort intérêt public et ajoute
   un fait de contexte vérifiable tiré des news.
2. Rédige un tweet en 3 temps :
   - RAPPEL (1 ligne) : « Ce matin → [le point]. »
   - NOUVEAUTÉ (1-2 lignes) : le développement, en faits seulement.
3. Rédige un SONDAGE : une question neutre + 2 à 4 options couvrant honnêtement
   le spectre des positions raisonnables (aucune caricature, aucune « bonne »
   réponse suggérée).

{regles}
- texte_tweet ≤ 240 caractères. Chaque option ≤ 25 caractères (limite X).

Réponds UNIQUEMENT avec un objet JSON, rien d'autre :
{{"sujet": "...", "texte_tweet": "...", "sondage": {{"question": "...", "options": ["...", "..."], "duree_minutes": 1440}}}}"""

SOIR_PROMPT = """Tu génères le tweet de FIN D'APRÈS-MIDI pour Presto, briefing québécois
factuel et non-partisan. Il est publié à 17h30, juste avant les bulletins
d'opinion du soir. Angle implicite : pendant que les autres vont commenter,
Presto donne juste les faits.

SUJETS DU MATIN :
{sujets_matin}

NEWS FRAÎCHES :
{news_fraiches}

TÂCHE :
1. Sélectionne 1 à 3 faits concrets qui ont bougé aujourd'hui (chiffres,
   décisions, événements vérifiables).
2. Ouvre par une accroche courte de la famille « X, pas Y » qui rappelle le
   positionnement (ex. : « Ce qui a bougé aujourd'hui — sans le spin du 18h : »).
3. Liste les faits en puces courtes et sèches (« • ... »).

{regles}
- texte_tweet complet ≤ 270 caractères, puces incluses.

Réponds UNIQUEMENT avec un objet JSON, rien d'autre :
{{"texte_tweet": "...", "faits_utilises": ["...", "..."]}}"""


def _extract_json(raw: str) -> dict:
    """Parse le JSON renvoyé par le modèle, en tolérant les ```json fences."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        if txt.lstrip().lower().startswith("json"):
            txt = txt.lstrip()[4:]
    start, end = txt.find("{"), txt.rfind("}")
    if start != -1 and end != -1:
        txt = txt[start:end + 1]
    return json.loads(txt)


def _call_model(client: anthropic.Anthropic, prompt: str) -> dict:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(msg.content[0].text)


def _trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if ". " in cut:
        cut = cut.rsplit(". ", 1)[0] + "."
    elif " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip()


def _texts_a_valider(data: dict) -> list[str]:
    """Champs texte à passer dans la garde anti-opinion."""
    champs = [data.get("texte_tweet", "")]
    sondage = data.get("sondage") or {}
    if sondage:
        champs.append(sondage.get("question", ""))
        champs.extend(sondage.get("options", []))
    return [c for c in champs if c]


def generer_neutre(client, prompt: str, label: str) -> dict | None:
    """Génère + valide la neutralité ; régénère si fautes ; None après échecs."""
    prompt_courant = prompt
    for essai in range(1, MAX_ESSAIS + 1):
        try:
            data = _call_model(client, prompt_courant)
        except Exception as e:
            print(f"[{label}] essai {essai} : erreur génération/parse : {e}")
            continue

        fautes = []
        for t in _texts_a_valider(data):
            fautes.extend(mots_evaluatifs(t))

        if not fautes:
            print(f"[{label}] OK (essai {essai}) — neutre.")
            return data

        uniques = sorted(set(fautes), key=str.lower)
        print(f"[{label}] essai {essai} : mots évaluatifs détectés {uniques} → régénération.")
        prompt_courant = (
            prompt
            + f"\n\nATTENTION : ta réponse précédente contenait ces mots évaluatifs "
            f"INTERDITS : {', '.join(uniques)}. Reformule en faits seulement, "
            f"sans aucun de ces mots ni aucun synonyme évaluatif."
        )

    print(f"[{label}] ÉCHEC après {MAX_ESSAIS} essais — tweet sauté (protection marque).")
    return None


def construire_midi(data: dict, date_slug: str) -> dict | None:
    sondage = data.get("sondage") or {}
    options = [(_trim(o, 25)) for o in sondage.get("options", []) if o.strip()][:4]
    if len(options) < 2:
        print("[midi] moins de 2 options valides → tweet sauté.")
        return None
    question = sondage.get("question", "").strip()
    corps = data.get("texte_tweet", "").strip()
    # La question doit rester intacte (elle porte le sondage) : on rogne le corps.
    if question:
        budget_corps = 280 - len(question) - 2  # 2 = "\n\n"
        corps = _trim(corps, max(0, budget_corps))
        text = f"{corps}\n\n{question}".strip()
    else:
        text = _trim(corps, 280)
    duree = sondage.get("duree_minutes", 1440)
    try:
        duree = max(5, min(int(duree), 10080))
    except (TypeError, ValueError):
        duree = 1440
    return {
        "date": date_slug,
        "kind": "poll",
        "text": text,
        "poll": {"options": options, "duration_minutes": duree},
    }


def construire_soir(data: dict, date_slug: str) -> dict | None:
    text = _trim(data.get("texte_tweet", "").strip(), 280)
    if not text:
        print("[soir] texte vide → tweet sauté.")
        return None
    return {"date": date_slug, "kind": "single", "text": text}


def main():
    parser = argparse.ArgumentParser(description="Génère les tweets midi + soir de Presto")
    parser.add_argument("--date", help="Date YYYY-MM-DD (défaut : aujourd'hui UTC)")
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans écrire les JSON")
    args = parser.parse_args()

    date_slug = args.date or _date.today().isoformat()
    tweets_dir = ROOT / "data" / "tweets"
    morning_path = tweets_dir / f"{date_slug}.json"

    if not morning_path.exists():
        print(f"Pas de thread du matin pour {date_slug} ({morning_path}). Rien à suivre, skip.")
        return

    morning = json.loads(morning_path.read_text(encoding="utf-8"))
    # Les 3 premiers tweets = les sujets ; le dernier est le CTA, on l'exclut.
    sujets = [t for t in morning.get("tweets", [])][:3]
    sujets_matin = "\n".join(f"{i}. {t}" for i, t in enumerate(sujets, 1)) or "(aucun)"

    print(f"Mini-refetch des feeds sur {REFETCH_HEURES}h...")
    news_fraiches = aggregate(CONFIG_PATH, since_hours=REFETCH_HEURES)
    news_fraiches = news_fraiches[:9000]  # borne le contexte
    print(f"News fraîches : {len(news_fraiches)} caractères.")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    fmt = dict(sujets_matin=sujets_matin, news_fraiches=news_fraiches, regles=_REGLES_NEUTRALITE)
    plan = [
        ("midi", POLL_PROMPT.format(**fmt), construire_midi, f"{date_slug}-midi.json"),
        ("soir", SOIR_PROMPT.format(**fmt), construire_soir, f"{date_slug}-soir.json"),
    ]

    for label, prompt, builder, filename in plan:
        print(f"\n=== Génération {label} ===")
        data = generer_neutre(client, prompt, label)
        if data is None:
            continue
        payload = builder(data, date_slug)
        if payload is None:
            continue
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not args.dry_run:
            out = tweets_dir / filename
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"→ écrit {out}")


if __name__ == "__main__":
    main()
