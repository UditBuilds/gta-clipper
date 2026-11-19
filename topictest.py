"""Checks for the launch-fixes work after the owner's posted sample Short (Nov 19): proof.py's close-ups and its GTA 6
HUD reading (heat icons, the look of the stars), labels kept only where the safety frames show them (safety_v3), the
candidate pool, topic candidates from the shot list and the HUD, and engine v3 (every Short the way the sample was
made: the hook card from frame 0, the voice with captions, close-ups as proof, music and sound effects).

No Ollama, no Kokoro voice and none of the owner's footage: the HUDs, recordings, model answers and voice timings are
made up here; ffmpeg makes the few real files a check needs.

  .venv-tts\\Scripts\\python.exe topictest.py
"""

import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import clip
import footage_short
import launch
import proof
import safety
import truth
from footagetest import made_up_index, made_up_timeline, seg
from launchtest import made_up_rec

CHECKS = []
TODAY = "2026-11-19"


def check(fn):
    CHECKS.append(fn)
    return fn


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return json.loads(out or "{}")


# --- 1. proof.py: close-ups ---

@check
def close_ups_are_16_9_and_inside_the_picture():
    """proof.zoom_rect on the owner's GTA 6 profile: "stars" holds the whole star row and the heat icons under it,
    "tip" the game's tip box, "minimap" the player arrow; each is 16:9 and inside 1920x1080; a box given by hand is
    made 16:9 round its middle; a bad name or an empty box is refused"""
    prof = clip.load_profile("gta6")
    rects = {name: proof.zoom_rect(name, prof, (1920, 1080)) for name in ("stars", "tip", "minimap")}
    ok = all(abs(w / h - 16 / 9) < 0.02 and x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080
             for x, y, w, h in rects.values())
    x, y, w, h = rects["stars"]
    r = prof.star_radius
    icons_bottom = prof.star_tip_y + r + (proof.ICON_DY + proof.ICON_R) * r
    ok &= x <= prof.star_tips_x[0] - r and prof.star_tips_x[-1] + r <= x + w and y + h >= icons_bottom
    cx, cy = prof.arrow["centre"]
    mx, my, mw, mh = rects["minimap"]
    ok &= mx < cx < mx + mw and my < cy < my + mh
    tx, ty, tw, th = rects["tip"]
    ok &= tx <= 30 and ty <= 286 and tx + tw >= 470 and ty + th >= 436
    hand = proof.zoom_rect([800, 500, 100, 100], prof, (1920, 1080))
    ok &= abs(hand[2] / hand[3] - 16 / 9) < 0.02 and abs(hand[0] + hand[2] / 2 - 850) <= 1 and hand[1] == 500
    refused = 0
    for bad in ("face", [0, 0, 0, 10]):
        try:
            proof.zoom_rect(bad, prof, (1920, 1080))
        except proof.ProofError:
            refused += 1
    return ok and refused == 2


@check
def a_close_up_is_cut_from_the_recording_at_1280x720():
    """proof.cut_zoom on a real made-up video: the part of the picture asked for, scaled to 1280x720, as long as
    asked, with no sound; a box outside the picture stops with ProofError"""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-f",
                        "lavfi", "-i", "sine=d=6", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
                       capture_output=True)
        out = proof.cut_zoom(src, 1.0, 3.0, [100, 50, 320, 180], Path(tmp) / "z" / "zoom.mp4")
        info = probe(out)
        refused = False
        try:
            proof.cut_zoom(src, 1.0, 1.0, [600, 300, 320, 180], Path(tmp) / "z" / "bad.mp4")
        except proof.ProofError:
            refused = True
    streams = info.get("streams", [])
    video = [s for s in streams if s["codec_type"] == "video"]
    return (len(streams) == 1 and video and (video[0]["width"], video[0]["height"]) == (1280, 720)
            and abs(float(info["format"]["duration"]) - 3.0) < 0.15 and refused)


# --- 2. proof.py: the GTA 6 HUD's heat icons and star looks ---

def six_profile(tmp):
    """calibtest's made-up GTA 6 profile: 4 star slots in a 320x180 picture, filled from the right."""
    data = {"name": "six_test", "game": "Made-up game", "frame": [320, 180], "stretch": {"min_s": 1.5, "pad_s": 0.5},
            "minimap": {"method": "arrow", "box": [45, 55, 31, 31], "centre": [60.5, 70.5], "min_share": 0.2},
            "stars": {"on": True, "method": "gta6", "box": [24, 2, 112, 30], "slots": 4, "first_tip_x": 40.0,
                      "tip_spacing": 24.0, "tip_y": 8.0, "radius": 10.0, "inner": 0.42, "min_score": 0.5,
                      "boost": 3.0},
            "phone": {"on": False}}
    path = Path(tmp) / "six_test.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return clip.load_profile("six_test", path)


