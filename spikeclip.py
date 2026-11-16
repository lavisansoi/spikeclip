"""SPIKECLIP — chat-spike auto clipper.

Idea: when a stream's chat suddenly floods, something just happened.
The moment itself started ~20 s earlier. So: find chat spikes, cut
[spike - PRE, spike + POST], make it vertical, burn captions, write an
upload pack, notify.

Usage:
  py spikeclip.py <youtube stream/VOD url> [--clips 5] [--pre 20] [--post 10]

Needs: yt-dlp (python module), ffmpeg on PATH, openai-whisper.
Works on YouTube live replays (chat replay must be on).
"""
import argparse, json, re, subprocess, sys, statistics, collections
from pathlib import Path

import shutil

PY = sys.executable
STOP = set("the a an and or to of is it i you u me my this that in on for lol".split())


def ytdlp(*args):
    """yt-dlp command. YouTube now needs a JS runtime (deno default; node used if that's what exists)
    plus the yt-dlp-ejs package (see requirements.txt)."""
    cmd = [PY, "-m", "yt_dlp", "--no-warnings"]
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    return cmd + list(args)


def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd)[:160], flush=True)
    return subprocess.run(cmd, check=True, **kw)


# ---------- 1. chat ----------
def get_chat(url, work):
    chat = next(work.glob("*.live_chat.json"), None)
    if chat:
        return chat
    run(ytdlp("--skip-download", "--write-subs", "--sub-langs", "live_chat",
              "-o", str(work / "src.%(ext)s"), url))
    chat = next(work.glob("*.live_chat.json"), None)
    if not chat:
        sys.exit("No chat replay found for this video.")
    return chat


def parse_chat(path):
    """-> list of (seconds, text)"""
    msgs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                act = d["replayChatItemAction"]
                t = int(act["videoOffsetTimeMsec"]) / 1000
                for a in act["actions"]:
                    item = a.get("addChatItemAction", {}).get("item", {})
                    r = item.get("liveChatTextMessageRenderer")
                    if not r:
                        continue
                    runs = r.get("message", {}).get("runs", [])
                    txt = "".join(x.get("text") or x.get("emoji", {}).get("shortcuts", [""])[0]
                                  for x in runs)
                    msgs.append((t, txt))
            except (KeyError, ValueError, IndexError):
                continue
    return msgs


# ---------- 2. spikes ----------
def find_spikes(msgs, n, win=5, base=120, gap=60):
    if not msgs:
        return []
    end = int(max(t for t, _ in msgs)) + 1
    per_sec = [0] * end
    for t, _ in msgs:
        if t >= 0:
            per_sec[int(t)] += 1
    roll = [sum(per_sec[max(0, i - win + 1):i + 1]) for i in range(end)]
    scores = []
    for i in range(base, end):
        hist = roll[i - base:i - win]
        med = statistics.median(hist)
        mad = statistics.median(abs(x - med) for x in hist) or 1
        scores.append(((roll[i] - med) / mad, i))
    scores.sort(reverse=True)
    picked = []
    for s, i in scores:
        if all(abs(i - j) > gap for _, j in picked):
            picked.append((s, i))
        if len(picked) == n:
            break
    return sorted(picked, key=lambda x: x[1])


def chat_words(msgs, a, b, k=5):
    c = collections.Counter()
    for t, txt in msgs:
        if a <= t <= b:
            for w in re.findall(r"[A-Za-z0-9:_']{2,}", txt):
                if w.lower() not in STOP:
                    c[w.upper()] += 1
    return [w for w, _ in c.most_common(k)]


# ---------- 3. cut ----------
UA = {"User-Agent": "Mozilla/5.0"}


def _get(u):
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
        return r.read()


def hls_fetch(playlist_url, a, b, dest):
    """Download only the HLS segments covering [a, b]. Returns start time of the first segment.
    (Plain Python HTTP: YouTube stalls ffmpeg's own long-range requests.)"""
    from urllib.parse import urljoin
    text = _get(playlist_url).decode("utf-8", "replace")
    t, dur, init, segs = 0.0, None, None, []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"', line)
            init = urljoin(playlist_url, m.group(1)) if m else None
        elif line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line and not line.startswith("#") and dur is not None:
            if t + dur > a and t < b:
                segs.append((t, urljoin(playlist_url, line)))
            t += dur
            dur = None
    if not segs:
        raise RuntimeError("no HLS segments in range")
    with open(dest, "wb") as f:
        if init:
            f.write(_get(init))
        for _, u in segs:
            f.write(_get(u))
    return segs[0][0]


