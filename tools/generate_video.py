#!/usr/bin/env python3
"""
generate_video.py — Combine artwork + audio + subtitles into an MP4 for YouTube.

Usage:
    python tools/generate_video.py <artwork.jpg> <audio.mp3> <subtitles.srt> <output.mp4>
"""

import subprocess
import sys
from pathlib import Path


def generate_video(
    artwork: Path,
    audio: Path,
    subtitles: Path,
    output: Path,
) -> None:
    # Build subtitle filter style string. DejaVu Sans is preinstalled on the
    # GitHub runner (fonts-dejavu-core) and renders accents correctly.
    # libass force_style fields are separated by COMMAS, not colons. Colons
    # collide with the subtitles filter's own option separator and trigger
    # "Error applying option 'FontSize': Option not found".
    subtitle_style = (
        "FontName=DejaVu Sans"
        ",FontSize=30"
        ",PrimaryColour=&H00FFFFFF"
        ",OutlineColour=&H00000000"
        ",Outline=2"
        ",Shadow=1"
        ",Alignment=2"
        ",MarginV=60"
    )

    # The ffmpeg `subtitles` filter is notoriously fragile with absolute paths
    # (quoting/escaping of `:`, `\`, spaces differs per platform and breaks the
    # filtergraph parser). The bulletproof fix: run ffmpeg with cwd set to the
    # SRT's directory and reference it by bare filename — no path, no quoting.
    srt_dir = subtitles.resolve().parent
    srt_name = subtitles.name

    # Build the video filter chain
    vf = (
        f"scale=1920:1080:force_original_aspect_ratio=increase,"
        f"crop=1920:1080,"
        f"colorlevels=rimin=0:gimin=0:bimin=0:rimax=0.6:gimax=0.6:bimax=0.6,"
        f"subtitles={srt_name}:force_style='{subtitle_style}'"
    )

    cmd = [
        "ffmpeg",
        "-y",                          # overwrite output without prompting
        "-loop", "1",                  # loop the still image
        "-i", str(artwork.resolve()),  # input 0: artwork
        "-i", str(audio.resolve()),    # input 1: audio
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",                   # stop when the shortest stream ends
        str(output.resolve()),
    ]

    print("Running ffmpeg command:")
    print(" ".join(cmd))
    print(f"(cwd: {srt_dir})")
    print()

    with subprocess.Popen(
        cmd,
        cwd=str(srt_dir),
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        for line in proc.stderr:
            print(line, end="", flush=True)
        proc.wait()

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)

    print(f"\nVideo written to: {output.resolve()}")


def main() -> None:
    if len(sys.argv) != 5:
        print(
            "Usage: python tools/generate_video.py "
            "<artwork.jpg> <audio.mp3> <subtitles.srt> <output.mp4>",
            file=sys.stderr,
        )
        sys.exit(1)

    artwork = Path(sys.argv[1])
    audio = Path(sys.argv[2])
    subtitles = Path(sys.argv[3])
    output = Path(sys.argv[4])

    for path, label in [(artwork, "artwork"), (audio, "audio"), (subtitles, "subtitles")]:
        if not path.exists():
            print(f"Error: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)

    generate_video(artwork, audio, subtitles, output)


if __name__ == "__main__":
    main()
