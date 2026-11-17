"""The script truth rules for /launch (PR 25), checked in code before anything is rendered.

Every narrated line says which kind of claim it makes:
  "claim": "on_screen"  it describes what the footage shows; "evidence" lists the ids (E1, E2 ...) from
                        candidates.json that show it, and at least one of them must be on screen while the line is
                        spoken (EVIDENCE_SLACK_S either way)
  "claim": "fact"       it says how the game works; "facts" lists facts.json ids (confirmed, or reported in its safe
                        wording, not stale), exactly as /new-short does
A line is refused when:
  - it has no claim type, an on_screen line has no evidence, or cites an id the moment doesn't have;
  - it uses a word that needs evidence it doesn't cite (WORDS): "stars" needs a HUD star item, "police" needs police
    seen, a chase or stars, "crash" needs a crash, and so on; a "seen" line (the vision model's own words) that uses
    the same word counts too;
  - it says a number that is not in its evidence (star counts, seconds) or in its facts' wording;
  - it sounds like a claim about how the game works (MECHANICS: "you can", "the game", "cops will", "lets you" ...)
    and lists no fact;
  - it has a name other than Jason and Lucia: a capitalised word that isn't a sentence's first word, isn't ALL CAPS
    emphasis, isn't GTA 6 / GTA VI / PS5 and the like (ALLOWED), and isn't in a fact the line lists; or one of the
    trailer names in NAMES even in capitals;
  - it has a story or mission word (SPOILERS);
  - in NO-HUD mode, it mentions stars or the wanted level at all (no star count was measured).
Hooks, the title and the description get the same word, number, name, spoiler and mechanics checks against all of
the moment's evidence. What code can't judge (is the line literally true? is the crash cinematic?) goes to the review
page as a flag, and the owner is the last check.

Standard library only.
"""

import re

import facts as facts_file
import library
import make_short

CLAIMS = ("on_screen", "fact")
EVIDENCE_SLACK_S = 1.5
# Words that need evidence, and the evidence that supports them: (kind, value or None for any value).
WORDS = [
    (("star", "stars", "wanted"), [("stars", None)]),
    (("cop", "cops", "police", "officer", "officers", "squad car", "cruiser"),
     [("police", None), ("activity", "chase"), ("stars", None)]),
    (("crash", "crashes", "crashed", "crashing", "wreck", "wrecked", "smash", "smashed", "collide", "collision",
      "slams", "slammed"), [("event", "crash")]),
    (("explode", "explodes", "exploded", "explosion", "blast", "boom", "fireball"), [("event", "explosion")]),
    (("jump", "jumps", "jumped", "airborne", "flip", "flips", "flipped", "stunt", "ramp", "mid-air", "midair"),
     [("event", "stunt")]),
    (("chase", "chased", "chasing", "pursuit", "escape", "escaped", "getaway"),
     [("activity", "chase"), ("stars", None), ("police", None)]),
    (("shoot", "shoots", "shooting", "shootout", "gun", "guns", "gunfight", "firing"), [("activity", "shootout")]),
    (("fly", "flies", "flying", "flight", "plane", "helicopter", "chopper"), [("activity", "flying")]),
    (("boat", "boats", "jet ski"), [("activity", "boat")]),
    (("swim", "swims", "swimming"), [("activity", "swimming")]),
]
MECHANICS = (r"\byou can\b", r"\byou can't\b", r"\byou cannot\b", r"\byou could\b", r"\byou'll\b", r"\byou will\b",
             r"\byou have to\b", r"\byou need to\b", r"\blets? you\b", r"\ballows? you\b", r"\bthe game\b",
             r"\bthis game\b", r"\b(cops|police) (will|always|never|can|can't|won't)\b", r"\bwill always\b",
             r"\bevery time\b", r"\bwhenever\b", r"\bif you\b", r"\bsystem\b", r"\bmechanic", r"\bfeature\b",
             r"\bnew in\b", r"\bunlike gta\b")
