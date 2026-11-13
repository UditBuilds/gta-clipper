"""The facts a Short may state, and the check that a script states nothing else.

facts.json holds every fact with a status:
  confirmed    may be stated
  reported     may be stated only in its safe wording ("over 200 GB", never "212 GB")
  unverified   may not be used at all

Each line of a script lists the facts it uses: "facts": ["release-date"], or [] when it states none ("Let me
explain."). check_script() refuses:
  - a line with no "facts" list, an ID that isn't in facts.json, or an unverified fact;
  - a reported fact whose safe wording is not in that line (said or shown);
  - a number, said or shown, that is not in the wording of a fact that line lists. For a reported fact only its safe
    wording counts, so "212 GB" fails under a fact whose safe wording is "over 200 GB".
The title and the description are checked for numbers too, against every fact the script lists.

It can't read meaning: a wrong claim made without a number, under a real fact ID, still passes. That is what
claims.txt is for: it puts each line next to its facts, their status and their sources, for the owner to read.

Launch-week facts change from day to day (a pre-load date moved once already). Every fact says whether it is one of
those ("time_sensitive": true) and the day it was last checked ("checked_on": "2026-11-12"). stale_facts() finds the
time-sensitive facts a script uses that were checked more than STALE_DAYS days ago; make_short.py refuses to render
with one, unless the owner passes --allow-stale.

Standard library only.
"""

import json
import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTS_FILE = HERE / "facts.json"
STATUSES = ("confirmed", "reported", "unverified")
STALE_DAYS = 3                           # a time-sensitive fact checked longer ago than this is stale

# Numbers written as words count as numbers. "one" is left out: "day one" and "no one" are not claims.
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
                "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}
# Names with a number in them are not claims.
NAMES_WITH_NUMBERS = re.compile(r"\b(gta\s*(vi|v|6|5|six|five)|ps\s?5|playstation\s*5|series\s*x\s*\|?\s*s|4k)\b",
                                re.IGNORECASE)
CARD_MARKS = re.compile(r"[~*|]")                # a text card's line breaks, strike-through and colour marks


class FactsError(Exception):
    """facts.json can't be used as it is."""


def load_facts(path=FACTS_FILE):
    """{id: fact} from facts.json, checked. FactsError says what is wrong with the file."""
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except OSError as e:
        raise FactsError(f"can't read the facts file: {e}") from None
    except ValueError as e:
        raise FactsError(f"{path} is not valid JSON: {e}") from None
    items = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise FactsError(f"{path} must hold a \"facts\" list")
    facts, problems = {}, []
    for n, fact in enumerate(items, 1):
        if not isinstance(fact, dict) or not isinstance(fact.get("id"), str) or not fact["id"].strip():
            problems.append(f"fact {n}: needs an id")
            continue
        name = fact["id"]
        if name in facts:
            problems.append(f"{name}: two facts have this id")
        for key in ("claim", "safe_wording"):
            if not isinstance(fact.get(key), str) or not fact[key].strip():
                problems.append(f"{name}: needs {key}")
        if fact.get("status") not in STATUSES:
            problems.append(f"{name}: status is {', '.join(STATUSES)}")
        also = fact.get("also_ok", [])
        if not isinstance(also, list) or not all(isinstance(a, str) and a.strip() for a in also):
            problems.append(f"{name}: also_ok is a list of wordings")
        if "checked" in fact:
            problems.append(f"{name}: \"checked\" is now called \"checked_on\"")
        if not isinstance(fact.get("time_sensitive"), bool):
            problems.append(f"{name}: needs \"time_sensitive\": true (it can change from day to day) or false")
        checked_on = fact.get("checked_on")
        if "checked_on" not in fact:
            problems.append(f"{name}: needs \"checked_on\": the day it was last checked, as YYYY-MM-DD (or null)")
        elif checked_on is not None and day_of(checked_on) is None:
            problems.append(f"{name}: checked_on must be a real day written as YYYY-MM-DD, not {checked_on!r}")
        elif checked_on is None and fact.get("status") in ("confirmed", "reported"):
            problems.append(f"{name}: a {fact['status']} fact needs the day it was checked, in checked_on")
        if fact.get("source") is not None and not isinstance(fact["source"], str):
            problems.append(f"{name}: source is text or null")
        more = fact.get("more_sources", [])
        if not isinstance(more, list) or not all(isinstance(m, str) and m.strip() for m in more):
            problems.append(f"{name}: more_sources is a list of links")
        facts[name] = fact
    if problems:
        raise FactsError(f"{path} needs fixing:\n  " + "\n  ".join(problems))
    return facts


