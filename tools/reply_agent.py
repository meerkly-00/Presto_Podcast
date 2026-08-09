"""
Agent de réponse quotidien Presto.

Pour chaque chapitre du briefing du jour :
  1. Cherche sur X le tweet le plus engagé sur ce sujet (dernières 24h)
  2. Génère une réponse factuelle courte via Claude Haiku
  3. Poste la réponse en reply

Usage : python tools/reply_agent.py output/scripts/2026-05-29.xml
"""

import os
import re
import sys
import time
import logging
from pathlib import Path

import anthropic
import tweepy
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

MAX_REPLIES = int(os.getenv("REPLY_AGENT_MAX", "3"))
MIN_LIKES    = int(os.getenv("REPLY_AGENT_MIN_LIKES", "5"))

SEARCH_PROMPT = """\
Voici le titre et le résumé d'un chapitre d'un briefing d'actualité québécoise :

Titre : {titre}
Résumé : {resume}

Génère UNE requête de recherche Twitter/X courte (max 60 chars) en français pour trouver
des tweets récents sur ce sujet. La requête doit utiliser 2-3 mots-clés précis du sujet.
Réponds avec la requête UNIQUEMENT, rien d'autre.
"""

REPLY_PROMPT = """\
Tu es @PrestoPodcast, un briefing d'actualité québécoise factuel généré par IA.
Ton style : neutre, précis, jamais opiniatif. Tu ajoutes de la valeur factuelle.

Tweet auquel tu réponds :
\"\"\"{tweet_text}\"\"\"

Faits pertinents du briefing Presto d'aujourd'hui sur ce sujet :
{faits}

Rédige UNE réponse en français québécois (max 200 chars) qui :
- Ajoute 1-2 faits concrets que le tweet ne mentionne pas
- Ne commence PAS par "Presto" ou "@"
- Termine par : -> prestopodcast.online
- Ton factuel, pas d'opinion

Réponds avec le texte du reply UNIQUEMENT.
"""


def extract_chapters(script_xml: str) -> list[dict]:
    chapters = re.findall(
        r'<chapitre titre="([^"]+)">(.*?)</chapitre>', script_xml, re.DOTALL
    )
    result = []
    for titre, body in chapters:
        sentences = [s.strip() for s in body.strip().split(".") if len(s.strip()) > 20]
        resume = ". ".join(sentences[:4]) + "."
        faits  = ". ".join(sentences[:6]) + "."
        result.append({"titre": titre, "resume": resume[:400], "faits": faits[:600]})
    return result


def _msg_text(msg) -> str:
    """Extrait le bloc texte (les modèles à réflexion renvoient d'abord un ThinkingBlock)."""
    for block in msg.content:
        if getattr(block, "type", "") == "text":
            return block.text
    return ""


def build_search_query(chapter: dict, claude: anthropic.Anthropic) -> str:
    msg = claude.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=80,
        messages=[{"role": "user", "content": SEARCH_PROMPT.format(**chapter)}],
    )
    query = _msg_text(msg).strip().strip('"')
    # Ajout de filtres : langue française, exclu retweets
    return f"{query} lang:fr -is:retweet"


def find_best_tweet(query: str, tw: tweepy.Client) -> dict | None:
    try:
        resp = tw.search_recent_tweets(
            query=query,
            max_results=15,
            tweet_fields=["public_metrics", "author_id", "created_at"],
            expansions=["author_id"],
            user_fields=["public_metrics"],
            sort_order="relevancy",
        )
    except Exception as e:
        log.warning("Search failed: %s", e)
        return None

    if not resp.data:
        return None

    # Index auteurs
    users = {u.id: u for u in (resp.includes.get("users") or [])}

    best = None
    best_score = -1
    for tweet in resp.data:
        m = tweet.public_metrics
        # Score = likes + retweets*2 (on veut du reach)
        score = m["like_count"] + m["retweet_count"] * 2
        author = users.get(tweet.author_id)
        # Bonus si l'auteur a une grosse audience
        if author and author.public_metrics["followers_count"] > 1000:
            score += 10
        # Filtre minimum
        if score < MIN_LIKES:
            continue
        if score > best_score:
            best_score = score
            best = {"id": tweet.id, "text": tweet.text, "score": score}

    return best


def generate_reply(tweet_text: str, faits: str, claude: anthropic.Anthropic) -> str:
    msg = claude.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=120,
        messages=[{"role": "user", "content": REPLY_PROMPT.format(
            tweet_text=tweet_text, faits=faits
        )}],
    )
    return _msg_text(msg).strip()


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/reply_agent.py <script.xml>", file=sys.stderr)
        sys.exit(1)

    script_path = Path(sys.argv[1])
    if not script_path.exists():
        log.error("Fichier introuvable : %s", script_path)
        sys.exit(1)

    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

    script_xml = script_path.read_text(encoding="utf-8")
    chapters   = extract_chapters(script_xml)[:MAX_REPLIES]

    if not chapters:
        log.error("Aucun chapitre trouvé dans le script.")
        sys.exit(1)

    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    tw = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
        wait_on_rate_limit=True,
    )

    posted = 0
    for ch in chapters:
        if posted >= MAX_REPLIES:
            break

        log.info("── Chapitre : %s", ch["titre"])

        query = build_search_query(ch, claude)
        log.info("   Recherche : %s", query)

        tweet = find_best_tweet(query, tw)
        if not tweet:
            log.info("   Aucun tweet pertinent trouvé, skip.")
            continue

        log.info("   Tweet trouvé (score=%d) : %s", tweet["score"], tweet["text"][:80])

        reply_text = generate_reply(tweet["text"], ch["faits"], claude)
        log.info("   Reply (%d chars) : %s", len(reply_text), reply_text)

        if dry_run:
            log.info("   [DRY_RUN] Reply non posté.")
        else:
            try:
                resp = tw.create_tweet(
                    text=reply_text,
                    in_reply_to_tweet_id=tweet["id"],
                )
                log.info("   Posté : https://x.com/prestopodcast/status/%s", resp.data["id"])
                posted += 1
                time.sleep(10)  # petit délai entre les replies
            except Exception as e:
                log.error("   Erreur posting : %s", e)

    log.info("── Total replies postés : %d", posted)


if __name__ == "__main__":
    main()