SPOILERS = ("story", "plot", "ending", "spoiler", "dies", "died", "death of", "killed off", "betray", "betrays",
            "betrayal", "twist", "mission", "missions", "chapter", "boss", "cutscene", "cutscenes")
# Words that may be capitalised anywhere: the game, the platforms, the two characters the brief allows.
ALLOWED = {"gta", "vi", "v", "6", "5", "ps5", "playstation", "xbox", "rockstar", "jason", "lucia", "i", "i'm", "i've",
           "i'd", "i'll", "ok"}
# Character names from Rockstar's own trailers and site: never said, even in capitals (only Jason and Lucia may be).
NAMES = ("duval", "caminos", "cal", "hampton", "boobie", "ike", "dre'quan", "drequan", "priest", "real dimez",
         "bae-luxe", "roxy", "raul", "bautista", "brian", "heder")
STAR_WORDS = ("star", "stars", "wanted")
HOOK_WORDS = (3, 6)


def words_of(text):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text or "")


def has_word(text, phrase):
    """phrase in text as whole words, where text isn't denying it ("no police" doesn't count)."""
    kept = library.positive_text(text or "")
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", kept) is not None


def supported(word_family, items):
    """True when one of the evidence items supports a word of that family: an item of a supporting kind/value, or a
    'seen' or 'event' item whose own words use the word too."""
    words, kinds = word_family
    for e in items:
        for kind, value in kinds:
            if e["kind"] == kind and (value is None or e["value"] == value):
                return True
        if e["kind"] in ("seen", "event") and any(has_word(str(e.get("source", "")) + " " + str(e["value"]), w)
                                                  for w in words):
            return True
    return False


def word_problems(text, items, nohud, where, facts_text=""):
    """The words in text that need evidence the items don't give (a word a listed fact itself uses is fine), and,
    in NO-HUD mode, any star word at all."""
    out = []
    for family in WORDS:
        used = [w for w in family[0] if has_word(text, w)]
        if not used:
            continue
        if nohud and family[0] == WORDS[0][0]:
            out.append(f"{where}: says \"{used[0]}\", but this footage has no wanted-star reading (NO-HUD mode): "
                       "describe what happens instead")
        elif any(has_word(facts_text, w) for w in used):
            continue
        elif not supported(family, items):
            need = ", ".join(sorted({k if v is None else f"{k} {v}" for k, v in family[1]}))
            out.append(f"{where}: says \"{used[0]}\", but none of its evidence shows that ({need}); cite the evidence "
                       "or describe something it does show")
    return out


def name_problems(text, facts_text, where):
    """Capitalised words that may be names (not a sentence's first word, not ALL CAPS, not allowed, not in the
    line's facts), and the trailer names even in capitals."""
    out = []
    low = (text or "").lower()
    for name in NAMES:
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", low):
            out.append(f"{where}: \"{name}\" is a character name; only Jason and Lucia may be named")
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        for k, w in enumerate(words_of(sentence)):
            bare = w.strip("'’").lower()
            if (k == 0 or not w[0].isupper() or w.isupper() or bare in ALLOWED or bare in NAMES
                    or re.search(r"(?<![a-z])" + re.escape(bare) + r"(?![a-z])", facts_text)):
                continue
            out.append(f"{where}: \"{w}\" looks like a name; only Jason and Lucia may be named (write it in lower "
                       "case if it isn't a name)")
    return out


def spoiler_problems(text, where):
    return [f"{where}: \"{w}\" is a story or mission word; describe the gameplay instead"
            for w in SPOILERS if has_word(text, w)]


def mechanics_problems(text, where):
    low = (text or "").lower().replace("’", "'")
    hits = [m.group(0) for p in MECHANICS for m in [re.search(p, low)] if m]
    return [f"{where}: \"{hits[0]}\" sounds like a claim about how the game works; make it a \"fact\" line with its "
            "fact, or describe the moment instead"] if hits else []


def number_problems(text, allowed, where):
    return [f"{where}: says the number {n}, which is not in its evidence or its facts"
            for n in dict.fromkeys(facts_file.numbers_in(text or "")) if n not in allowed]


