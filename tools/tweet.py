"""
Génère le thread quotidien Presto à partir du script XML du jour.
Structure : tweet hook → 2-3 faits supplémentaires → CTA podcast

Usage :
  Générer + sauvegarder JSON (GitHub Actions → Cloudflare Worker) :
    python tools/tweet.py output/scripts/2026-05-31.xml --output-only data/tweets/2026-05-31.json

  Poster directement sur X (local/debug) :
    python tools/tweet.py output/scripts/2026-05-31.xml
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic
import tweepy
from dotenv import load_dotenv

# Force UTF-8 sur la sortie console (Windows cp1252 plante sur les emojis/accents)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent.parent / ".env")

THREAD_PROMPT = """Tu es chargé de rédiger un thread en français québécois pour le compte @prestopodcast.

À partir du script XML du briefing ci-dessous, génère exactement 3 tweets qui forment un thread.

TWEET 1 — Le hook :
- Le fait le plus fort, surprenant ou important de la journée
- Commence directement par le fait, jamais par "Aujourd'hui" ou "Dans le briefing"
- Chiffres bruts si possible (ex: "4 morts", "86e jour", "10x la vitesse du son")
- Maximum 240 caractères
- Pas de hashtags, pas d'URL

TWEET 2 — Deuxième fait fort (sujet différent du tweet 1) :
- Un autre fait marquant de la journée, différent chapitre
- Commence directement par le fait
- Maximum 260 caractères
- Pas de hashtags, pas d'URL

TWEET 3 — Troisième fait ou angle (sujet différent des tweets 1 et 2) :
- Encore un autre fait, ou une stat, ou un angle inattendu
- Maximum 260 caractères
- Pas de hashtags, pas d'URL

Règles globales :
- Ton neutre, factuel, percutant — jamais promotionnel
- Chaque tweet doit pouvoir se lire indépendamment
- Pas de "Suite :", "2/3 :", numérotation ou connecteurs

Réponds UNIQUEMENT avec les 3 tweets séparés par une ligne contenant exactement "---", rien d'autre.

Script XML :
{script}"""

CTA_TWEET = "Le briefing complet est disponible sur toutes les plateformes 🎙️\n→ prestopodcast.online"


def extract_thread(script_xml: str) -> list[str]:
    truncated = script_xml[:8000] if len(script_xml) > 8000 else script_xml

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=512,
        messages=[{"role": "user", "content": THREAD_PROMPT.format(script=truncated)}],
    )
    raw = message.content[0].text.strip()
    tweets = [t.strip().strip('"') for t in raw.split("---") if t.strip()]
    return tweets[:3]  # max 3 tweets de contenu


def _trim_tweet(text: str, max_chars: int = 280) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in (". ", " "):
        if sep in cut:
            cut = cut.rsplit(sep, 1)[0] + ("." if sep == ". " else "")
            break
    return cut.strip()


def post_thread(tweets: list[str]) -> list[str]:
    """Poste les tweets en thread (usage local/debug), retourne la liste des IDs."""
    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )

    ids = []
    reply_to = None

    for i, text in enumerate(tweets):
        print(f"Posting tweet {i+1}/{len(tweets)}...", flush=True)
        try:
            if reply_to:
                try:
                    response = client.create_tweet(text=text, in_reply_to_tweet_id=reply_to)
                except TypeError:
                    response = client.create_tweet(text=text, reply={"in_reply_to_tweet_id": reply_to})
            else:
                response = client.create_tweet(text=text)
        except tweepy.errors.Forbidden as e:
            print(f"  403 Forbidden on tweet {i+1}: {e}", flush=True)
            if hasattr(e, 'response') and e.response is not None:
                print(f"  Response body: {e.response.text[:500]}", flush=True)
            raise

        tweet_id = response.data["id"]
        ids.append(tweet_id)
        reply_to = tweet_id
        print(f"  → posted: {tweet_id}", flush=True)

        if i < len(tweets) - 1:
            time.sleep(2)

    return ids


def main():
    parser = argparse.ArgumentParser(description="Génère et/ou publie le thread Presto du jour")
    parser.add_argument("script_path", help="Chemin vers le script XML du jour")
    parser.add_argument(
        "--output-only",
        metavar="PATH",
        help="Sauvegarder les tweets en JSON au lieu de poster sur X",
    )
    args = parser.parse_args()

    script_path = Path(args.script_path)
    if not script_path.exists():
        print(f"Fichier introuvable : {script_path}", file=sys.stderr)
        sys.exit(1)

    script_xml = script_path.read_text(encoding="utf-8")
    print("Génération du thread...")
    content_tweets = extract_thread(script_xml)

    all_tweets = content_tweets + [CTA_TWEET]
    all_tweets = [_trim_tweet(t) for t in all_tweets]

    print(f"\n{'='*50}")
    for i, t in enumerate(all_tweets, 1):
        print(f"Tweet {i}/{len(all_tweets)} ({len(t)} chars):\n{t}\n")
    print('='*50)

    if args.output_only:
        date = script_path.stem
        output = {"date": date, "tweets": all_tweets}
        out_path = Path(args.output_only)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nTweets sauvegardés → {out_path}")
        return

    if os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes"):
        print("[DRY_RUN] Thread non publié.")
        return

    ids = post_thread(all_tweets)
    print(f"\nThread publié ({len(ids)} tweets) :")
    print(f"→ https://x.com/prestopodcast/status/{ids[0]}")


if __name__ == "__main__":
    main()