def paint_row(prof, buf, width, top, lit=2, look="red", icons=0, seed=1, bg=(95, 110, 100)):
    """Draws, into an RGB picture `width` wide whose row 0 is picture row `top`, a busy background, the star row
    with `lit` stars lit from the right in one look ("red", "red outline", "white outline", "white") and `icons` heat
    icons (red discs with a white mark) under the stars, right-aligned."""
    rnd = random.Random(seed)
    rows = len(buf) // (width * 3)
    for i in range(width * rows):
        buf[3 * i:3 * i + 3] = bytes(max(0, min(255, c + rnd.randrange(-15, 16))) for c in bg)
    polys = clip.star_polygons(prof)
    for k, poly in enumerate(polys):
        on = k >= len(polys) - lit
        for y in range(rows):
            for x in range(width):
                px, py = x + 0.5, top + y + 0.5
                if not clip.point_inside(px, py, poly):
                    continue
                d = clip.edge_dist(px, py, poly)
                i = (y * width + x) * 3
                old = tuple(buf[i:i + 3])
                if not on:
                    col = tuple(round(0.75 * c + 0.25 * 150) for c in old)
                elif look == "red":
                    col = (25, 20, 22) if d < 1.0 else (175, 35, 45)
                elif look == "red outline":
                    col = (30, 15, 18) if d < 1.2 else (120, 25, 32) if d < 2.6 else old
                elif look == "white outline":
                    col = (40, 40, 40) if d < 1.2 else (235, 235, 235) if d < 2.6 else old
                else:     # solid white
                    col = (40, 40, 40) if d < 1.2 else (240, 240, 240)
                buf[i:i + 3] = bytes(col)
    r = prof.star_radius
    cy = prof.star_tip_y + r + proof.ICON_DY * r
    for k in range(icons):
        cx = prof.star_tips_x[-1] - k * proof.ICON_DX * r
        for y in range(rows):
            for x in range(width):
                dx, dy = x + 0.5 - cx, top + y + 0.5 - cy
                if math.hypot(dx, dy) <= proof.ICON_R * r:
                    i = (y * width + x) * 3
                    buf[i:i + 3] = bytes((240, 240, 240) if abs(dx) < 2 and abs(dy) < 2 else (200, 38, 45))


def row_crop(prof, **kw):
    """One crop in proof.hud6's layout: the star box and the heat-icon row under it."""
    rp = proof._row_prof(prof)
    x0, y0, w, _ = rp.star_box
    full_w = x0 + w
    buf = bytearray(full_w * (y0 + rp.rows) * 3)
    paint_row(prof, buf, full_w, 0, **kw)
    crop = bytearray()
    for y in range(y0, y0 + rp.rows):
        crop += buf[(y * full_w + x0) * 3:(y * full_w + x0 + w) * 3]
    return bytes(crop), rp


@check
def heat_icons_and_star_looks_are_read_on_made_up_huds():
    """proof's GTA 6 reading on drawn HUD crops: 0 to 3 heat icons are counted under 2 lit stars; the lit stars'
    look is read as red, red outline, white outline or (solid) white; no star lit has no look"""
    with tempfile.TemporaryDirectory() as tmp:
        prof = six_profile(tmp)
    ok = True
    for n in range(4):
        raw, rp = row_crop(prof, lit=2, look="red", icons=n, seed=n + 1)
        ok &= clip.gta6_star_count(raw, rp) == 2 and proof.icon_count(raw, rp) == n
    looks = {}
    for look in ("red", "red outline", "white outline", "white"):
        raw, rp = row_crop(prof, lit=2, look=look, seed=7)
        count = clip.gta6_star_count(raw, rp)
        looks[look] = (count, proof.star_look(raw, rp, count))
    raw, rp = row_crop(prof, lit=0, seed=9)
    return (ok and all(v == (2, k) for k, v in looks.items())
            and proof.star_look(raw, rp, clip.gta6_star_count(raw, rp)) is None), looks


