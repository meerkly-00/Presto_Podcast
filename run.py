#!/usr/bin/env python3
"""
Point d'entrée CLI du pipeline de briefing matinal.

Utilisation :
    python run.py                      # Briefing complet du jour
    python run.py --dry-run            # Agrégation seulement, sans génération ni TTS
    python run.py --no-tts             # Script généré, sans audio
    python run.py --no-feed            # Script + audio, sans mise à jour du feed RSS
    python run.py --date 2026-05-23    # Briefing pour une date spécifique
    python run.py --duree 15           # Cible 15 minutes
    python run.py --serve              # Démarre un serveur HTTP local pour tester le feed
"""

import argparse
import http.server
import logging
import os
import sys
import threading
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)


def _serve(directory: str, port: int = 8000) -> None:
    os.chdir(directory)
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *a: None  # Silence les logs HTTP

    with http.server.HTTPServer(("", port), handler) as httpd:
        print(f"\nServeur démarré sur http://localhost:{port}")
        print(f"Feed RSS :       http://localhost:{port}/output/feed.xml")
        print(f"Fichiers audio : http://localhost:{port}/output/audio/")
        print("Ctrl-C pour arrêter.\n")
        httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline de briefing matinal automatisé.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", help="Date au format YYYY-MM-DD (défaut : aujourd'hui)")
    parser.add_argument("--duree", type=int, help="Durée cible en minutes (défaut : 12)")
    parser.add_argument("--fenetre", type=int, help="Fenêtre d'agrégation en heures (défaut : 24)")
    parser.add_argument("--no-tts", action="store_true", help="Ne pas générer l'audio")
    parser.add_argument("--no-feed", action="store_true", help="Ne pas mettre à jour le feed RSS")
    parser.add_argument("--dry-run", action="store_true", help="Agrégation seulement, sans API LLM ni TTS")
    parser.add_argument("--serve", action="store_true", help="Démarre un serveur HTTP local")
    parser.add_argument("--port", type=int, default=8000, help="Port du serveur HTTP (défaut : 8000)")
    args = parser.parse_args()

    if args.serve:
        project_root = os.path.dirname(os.path.abspath(__file__))
        _serve(project_root, args.port)
        return

    date = None
    if args.date:
        try:
            date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Erreur : format de date invalide '{args.date}'. Utilise YYYY-MM-DD.")
            sys.exit(1)

    # Import ici pour que --serve fonctionne sans les dépendances installées
    from src.pipeline import run

    result = run(
        date=date,
        duree_cible=args.duree,
        since_hours=args.fenetre,
        skip_tts=args.no_tts,
        skip_feed=args.no_feed,
        dry_run=args.dry_run,
    )

    print("\n=== Résultat ===")
    for key, val in result.items():
        if key == "articles_xml":
            print(f"  articles_xml      : {len(val)} caractères")
        elif key == "articles_xml_len":
            print(f"  articles_xml_len  : {val} caractères")
        else:
            print(f"  {key:<18}: {val}")


if __name__ == "__main__":
    main()