def day_of(text):
    """A YYYY-MM-DD day as a date, or None if it isn't one."""
    if isinstance(text, date):
        return text
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# --- Reading text ---

def plain(text):
    """Text as wordings are compared: lowercase, number words as digits, every way of writing gigabytes as "gb",
    and no punctuation."""
    text = CARD_MARKS.sub(" ", str(text).lower()).replace("’", "'")
    text = re.sub(r"(\d),(\d{3})\b", r"\1\2", text)
    text = re.sub(r"(\d)\s*(gigabytes?|gigs?|gb)\b", r"\1 gb", text)
    text = re.sub(r"\b(gigabytes?|gigs?)\b", "gb", text)
    words = re.findall(r"[a-z0-9]+(?:\.\d+)?", text)
    return " ".join(str(NUMBER_WORDS.get(w, w)) for w in words)


def numbers_in(text):
    """The numbers a text states, as strings, leaving out names such as GTA 6 and PS5."""
    text = NAMES_WITH_NUMBERS.sub(" ", CARD_MARKS.sub(" ", str(text)))
    return [w for w in plain(text).split() if re.fullmatch(r"\d+(\.\d+)?", w)]


def wordings(fact):
    """The wordings whose numbers a line may use: a reported fact's safe ones only, and a confirmed fact's claim too."""
    safe = [fact["safe_wording"], *fact.get("also_ok", [])]
    return safe if fact["status"] == "reported" else [fact["claim"], *safe]


def shot_texts(shot):
    """The words a shot puts on screen: a card's text, a snippet's, a banner's, and every label."""
    if isinstance(shot, str):
        return [shot[5:]] if shot.startswith("card:") else []
    if not isinstance(shot, dict):
        return []
    texts = [shot[key] for key in ("card", "text") if isinstance(shot.get(key), str)]
    for part in (shot, shot.get("top"), shot.get("bottom")):
        label = part.get("label") if isinstance(part, dict) else None
        if isinstance(label, str):
            texts.append(label)
        elif isinstance(label, dict) and isinstance(label.get("text"), str):
            texts.append(label["text"])
    return texts


def line_shots(line):
    shots = line.get("beats", line.get("visuals"))
    return shots if isinstance(shots, list) else []


# --- The check ---

def check_script(script, facts):
    """(problems, warnings, claims). claims lists every line with the facts it uses, for claims.txt:
    [{"line", "say", "shown", "facts": [fact, ...]}]."""
    problems, warnings, claims = [], [], []
    lines = script.get("lines") if isinstance(script.get("lines"), list) else []
    cited = {}
    for n, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            continue
        say = line.get("say") if isinstance(line.get("say"), str) else ""
        shown = [t for shot in line_shots(line) for t in shot_texts(shot)]
        ids = line.get("facts")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            problems.append(f"line {n}: needs \"facts\": the IDs of the facts it states, or [] if it states none")
            ids = []
        used = []
        for name in dict.fromkeys(ids):
            fact = facts.get(name)
            if fact is None:
                problems.append(f"line {n}: there is no fact {name!r} in facts.json")
            elif fact["status"] == "unverified":
                problems.append(f"line {n}: fact {name!r} is unverified, so it can't be used until the owner checks it")
            else:
                used.append(fact)
                cited[name] = fact
        everything = [plain(say)] + [plain(t) for t in shown]
        for fact in used:
            if fact["status"] != "reported":
                continue
            safe = [fact["safe_wording"], *fact.get("also_ok", [])]
            if not any(plain(s) in text for s in safe for text in everything):
                problems.append(f"line {n}: fact {fact['id']!r} is only reported, so the line must use its safe "
                                f"wording, \"{fact['safe_wording']}\"")
        allowed = {num for fact in used for w in wordings(fact) for num in numbers_in(w)}
        for where, text in [("says", say)] + [("shows", t) for t in shown]:
            for num in dict.fromkeys(numbers_in(text)):
                if num not in allowed:
                    reported = [f for f in used if f["status"] == "reported"]
                    hint = (f" (the safe wording of {reported[0]['id']!r} is \"{reported[0]['safe_wording']}\")"
                            if reported else "")
                    problems.append(f"line {n}: it {where} the number {num} ({text.strip()[:60]!r}), which is not in "
                                    f"any fact this line lists{hint}")
        claims.append({"line": n, "say": say, "shown": shown, "facts": used,
                       "ids": [i for i in dict.fromkeys(ids)]})
    allowed = {num for fact in cited.values() for w in wordings(fact) for num in numbers_in(w)}
    for key in ("title", "description"):
        text = script.get(key) if isinstance(script.get(key), str) else ""
        for num in dict.fromkeys(numbers_in(text)):
            if num not in allowed:
                problems.append(f"{key}: the number {num} is not in any fact the script lists")
    if lines and not cited and not problems:
        warnings.append("no line lists a fact: a Short with no checked fact in it has nothing to prove")
    return problems, warnings, claims


