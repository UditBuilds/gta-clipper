"""Build a blind rating page for one gta-clipper run: the clips only, nothing about how they were picked.

For the held-out test (eval/HELDOUT_PLAN.md step 3) the owner rates every clip of a `--count 10` run before any model
verdict exists. make_review.py's page can't be used for that: it shows each clip's scores, peak and "why picked", lists
the clips best first, and its player prints the file names (clip_01_...), which carry the rank. This page shows:
- the clips in source-time order, lettered A, B, C ...;
- each clip's source time range and length, and a player for the clip file;
- for each clip, postable yes/no and a reason, kept in this browser only (localStorage), and a button that copies all
  the ratings as text to paste into the chat.

The clip files are copied next to the page under neutral names (<out stem>_clips/A.mp4 ...). No score, peak, rank,
tool name, run folder, config or model verdict is written to the page or the folder.

Usage:  python make_rating_page.py <run_dir> <out.html>
  e.g.  python make_rating_page.py C:\\AI\\gta\\output\\heldout1\\<run> C:\\AI\\gta\\review\\heldout1_rating.html
"""

import argparse
import html
import json
import shutil
import string
import sys
from pathlib import Path


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def main():
    ap = argparse.ArgumentParser(description="Blind rating page: a run's clips with no scores, ranks or verdicts.")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    scores = json.loads((args.run_dir / "scores.json").read_text(encoding="utf-8"))
    video = Path(scores["input"]).stem
    clips = sorted(scores["clips"], key=lambda c: c["start_s"])  # source-time order, not rank
    if len(clips) > len(string.ascii_uppercase):
        sys.exit("ERROR: more than 26 clips")
    clip_dir = args.out.parent / (args.out.stem + "_clips")
    clip_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    for letter, c in zip(string.ascii_uppercase, clips):
        shutil.copyfile(args.run_dir / c["file"], clip_dir / f"{letter}.mp4")
        span = f"{hms(c['start_s'])}-{hms(c['end_s'])}"
        cards.append(
            f'<article class="clip" data-id="{letter}" data-label="{html.escape(f"{video} {letter} {span}")}">'
            f'<h2>{letter} <span class="meta">{span} in the source, {c["end_s"] - c["start_s"]:.1f} s</span></h2>'
            f'<video controls preload="metadata" src="{clip_dir.name}/{letter}.mp4"></video>'
            f'<div class="answer"><label><input type="radio" name="{letter}" value="yes"> postable</label> '
            f'<label><input type="radio" name="{letter}" value="no"> not postable</label> '
            f'<input type="text" class="cmt" placeholder="reason"></div></article>')
    page = (TEMPLATE.replace("{VIDEO}", html.escape(video)).replace("{N}", str(len(clips)))
            .replace("{CARDS}", "".join(cards)))
    args.out.write_text(page, encoding="utf-8")
    print(f"{args.out}: {len(clips)} clips, copied to {clip_dir}")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{VIDEO} clip ratings</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --text:#e6e8ec; --muted:#9aa3b2; --accent:#facc15; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui, "Segoe UI", sans-serif; }
header, main, footer { max-width:1500px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:0 0 6px; }
.meta { color:var(--muted); font-weight:400; }
main { display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:12px; }
.clip { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px; }
.clip video { width:100%; aspect-ratio:9/16; max-height:70vh; background:#000; display:block; border-radius:4px; }
.answer { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:8px; }
.cmt { flex:1 1 200px; min-width:0; font:inherit; color:var(--text); background:#0b0d11; border:1px solid var(--line);
  border-radius:4px; padding:4px 8px; }
#out { width:100%; min-height:200px; font:12px/1.4 ui-monospace, Consolas, monospace; color:var(--text); background:#0b0d11;
  border:1px solid var(--line); border-radius:4px; padding:8px; }
.bar { display:flex; gap:10px; align-items:center; margin:8px 0; }
.bar button { font:inherit; color:#111; background:var(--accent); border:0; border-radius:4px; padding:6px 14px; cursor:pointer;
  font-weight:700; }
</style></head><body>
<header>
<h1>{VIDEO}: rate {N} clips</h1>
<p class="meta">The clips are in source-time order. Watch each one, mark it postable or not and give a reason, then press
<b>Copy ratings</b> at the bottom and paste the text into the chat. Your marks are kept in this browser only.</p>
</header>
<main>{CARDS}</main>
<footer>
<div class="bar"><button id="copy">Copy ratings</button><span id="copied" class="meta"></span></div>
<textarea id="out" readonly></textarea>
</footer>
<script>
const cards = [...document.querySelectorAll(".clip")];
const KEY = "rating|" + cards.map(c => c.dataset.label).join("|");  // one store per set of clips
function load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } }
function save(s) { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) {} }
function render() {
  const s = {};
  let n = 0;
  const lines = cards.map(c => {
    const r = c.querySelector("input[type=radio]:checked"), t = c.querySelector(".cmt").value;
    s[c.dataset.id] = { v: r ? r.value : "", c: t };
    if (r) n++;
    return `${c.dataset.label}: ${r ? (r.value === "yes" ? "postable" : "not postable") : "(not rated)"}${t ? " - " + t : ""}`;
  });
  save(s);
  document.getElementById("out").value = `Ratings: ${n} of ${cards.length} rated\\n` + lines.join("\\n");
}
const saved = load();
cards.forEach(c => { const x = saved[c.dataset.id]; if (x) {
  if (x.v) { const r = c.querySelector(`input[value="${x.v}"]`); if (r) r.checked = true; }
  c.querySelector(".cmt").value = x.c || ""; }
  c.addEventListener("input", render); c.addEventListener("change", render); });
render();
document.getElementById("copy").addEventListener("click", () => {
  const t = document.getElementById("out"); t.select();
  const done = () => document.getElementById("copied").textContent = "Copied. Paste it into the chat.";
  if (navigator.clipboard) navigator.clipboard.writeText(t.value).then(done, () => { document.execCommand("copy"); done(); });
  else { document.execCommand("copy"); done(); }
});
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