@check
def hud6_reads_a_made_up_recording_and_finds_the_changes():
    """proof.hud6 on a real made-up video (the HUD drawn into its frames, lossless): every half second the lit stars,
    their look and the icons; hud6_events() finds the one change of icons (3 to 2) and of look (red to red outline),
    at the right half second, and ignores a one-frame misread and the stretch where no star is lit"""
    with tempfile.TemporaryDirectory() as tmp:
        prof = six_profile(tmp)
        frames = []
        for k in range(24):                                 # 12 s at 2 frames a second
            t = k / 2
            lit, look, icons = 2, "red" if t < 8 else "red outline", 3 if t < 4 else 2
            if t == 6.0:
                icons = 1                                   # a one-frame misread
            if t >= 10:
                lit, icons = 0, 0
            buf = bytearray(320 * 180 * 3)
            paint_row(prof, buf, 320, 0, lit=lit, look=look, icons=icons, seed=k + 1)
            frames.append(bytes(buf))
        video = Path(tmp) / "hud.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180", "-r", "2",
                        "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", "-r", "30", str(video)],
                       input=b"".join(frames), capture_output=True)
        series = proof.hud6(video, 0.0, 12.0, prof)
    events = proof.hud6_events(series)
    heat = [e for e in events if e["kind"] == "heat"]
    look = [e for e in events if e["kind"] == "star look"]
    early = [s for s in series if s[0] < 3.5]
    late = [s for s in series if s[0] >= 10.5]
    return (len(series) >= 23 and all(s[1:] == (2, "red", 3) for s in early)
            and all(s[1] == 0 and s[2] is None and s[3] is None for s in late)
            and len(heat) == 1 and (heat[0]["before"], heat[0]["after"]) == (3, 2) and abs(heat[0]["at_s"] - 4.0) <= 0.5
            and len(look) == 1 and look[0]["after"] == "red outline" and abs(look[0]["at_s"] - 8.0) <= 0.5), events


@check
def hud6_events_ignore_misreads_and_unknown_samples():
    """hud6_events() on a made-up series: a value must hold 2 s (4 samples) to count as a change; samples with no
    star row on screen (icons and look unknown) are skipped, so the icons 'coming back' after the stars went away
    is no change"""
    series = ([(k / 2, 2, "red", 3) for k in range(10)] + [(5.0, 2, "red", 2)]
              + [(5.5 + k / 2, 2, "red", 3) for k in range(6)] + [(8.5 + k / 2, 0, None, None) for k in range(6)]
              + [(11.5 + k / 2, 2, "red", 3) for k in range(4)] + [(13.5 + k / 2, 2, "red", 1) for k in range(6)])
    events = proof.hud6_events(series)
    return (len(events) == 1 and events[0]["kind"] == "heat" and (events[0]["before"], events[0]["after"]) == (3, 1)
            and events[0]["at_s"] == 13.5), events


# --- 3. Labels from the frames (safety_v3) ---

def shoot_rec_moment():
    rec = made_up_rec()
    m = next(m for m in launch.mine(rec) if 380 <= m["start_s"] <= 405)
    return rec, m


@check
def a_label_stands_only_where_the_frames_show_it():
    """launch-fixes: the made-up shootout moment (the index says shootout, a sheet says a car explodes): with safety
    answers that saw neither, verify_terms drops both labels and the score falls, the evidence calls the segment
    on_foot, has no explosion event and marks the sheet's line not_seen, and truth then refuses "explodes" and
    "shootout"; with answers that saw them, every label stays"""
    rec, m = shoot_rec_moment()
    unseen = dict(m, terms=dict(m["terms"]), verified={})
    before = unseen["score"]
    dropped = launch.verify_terms(rec, unseen)
    a, b = unseen["start_s"], unseen["end_s"]
    items = launch.evidence(rec, a, b, verified={})
    seen_v = {"shooting": [[a, b]], "explosion": [[406.0, 414.0]]}
    seen = dict(m, terms=dict(m["terms"]), verified=seen_v)
    kept = launch.verify_terms(rec, seen)
    items_seen = launch.evidence(rec, a, b, verified=seen_v)
    by_id = {e["id"]: e for e in items}
    line = {"say": "Then a car explodes in the shootout.", "claim": "on_screen",
            "evidence": [e["id"] for e in items if e["kind"] in ("seen", "activity")], "teaches": "x y z"}
    refused = truth.line_problems(1, line, by_id, False, {})
    acts = {e["value"] for e in items if e["kind"] == "activity"}
    return ("shootout" in dropped and "explosion" in dropped and unseen["score"] < before
            and unseen["dropped_labels"] == dropped and "shootout" not in acts and "on_foot" in acts
            and not any(e["kind"] == "event" and e["value"] == "explosion" for e in items)
            and any("explosion" in (e.get("not_seen") or []) for e in items if e["kind"] == "seen")
            and any("explode" in p or "explosion" in p for p in refused)
            and kept == [] and any(e["kind"] == "event" and e["value"] == "explosion" for e in items_seen)
            and "shootout" in {e["value"] for e in items_seen if e["kind"] == "activity"}), (dropped, refused)


