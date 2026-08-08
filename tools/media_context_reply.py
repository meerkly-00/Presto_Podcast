"""
Réponses de contexte factuel sous les tweets de grands médias québécois.

Toutes les 20 min via GitHub Actions : lit les derniers tweets des comptes
surveillés (défaut @JdeMontreal), et si le briefing Presto du jour contient
des faits précis qui recadrent l'angle du tweet, poste une réponse de
contexte sourcée. Être tôt dans les réponses = visibilité algorithmique.

Garde-fous : max 1 reply par run, max REPLY_MAX_PER_DAY par jour, tweets de
moins de FRESH_MINUTES seulement, skip systématique si aucun fait solide,
skip les drames humains. State persisté dans data/media_reply_state.json.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import tweepy
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / "data" / "media_reply_state.json"

HANDLES = [h.strip() for h in os.getenv("MEDIA_WATCH_HANDLES", "JdeMontreal").split(",") if h.strip()]
MAX_PER_DAY = int(os.getenv("REPLY_MAX_PER_DAY", "3"))
FRESH_MINUTES = int(os.getenv("FRESH_MINUTES", "120"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
SCREEN_MODEL = os.getenv("SCREEN_MODEL", "claude-haiku-4-5-20251001")

SCREEN_PROMPT = """\
Sujets couverts par le briefing d'actualité de ce matin :
{topics}

Tweet d'un média : \"\"\"{tweet_text}\"\"\"

Ce tweet porte-t-il directement sur un des sujets couverts ci-dessus ?
Réponds uniquement OUI ou NON."""

PROMPT = """\
Tu écris pour @PrestoPodcast, un briefing d'actualité québécois strictement factuel.
Un grand média vient de publier ce tweet :

Compte : @{handle}
Tweet : \"\"\"{tweet_text}\"\"\"

Voici le briefing Presto du jour (ta SEULE source de faits autorisée) :
\"\"\"{briefing}\"\"\"

Décide si le briefing contient des faits précis (chiffres, déclarations sourcées,
éléments de contexte) que l'angle du tweet omet, exagère ou déforme. Si oui,
rédige UNE réponse en français québécois, maximum 270 caractères.

RÈGLES ABSOLUES :
- Uniquement des faits présents dans le briefing ci-dessus. Aucune invention.
- La réponse recadre l'angle avec des faits, elle n'attaque jamais le média ni
  l'auteur. Aucun mot comme "faux", "mensonge", "désinformation", "clickbait".
- Structure : le contexte factuel d'abord, la source nommée ("Selon Reuters...",
  "D'après Radio-Canada..."). Le lecteur tire sa propre conclusion.
- Ton calme et sec. Pas d'emoji, pas de hashtag, pas de lien, pas de point
  d'exclamation, aucun tiret long.
- SKIP obligatoire si : le briefing n'a aucun fait directement pertinent, le
  tweet est un fait divers ou un drame humain (décès, accident, violence contre
  une personne), du sport, du divertissement, ou si le tweet est factuellement
  correct et complet. Ne force jamais une réponse.

Réponds avec le texte du reply UNIQUEMENT, ou exactement « SKIP ».
"""


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"user_ids": {}, "last_seen": {}, "replied": [], "daily": {}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def daily_count(state: dict) -> int:
    return state["daily"].get(today(), 0)


def load_briefing() -> str:
    """Dernier script Presto disponible (aujourd'hui, sinon le plus récent)."""
    scripts = sorted((PROJECT_ROOT / "output" / "scripts").glob("*.xml"))
    scripts = [s for s in scripts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.stem)]
    if not scripts:
        return ""
    raw = scripts[-1].read_text(encoding="utf-8")
    return re.sub(r"<[^>]+>", " ", raw)[:12000]


def load_topics() -> str:
    """Résumé compact du briefing (titres de chapitres + première phrase) pour le pré-filtre."""
    scripts = sorted((PROJECT_ROOT / "output" / "scripts").glob("*.xml"))
    scripts = [s for s in scripts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.stem)]
    if not scripts:
        return ""
    raw = scripts[-1].read_text(encoding="utf-8")
    lines = []
    for m in re.finditer(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', raw, re.DOTALL):
        text = re.sub(r"\s+", " ", m.group(2)).strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)[:3]
        lines.append(f"- {m.group(1)} : {' '.join(sentences)}")
    return "\n".join(lines)


