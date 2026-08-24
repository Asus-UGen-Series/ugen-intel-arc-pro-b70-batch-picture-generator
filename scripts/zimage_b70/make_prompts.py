#!/usr/bin/env python3
"""Create deterministic, unique prompts for a visual-search stress dataset."""

import argparse
import itertools
import random
from pathlib import Path


SUBJECTS = [
    "a red sports car", "a blue city bicycle", "a yellow school bus", "a silver wristwatch",
    "a green hiking backpack", "a ceramic coffee mug", "a golden retriever", "a black cat",
    "a white running shoe", "a wooden dining chair", "a vintage camera", "a bowl of ramen",
    "a glass perfume bottle", "a tropical houseplant", "a passenger airplane", "a cargo ship",
    "a red umbrella", "a violin", "a desktop computer", "a chocolate birthday cake", "an oscilloscope", 
    "digital calipers", "intricate circuit board"
]
SCENES = [
    "in a clean photo studio", "on a busy Tokyo street", "on a quiet beach", "inside a modern apartment",
    "in a snowy mountain village", "at an international airport", "in a dense green forest",
    "at an outdoor market", "inside a warehouse", "on a marble museum pedestal", "in a laboratory"
]
VIEWS = [
    "front view", "rear view", "side profile", "three-quarter view", "top-down view",
    "low-angle view", "close-up detail", "wide establishing shot",
]
LIGHTING = [
    "soft daylight", "golden-hour sunlight", "overcast natural light", "dramatic studio lighting",
    "neon night lighting", "hard midday sunlight", "soft window light", "cinematic rim lighting",
]
STYLES = [
    "photorealistic product photography", "editorial photography", "documentary photography",
    "minimal commercial photography", "cinematic photography", "macro photography",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "prompts_10000.txt")
    parser.add_argument("--shuffle-seed", type=int, default=70)
    args = parser.parse_args()

    combinations = list(itertools.product(SUBJECTS, SCENES, VIEWS, LIGHTING, STYLES))
    if args.count > len(combinations):
        raise SystemExit(f"Maximum unique prompts: {len(combinations)}")
    random.Random(args.shuffle_seed).shuffle(combinations)
    prompts = [f"{s}, {scene}, {view}, {light}, {style}, highly detailed" for s, scene, view, light, style in combinations[:args.count]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    print(f"Wrote {len(prompts)} unique prompts to {args.output}")


if __name__ == "__main__":
    main()