@check
def police_with_stars_stay_and_mission_dialogue_costs_points():
    """the chase moment: police the frames didn't show stay while the HUD shows stars (the stars are the police);
    a crash they didn't show goes; subtitles on most of the safety sheets cost DIALOGUE_COST points"""
    rec = made_up_rec()
    m = next(m for m in launch.mine(rec) if 95 <= m["start_s"] <= 130)
    a, b = m["start_s"], m["end_s"]
    sheets = [{"from_s": t, "to_s": t + 4.0, "answer": {"subtitles": True}} for t in range(int(a), int(b), 4)]
    talk = dict(m, terms=dict(m["terms"]), verified={}, safety={"sheets": sheets})
    dropped = launch.verify_terms(rec, talk)
    return ("police" in talk["terms"] and "police" not in dropped
            and ("crash" in dropped or "crash" not in m["terms"])
            and talk["terms"].get("dialogue") == -launch.DIALOGUE_COST), talk["terms"]


@check
def the_safety_answers_give_what_was_seen_where():
    """safety.seen_spans: per thing seen (crash, shooting ...), the sheets' spans where the model said yes"""
    sheets = [{"from_s": 0.0, "to_s": 4.0, "answer": {"crash": True, "police": True}},
              {"from_s": 4.0, "to_s": 8.0, "answer": {"crash": False, "police": True}},
              {"from_s": 8.0, "to_s": 12.0, "answer": None}]
    spans = safety.seen_spans(sheets)
    return (spans == {"crash": [[0.0, 4.0]], "police": [[0.0, 4.0], [4.0, 8.0]]}
            and launch.seen_near(spans, "police", 6.0, 7.0) and not launch.seen_near(spans, "crash", 9.0, 10.0))


# --- 4. The candidate pool ---

def six_recordings():
    recs = {}
    for k in range(6):
        stem = f"rec_{k}"
        recs[stem] = made_up_rec(stem=stem, offset=k * 600.0)
    return recs


@check
def the_pool_spreads_over_the_recordings_and_every_reject_says_why():
    """pick_pool over six made-up recordings: the pool is 15-25 candidates (5 a Short asked for), at most its share
    from each recording, none 60 s or less from another of its recording; every one is checked; every one that
    fails is listed with why; the best that pass are picked (3, or --count 5)"""
    recs = six_recordings()
    moments = [m for rec in recs.values() for m in launch.mine(rec)]
    fails = lambda m: (m["video"] != "rec_1", ["made up: rec_1 fails"] if m["video"] == "rec_1" else [])
    picks, rejected, pool = launch.pick_pool(moments, fails, count=3, log=lambda s: None)
    five, _, pool5 = launch.pick_pool([dict(m) for m in moments], fails, count=5, log=lambda s: None)
    per = {}
    for m in pool:
        per[m["video"]] = per.get(m["video"], 0) + 1
    apart = all(abs(p["start_s"] - q["start_s"]) >= launch.POOL_APART_S for p in pool for q in pool
                if p is not q and p["video"] == q["video"])
    return (launch.POOL_MIN <= len(pool) <= launch.POOL_MAX and len(per) == 6 and max(per.values()) <= 4 and apart
            and all("checked" in m for m in pool) and len(picks) == 3 and len(five) == 5
            and all(p["video"] != "rec_1" for p in picks + five)
            and rejected and all(r["video"] == "rec_1" and r["why"] for r in rejected)
            and len(pool5) >= len(pool)), (len(pool), per)


# --- 5. Topics first ---

def gta6_rec():
    """The made-up recording read with the owner's GTA 6 profile (stars 110-160 s)."""
    rec = made_up_rec()
    rec["profile"] = "gta6"
    return rec