def evidence_numbers(items):
    return {str(n) for e in items for n in e.get("numbers", [])}


def line_problems(n, line, by_id, nohud, facts, line_span=None):
    """Everything wrong with one narrated line. line_span: (from_s, to_s) of the footage on screen while it is
    spoken, in the recording's seconds; None skips the on-screen timing check (before the voice is timed)."""
    where = f"line {n}"
    say = line.get("say") if isinstance(line.get("say"), str) else ""
    claim = line.get("claim")
    ids = line.get("evidence", [])
    fact_ids = line.get("facts", [])
    out = []
    if claim not in CLAIMS:
        out.append(f"{where}: needs \"claim\": \"on_screen\" (it describes the footage) or \"fact\" (how the game "
                   "works, from facts.json)")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        out.append(f"{where}: \"evidence\" is a list of evidence ids such as [\"E2\"]")
        ids = []
    if not isinstance(fact_ids, list):
        fact_ids = []
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        out.append(f"{where}: no evidence {', '.join(unknown)} in this moment")
    items = [by_id[i] for i in ids if i in by_id]
    if claim == "on_screen" and not items:
        out.append(f"{where}: an on_screen line must cite the evidence that shows it")
    if claim == "fact" and not fact_ids:
        out.append(f"{where}: a fact line must list its facts.json ids in \"facts\"")
    if claim == "on_screen" and items and line_span is not None:
        a, b = line_span
        if not any(e["from_s"] - EVIDENCE_SLACK_S < b and a < e["to_s"] + EVIDENCE_SLACK_S for e in items):
            shown = ", ".join(f"{e['id']} at {library.hms(e['from_s'])}" for e in items)
            out.append(f"{where}: none of its evidence ({shown}) is on screen while it is spoken "
                       f"({library.hms(a)}-{library.hms(b)} of the recording); move the line or cite what is there")
    used = [facts[i] for i in fact_ids if i in facts]
    facts_text = " ".join(f"{f['claim']} {f['safe_wording']} {' '.join(f.get('also_ok', []))}" for f in used).lower()
    allowed = evidence_numbers(items) | {x for f in used for w in facts_file.wordings(f)
                                         for x in facts_file.numbers_in(w)}
    out += word_problems(say, items, nohud, where, facts_text)
    out += number_problems(say, allowed, where)
    if not fact_ids:
        out += mechanics_problems(say, where)
    out += name_problems(say, facts_text, where)
    out += spoiler_problems(say, where)
    return out


def fact_problems(lines, facts, today):
    """facts.py's own checks on the lines that list facts (unknown, unverified, reported wording, stale), leaving out
    its number check, which line_problems does with the evidence's numbers as well."""
    script = {"lines": [{"say": l.get("say", ""), "facts": l.get("facts", []) if isinstance(l.get("facts"), list)
                         else []} for l in lines]}
    problems, _, claims = facts_file.check_script(script, facts)
    problems = [p for p in problems if " the number " not in p]
    stale = facts_file.stale_facts(claims, today)
    if stale:
        problems.append(facts_file.stale_text(stale, today))
    return problems, claims


def text_problems(text, items, nohud, where, facts_text=""):
    """The checks for a hook, the title or the description, against all of the moment's evidence."""
    out = word_problems(text, items, nohud, where)
    out += number_problems(text, evidence_numbers(items), where)
    out += mechanics_problems(text, where)
    out += name_problems(text, facts_text, where)
    out += spoiler_problems(text, where)
    return out


def hook_problems(hooks, star, items, nohud):
    """3 hooks of 3-6 words, no emoji, the starred one by its index (0, 1 or 2), each true to the evidence."""
    import hooks as hook_tool
    out = []
    if not isinstance(hooks, list) or len(hooks) != 3 or not all(isinstance(h, str) and h.strip() for h in hooks):
        return ["hooks: write exactly 3 hook options"]
    if not isinstance(star, int) or isinstance(star, bool) or not 0 <= star <= 2:
        out.append("star: the index of the starred hook, 0, 1 or 2")
    for k, h in enumerate(hooks, 1):
        clean, emoji = hook_tool.clean_hook(h)
        if emoji:
            out.append(f"hook {k}: no emoji")
        if not HOOK_WORDS[0] <= len(words_of(clean)) <= HOOK_WORDS[1]:
            out.append(f"hook {k}: {len(words_of(clean))} words; a hook is {HOOK_WORDS[0]}-{HOOK_WORDS[1]} words")
        out += text_problems(clean, items, nohud, f"hook {k}")
    return out


