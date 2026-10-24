"""Build a self-contained HTML review sheet for one gta-clipper run.

One thumbnail every --every seconds of the source video, labelled with its timestamp. Thumbnails whose
instant falls inside a picked clip (start_s <= t < end_s) are outlined in that clip's colour. Each tile
also shows clip.py's highest combined score in the --every seconds after it (from timeline.csv), so
loud/busy stretches that were not picked stand out. Images are embedded as base64, so the HTML is a
single file that opens by double-click. Clicking a tile plays the source video from that time
(relative link to the source, so that part needs the file to stay where it is). Each clip in the table has a play
button that plays the clip file itself in the same player.

Thumbnails assume the source is limited range (16-235) with BT.709 colour, which is what PS5 SDR
captures contain. A truly full-range source would come out with crushed blacks and clipped whites.
Thumbnails are cached in <out>_thumbs; delete that folder to rebuild them.

For a --vlm run (v6) the page also shows every candidate the model judged, vetoed ones included: the contact sheet
it saw, its category and reason, whether it was kept, vetoed or used as filler, and the owner's rating if one
applies (eval.py's rule). Vetoed candidates have no clip file; their button plays the source from the start.
Picks with no rating are listed at the top so the owner can rate them. For a v7 run with --vlm-hint stars each card
also shows the line the model was given; with --vlm-rule stars, the measured stars and whether the rule cancelled
the veto.

Usage:  python make_review.py <video> <run_dir> <out.html> [--every 10] [--width 384]
  e.g.  python make_review.py C:\\AI\\gta\\input\\test1.webm C:\\AI\\gta\\output\\test1\\<run> C:\\AI\\gta\\review\\test1_review.html
"""

import argparse
import base64
import csv
import html
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import eval as ev

CLIP_COLOURS = ["#f5a524", "#22d3ee", "#e879f9", "#a3e635", "#fb7185", "#60a5fa", "#fbbf24", "#34d399"]
SCORE_MAX = 6.0  # combined score is the weighted mean of two z-scores capped at 6


def ts(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def ts1(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def duration(ffprobe, video):
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(video)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def grab(ffmpeg, video, t, width, out):
    # BT.709 limited-range source -> JPEG's BT.601 full range, so thumbnail colours are not shifted.
    vf = f"scale={width}:-2:in_color_matrix=bt709:in_range=tv:out_color_matrix=bt601:out_range=pc"
    if not out.exists():
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                        "-frames:v", "1", "-vf", vf, "-q:v", "4", str(out)], check=True)
    return out


def rel_url(target, from_dir):
    return quote(Path(os.path.relpath(target, from_dir)).as_posix())