@check
def topics_come_from_the_shot_list_and_the_hud():
    """topic_candidates: a shot-list shot the footage matches (a chase with stars) becomes a topic candidate inside
    the clean stretch, with its topic, shot and facts and the topic bonus; on GTA 6 each change of the heat icons
    (proof.hud6, made up here) is one too, anchored on its second; a shot nothing matches gives none; footage an
    earlier Short showed gives none"""
    rec = gta6_rec()
    recs = {rec["stem"]: rec}
    shotlist = {"topics": [{"id": "police-chase", "shots": [{"id": "chase-stars", "want": "a chase with stars",
                                                               "query": {"activity": ["chase"], "min_stars": 1}}]},
                           {"id": "combat-weapons", "shots": [{"id": "boats", "want": "a boat",
                                                                "query": {"activity": ["boat"]}}]}]}
    asked = []

    def hud6(path, a, b, prof):
        asked.append((a, b))
        return [(a + k / 2, 2, "red", 3 if a + k / 2 < 130 else 2) for k in range(int((b - a) * 2))]
    got = launch.topic_candidates(recs, (), shotlist, hud6, log=lambda s: None)
    used = launch.topic_candidates(recs, [("rec_a", 90.0, 170.0)], shotlist, hud6, log=lambda s: None)
    chase = [m for m in got if m["topic"]["id"] == "police-chase"]
    heat = [m for m in got if m["topic"]["id"] == "heat-icons"]
    return (chase and heat and not any(m["topic"]["id"] == "combat-weapons" for m in got)
            and all(m["span"][0] + launch.GUARD_S <= m["start_s"] <= m["topic"]["at_s"] <= m["end_s"]
                    <= m["span"][1] - launch.GUARD_S for m in got)
            and chase[0]["topic"]["facts"] == launch.TOPIC_FACTS["police-chase"] and chase[0]["terms"]["topic"] > 0
            and heat[0]["topic"]["at_s"] == 130.0 and heat[0]["topic"]["event"]["after"] == 2
            and asked and asked[0] == (110.0, 160.0) and used == []
            and launch.anchor(rec, heat[0]) == 130.0), [(m["topic"]["id"], m["start_s"]) for m in got]


@check
def a_topic_pick_is_narrated_and_gets_the_hud_evidence():
    """a topic candidate whose narrated window fits becomes NARRATED (topic first), and hud6_evidence adds its heat
    icons and their change as evidence with the numbers, so a line may say "three icons" and "two are left" """
    rec = gta6_rec()

    def hud6(path, a, b, prof):
        return [(a + k / 2, 2, "red", 3 if a + k / 2 < 130 else 2) for k in range(int((b - a) * 2))]
    m = launch.topic_candidates({"rec_a": rec}, (), {"topics": []}, hud6, log=lambda s: None)[0]
    pick = launch.choose_types([m], {"rec_a": rec})[0]
    items = launch.hud6_evidence(rec, pick, hud6)
    numbers = truth.evidence_numbers(items)
    ids = [e["id"] for e in pick["evidence"] + items]
    by_id = {e["id"]: e for e in pick["evidence"] + items}
    line = {"say": "Then an icon goes, and two are left.", "claim": "on_screen",
            "evidence": [e["id"] for e in items], "teaches": "an icon can go away"}
    return (pick["type"] == "narrated" and "topic" in pick["type_why"]
            and any(e["kind"] == "heat" and e["numbers"] == [3, 2] for e in items)
            and any(e["kind"] == "heat" and e["value"].startswith("3 heat icons") for e in items)
            and {"2", "3"} <= {str(x) for x in numbers} and len(ids) == len(set(ids))
            and truth.line_problems(1, line, by_id, False, {}) == []), [e["value"] for e in items]


@check
def a_topic_passes_the_quality_gate_with_its_topic():
    """story_gate offers T1 to a topic candidate, so a topic with no older format (F1-F4) still has a point; a calm
    drive with neither a topic nor a format is still skipped"""
    rec = gta6_rec()

    def hud6(path, a, b, prof):
        return [(a + k / 2, 2, "red", 3 if a + k / 2 < 130 else 2) for k in range(int((b - a) * 2))]
    m = launch.topic_candidates({"rec_a": rec}, (), {"topics": []}, hud6, log=lambda s: None)[0]
    passes, why = launch.story_gate(rec, m, "gta6", False)
    calm = dict(m, start_s=470.0, end_s=500.0, span=[430.0, 600.0], terms={}, score=0.0)
    calm.pop("topic")
    calm_passes, calm_why = launch.story_gate(rec, calm, "gta6", False)
    return (passes and "T1" in m["point"]["formats"] and any(f["format"] == "T1" for f in m["format_options"])
            and not calm_passes and calm_why), (why, calm_why)


# --- 6. Engine v3: every Short like the sample ---