def cut_section(url, a, b, out, local=None):
    """Download only [a, b] seconds of the stream (never the whole VOD),
    or cut from a local recording (e.g. your own OBS recording of the stream)."""
    if out.exists():
        return
    if local:
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", local,
             "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-c:a", "aac", str(out)])
        return
    # stream replays expose H.264 HLS playlists: fetch just the needed segments
    r = subprocess.run(ytdlp("-g", "-f", "312+234/311+234/231+234/230+233/b[protocol^=m3u8]", url),
                       capture_output=True, text=True, check=True)
    lists = r.stdout.split()
    parts = []
    for k, pl in enumerate(lists):
        part = out.with_suffix(f".part{k}.mp4")
        t0 = hls_fetch(pl, a, b, part)
        parts.append((part, t0))
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for part, t0 in parts:
        cmd += ["-ss", f"{a - t0:.2f}", "-t", f"{b - a:.2f}", "-i", str(part)]
    cmd += ["-map", "0:v:0", "-map", f"{len(parts) - 1}:a:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-c:a", "aac", str(out)]
    run(cmd)
    for part, _ in parts:
        part.unlink(missing_ok=True)


def srt_time(s):
    h, s = divmod(s, 3600); m, s = divmod(s, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int(s % 1 * 1000):03}"


def captions(model, clip, srt):
    r = model.transcribe(str(clip), fp16=False, word_timestamps=True)
    words = [w for seg in r["segments"] for w in seg.get("words", [])]
    lines, i = [], 0
    while i < len(words):           # 3-word punchy captions
        grp = words[i:i + 3]
        lines.append((grp[0]["start"], grp[-1]["end"], " ".join(w["word"].strip() for w in grp)))
        i += 3
    with open(srt, "w", encoding="utf-8") as f:
        for n, (a, b, t) in enumerate(lines, 1):
            f.write(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{t.upper()}\n\n")
    return r["text"].strip()


def vertical(src, srt, out):
    # 9:16 with blurred fill; captions in the lower-middle safe zone
    style = ("FontName=Arial Black,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=70")
    srt_esc = str(srt).replace("\\", "/").replace(":", "\\:")
    subs = f"subtitles='{srt_esc}':force_style='{style}'," if srt.stat().st_size > 0 else ""
    vf = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          "boxblur=30:2,eq=brightness=-0.15[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
          f"{subs}format=yuv420p")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", vf,
         "-c:v", "libx264", "-profile:v", "high", "-crf", "20", "-preset", "medium",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])


# ---------- 4. upload pack + notify ----------
def upload_pack(i, a, b, score, words, text, title_src, path):
    hook = (text.split(".")[0][:60] or "Wait for it...").strip()
    tags = " ".join("#" + w.lower() for w in words[:3] if w.isalnum())
    path.write_text(
        f"CLIP {i:02}\n"
        f"Source time: {srt_time(a)[:8]} - {srt_time(b)[:8]}   (chat spike score {score:.1f})\n"
        f"Chat was spamming: {', '.join(words) or '-'}\n\n"
        f"TITLE / ON-SCREEN HOOK:\n{hook}\n\n"
        f"CAPTION:\nChat went crazy at this moment 😳 {hook}\n"
        f"Credit: {title_src}\n\n"
        f"HASHTAGS:\n#gta6 #gaming #clips #streamer {tags}\n\n"
        f"TRANSCRIPT:\n{text}\n", encoding="utf-8")


def notify(msg):
    print("\a*** " + msg + " ***")
    if sys.platform != "win32":
        return
    ps = ("Add-Type -AssemblyName System.Windows.Forms;$n=New-Object System.Windows.Forms.NotifyIcon;"
          "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
          f"$n.ShowBalloonTip(8000,'SPIKECLIP','{msg}','Info');Start-Sleep 9;$n.Dispose()")
    subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--pre", type=float, default=20)
    ap.add_argument("--post", type=float, default=10)
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--whisper", default="small")
    ap.add_argument("--video", help="local recording of the same stream (skip video download)")
    o = ap.parse_args()

    vid = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", o.url)
    work = Path(o.out) / (vid.group(1) if vid else "job")
    work.mkdir(parents=True, exist_ok=True)

    title = subprocess.run(ytdlp("--print", "%(channel)s - %(title)s", o.url),
                           capture_output=True, text=True, encoding="utf-8").stdout.strip()
    msgs = parse_chat(get_chat(o.url, work))
    print(f"{len(msgs)} chat messages")
    spikes = find_spikes(msgs, o.clips)
    (work / "spikes.json").write_text(json.dumps(
        [{"t": t, "score": round(s, 2)} for s, t in spikes], indent=1))

    model = None
    if not o.no_captions:
        import whisper
        model = whisper.load_model(o.whisper)

    for i, (score, t) in enumerate(spikes, 1):
        a, b = max(0, t - o.pre), t + o.post
        raw = work / f"raw_{i:02}.mp4"
        cut_section(o.url, a, b, raw, o.video)
        srt = work / f"clip_{i:02}.srt"
        text = captions(model, raw, srt) if model else ""
        if not model:
            srt.write_text("", encoding="utf-8")
        vertical(raw, srt, work / f"CLIP_{i:02}.mp4")
        upload_pack(i, a, b, score, chat_words(msgs, t - 5, t + 5), text, title,
                    work / f"CLIP_{i:02}_upload.txt")
        print(f"clip {i} done  ({srt_time(a)[:8]})")

    notify(f"{len(spikes)} clips ready - {title[:40]}")
    print("DONE ->", work.resolve())


if __name__ == "__main__":
    main()