def upload_problems(short):
    """The title (make_short's rules), a 2-sentence description and 3-5 hashtags."""
    out = make_short.title_problems(short.get("title")) + make_short.upload_problems(short)
    description = short.get("description") or ""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", description.strip()) if s]
    if isinstance(description, str) and description.strip() and len(sentences) != 2:
        out.append(f"description: {len(sentences)} sentence(s); write exactly 2")
    return out


def check_short(short, moment, facts, today, line_spans=None):
    """(problems, flags, claims) for one Short Claude wrote. moment: its pick from candidates.json (type, evidence,
    mode). line_spans: per narrated line, (from_s, to_s) of the footage on screen while it is spoken, once timed."""
    items = moment.get("evidence") or []
    by_id = {e["id"]: e for e in items}
    nohud = moment.get("mode") == "nohud"
    problems, flags = [], []
    if short.get("type") != moment.get("type"):
        problems.append(f"type: this moment is {moment.get('type')} (decided in prepare), not {short.get('type')}")
    problems += hook_problems(short.get("hooks"), short.get("star"), items, nohud)
    problems += upload_problems(short)
    for key in ("title", "description"):
        if isinstance(short.get(key), str):
            problems += text_problems(short[key], items, nohud, key)
    claims = []
    if moment.get("type") == "narrated":
        lines = short.get("lines")
        if not isinstance(lines, list) or not lines or not all(isinstance(l, dict) for l in lines):
            return problems + ["lines: a narrated Short needs its lines"], flags, claims
        hooks = short.get("hooks") or []
        star = short.get("star")
        if isinstance(star, int) and 0 <= star < len(hooks) and lines[0].get("say", "").strip() != hooks[star].strip():
            problems.append("line 1 must be the starred hook, word for word (the narrated hook is spoken)")
        for n, line in enumerate(lines, 1):
            span = line_spans[n - 1] if line_spans and n - 1 < len(line_spans) else None
            problems += line_problems(n, line, by_id, nohud, facts, span)
        more, claims = fact_problems(lines, facts, today)
        problems += more
        if any(f["status"] == "reported" for c in claims for f in c["facts"]):
            flags.append("REPORTED FACT: a line uses a reported fact; read claims.txt")
    elif short.get("lines"):
        problems.append("lines: an ACTION CLIP has no narration; leave \"lines\" out")
    return problems, flags, claims


def claims_text(short, moment, claims, facts):
    """claims.txt for a narrated Short: every line with its claim type, the evidence it cites (what, when, where from)
    and its facts with their status and sources."""
    by_id = {e["id"]: e for e in moment.get("evidence") or []}
    out = [f"Claims in: {short.get('title', '')}", "",
           "Each line, the evidence it points to and the facts it uses. Read every line against its evidence.", ""]
    for n, line in enumerate(short.get("lines") or [], 1):
        out.append(f"Line {n} ({line.get('claim')}): {line.get('say', '')}")
        for i in line.get("evidence", []):
            e = by_id.get(i)
            if e:
                out.append(f"    [{i}] {e['kind']}: {e['value']}  ({library.hms(e['from_s'])}-{library.hms(e['to_s'])} of "
                           f"{moment['video']}; {e['source']})")
        claim = next((c for c in claims if c["line"] == n), None)
        for f in (claim or {}).get("facts", []):
            out.append(f"    [{f['id']}] {f['status'].upper()}: {f['claim']}  (source: {f.get('source') or 'none'}; "
                       f"checked {f.get('checked_on') or 'never'})")
        out.append("")
    return "\n".join(out) + "\n"
