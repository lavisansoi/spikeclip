# SPIKECLIP

**When chat explodes, something just happened. SPIKECLIP clips it.**

Give it a YouTube stream link. It:

1. Downloads the stream's **chat replay**.
2. Finds the **chat spikes**: seconds where messages jump far above normal.
3. Cuts each moment from **20 s before** the spike to 10 s after
   (the moment always starts before chat reacts). Only those seconds are downloaded.
4. Makes it **vertical 9:16** with a blurred fill and burns in **punchy captions** (Whisper, runs locally, free).
5. Writes an **upload pack** per clip: hook title, caption, hashtags, credit, what chat was spamming.
6. **Notifies** you when the clips are ready.

## Fastest way: one prompt

Paste this into Claude Code:

```
Clip this stream for me using github.com/lavisansoi/spikeclip (follow its README): <PASTE YOUTUBE STREAM LINK>
```

## Instructions for Claude Code (the agent running this)

1. `git clone https://github.com/lavisansoi/spikeclip` and `cd spikeclip`.
2. Create a virtual env in the repo (`python -m venv .venv`) and install:
   `.venv` python `-m pip install -r requirements.txt`.
3. Check `ffmpeg` and a JS runtime (`deno` or `node`) are on PATH. If one is missing,
   give the user the exact install command for their OS and wait.
4. Run `spikeclip.py "<link>" --clips 5` with the venv python. The chat download can take
   several minutes on long streams; keep the user posted.
5. When done, list `out/<video id>/`: show each `CLIP_XX.mp4` with its `CLIP_XX_upload.txt`,
   best (highest spike score) first.

## Manual

```
pip install -r requirements.txt     # also needs ffmpeg + deno or node on PATH
python spikeclip.py "https://www.youtube.com/watch?v=VIDEO_ID" --clips 5
```

Options:

| flag | default | what |
|---|---|---|
| `--clips` | 5 | how many moments |
| `--pre` | 20 | seconds before the spike |
| `--post` | 10 | seconds after the spike |
| `--whisper` | small | caption model (`base` = faster, `medium` = better) |
| `--video FILE` | | cut from your own recording of the stream instead of downloading |
| `--no-captions` | | skip captions |

Output: `out/<video id>/CLIP_01.mp4`, `CLIP_01_upload.txt`, …

## Timing (2-hour stream, busy chat)

- Chat download: ~9 min (the slow part)
- Spike detection: seconds
- Per clip (cut + captions + vertical): ~1-2 min on CPU

## Notes

- Works on YouTube streams with chat replay turned on.
- Only post clips you have the right to post, for example through official clipping
  campaigns. Add your own voice or captions; raw re-uploads usually can't be monetized.
