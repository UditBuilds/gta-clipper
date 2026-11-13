---
description: Make one finished GTA 6 explainer Short for each topic in a list
argument-hint: <topic 1>; <topic 2>; <topic 3> (or one topic per line)
---

Make one explainer Short for each of these topics:

$ARGUMENTS

The topics are separated by semicolons or by new lines. Trim each one and skip empty ones.

For **each topic, one at a time and in the order given**, follow every step of `.claude/commands/new-short.md` (read
that file first): score the topic, read `facts.json`, write the script, check it, render it, look at its `picks.jpg`
yourself, and fix what doesn't fit.

Rules for a batch:

- Finish one Short completely before starting the next. The picker avoids pictures used in the last 5 Shorts, and it
  only knows about a Short once that Short is READY.
- Don't let two Shorts open with the same hook or use the same title.
- If a topic can't be made from the checked facts, don't force it: skip it, say which facts are missing, and go on to
  the next topic.
- Never upload anything. Never change a fact's status. Never pass `--allow-stale`.
- If `make_short.py` refuses a topic because a fact was last checked more than 3 days ago, skip that topic, list the
  stale facts for the owner, and go on to the next topic.

When all are done, report in plain, simple English, one block per topic:

1. The topic, its score and one line why.
2. READY, NOT READY (and why), or skipped (and which facts were missing).
3. The paths to `short.mp4`, `picks.jpg` and `upload.txt`.
4. The title and the pacing check.

End with one list of every fact you wanted but could not use, so the owner can check them.