def vlm_section(scores, run_dir, out_dir, video_stem):
    """HTML for a --vlm run: the picks that need a rating, then every candidate with the model's verdict."""
    v = scores["vlm"]
    rated = ev.load_ratings(video_stem)
    clips = sorted(scores["clips"], key=lambda c: c["rank"])
    need = [c for c in clips if ev.rating_for(c, rated)[0] is None]
    items = "".join(
        f'<li style="--clip:{CLIP_COLOURS[(c["rank"] - 1) % len(CLIP_COLOURS)]}"><span class="dot"></span>'
        f'clip {c["rank"]} <button class="seek" data-t="{c["start_s"]}">{ts1(c["start_s"])}&ndash;{ts1(c["end_s"])}'
        f'</button> peak {ts1(c["peak_time_s"])}, candidate {c["candidate_rank"]}, '
        f'{html.escape(c["vlm"]["category"] or "model error")}{" (vetoed, used as filler)" if c["vlm"]["filler"] else ""} '
        f'<button class="seek" data-clip="{rel_url(run_dir / c["file"], out_dir)}">play clip</button> '
        f'<code>{html.escape(c["file"])}</code></li>' for c in need)
    need_html = (f"<h2>Picks that need your rating ({len(need)})</h2><ul class=\"need\">{items}</ul>" if need
                 else "<h2>Picks that need your rating</h2><p class=\"meta\">None: every pick has a rating.</p>")
    cards = []
    for c in v["candidates"]:
        m = c["vlm"]
        g, _ = ev.rating_for(c, rated)
        owner = ("unrated" if g is None else f"{'postable' if g['postable'] else 'not postable'}: "
                 f"{html.escape(g['reason'])} <span class=\"meta\">({html.escape(g['file'])})</span>")
        cls = "veto" if m["vetoed"] else "err" if m["error"] else "kept"
        status = m["status"] + (f" &rarr; clip {c['final_rank']}" if c["final_rank"] else "")
        b64 = base64.b64encode((run_dir / c["sheet"]).read_bytes()).decode("ascii")
        what = (f"<b>{html.escape(m['category'])}</b>{' &middot; player stalled' if m['player_stalled'] else ''}"
                f"<br>{html.escape(m['reason'])}" if m["category"] else f"<b>model error</b><br>{html.escape(m['error'])}")
        if "hint_text" in m:
            what += f"<br><span class=\"meta\">Line in the prompt: {html.escape(m['hint_text'])}</span>"
        elif "stars_in_clip_s" in m:
            what += f"<br><span class=\"meta\">Measured stars in the clip: {m['stars_in_clip_s']:g} s</span>"
        if m.get("star_rule_kept"):
            what += " <span class=\"meta\">(the star rule cancelled this veto)</span>"
        cards.append(
            f'<div class="cand {cls}"><img src="data:image/jpeg;base64,{b64}" alt="contact sheet of candidate '
            f'{c["candidate_rank"]}"><div class="info"><div class="cap"><span class="ts">#{c["candidate_rank"]}</span> '
            f'<button class="seek" data-t="{c["start_s"]}">{ts1(c["start_s"])}&ndash;{ts1(c["end_s"])}</button> '
            f'{c["duration_s"]:.1f} s, peak {ts1(c["peak_time_s"])} <span class="status">{status}</span></div>'
            f'<p>{what}</p><p class="meta">Owner: {owner}</p></div></div>')
    calls = [c["vlm"].get("call") or {} for c in v["candidates"] if not c["vlm"]["cached"]]
    mem = v.get("memory") or {}
    mem_txt = (f", model memory {mem['size_bytes'] / 2**30:.2f} GiB ({mem['vram_bytes'] / 2**30:.2f} GiB on the GPU)"
               if mem.get("size_bytes") else "")
    extras = ((" &middot; measured stars in the prompt" if v.get("hint") else "")
              + (f" &middot; star rule: no {v['star_rule']['category']} veto with at least "
                 f"{v['star_rule']['min_stars_s']:g} s of stars ({v['star_rule']['vetoes_cancelled']} cancelled)"
                 if v.get("star_rule") else ""))
    meta = (f"{v['model']} &middot; prompt {html.escape(v['prompt_version'])}{extras} &middot; sheet version "
            f"{v['sheet_version']} &middot; {v['model_calls']} model call(s), {v['cache_hits']} cached, "
            f"{v['model_errors']} error(s) &middot; model step {scores['timing_s'].get('vlm_model', 0):.1f} s"
            f"{mem_txt}{' &middot; <b>thinking text returned</b>' if v['thinking_returned'] else ''}")
    return (f"<section>{need_html}<h2>All {len(v['candidates'])} candidates, as the model saw them</h2>"
            f"<p class=\"meta\">{meta}. Veto categories: {', '.join(v['veto_categories'])}. The model never ranks: "
            f"the first {scores['settings']['count']} candidates not vetoed are the clips, then vetoed ones fill any "
            f"gap. {len(calls)} answer(s) are new in this run.</p><div class=\"cands\">{''.join(cards)}</div></section>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--every", type=float, default=10.0)
    ap.add_argument("--width", type=int, default=384)
    args = ap.parse_args()
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")

    scores = json.loads((args.run_dir / "scores.json").read_text(encoding="utf-8"))
    clips = sorted(scores["clips"], key=lambda c: c["rank"])
    floor = scores["scoring"]["min_peak_score"]
    with open(args.run_dir / "timeline.csv", newline="", encoding="utf-8") as f:
        tl = [(float(r["time_s"]), float(r["combined"])) for r in csv.DictReader(f)]

    dur = duration(ffprobe, args.video)
    times = []
    t = 0.0
    while t < dur - 0.1:
        times.append(t)
        t += args.every

    thumb_dir = args.out.parent / (args.out.stem + "_thumbs")
    thumb_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda t: grab(ffmpeg, args.video, t, args.width,
                                             thumb_dir / f"t{int(t):05d}.jpg"), times))

    out_dir = args.out.parent
    video_url = rel_url(args.video, out_dir)
    tiles, n_in = [], 0
    for t, p in zip(times, paths):
        window = [c for (x, c) in tl if t <= x < t + args.every]
        best = max(window) if window else 0.0
        inside = [c for c in clips if c["start_s"] <= t < c["end_s"]]
        starts = [c for c in clips if t < c["start_s"] < t + args.every and c not in inside]
        cls, style, badge = "tile", "", ""
        if inside:
            c = inside[0]
            n_in += 1
            colour = CLIP_COLOURS[(c["rank"] - 1) % len(CLIP_COLOURS)]
            cls += " in"
            style = f' style="--clip:{colour}"'
            badge = f'<span class="badge">clip {c["rank"]}</span>'
        note = "".join(f'<span class="note" style="--clip:{CLIP_COLOURS[(c["rank"] - 1) % len(CLIP_COLOURS)]}">'
                       f'clip {c["rank"]} starts {ts1(c["start_s"])}</span>' for c in starts)
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        pct = min(best / SCORE_MAX, 1.0) * 100
        hot = " hot" if best >= floor else ""
        tiles.append(
            f'<button class="{cls}"{style} data-t="{t:g}" title="Play source from {ts(t)}">'
            f'<img src="data:image/jpeg;base64,{b64}" alt="frame at {ts(t)}" loading="lazy">'
            f'<span class="cap"><span class="ts">{ts(t)}</span>{badge}'
            f'<span class="sc{hot}">max {best:.2f}</span></span>'
            f'<span class="bar"><i class="{hot.strip()}" style="width:{pct:.1f}%"></i></span>{note}</button>')

    rows = []
    for c in clips:
        colour = CLIP_COLOURS[(c["rank"] - 1) % len(CLIP_COLOURS)]
        clip_url = rel_url(args.run_dir / c["file"], out_dir)
        rows.append(
            f'<tr style="--clip:{colour}"><td><span class="dot"></span>{c["rank"]}</td>'
            f'<td><button class="seek" data-t="{c["start_s"]}">{ts1(c["start_s"])}&ndash;{ts1(c["end_s"])}</button></td>'
            f'<td>{c["duration_s"]:.1f} s</td><td>{ts1(c["peak_time_s"])}</td><td>{c["peak_score"]:.2f}</td>'
            f'<td class="why">{html.escape(c["reason"])}</td>'
            f'<td><button class="seek" data-clip="{clip_url}">play clip</button> '
            f'<a href="{clip_url}">{html.escape(c["file"])}</a></td></tr>')

    info = scores["input_info"]
    st = scores["settings"]
    notes = "".join(f"<p class=\"warn\">NOTE: {html.escape(n)}</p>" for n in scores.get("notes", []))
    page = TEMPLATE.format(
        name=html.escape(args.video.name),
        meta=html.escape(f"{info['width']}x{info['height']} (stream size) · {info['fps']} fps · {ts1(dur)} · "
                         f"{info['video_codec']} · run {args.run_dir.name} · count {st['count']}, "
                         f"len {st['min_len']:g}-{st['max_len']:g} s, gap {st['min_gap']:g} s, "
                         f"encoder {st['encoder']}"),
        every=f"{args.every:g}", n=len(times), n_in=n_in, floor=f"{floor:.1f}",
        floor_pct=f"{floor / SCORE_MAX * 100:.1f}", notes=notes, rows="".join(rows), tiles="".join(tiles),
        video_url=video_url,
        vlm=vlm_section(scores, args.run_dir, out_dir, args.video.stem) if "vlm" in scores else "")
    args.out.write_text(page, encoding="utf-8")
    print(f"{args.out}: {len(times)} thumbnails, {n_in} inside clips, {args.out.stat().st_size / 1e6:.1f} MB")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} review</title>
