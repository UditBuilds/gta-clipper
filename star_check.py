"""Build a page to check the indexer's wanted-star count by eye (PR 21, session 1).

For each star count asked for, it takes moments the index says show that many stars, grabs the frame the count was
read from, and writes C:\\AI\\gta\\library\\star_check.html: each frame large, a zoomed strip of the star corner
under it, and the index's count under that. Nothing is changed in the library; the frames go to star_check\\.

"The index says" = index.hud_timeline(): the count per half second, from the HUD samples saved in hud.json.
Moments are spread evenly over all the half seconds with that count, and only steady ones are used (the same
count half a second before and after), so a frame caught while the stars change is not shown.

Usage:  python star_check.py <video name> [<video name> ...] --want 3:6 4:6 1:3 2:3 [--fallback <video name> ...]
  --want COUNT:HOW_MANY, in the order they should appear. Video names are library folder names.
  --fallback: recordings used only for a count the main ones don't have enough of.
"""

import argparse
import html
import shutil
import subprocess
import sys
from pathlib import Path

import clip
import index

MARGIN = 24   # profile pixels shown round the stars box in the zoomed strip
ZOOM = 4
FULL_W = 1280


def hms(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def candidates(library, stem):
    """(index dict, {count: [seconds]}) for one recording: the steady half seconds of each star count."""
    idx = index.read_json(library / stem / "index.json")
    saved = index.read_json(library / stem / "hud.json")
    if idx is None or saved is None:
        sys.exit(f"ERROR: {stem} is not in {library} (no index.json or hud.json)")
    prof = clip.load_profile(idx["profile"]["name"])
    tl = index.hud_timeline([tuple(s) for s in saved["samples"]], idx["video"]["duration_s"], prof)
    by = {}
    for i in range(1, tl["n"] - 1):
        c = tl["count"][i]
        if c and tl["count"][i - 1] == c == tl["count"][i + 1]:
            by.setdefault(c, []).append(i * clip.BUCKET)
    return idx, prof, by


def spread(items, n):
    """n items spread evenly over the list (all of them if there are fewer)."""
    if len(items) <= n:
        return list(items)
    return [items[round((k + 0.5) * len(items) / n - 0.5)] for k in range(n)]


def grab(ffmpeg, ffprobe, video, t, prof, full, strip):
    """The frame at t as a JPEG (full), and the star corner zoomed (strip)."""
    info = clip.probe(ffprobe, video)
    conv = ("in_color_matrix=bt709:in_range=tv:" if clip.needs_range_fix(info["video_codec"], info["color_range"])
            else "")
    c = info["container_crop"]
    sx = (info["width"] - c["left"] - c["right"]) / prof.frame[0]
    sy = (info["height"] - c["top"] - c["bottom"]) / prof.frame[1]
    x, y, w, h = prof.star_box
    x0, y0 = max(0, x - MARGIN), max(0, y - MARGIN)
    x1, y1 = min(prof.frame[0], x + w + MARGIN), min(prof.frame[1], y + h + MARGIN)
    graph = (f"[0:v]split=2[a][b];[a]scale={FULL_W}:-2:{conv}out_color_matrix=bt601:out_range=pc[full];"
             f"[b]crop={round((x1 - x0) * sx)}:{round((y1 - y0) * sy)}:{round(x0 * sx)}:{round(y0 * sy)},"
             f"scale={(x1 - x0) * ZOOM}:{(y1 - y0) * ZOOM}:flags=neighbor:{conv}out_color_matrix=bt601:out_range=pc"
             f"[strip]")
    done = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                           "-filter_complex", graph, "-map", "[full]", "-frames:v", "1", "-q:v", "3", str(full),
                           "-map", "[strip]", "-frames:v", "1", "-q:v", "2", str(strip)],
                          capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"ERROR: ffmpeg could not read {video} at {t} s: {done.stderr.strip()[-300:]}")


