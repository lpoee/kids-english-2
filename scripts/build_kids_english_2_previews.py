#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARDS_PATH = ROOT / "data" / "kids_english_2_cards.json"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_from_gif(source: Path, target: Path, poster: Path) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "3",
            "-i",
            str(source),
            "-t",
            "3.4",
            "-vf",
            "fps=24,scale=720:480:force_original_aspect_ratio=decrease,pad=720:480:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )
    if not poster.exists():
        run(["ffmpeg", "-y", "-i", str(source), "-frames:v", "1", str(poster)])


def build_from_image(source: Path, target: Path) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-t",
            "3.4",
            "-i",
            str(source),
            "-vf",
            "scale=760:506:force_original_aspect_ratio=increase,crop=720:480,zoompan=z='min(zoom+0.0008,1.05)':d=82:s=720x480:fps=24,format=yuv420p",
            "-frames:v",
            "82",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )


def main() -> int:
    data = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
    for card in data["cards"]:
        target = ROOT / card["video"]
        source = ROOT / card["source_preview"]
        poster = ROOT / card["poster"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            raise FileNotFoundError(f"Missing preview source for {card['slug']}: {source}")
        if source.suffix.lower() == ".gif":
            build_from_gif(source, target, poster)
        else:
            build_from_image(source, target)
        print(f"built {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
