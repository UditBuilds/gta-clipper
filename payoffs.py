"""Payoffs that are neither an impact nor a HUD change (the owner, Nov 25): an ANIMAL ATTACK, and TAKING AN AIRCRAFT. Each
counts as a payoff of 2/3 (launch.payoff_strength), the bar every pick must meet, and only when TWO CHECKS AGREE.

The two checks are the two looks the vision model has had at the same seconds, with different questions and different
frames: the INDEX (a line, an activity and a vehicle for every 6 s; it logs the whole recording) and the SAFETY QUESTION
(a note on 4 frames a second apart). Neither alone is enough, and neither counts twice. The text rules here read what
the two looks say; a line that denies a thing ("no attack") never says it (library.positive_text), and what is written
on the picture ("text: ...", "dialogue about ...") is not what happens in it.

  animal attack    one of the two says an animal attacks ("gets attacked and falls" with an alligator in the line,
                   "crocodile attack", "attacked by a wild boar", "the shark bites"), the other says so too or sees them
                   fight ("Player fights alligator"), the same animal, on the same seconds (a second of them at least).
                   Two fights are none: he may be hunting it. The player attacking or shooting an animal is none.
  aircraft taken   the index goes from ON FOOT AT an aircraft (he walks up to it, opens its door, boards it) straight to
                   FLYING a plane or helicopter, and the safety note there sees the PLAYER fly it or board it ("Player flies
                   ..."). Flying when the recording starts, or after a map or a cutscene, is none (nobody saw it taken);
                   nor is a plane that flies over, nor one he only walks up to: the pink jet of _135610 (13:08), whose
                   stairs he climbs and whose door he never gets through, has vehicle "plane" for a sheet and is on foot.

Standard library only; asks the model nothing (bank.py does).
"""
import re

import library

ATTACK, AIRCRAFT = "animal attack", "aircraft taken"
TERM = {ATTACK: "animal_attack", AIRCRAFT: "aircraft_taken"}      # the score terms of a moment (launch.score_window's)
POINTS = 1.0                  # a moment with one is worth this much more, as a HUD change the topic candidates count is
WHY = {ATTACK: "an animal attack that two checks see (the index's line and the safety look)",
       AIRCRAFT: "an aircraft taken, which two checks see (the index's lines and the safety look)"}
AGREE_S = 1.0                 # the two checks share this many seconds, at least
HOLD_S = 2.0                  # a moment has an attack when it holds its first seconds (this many) whole
GAP_S = 12.0                  # on foot at an aircraft, then flying: at most this far apart (two sheets)
AIR_S = 12.0                  # the safety look that sees the player in it: at most this long after the first flying sheet

ANIMALS = {"gator": r"alligators?|gators?|crocodiles?|crocs?|caimans?", "shark": r"sharks?", "boar": r"boars?|hogs?|pigs?",
           "dog": r"dogs?|hounds?|coyotes?|wolf|wolves", "snake": r"snakes?|pythons?|vipers?", "bear": r"bears?",
           "cat": r"panthers?|cougars?|pumas?|mountain lions?|big cats?|bobcats?", "bird": r"birds?|pelicans?|seagulls?|eagles?|ostrich(?:es)?",
           "ray": r"stingrays?|manta rays?", "jellyfish": r"jellyfish", "eel": r"eels?", "bull": r"bulls?|cows?", "horse": r"horses?",
           "deer": r"deer|stags?", "monkey": r"monkeys?|apes?", "any": r"animals?(?! rights)|creatures?|wildlife|beasts?"}
FAMILY = {k: re.compile(rf"\b(?:{v})\b") for k, v in ANIMALS.items()}
A = r"(?:" + "|".join(ANIMALS.values()) + r")"
HURT = (r"(?:attacked|bitten|mauled|charged|gored|stung|rammed|dragged|grabbed|tackled|pinned|chased|knocked (?:down|over)|"
        r"taken down)")
# "attacked by a wild boar": the animal is the one who does it
BY = re.compile(rf"\b{HURT}(?: \w+){{0,2}} by (?:\w+ ){{0,3}}?{A}\b")
BY_OTHER = re.compile(rf"\b{HURT}(?: \w+){{0,2}} by \w+")
# "alligator lunges and bites": an animal is the subject (not "near the alligator attacks a man": see subject_before)
DOES = re.compile(rf"\b({A}) (?:\w+ ){{0,2}}?(?:attacks|attacking|bites|biting|mauls|charges|lunges|lunging|rams|gores|stings|"
                  r"drags|pins|tackles|grabs|knocks)\b")