FACTS = {"heat": {"id": "heat", "claim": "Small icons under the stars show what the police know.",
                  "safe_wording": "small icons under the stars reportedly show what police know about you",
                  "status": "reported", "time_sensitive": False, "checked_on": "2026-11-17",
                  "source": "https://example.com/heat"}}


def topic_pick(video):
    return {"id": "M1", "short": 1, "type": "narrated", "video": "rec_z", "path": str(video), "mode": "hud",
            "profile": "gta6", "window": [2.0, 30.0], "span": [0.0, 40.0], "flags": [], "format_options": [],
            "topic": {"id": "heat-icons", "shot": "heat", "why": "made up", "at_s": 14.0, "facts": ["heat"]},
            "evidence": [
                {"id": "E1", "kind": "stars", "value": "2 wanted stars showing", "from_s": 2.0, "to_s": 30.0,
                 "numbers": [2], "source": "made up"},
                {"id": "E2", "kind": "heat", "value": "3 heat icons under the wanted stars", "from_s": 2.0,
                 "to_s": 14.0, "numbers": [3], "source": "made up"},
                {"id": "E3", "kind": "heat", "value": "heat icons 3 to 2", "from_s": 14.0, "to_s": 14.0,
                 "at_s": 14.0, "numbers": [3, 2], "source": "made up"},
                {"id": "E4", "kind": "heat", "value": "2 heat icons under the wanted stars", "from_s": 14.0,
                 "to_s": 30.0, "numbers": [2], "source": "made up"}]}


def topic_short():
    return {"short": 1, "moment": "M1", "type": "narrated", "format": "T1",
            "format_why": "the HUD shows an icon go: the footage proves it",
            "hooks": ["What do these icons mean?", "Watch this icon vanish", "Your stars hide a secret"], "star": 0,
            "title": "What the GTA 6 HEAT icons tell you",
            "description": "The small icons under the wanted stars change. Watch an icon disappear.",
            "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
            "lines": [
                {"say": "What do the icons under your stars mean?", "claim": "ask", "evidence": ["E1", "E2"],
                 "answered_by": 4, "beats": [{"beat": "full", "at": 4.0}]},
                {"say": "Look closer: small icons under the stars reportedly show what police know about you.",
                 "claim": "fact", "facts": ["heat"], "evidence": ["E1", "E2"], "teaches": "the icons are what police know",
                 "beats": [{"beat": "card", "at": 6.0, "zoom": "stars",
                            "label": {"text": "What police know", "style": "info"}},
                           {"beat": "card", "at": 9.0, "label": {"text": "Game tip", "style": "grey"}}]},
                {"say": "Here the stars still carry three icons.", "claim": "on_screen", "evidence": ["E1", "E2"],
                 "teaches": "three things are known", "beats": [{"beat": "card", "at": 10.0, "zoom": "stars"}]},
                {"say": "Then an icon goes, and two are left.", "claim": "on_screen", "evidence": ["E3", "E4"],
                 "teaches": "an icon can disappear", "payoff": True,
                 "beats": [{"beat": "compare", "top": {"at": 10.0, "zoom": "stars", "label": "Before"},
                            "bottom": {"at": 16.0, "zoom": "stars", "label": "After"}}]},
                {"say": "So next time the stars show up, look at", "claim": "on_screen", "evidence": ["E1"],
                 "teaches": "the loop back to the question", "loop": True, "beats": [{"beat": "full", "at": 22.0}]}]}


def stand_in_timing(path, root):
    """Every line 5.8 s, shared by its beats; a 1.5 s loop ending."""
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = [[5.8 / len(l["beats"])] * len(l["beats"]) for l in script["lines"]]
    return {"total_s": 5.8 * len(lines), "lines": lines, "loop_s": 1.5, "first": (0, 0), "behind": {}}


def stand_in_render(made):
    def render(resolved, out):
        made["script"] = json.loads(Path(resolved).read_text(encoding="utf-8"))
        (Path(out) / "log.json").write_text(json.dumps({"durations": {"video_s": 29.0},
                                                        "music": {"file": "C:/made_up/bed.mp3"}}), encoding="utf-8")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=108x192:d=1", "-f", "lavfi",
                        "-i", "sine=f=440:d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                        str(Path(out) / "short.mp4")], capture_output=True)
    return render


