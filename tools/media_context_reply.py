"""
Veille médias → brouillons de replies factuels envoyés par courriel.

X a fermé les replies programmatiques en février 2026 : `POST /2/tweets` refuse
tout reply sous un post dont on n'est ni l'auteur ni mentionné (403
`not-authorized-for-resource`), sur tous les tiers sauf Enterprise. Le bot ne
poste donc plus lui-même. Il fait le travail de détection et de rédaction, et
envoie un courriel avec un lien d'intention X pré-rempli : un tap et la fenêtre
de réponse s'ouvre déjà remplie sous le tweet du média.

Sélectivité : chaque candidat est noté de 0 à 10 sur la FORCE du contraste
factuel entre le cadrage du tweet et ce que disent les faits. Seuls les
MIN_SCORE et plus déclenchent un courriel, avec un plafond quotidien. Le but
est d'en recevoir peu et que chacun vaille la peine d'être posté.

State persisté dans data/media_reply_state.json.
"""

import json
import logging
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import quote

import anthropic
import tweepy
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from refresh_facts import load_fresh_facts  # noqa: E402  (lecteur partagé)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / "data" / "media_reply_state.json"

HANDLES = [h.strip() for h in os.getenv("MEDIA_WATCH_HANDLES", "JdeMontreal").split(",") if h.strip()]
MAX_EMAILS_PER_DAY = int(os.getenv("MAX_EMAILS_PER_DAY", "3"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "7"))
FRESH_MINUTES = int(os.getenv("FRESH_MINUTES", "120"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
SCREEN_MODEL = os.getenv("SCREEN_MODEL", "claude-haiku-4-5-20251001")
FRESH_MAX_AGE_H = int(os.getenv("FRESH_MAX_AGE_H", "12"))

SCREEN_PROMPT = """\
Sujets couverts par le briefing d'actualité de ce matin :
{topics}

Tweet d'un média : \"\"\"{tweet_text}\"\"\"

Ce tweet porte-t-il directement sur un des sujets couverts ci-dessus ?
Réponds uniquement OUI ou NON."""

PROMPT = """\
Tu évalues pour @PrestoPodcast, un briefing d'actualité québécois strictement factuel.
Un grand média vient de publier ce tweet :

Compte : @{handle}
Tweet : \"\"\"{tweet_text}\"\"\"

Voici les faits vérifiés dont tu disposes (ta SEULE source autorisée) :
\"\"\"{briefing}\"\"\"

ÉTAPE 1 — Note de 0 à 10 la FORCE du contraste entre le cadrage du tweet et les faits :
- 9-10 : les faits contredisent directement une affirmation ou une conclusion
  clairement suggérée par le tweet.
- 7-8 : le tweet omet un fait décisif (chiffre, date, condition, déclaration
  sourcée) qui change la lecture qu'un lecteur fait de la nouvelle.
- 4-6 : les faits ajoutent du contexte utile, mais le tweet reste honnête.
- 1-3 : même sujet, aucune tension réelle.
- 0 : aucun fait pertinent, ou catégorie exclue.

Note 0 D'OFFICE si : fait divers, drame humain (décès, accident, violence contre
une personne), sport, divertissement, ou si le tweet est factuellement complet.
Sois sévère. Un 7 doit être rare. Dans le doute, note plus bas.

ÉTAPE 2 — Si et seulement si la note est {min_score} ou plus, rédige la réponse.

RÈGLES DE RÉDACTION :
- Maximum 270 caractères, français québécois.
- Le fait qui mord EN PREMIER. Aucun préambule, ne commence jamais par
  "Contexte" ni par "À noter". Le chiffre ou la déclaration ouvre la réponse.
- La source nommée ensuite ("Selon Reuters", "D'après la BBC").
- Ne cite JAMAIS @{handle} comme source : lui resservir son propre article ne
  dit rien. Une autre source, ou aucune si le fait vient de lui.
- Uniquement des faits présents ci-dessus. Aucune invention, aucune extrapolation.
- Le contraste doit se passer de commentaire. Tu poses le fait, le lecteur
  conclut. N'attaque jamais le média ni l'auteur, n'écris jamais "faux",
  "mensonge", "désinformation", "biais", "clickbait", "orienté".
- Ton sec. Pas d'emoji, pas de hashtag, pas de lien, pas de point
  d'exclamation, aucun tiret long.

Réponds UNIQUEMENT avec un objet JSON, sans texte autour :
{{"score": <entier 0-10>, "faille": "<en 10 mots, ce que le tweet omet ou déforme>", "reply": "<le texte du reply, ou une chaîne vide si score insuffisant>"}}
"""


def _msg_text(msg) -> str:
    """Extrait le bloc texte d'une réponse Claude (ignore les blocs de réflexion)."""
    for block in msg.content:
        if getattr(block, "type", "") == "text":
            return block.text.strip()
    return ""


def _parse_json(raw: str) -> dict | None:
    """Le modèle enrobe parfois le JSON dans un bloc de code."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def load_state() -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {}
    state.setdefault("user_ids", {})
    state.setdefault("last_seen", {})
    state.setdefault("drafted", [])
    state.setdefault("daily", {})
    return state


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


def intent_url(tweet_id: str, reply_text: str) -> str:
    """Lien d'intention officiel X : ouvre la fenêtre de réponse pré-remplie."""
    return (f"https://x.com/intent/post?in_reply_to={tweet_id}"
            f"&text={quote(reply_text, safe='')}")


def build_email(handle: str, tweet, reply_text: str, score: int, faille: str) -> str:
    tweet_url = f"https://x.com/{handle}/status/{tweet.id}"
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;
            margin:0 auto;padding:20px;color:#111">
  <p style="margin:0 0 4px;font-size:13px;color:#666">
    Contraste factuel <strong style="color:#111">{score}/10</strong> &middot; @{handle}
  </p>
  <p style="margin:0 0 18px;font-size:15px;color:#444">{escape(faille)}</p>

  <div style="border-left:3px solid #ddd;padding:2px 0 2px 14px;margin:0 0 18px">
    <p style="margin:0;font-size:15px;line-height:1.45;color:#555">{escape(tweet.text)}</p>
    <p style="margin:8px 0 0">
      <a href="{tweet_url}" style="font-size:13px;color:#888">voir le tweet original</a>
    </p>
  </div>

  <p style="margin:0 0 6px;font-size:13px;color:#666">Réponse proposée
     ({len(reply_text)} caractères)</p>
  <div style="background:#f6f8fa;border-radius:10px;padding:16px;margin:0 0 22px">
    <p style="margin:0;font-size:17px;line-height:1.5">{escape(reply_text)}</p>
  </div>

  <a href="{escape(intent_url(str(tweet.id), reply_text))}"
     style="display:block;background:#000;color:#fff;text-decoration:none;
            text-align:center;padding:15px;border-radius:999px;font-size:16px;
            font-weight:600">Ouvrir la réponse sur X</a>
  <p style="margin:14px 0 0;font-size:12px;color:#999;text-align:center">
    La fenêtre s'ouvre pré-remplie sous le tweet. Relis, ajuste, poste.
  </p>
</div>"""


def send_email(subject: str, html: str):
    user = os.environ["SMTP_USER"].strip()
    # Google affiche le mot de passe d'application en 4 groupes de 4 : on retire
    # les espaces, sinon le login échoue. Ce projet s'est déjà fait avoir 3 fois
    # par un secret collé avec des blancs parasites.
    password = os.environ["SMTP_PASSWORD"].replace(" ", "").strip()
    recipient = (os.getenv("ALERT_EMAIL") or user).strip()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.set_content("Ce courriel nécessite un client HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"),
                          int(os.getenv("SMTP_PORT", "465")), timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    log.info("Courriel envoyé à %s.", recipient)


def build_reader() -> tweepy.Client:
    """Lecture : Bearer App-Only de préférence, sinon OAuth 1.0a en repli.

    .strip() : un secret collé avec un retour de ligne casse la signature OAuth.
    """
    bearer = (os.getenv("TWITTER_BEARER_TOKEN") or "").strip()
    if bearer:
        return tweepy.Client(bearer_token=bearer, wait_on_rate_limit=True)
    keys = ["TWITTER_API_KEY", "TWITTER_API_SECRET",
            "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET"]
    if not all(os.getenv(k) for k in keys):
        raise RuntimeError("Aucun credential de lecture X (ni Bearer, ni OAuth 1.0a).")
    return tweepy.Client(
        consumer_key=os.environ[keys[0]].strip(),
        consumer_secret=os.environ[keys[1]].strip(),
        access_token=os.environ[keys[2]].strip(),
        access_token_secret=os.environ[keys[3]].strip(),
        wait_on_rate_limit=True,
    )


def main():
    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")

    # Échec immédiat si le courriel n'est pas configuré : autrement la faute ne
    # se révélerait qu'au premier candidat retenu, potentiellement des jours plus tard.
    if not dry_run:
        missing = [k for k in ("SMTP_USER", "SMTP_PASSWORD") if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"Secrets manquants : {', '.join(missing)}")

    state = load_state()

    if daily_count(state) >= MAX_EMAILS_PER_DAY:
        log.info("Plafond quotidien atteint (%d courriels). Rien à faire.", MAX_EMAILS_PER_DAY)
        return

    briefing = load_briefing()
    if not briefing:
        log.warning("Aucun briefing disponible, skip.")
        return
    topics = load_topics()

    # Faits de la journée : élargit la base au-delà du briefing du matin
    fresh = load_fresh_facts(max_age_h=FRESH_MAX_AGE_H, log=log.info)
    if fresh:
        log.info("%d fait(s) frais chargé(s) en plus du briefing.", len(fresh))
        briefing += "\n\nDéveloppements plus récents de la journée :\n" + "\n".join(
            f"- {f['fait']}" for f in fresh)
        topics += "\n" + "\n".join(f"- {f['sujet']} : {f['fait']}" for f in fresh)

    reader = build_reader()
    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    drafted = set(state["drafted"])
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
            log.error("Lecture timeline @%s impossible : %s", handle, e)
            continue

        if not resp.data:
            log.info("@%s : aucun nouveau tweet.", handle)
            continue

        state["last_seen"][handle] = str(max(int(t.id) for t in resp.data))

        # Du plus récent au plus vieux, premier candidat frais non traité
        for tweet in sorted(resp.data, key=lambda t: int(t.id), reverse=True):
            if str(tweet.id) in drafted:
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
                if "OUI" not in _msg_text(screen).upper():
                    log.info("@%s %s : hors sujets du briefing (pré-filtre). Tweet : %s",
                             handle, tweet.id, tweet.text[:90])
                    continue
            except Exception as e:
                log.error("Erreur pré-filtre : %s", e)
                continue

            try:
                # Réflexion désactivée : sonnet-5 la lance par défaut et elle
                # épuiserait les tokens avant d'écrire le JSON.
                msg = claude.messages.create(
                    model=MODEL,
                    max_tokens=800,
                    thinking={"type": "disabled"},
                    messages=[{"role": "user", "content": PROMPT.format(
                        handle=handle, tweet_text=safe_text, briefing=briefing,
                        min_score=MIN_SCORE)}],
                )
                verdict = _parse_json(_msg_text(msg))
            except Exception as e:
                log.error("Erreur Claude : %s", e)
                continue

            if not verdict:
                log.error("@%s %s : réponse du modèle illisible, skip.", handle, tweet.id)
                continue

            score = int(verdict.get("score", 0) or 0)
            reply_text = (verdict.get("reply") or "").strip().strip('"')
            faille = (verdict.get("faille") or "").strip()

            if score < MIN_SCORE:
                log.info("@%s %s : score %d/10 (< %d). Tweet : %s",
                         handle, tweet.id, score, MIN_SCORE, tweet.text[:80])
                continue
            if len(reply_text) < 30:
                log.info("@%s %s : score %d mais reply vide ou trop court, skip.",
                         handle, tweet.id, score)
                continue
            if len(reply_text) > 275:
                log.info("@%s %s : reply trop long (%d chars), skip.",
                         handle, tweet.id, len(reply_text))
                continue

            log.info("RETENU @%s %s — score %d/10 — %s", handle, tweet.id, score, faille)
            log.info("Tweet : %s", tweet.text[:120])
            log.info("Reply (%d chars) : %s", len(reply_text), reply_text)

            if dry_run:
                log.info("[DRY_RUN] Courriel non envoyé. Lien : %s",
                         intent_url(str(tweet.id), reply_text))
                continue

            try:
                send_email(
                    subject=f"Presto — reply {score}/10 sous @{handle}",
                    html=build_email(handle, tweet, reply_text, score, faille),
                )
            except Exception as e:
                log.error("Erreur envoi courriel : %s", e)
                # Le tweet n'est pas marqué : le prochain run pourra réessayer.
                save_state(state)
                raise

            drafted.add(str(tweet.id))
            state["drafted"] = list(drafted)[-300:]
            state["daily"][today()] = daily_count(state) + 1
            keep = {(datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
                    for d in range(3)}
            state["daily"] = {k: v for k, v in state["daily"].items() if k in keep}
            save_state(state)
            return  # Un seul courriel par run

    # En dry run, ne rien persister : le vrai run doit revoir les mêmes tweets
    if not dry_run:
        save_state(state)
    log.info("Run terminé sans candidat retenu.")


if __name__ == "__main__":
    main()
