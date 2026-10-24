# Held-out test plan (pre-registered, not run)

Written in PR 7 (2026-10-24), before any held-out output exists, and approved by the owner. The held-out videos are
`heldout1` and `heldout2` in `C:\AI\gta\heldout\`. Until the owner says go, nothing in that folder is read, listed or
hashed. The test is run once. Every step below happens in this order, and each step's result is committed before
the next one starts.

## 1. Freeze the code and choose the final config (from dev results only)

The configs, as named in eval/BASELINE.md:

| Config | clip.py flags |
|---|---|
| off | (none) |
| 4b v3 | `--vlm qwen3-vl:4b-instruct` |
| 8b v3 | `--vlm qwen3-vl:8b-instruct` |
| 4b v3 + star rule | `--vlm qwen3-vl:4b-instruct --vlm-rule stars` |
| 8b v3 + star rule | `--vlm qwen3-vl:8b-instruct --vlm-rule stars` |
| 4b + stars | `--vlm qwen3-vl:4b-instruct --vlm-hint stars` (prompt stars_v2, the final version; see prompts/LOG.md) |
| 8b + stars | `--vlm qwen3-vl:8b-instruct --vlm-hint stars` |

All other flags are the defaults (`--count 5`, 15-30 s clips, gap 10 s).

**Before the final config is chosen,** the owner rates every pick that has no rating in the PR 7 dev runs of the
configs above (not the runs of stars_v1, a failed version), so that none of their picks is unrated.

**The selection rule.** It is applied to the dev results on test1 and test2, with the ratings as they are at that
point:
1. Only configs with 0 costly vetoes qualify. A costly veto is a candidate rated postable that the config vetoed
   (Table B, both videos). `off` vetoes nothing, so it always qualifies.
2. Among the configs that qualify, the highest Postable@5, summed over test1 and test2, wins. Unrated counts as not
   postable.
3. Ties go to the fewest "kept but not postable" in Table B. For `off`, that is every candidate rated not postable,
   because it keeps everything.
4. Then the lowest model time: `vlm_model` + `vlm_unload` of the first, uncached runs, summed over both videos. A
   star-rule config carries its v3 config's model time (same answers). `off` has none.
5. `off` wins any tie that remains. Between two `--vlm` configs that are still tied, the smaller model wins, then the
   config listed first in the table above.

The chosen config, the commit hash of the code, both model digests (`qwen3-vl:4b-instruct` 84f7ed8042fa,
`qwen3-vl:8b-instruct` 70e58e37beb1) and the sha256 of every prompt file used are then written into this file and
committed, before step 2. If Ollama reports a different digest at run time, the run stops; that is reported, and
nothing is substituted.

**Status at the end of PR 7 (not yet final).** None of the seven configs' dev picks is unrated, and on the PR 7 results
the rule gives **4b v3 + star rule** (eval/BASELINE.md, v7):
- 0 costly vetoes: off, 8b v3, 4b v3 + star rule and 8b v3 + star rule;
- all four have Postable@5 8 of 10;
- 4b v3 + star rule has 0 kept but not postable, the others 2.

It becomes final, with the commit hash, digests and prompt hashes, in a commit after PR 7 is merged.

## 2. Answer keys (before any clip.py run on held-out)

With the owner's go, Claude drafts `eval/answer_keys/heldout1.json` and `heldout2.json` the same way as test1/test2:
- contact sheets of one frame every 10 s;
- pause-map, phone-call and cutscene edges checked on 2.5 s and 0.5 s sheets;
- `action` and `never_pick` ranges, with notes.

The owner confirms or corrects them, and they are committed. No clip.py output exists yet, so the keys can't be
fitted to any config's picks.

## 3. Blind owner ratings (before any `--vlm` run)

One run per video, `clip.py <video> --count 10` (`--vlm off`). Its 10 clips are exactly the 10 candidates every
`--vlm` run judges: with `--vlm` the picker makes 2 x `--count` candidates in the same call (vlmtest checks the first
5 of 10 are the 5 picks).

- The owner watches the 10 clips per video on a review page of that run (an off run's page has no model verdicts) and
  rates every one: postable yes/no and a reason.
- The ratings are stored as `eval/ratings/heldout1_blind.json` and `heldout2_blind.json`, with `peak_s` on each clip,
  and committed.
- So every pick of every config, and every candidate in Table B, has a rating that was given before any model
  verdict was seen. There are 0 unrated picks.

## 4. The runs

- Each config runs once per video, in the order of the table above: v3 before its star-rule config, so the rule uses
  the v3 answers from the cache.
- One run at a time, with the GPU log, pre-flight checks and the 85 C stop rule as in PR 6/7.
- A fresh cache: the held-out videos have no cached answers, because the key includes the video's name and size.
- A run that crashes before writing scores.json may be repeated once, and the reason is logged. No other run is
  repeated.
- Before any table is built, each `--vlm` run's 10 candidates are checked against the rating run's 10 clips (same
  start, end and peak). If they differ, the test stops and that is reported.

## 5. What gets reported

- **Table A** per video and summed: hits (strict), recall, bad picks, Postable@5, unrated (expected 0), average length,
  model time. One row per config.
- **Table B:** vetoed but postable, kept but not postable, and agreements over the 20 rated candidates, one row per
  `--vlm` config.
- **Table C:** each config's vetoes per video, with the candidate and category.
- **Primary result:** the chosen config against `off`. The other configs are secondary: they are reported but were
  not chosen on held-out data.
- Dev results (PR 7 tables) next to the held-out ones, and the GPU peaks.
- Two lists: what was observed, and what was only reasoned about.

## 6. After the test

- Nothing in the code, the prompts, the thresholds or the selection rule changes because of held-out output.
- Any idea that held-out results suggest is a new version. After the test, heldout1 and heldout2 are dev data, and a
  later claim needs new held-out footage.
- With 10 candidates per video, the held-out test can show a gross failure or a clear win, not an accuracy.