# "crocodile attack", "shark attack on the player" (not "dog attack man")
NOUN = re.compile(rf"\b{A} attack\b(?=\s*(?:$|[,.;!]|on\b|and\b|then\b|near\b|in\b|at\b|by\b|from\b|while\b|underwater\b))")
# "gets attacked and falls", with an animal somewhere in the line and nobody else named as the one who does it
GETS = re.compile(rf"\b(?:gets?|getting|got|is|was|being|been) (?:\w+ )?{HURT}\b")
CONTACT = re.compile(r"\b(?:fights?|fighting|fought|wrestl\w+|struggl\w+|grappl\w+|bites?|biting|bitten|attacks?|attacked|attacking|"
                     r"mauls?|mauled|dragged|pinned)\b")
NOT_SUBJECT = {"with", "near", "at", "toward", "towards", "past", "beside", "by", "over", "and", "on", "to", "from", "holding",
               "riding", "behind", "of", "against", "shoots", "attacks", "fights", "hits", "kills", "into"}
ARTICLES = {"a", "an", "the", "his", "her", "their", "wild", "large", "big", "dead", "huge", "small", "another", "hot"}
WRITTEN = re.compile(r"\b(?:text|dialogue|subtitles?|captions?|says?|saying|reads?)\b.*")

AIR = (r"(?:planes?|airplanes?|aeroplanes?|aircraft|airliners?|jetliners?|biplanes?|seaplanes?|helicopters?|choppers?|"
       r"jets?(?![ -]?skis?\b| bridges?\b|ways?\b))")
AIR_WORD = re.compile(rf"\b{AIR}\b")
AT_IT = re.compile(r"\b(?:approach\w*|enters?|entering|boards?|boarding|climbs?|climbing|opens?|gets? (?:in|into|on|onto|aboard)|"
                   r"walks? (?:up|to|toward|towards)|runs? (?:up|to|toward|towards)|heads? (?:to|toward|towards)|steals?|stealing|"
                   r"hijacks?|hijacking|jumps? (?:in|into))\b")
PLAYER = r"\b(?:player|man|woman|character|pilot|he|she|jason|lucia)\b"
FLIES = re.compile(rf"{PLAYER}(?: \w+){{0,3}}? (?:flies|flying|pilots|piloting|taxis|taxiing|controls)\b")
BOARDS = re.compile(rf"{PLAYER}[^.;]*?\b(?:(?:boards|boarding|enters|entering|gets? (?:in|into)|climbs? (?:in|into)|takes? control of)"
                    rf"(?: (?!boats?|cars?|trucks?|vans?|bus|suv|near|beside|by)\w+){{0,3}}? {AIR}|in (?:the |a )?(?:cockpit|pilot seat))\b")
IN_VEHICLE = ("plane", "helicopter")


def happens(text):
    """What a line says happens in the picture: lower case, denied things blanked, and nothing that is written or said
    in it ("text: ...", a quote)."""
    low = library.positive_text(text or "")
    return re.sub(r"\s+", " ", WRITTEN.sub(" ", re.sub(r"\"[^\"]*\"|(?<!\w)'[^']*'", " ", low)))


def animals(p):
    return {k for k, rx in FAMILY.items() if rx.search(p)}


def is_subject(p, start):
    """Whether the word at start can be what the verb after it is about: not after "near the", "with a", "shoots the"."""
    before = p[:start].split()
    while before and before[-1].strip(",") in ARTICLES:
        before.pop()
    return not before or before[-1].endswith(",") or before[-1] not in NOT_SUBJECT


def attack_in(text):
    """(animals the line says attack, animals it sees in a fight): two sets of ANIMALS' names."""
    p = happens(text)
    here = animals(p)
    if not here:
        return set(), set()
    strong = set()
    for m in [*BY.finditer(p), *NOUN.finditer(p)]:
        strong |= animals(m.group(0))
    for m in DOES.finditer(p):
        if is_subject(p, m.start(1)):
            strong |= animals(m.group(1))
    if GETS.search(p) and not BY_OTHER.search(p):
        strong |= here
    if strong:
        return strong, strong
    return set(), (here if CONTACT.search(p) else set())


def same_animal(x, y):
    return bool(x & y) or "any" in (x | y) and bool(x and y)


def in_it(text):
    """Whether a note sees the player fly an aircraft, or get into one."""
    p = happens(text)
    return bool(FLIES.search(p) or BOARDS.search(p))


def on_foot(s):
    return s.get("activity") == "on_foot" and (s.get("vehicle") or "none") == "none"


def at_aircraft(s):
    p = happens(s.get("notable"))
    return on_foot(s) and bool(AIR_WORD.search(p) and AT_IT.search(p))


def in_aircraft(s):
    return s.get("activity") == "flying" and s.get("vehicle") in IN_VEHICLE