<style>
:root {{ --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --text:#e6e8ec; --muted:#9aa3b2; --hot:#f97316; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui, "Segoe UI", sans-serif; }}
header, section {{ max-width:1600px; margin:0 auto; padding:16px; }}
h1 {{ font-size:20px; margin:0 0 4px; }}
.meta, .legend {{ color:var(--muted); }}
.warn {{ color:#fbbf24; margin:6px 0 0; }}
table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); }}
th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; font-size:12px; }}
td.why {{ color:var(--muted); font-size:12px; }}
.dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:var(--clip); margin-right:6px; }}
a {{ color:#93c5fd; }}
button.seek {{ font:inherit; color:var(--text); background:none; border:1px solid var(--clip); border-radius:4px;
  padding:1px 6px; cursor:pointer; white-space:nowrap; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:10px; }}
.tile {{ font:inherit; color:inherit; text-align:left; padding:0; background:var(--panel); cursor:pointer;
  border:3px solid transparent; border-radius:6px; overflow:hidden; display:flex; flex-direction:column; }}
.tile:hover {{ border-color:#4b5563; }}
.tile.in {{ border-color:var(--clip); box-shadow:0 0 0 1px var(--clip); }}
.tile img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; background:#000; }}
.cap {{ display:flex; gap:8px; align-items:center; padding:5px 8px 3px; }}
.ts {{ font-weight:700; font-variant-numeric:tabular-nums; }}
.badge {{ background:var(--clip); color:#111; font-weight:700; font-size:12px; padding:0 6px; border-radius:3px; }}
.sc {{ margin-left:auto; color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }}
.sc.hot {{ color:var(--hot); }}
.bar {{ position:relative; height:5px; margin:0 8px 7px; background:#262b35; border-radius:3px; }}
.bar::after {{ content:""; position:absolute; left:{floor_pct}%; top:-2px; bottom:-2px; width:1px; background:#9aa3b2; }}
.bar i {{ display:block; height:100%; background:#64748b; border-radius:3px; }}
.bar i.hot {{ background:var(--hot); }}
.note {{ font-size:12px; color:var(--clip); padding:0 8px 6px; }}
h2 {{ font-size:16px; margin:8px 0; }}
ul.need {{ margin:0 0 8px; padding-left:18px; }}
ul.need li {{ margin:4px 0; }}
.cands {{ display:flex; flex-direction:column; gap:10px; }}
.cand {{ display:grid; grid-template-columns:minmax(0, 768px) minmax(220px, 1fr); gap:12px; background:var(--panel);
  border:1px solid var(--line); border-left:4px solid #64748b; border-radius:6px; padding:8px; --clip:#64748b; }}
.cand.kept {{ border-left-color:#22c55e; }}
.cand.veto {{ border-left-color:#ef4444; }}
.cand.err {{ border-left-color:#fbbf24; }}
.cand img {{ width:100%; display:block; border-radius:4px; }}
.cand .cap {{ padding:0 0 4px; flex-wrap:wrap; }}
.cand p {{ margin:4px 0; }}
.status {{ margin-left:auto; font-weight:700; }}
.cand.veto .status {{ color:#f87171; }}
.cand.kept .status {{ color:#4ade80; }}
.cand.err .status {{ color:#fbbf24; }}
@media (max-width:900px) {{ .cand {{ grid-template-columns:1fr; }} }}
#player {{ position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center;
  justify-content:center; flex-direction:column; gap:8px; z-index:10; padding:16px; }}
#player.open {{ display:flex; }}
#player video {{ max-width:min(1280px, 100%); max-height:80vh; background:#000; }}
#player p {{ color:var(--muted); margin:0; }}
#player button {{ font:inherit; color:var(--text); background:var(--panel); border:1px solid var(--line);
  border-radius:4px; padding:4px 12px; cursor:pointer; }}
</style></head><body>
<header>
<h1>{name}</h1>
<div class="meta">{meta}</div>
<p class="legend">{n} thumbnails, one every {every} s; {n_in} fall inside a picked clip (coloured outline).
"max" under each tile is clip.py's highest combined score (0&ndash;6) in the {every} s after that thumbnail; orange
means it reaches clip.py's {floor} floor, and the tick on each bar marks that floor. Click a tile or a clip time to play
the source from there, or "play clip" to watch the clip as cut; Esc closes the player.</p>
{notes}
</header>
<section>
<table><thead><tr><th>#</th><th>Source time</th><th>Length</th><th>Peak</th><th>Score</th><th>Why picked (scores.json)</th>
<th>File</th></tr></thead><tbody>{rows}</tbody></table>
</section>
{vlm}
<section><div class="grid">{tiles}</div></section>
<div id="player" role="dialog" aria-label="Source video player">
<video id="vid" controls preload="none"></video>
<p id="vidmsg"></p>
<button id="close">Close (Esc)</button>
</div>
<script>
const player = document.getElementById("player"), vid = document.getElementById("vid"),
  msg = document.getElementById("vidmsg");
function play(src, t, what) {{
  msg.textContent = what + " (" + decodeURIComponent(src) + "). If nothing plays, the file has been moved.";
  player.classList.add("open");
  const go = () => {{ vid.currentTime = t; vid.play().catch(() => {{}}); }};
  if (vid.getAttribute("src") === src && vid.readyState >= 1) {{ go(); return; }}
  vid.addEventListener("loadedmetadata", go, {{ once: true }});
  vid.src = src;
}}
function closePlayer() {{ vid.pause(); player.classList.remove("open"); }}
document.querySelectorAll("[data-t]").forEach(el => el.addEventListener("click",
  () => play("{video_url}", parseFloat(el.dataset.t), "Source video")));
document.querySelectorAll("[data-clip]").forEach(el => el.addEventListener("click",
  () => play(el.dataset.clip, 0, "Clip as cut")));
document.getElementById("close").addEventListener("click", closePlayer);
player.addEventListener("click", e => {{ if (e.target === player) closePlayer(); }});
document.addEventListener("keydown", e => {{ if (e.key === "Escape") closePlayer(); }});
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
