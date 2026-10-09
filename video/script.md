# Citation Lens explainer: storyboard and voice-over

Target: 1920x1080, 60 fps, about 47 s, with a Kokoro voice-over (`af_heart`, offline, open source).
The on-screen captions carry the message without sound.
The animation is retimed to the narration: each scene lasts as long as its spoken lines, and `PACE` in `index.html` speeds up or slows down inside a scene.
The voice varies its speed per phrase (quicker through lists, slower on the key claims) and pauses between sentences.

| Time (s) | Scene | On screen | Voice-over |
| --- | --- | --- | --- |
| 0.0-6.9 | Hook | "Web search finds pages. Research needs a graph." Logos of Claude Code and Codex. | Web search finds pages. Research needs a graph. Citation Lens gives your coding agent one. |
| 6.9-12.3 | 1. Search wide | A question is typed. Query variants fan out to Semantic Scholar, OpenAlex and arXiv at once; ranked cards come back. | One call searches three scholarly indexes at once, and ranks everything that comes back. |
| 12.3-17.0 | 2. Pick seeds | Six seed papers are picked by the agent. "Your agent stays in charge." | Your agent stays in charge. It picks the papers that define the topic. |
| 17.0-26.9 | 3. Walk the graph | Seeds in a ring. Arrows to prior work (Foundation), from citing papers (Follow-up) and from new work (Recent). Counters: 998 neighbors, 60 ranked cards, 120 links. | Then Lens follows citations both ways, ranking nearly a thousand neighbors into three lanes: foundations, follow-ups, and what's new. |
| 26.9-34.4 | 4. Verify links | "Does Kimi Linear really cite MiniMax-01?" The paper's reference list is scanned, the entry highlighted, a badge appears. 45 claimed, 23 proven. | But a link only counts if the paper's own reference list says so. Forty-five claimed. Twenty-three proven. |
| 34.4-42.5 | 5. Compare | Claude Code against Claude Code plus Citation Lens: verified links 7 to 23, recent relevant papers 14 to 18, tool calls 8 to 5, time 143 s against 173 s. | Same agent, same question. Seven verified links became twenty-three, in five calls instead of eight. A little slower. |
| 42.5-47.3 | End card | Logo, tagline, the two agent logos, the GitHub address. | Citation Lens, for Codex and Claude Code. |

Scene times come from `timeline.js`, which `tts.py` writes from the measured narration.

## What is real in the video

Everything shown comes from one recorded evaluation run of Claude Code on the held-out question "What is new in linear-time sequence modeling?" (`output/evals-v3/heldout-claude-rerun`, attempt `state_space_frontier-r2`):

- the query variants, the cards returned, the six seeds, the titles in each lane, and the counters (998 neighbors, 60 cards, 120 links);
- the reference-list entry `[66] MiniMax et al. "MiniMax-01 ..." arXiv: 2501.08313` found in the Kimi Linear paper;
- the comparison numbers, counted by the independent grader (`evals/grade.py`), which accepts a citation link only if the cited paper appears in the citing paper's reference list.

The small dots around each lane stand for the other neighbors Lens found; they are not individual papers.
The grey bars on the paper sheet stand for the other reference entries.

## Honest framing

- This is one run, chosen because its expansion returned all three lanes.
  The end card states the broader result: across 18 paired runs, Citation Lens verified more citation links in 13 and fewer in 4.
- In the overall order-swapped quality judgement on those same runs, Claude Code alone was preferred more often than Claude Code plus Citation Lens.
  The video claims only what the numbers show: more links that can be verified, and fewer tool calls.
- The time cost (143 s against 173 s) is shown, not hidden.

## Making changes

- Install: `npm install` in `video/`; for the voice, `pip install kokoro soundfile numpy` (Python 3.10-3.12, `espeak-ng` installed).
- Reword or re-pace the voice: edit `narration.json`, then `python tts.py` (writes `narration.wav` and `timeline.js`).
- Another question: `python make_data.py --run RUN --grades GRADES --task NAME --rep N`.
- Render: `node build-assets.mjs` once, then `node render.mjs`; one frame: `node render.mjs --still 14.5 frame.png`.
- Preview with controls: open `index.html` in a browser (Space plays and pauses, arrow keys step one second).