def run_folder(tmp, picks, shorts):
    folder = Path(tmp) / "launch-2026-11-19"
    folder.mkdir(parents=True)
    cand = {"game": "gta6", "game_name": "GTA 6", "dry_run": False, "picks": picks, "settings": {"library": str(tmp)},
            "recordings": [{"name": "rec_z.mp4", "path": picks[0]["path"], "duration_s": 40.0, "mode": "hud",
                            "hud_check": None}],
            "calibration": {"profile": "gta6", "why": "made up"}, "timing_s": {"index": 1.0}}
    (folder / "candidates.json").write_text(json.dumps(cand), encoding="utf-8")
    (folder / "shorts.json").write_text(json.dumps({"shorts": shorts}), encoding="utf-8")
    return folder


def big_video(path, seconds=30):
    return subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={seconds}",
                           "-f", "lavfi", "-i", f"sine=f=330:d={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                          capture_output=True).returncode == 0


def v3_index(video):
    idx = made_up_index("rec_z", [seg(0, 40, "chase", stars=2, police=True)], 40.0,
                        made_up_timeline([2] * 80))
    idx["video"]["path"] = str(video)
    idx["profile"] = {"name": "gta6"}
    return idx


@check
def engine_v3_makes_the_samples_script_with_close_ups_and_logs_it():
    """render() with engine v3 (the default; a made-up voice and render): the hook card from the starred hook, the
    am_puck voice, music, sound effects and the end pill; full-screen beats play the recording at their second, the
    "stars" close-ups are cut from it at 1280x720 and the compare shows both; explainer.py reads the script; the
    Short is READY, the footage it showed (close-ups as their recording seconds) is logged as used, and a narrated
    Short with no game sound gets no SOUND flag"""
    import explainer
    made = {}
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "rec_z.mp4"
        if not big_video(video):
            return False
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = run_folder(tmp, [topic_pick(video)], [topic_short()])
        data = launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS, checker=None,
                             timing=stand_in_timing, v3_render=stand_in_render(made), indexes=[v3_index(video)])
        entry = data["shorts"][0]
        script = made.get("script") or {}
        zooms = [b["file"] for l in script.get("lines", []) for b in l["beats"] if "zoom" in str(b.get("file"))]
        zooms += [b[s]["file"] for l in script.get("lines", []) for b in l["beats"] if b["beat"] == "compare"
                  for s in ("top", "bottom")]
        sizes = {(s["width"], s["height"]) for z in zooms for s in probe(z).get("streams", [])}
        out = Path(entry.get("folder", tmp))
        loaded = explainer.load_script(out / "script_resolved.json", launch.GTA_ROOT) if (
            out / "script_resolved.json").is_file() else None
        used = json.loads((shorts / footage_short.USED_FOOTAGE).read_text(encoding="utf-8"))
    full = [b for l in script.get("lines", []) for b in l["beats"] if b["beat"] == "full"]
    return (entry["status"] == "READY" and script.get("open_card") == "WHAT DO THESE|ICONS MEAN?"
            and script.get("voice") == "am_puck" and script.get("music") == "auto" and script.get("sfx") is True
            and script.get("end_pill") == launch.END_PILL and len(zooms) == 4 and sizes == {(1280, 720)}
            and full and all(b["file"] == str(video) for b in full) and float(full[0]["at"]) == 4.0
            and loaded is not None and entry["music"] == "bed.mp3"
            and not any(f.startswith("SOUND") for f in entry["flags"])
            and any(f.startswith("REPORTED FACT") for f in entry["flags"])
            and used["shorts"] and all(s[0] == "rec_z" for s in used["shorts"][0]["spans"])
            and any(abs(s[1] - 16.0) < 0.01 for s in used["shorts"][0]["spans"])), entry.get("problems")