# --- Facts that have gone stale ---

def stale_facts(claims, today=None, max_days=STALE_DAYS):
    """The time-sensitive facts a script uses that were last checked more than max_days days before today, or on a
    day after today: [{"fact", "days", "lines"}], in the order the script uses them. claims is what check_script
    returns. A fact checked exactly max_days days ago is still fresh."""
    today = day_of(today) or date.today()
    found = {}
    for claim in claims:
        for fact in claim["facts"]:
            if not fact.get("time_sensitive"):
                continue
            checked = day_of(fact.get("checked_on"))
            days = (today - checked).days if checked else None
            if days is None or days > max_days or days < 0:
                found.setdefault(fact["id"], {"fact": fact, "days": days, "lines": []})["lines"].append(claim["line"])
    return list(found.values())


def stale_text(stale, today=None, max_days=STALE_DAYS):
    """The refusal, in plain English: which facts are stale, where the script uses them and what to do about it."""
    today = day_of(today) or date.today()
    out = [f"{len(stale)} fact{'s' if len(stale) != 1 else ''} in this script can change from day to day and "
           f"{'were' if len(stale) != 1 else 'was'} last checked more than {max_days} days ago, so nothing was rendered "
           f"(today is {today.isoformat()}):"]
    for item in stale:
        fact, days, lines = item["fact"], item["days"], item["lines"]
        where = ("line " if len(lines) == 1 else "lines ") + ", ".join(str(n) for n in lines)
        if days is None:
            when = "it has no day in checked_on"
        elif days < 0:
            when = f"its checked_on, {fact['checked_on']}, is after today"
        else:
            when = f"last checked on {fact['checked_on']}, {days} days ago"
        out.append(f"    - \"{fact['claim']}\"  ({fact['id']}, {where}): {when}")
    out.append("  Read each one again against its source link. If it still holds, set its \"checked_on\" in facts.json "
               "to today's date; if it changed, fix the fact first. To render anyway, add --allow-stale.")
    return "\n".join(out)


def claims_text(script, claims, facts):
    """claims.txt: every line next to the facts it uses, their status, their source and when they were checked."""
    out = [f"Claims in: {script.get('title', '')}", "",
           "Each line of the Short, with the facts it uses. Read the line against the claim next to it.", ""]
    for claim in claims:
        out.append(f"Line {claim['line']}: {claim['say']}")
        for text in claim["shown"]:
            out.append(f"    on screen: {CARD_MARKS.sub(' ', text).strip()}")
        if not claim["facts"]:
            out.append("    facts: none" + (f" (listed but not usable: {', '.join(claim['ids'])})" if claim["ids"] else ""))
        for fact in claim["facts"]:
            out.append(f"    [{fact['id']}]  {fact['status'].upper()}")
            out.append(f"        claim:  {fact['claim']}")
            if fact["status"] == "reported":
                out.append(f"        safe wording, which the line must use:  {fact['safe_wording']}")
            out.append(f"        source: {fact.get('source') or 'none given'}")
            out.extend(f"        also:   {link}" for link in fact.get("more_sources", []))
            out.append(f"        checked: {fact.get('checked_on') or 'never'}"
                       + (f" by {fact['checked_by']}" if fact.get("checked_by") else "")
                       + (f"  (can change from day to day: check again after {STALE_DAYS} days)"
                          if fact.get("time_sensitive") else ""))
        out.append("")
    used = {f["id"]: f for claim in claims for f in claim["facts"]}
    out.append(f"Facts used: {len(used)}")
    for fact in used.values():
        out.append(f"  {fact['id']}  ({fact['status']})  {fact.get('source') or 'no source link'}")
    return "\n".join(out) + "\n"