def main():
    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    state = load_state()

    if daily_count(state) >= MAX_PER_DAY:
        log.info("Max quotidien atteint (%d replies). Rien à faire.", MAX_PER_DAY)
        return

    briefing = load_briefing()
    if not briefing:
        log.warning("Aucun briefing disponible, skip.")
        return
    topics = load_topics()

    # Écriture : OAuth1 (compte @PrestoPodcast). Lecture : Bearer App-Only si
    # disponible (requis pour les GET sur le tier pay-per-use), sinon OAuth1.
    tw = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
        wait_on_rate_limit=True,
    )
    bearer = os.getenv("TWITTER_BEARER_TOKEN")
    reader = tweepy.Client(bearer_token=bearer, wait_on_rate_limit=True) if bearer else tw
    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    replied = set(state.get("replied", []))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=FRESH_MINUTES)

    for handle in HANDLES:
        # Résout et met en cache l'ID numérique du compte
        uid = state["user_ids"].get(handle)
        if not uid:
            try:
                uid = str(reader.get_user(username=handle).data.id)
                state["user_ids"][handle] = uid
            except Exception as e:
                log.error("Résolution @%s impossible : %s", handle, e)
                continue

        try:
            resp = reader.get_users_tweets(
                id=uid,
                max_results=5,
                since_id=state["last_seen"].get(handle),
                exclude=["retweets", "replies"],
                tweet_fields=["created_at", "public_metrics"],
            )
        except Exception as e:
            log.error("Lecture timeline @%s impossible (tier API lecture requis ?) : %s", handle, e)
            continue

        if not resp.data:
            log.info("@%s : aucun nouveau tweet.", handle)
            continue

        state["last_seen"][handle] = str(max(int(t.id) for t in resp.data))

        # Du plus récent au plus vieux, premier candidat frais non traité
        for tweet in sorted(resp.data, key=lambda t: int(t.id), reverse=True):
            if str(tweet.id) in replied:
                continue
            if tweet.created_at and tweet.created_at < cutoff:
                log.info("@%s %s : trop vieux (%s), skip.", handle, tweet.id, tweet.created_at)
                continue

            safe_text = tweet.text.replace('"', "'").replace("{", "").replace("}", "")

            # Pré-filtre économique : le tweet recoupe-t-il un sujet du briefing ?
            try:
                screen = claude.messages.create(
                    model=SCREEN_MODEL,
                    max_tokens=5,
                    messages=[{"role": "user", "content": SCREEN_PROMPT.format(
                        topics=topics, tweet_text=safe_text)}],
                )
                if "OUI" not in screen.content[0].text.strip().upper():
                    log.info("@%s %s : hors sujets du briefing (pré-filtre). Tweet : %s",
                             handle, tweet.id, tweet.text[:90])
                    continue
            except Exception as e:
                log.error("Erreur pré-filtre : %s", e)
                continue

            try:
                msg = claude.messages.create(
                    model=MODEL,
                    max_tokens=200,
                    messages=[{"role": "user", "content": PROMPT.format(
                        handle=handle, tweet_text=safe_text, briefing=briefing)}],
                )
                reply_text = msg.content[0].text.strip().strip('"')
            except Exception as e:
                log.error("Erreur Claude : %s", e)
                continue

            if reply_text.upper().startswith("SKIP") or len(reply_text) < 30:
                log.info("@%s %s : SKIP (pas de fait pertinent). Tweet : %s",
                         handle, tweet.id, tweet.text[:90])
                continue
            if len(reply_text) > 275:
                log.info("Reply trop long (%d chars), skip.", len(reply_text))
                continue

            log.info("Tweet @%s : %s", handle, tweet.text[:120])
            log.info("Reply (%d chars) : %s", len(reply_text), reply_text)

            if dry_run:
                log.info("[DRY_RUN] Non posté.")
                # En dry run on continue d'évaluer les autres tweets frais pour la démo
                continue
            else:
                try:
                    posted = tw.create_tweet(text=reply_text, in_reply_to_tweet_id=int(tweet.id))
                    log.info("Posté : https://x.com/PrestoPodcast/status/%s", posted.data["id"])
                    replied.add(str(tweet.id))
                    state["replied"] = list(replied)[-300:]
                    state["daily"][today()] = daily_count(state) + 1
                    keep = {(datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
                            for d in range(3)}
                    state["daily"] = {k: v for k, v in state["daily"].items() if k in keep}
                    save_state(state)
                    time.sleep(3)
                except Exception as e:
                    log.error("Erreur posting : %s", e)

            # Un seul reply posté par run
            save_state(state)
            return

    # En dry run, ne rien persister : le vrai run doit revoir les mêmes tweets
    if not dry_run:
        save_state(state)
    log.info("Run terminé sans reply posté.")


if __name__ == "__main__":
    main()
