#!/usr/bin/env python3
"""Record the narration with Kokoro (open-source neural TTS, runs offline) and time the video to it.

Writes narration.wav and timeline.js. Each scene lasts as long as its spoken line needs (plus a
short lead and tail), but never less than min_stretch times its designed length, so the pace stays
relaxed. index.html warps its animation to these scene boundaries.

    pip install kokoro soundfile numpy
    python video/tts.py
"""

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

HERE = Path(__file__).resolve().parent
RATE = 24000


def trim(audio, edge=0.05, floor=0.012):
    """Cut the silence the model leaves around every utterance; keep a short breath at each end."""
    voiced = np.where(np.abs(audio) > floor)[0]
    if not len(voiced):
        return audio
    pad = int(edge * RATE)
    return audio[max(voiced[0] - pad, 0) : voiced[-1] + pad]


def speak(pipeline, phrases, voice):
    """Each phrase has its own speaking rate and pause, so the delivery speeds up and slows down."""
    parts = []
    for phrase in phrases:
        audio = np.concatenate(
            [
                np.asarray(a, dtype=np.float32)
                for _g, _p, a in pipeline(phrase["text"], voice=voice, speed=phrase["speed"])
            ]
        )
        parts += [trim(audio), np.zeros(int(max(phrase["pause"], 0.08) * RATE), dtype=np.float32)]
    return np.concatenate(parts[:-1]) if parts else np.zeros(0, dtype=np.float32)


def main():
    cfg = json.loads((HERE / "narration.json").read_text())
    pipeline = KPipeline(lang_code="a")
    clips = [speak(pipeline, s["phrases"], cfg["voice"]) for s in cfg["scenes"]]
    bounds, starts, cursor = [0.0], [], 0.0
    for clip, design in zip(clips, cfg["design"], strict=True):
        spoken = len(clip) / RATE
        length = max(cfg["lead"] + spoken + cfg["tail"], cfg["min_stretch"] * design)
        starts.append(cursor + cfg["lead"])
        cursor += length
        bounds.append(round(cursor, 3))
    duration = bounds[-1] + cfg["end_hold"]
    track = np.zeros(int(duration * RATE) + RATE, dtype=np.float32)
    for clip, start in zip(clips, starts, strict=True):
        track[int(start * RATE) : int(start * RATE) + len(clip)] += clip
    peak = float(np.max(np.abs(track))) or 1.0
    sf.write(HERE / "narration.wav", track[: int(duration * RATE)] * (0.89 / peak), RATE)
    bounds[-1] = round(duration, 3)
    timeline = {
        "duration": round(duration, 3),
        "bounds": bounds,
        "spoken": [round(len(c) / RATE, 2) for c in clips],
    }
    (HERE / "timeline.js").write_text("window.TIMELINE = " + json.dumps(timeline) + ";\n")
    print(json.dumps(timeline))


if __name__ == "__main__":
    main()
