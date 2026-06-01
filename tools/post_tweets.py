"""
Poste un thread Twitter depuis un fichier data/tweets/YYYY-MM-DD.json.
Usage : py tools/post_tweets.py 2026-05-31
"""

import json
import os
import sys
import time
from pathlib import Path

import tweepy
from dotenv import load_dotenv

# Force UTF-8 sur la sortie console (Windows cp1252 plante sur les emojis/accents)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent.parent / ".env")


def post_thread(tweets: list[str]) -> list[str]:
    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )

    ids = []
    reply_to = None

    for i, text in enumerate(tweets):
        print(f"Tweet {i+1}/{len(tweets)} ({len(text)} chars):\n{text}\n")
        try:
            if reply_to:
                try:
                    response = client.create_tweet(text=text, in_reply_to_tweet_id=reply_to)
                except TypeError:
                    response = client.create_tweet(text=text, reply={"in_reply_to_tweet_id": reply_to})
            else:
                response = client.create_tweet(text=text)
        except Exception as e:
            print(f"ERREUR sur tweet {i+1}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Détails: {e.response.text[:500]}")
            sys.exit(1)

        tweet_id = response.data["id"]
        ids.append(tweet_id)
        reply_to = tweet_id
        print(f"  -> poste: {tweet_id}\n")

        if i < len(tweets) - 1:
            time.sleep(2)

    return ids


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else __import__("datetime").date.today().isoformat()
    json_path = Path(f"data/tweets/{date}.json")

    if not json_path.exists():
        print(f"Fichier introuvable : {json_path}")
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    tweets = data["tweets"]

    print(f"=== Thread du {date} ({len(tweets)} tweets) ===\n")
    ids = post_thread(tweets)
    print(f"Thread publié → https://x.com/prestopodcast/status/{ids[0]}")


if __name__ == "__main__":
    main()