def main():
    ap = argparse.ArgumentParser(description="Build a page to check the indexer's wanted-star count by eye.")
    ap.add_argument("videos", nargs="+", help="recording names in the library (folder names)")
    ap.add_argument("--want", nargs="+", required=True, metavar="COUNT:HOW_MANY", help="e.g. 3:6 4:6 1:3 2:3")
    ap.add_argument("--fallback", nargs="+", default=[], metavar="VIDEO",
                    help="recordings used only for a count the main ones can't fill")
    ap.add_argument("--library", type=Path, default=index.LIBRARY)
    args = ap.parse_args()
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")

    def pool(stems):
        """count -> [(stem, video path, profile, seconds)]"""
        out = {}
        for stem in stems:
            idx, prof, by = candidates(args.library, stem)
            if index.off_limits(Path(idx["video"]["path"])):
                sys.exit(f"ERROR: {stem} is held-out footage")
            for count, times in by.items():
                out.setdefault(count, []).extend((stem, Path(idx["video"]["path"]), prof, t) for t in times)
        return out
    pools, spare = pool(args.videos), pool(args.fallback)
    out_dir = args.library / "star_check"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("[0-9][0-9]_*stars_*.jpg"):   # pictures of an earlier run of this script
        old.unlink()
    cards, n = [], 0
    for item in args.want:
        count, how_many = (int(v) for v in item.split(":"))
        picks = spread(pools.get(count, []), how_many)
        extra = spread(spare.get(count, []), how_many - len(picks))
        print(f"{count} star(s): {len(pools.get(count, []))} steady half seconds in the main recordings, showing "
              f"{len(picks)}" + (f", plus {len(extra)} from {', '.join(args.fallback)}" if extra else ""))
        picks += extra
        cards.append(f"<h2>The index says {count} star{'s' if count != 1 else ''} "
                     f"({len(picks)} of {how_many} wanted)</h2>")
        for stem, video, prof, t in picks:
            n += 1
            name = f"{n:02d}_{count}stars_{round(t * 1000):09d}"
            grab(ffmpeg, ffprobe, video, t, prof, out_dir / f"{name}.jpg", out_dir / f"{name}_zoom.jpg")
            cards.append(
                f'<figure><img src="star_check/{name}.jpg" alt="frame {n}">'
                f'<img class="zoom" src="star_check/{name}_zoom.jpg" alt="star corner of frame {n}, zoomed">'
                f'<figcaption><b>Picture {n}</b> &middot; the index says: <span class="stars">'
                f'{"&#9733;" * count}</span> <b>{count}</b> &middot; {html.escape(stem)} at {hms(t)}'
                f'</figcaption></figure>')
    page = args.library / "star_check.html"
    page.write_text(PAGE.replace("{CARDS}", "\n".join(cards)), encoding="utf-8")
    print(f"Wrote {page} ({n} pictures in {out_dir})")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wanted-star count check</title>
<style>
body { margin:0; background:#0f1115; color:#e6e8ec; font:15px/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width:1320px; margin:0 auto; padding:16px; }
h1 { font-size:22px; margin:0 0 6px; }
h2 { font-size:18px; margin:28px 0 8px; border-top:1px solid #2a2f3a; padding-top:16px; }
ol { color:#c9ced8; }
figure { margin:0 0 26px; background:#171a21; border:1px solid #2a2f3a; border-radius:6px; padding:10px; }
figure img { display:block; width:100%; height:auto; border-radius:4px; }
figure img.zoom { width:auto; max-width:100%; margin-top:8px; image-rendering:pixelated; border:1px solid #2a2f3a; }
figcaption { margin-top:8px; font-size:17px; }
.stars { color:#fbbf24; font-size:20px; }
</style></head><body><main>
<h1>Wanted-star count check</h1>
<ol>
<li>Look at the top-right corner of each picture. The strip under the picture shows that corner 4 times bigger.</li>
<li>Count the stars that have a black outline (white inside, or grey while they flash). Plain grey shapes with no
outline are empty slots and don't count.</li>
<li>Compare with the number under the picture: that is what the tool counted for this exact frame.</li>
<li>If a number is wrong, tell Claude the picture number.</li>
</ol>
{CARDS}
</main></body></html>
"""

if __name__ == "__main__":
    main()