@check
def engine_v3_refuses_a_plain_clip_a_late_payoff_and_used_footage():
    """engine v3 refuses what the sample's way doesn't allow: an action Short with no lines (no Short ships as raw
    gameplay: every Short has a voice), a payoff outside 45-85 % of the Short, a beat on footage an earlier Short
    showed, a beat far from the pick's window, and a label that names what its line's evidence doesn't show"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "rec_z.mp4"
        if not big_video(video, 30):
            return False
        pick = topic_pick(video)
        action = dict(pick, id="M2", short=2, type="action", format_options=[])
        plain = {"short": 2, "moment": "M2", "type": "action", "hooks": ["He did not see that", "Wait for the crash",
                                                                         "That wall came fast"], "star": 1,
                 "title": "This CRASH came out of absolutely nowhere",
                 "description": "A drive ends against a wall. It all happens in one second.",
                 "hashtags": ["#GTA6", "#GTAVI", "#Shorts"]}
        late = topic_short()
        late["lines"][3]["payoff"] = False
        late["lines"][1]["payoff"] = True            # the payoff in line 2: 20 % in
        late["lines"][1]["claim"] = "fact"
        far = topic_short()
        far["lines"][4]["beats"] = [{"beat": "full", "at": 300.0}]
        crash = topic_short()
        crash["lines"][2]["beats"][0]["label"] = "A huge crash"
        results = []
        for short, p in ((plain, action), (late, pick), (far, pick), (crash, pick)):
            shorts = Path(tmp) / f"shorts{len(results)}"
            shorts.mkdir()
            folder = run_folder(Path(tmp) / f"r{len(results)}", [p], [dict(short, short=p["short"])])
            results.append(launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS,
                                         checker=None, timing=stand_in_timing, v3_render=stand_in_render({}),
                                         indexes=[v3_index(video)])["shorts"][0])
        shorts = Path(tmp) / "shorts_used"
        shorts.mkdir()
        footage_short.save_used(shorts, "earlier@launch-2026-11-18", [["rec_z", 9.0, 12.0]])
        folder = run_folder(Path(tmp) / "r_used", [pick], [topic_short()])
        results.append(launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS,
                                     checker=None, timing=stand_in_timing, v3_render=stand_in_render({}),
                                     indexes=[v3_index(video)])["shorts"][0])
    plain_r, late_r, far_r, crash_r, used_r = results
    has = lambda r, words: r["status"] == "FAILED" and any(words in p for p in r["problems"])
    return (has(plain_r, "every Short has a voice") and has(late_r, "payoff") and has(far_r, "far from")
            and has(crash_r, "label") and has(used_r, "earlier Short")), [r["problems"] for r in results]


@check
def the_v3_rules_in_truth():
    """truth.check_short with engine v3: line 1 needn't repeat the hook (the hook is the opening card); T1 is offered
    to a topic pick and A1 to an action pick, not the other way round; with engine v2 neither exists"""
    with tempfile.TemporaryDirectory() as tmp:
        pick = topic_pick(Path(tmp) / "x.mp4")
    short = topic_short()
    v3, _, _ = truth.check_short(short, pick, FACTS, TODAY, engine="v3")
    v2, _, _ = truth.check_short(short, pick, FACTS, TODAY)
    no_topic, _, _ = truth.check_short(short, dict(pick, topic=None), FACTS, TODAY, engine="v3")
    action, _, _ = truth.check_short(dict(short, type="action", format="A1"), dict(pick, type="action"), FACTS, TODAY,
                                     engine="v3")
    return (v3 == [] and any("format" in p for p in v2) and any("line 1 must be the starred hook" in p for p in v2)
            and any("T1 isn't one this moment offers" in p for p in no_topic) and action == []), (v3, no_topic, action)


@check
def the_hook_card_and_beats_for_lines_that_name_none():
    """open_card_text: the starred hook in capitals, 2-3 lines of at most 13 characters ("CAN COPS|FORGET YOU?"), or
    nothing when a word is too long or it needs 4 lines; auto_beats: a line that names no beats gets a close-up of
    the stars for a heat item and footage for the rest, 2 beats for a line of 7 words or more"""
    by_id = {"E1": {"id": "E1", "kind": "heat", "from_s": 14.0, "to_s": 14.0, "at_s": 14.0},
             "E2": {"id": "E2", "kind": "activity", "from_s": 2.0, "to_s": 30.0}}
    one = launch.auto_beats({"say": "An icon goes.", "evidence": ["E1"]}, by_id, 1, False)
    two = launch.auto_beats({"say": "The chase goes on for a long, long time.", "evidence": ["E2"]}, by_id, 1, False)
    return (launch.open_card_text("Can cops forget you?") == "CAN COPS|FORGET YOU?"
            and launch.open_card_text("Incomprehensibly fast") is None
            and launch.open_card_text("one two three four five six seven eight nine ten eleven twelve") is None
            and one == [{"beat": "card", "at": 14.0, "zoom": "stars"}]
            and len(two) == 2 and two[0]["beat"] == "full" and two[1]["beat"] == "card"), (one, two)


def main():
    ok = True
    for fn in CHECKS:
        try:
            got = fn()
            extra = ""
            if isinstance(got, tuple):
                got, extra = got[0], got[1]
            good, err = bool(got), ""
        except Exception as e:
            good, err, extra = False, f"{type(e).__name__}: {e}", ""
        ok &= good
        doc = " ".join((fn.__doc__ or "").split())
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {doc}" + (f"  ({err})" if err else "")
              + (f"  [{extra}]" if extra and not good else ""))
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