def attack_sheets(sheets):
    """The index's sheets that say an animal attacks, or that see a fight with one (the first check)."""
    return [s for s in sheets if any(attack_in(s.get("notable")))]


def boardings(sheets, every):
    """[(the sheets on foot at an aircraft, the flying sheets after them)] of a recording: the index's side of an aircraft
    taken. Every sheet between the first and the last is one of the two, next to each other in time."""
    out = []
    for i, s in enumerate(sheets):
        if not (in_aircraft(s) and i and not in_aircraft(sheets[i - 1])):
            continue
        foot, k = [], i - 1
        while k >= 0 and at_aircraft(sheets[k]) and s["t"] - sheets[k]["t"] <= GAP_S + 1e-6:
            foot.insert(0, sheets[k])
            k -= 1
        if not foot or s["t"] - foot[-1]["t"] > every + 1e-6:
            continue
        fly = [s]
        while i + len(fly) < len(sheets) and in_aircraft(sheets[i + len(fly)]) and len(fly) * every < AIR_S:
            fly.append(sheets[i + len(fly)])
        out.append((foot, fly))
    return out


def need_looks(sheets, every):
    """[(from, to)]: the seconds whose second check must have been looked at: every sheet where the index says an animal
    attacks, and the first flying sheet of an aircraft it says was taken."""
    out = [(s["t"], s["t"] + every) for s in sheets if attack_in(s.get("notable"))[0]]
    return out + [(fly[0]["t"], fly[0]["t"] + every) for _, fly in boardings(sheets, every)]


def check_(by, a, b, text):
    return {"by": by, "from_s": round(a, 2), "to_s": round(b, 2), "text": text}


def events(sheets, every, answers):
    """Every new payoff of a recording that two checks agree on: [{"kind", "from_s", "to_s", "core": [a, b], "checks": [...]}].
    sheets: the index's (with notable, activity, vehicle); answers: the safety question's (bank.saved_answers). core: the
    seconds a moment must hold to have it (an attack's first HOLD_S; from his last second outside an aircraft into it)."""
    looks = [(s["from_s"], s["to_s"], s["answer"]["note"]) for s in answers if s["every_s"] <= 1.0]
    out = []
    for s in attack_sheets(sheets):
        says, fights = attack_in(s["notable"])
        for a, b, note in looks:
            lo, hi = max(a, s["t"]), min(b, s["t"] + every)
            said, fought = attack_in(note)
            if hi - lo < AGREE_S - 1e-6 or not (says or said) or not same_animal(says or fights, said or fought):
                continue
            checks = [check_("index line", s["t"], s["t"] + every, s["notable"]), check_("safety look", a, b, note)]
            near = next((e for e in out if lo <= e["to_s"] + every and e["from_s"] <= hi + every), None)
            if near:                                    # the same attack, seen again
                near["from_s"], near["to_s"] = round(min(near["from_s"], lo), 2), round(max(near["to_s"], hi), 2)
                near["checks"] += [c for c in checks if c not in near["checks"]]
            else:
                out.append({"kind": ATTACK, "from_s": round(lo, 2), "to_s": round(hi, 2), "checks": checks})
    for e in out:
        e["core"] = [e["from_s"], round(min(e["to_s"], e["from_s"] + HOLD_S), 2)]
    for foot, fly in boardings(sheets, every):
        seen = [(a, b, note) for a, b, note in looks if min(b, fly[-1]["t"] + every) - max(a, fly[0]["t"]) >= AGREE_S - 1e-6 and in_it(note)]
        if seen:
            out.append({"kind": AIRCRAFT, "from_s": foot[-1]["t"], "to_s": round(fly[0]["t"] + every, 2),
                        "core": [round(foot[-1]["t"] + 1.0, 2), round(fly[0]["t"] + 1.0, 2)],
                        "checks": [check_("index line", s["t"], s["t"] + every, s["notable"]) for s in (*foot, fly[0])]
                        + [check_("safety look", *x) for x in seen]})
    return sorted(out, key=lambda e: e["from_s"])


def holds(m, e):
    """Whether a moment's own seconds hold the event (its core, whole)."""
    return m["start_s"] <= e["core"][0] + 1e-6 and e["core"][1] <= m["end_s"] + 1e-6


def kind_of(terms):
    """The new payoff a moment's terms name, or None."""
    return next((k for k, t in TERM.items() if (terms or {}).get(t)), None)


def source(e):
    """One line that names both checks, for a moment's evidence and the review page."""
    return "two checks: " + "; ".join(f"{c['by']} at {library.hms(c['from_s'])}-{library.hms(c['to_s'])}: \"{c['text']}\"" for c in e["checks"])
