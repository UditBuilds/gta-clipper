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

**Final config (frozen in PR 8, 2026-10-24, before any file in `C:\AI\gta\heldout\` was read).** The rule above,
recomputed from the committed PR 7 dev runs (eval.py and vlmeval.py on the 14 runs of eval/BASELINE.md v7 Table A;
model time from the first, uncached runs):

| Config | Costly vetoes | Postable@5, test1 + test2 | Kept but not postable | Model time (both videos) | Unrated picks |
|---|---|---|---|---|---|
| off | 0 | 8 of 10 | 2 | - | 0 |
| 4b v3 | 3 | 8 of 10 | 0 | 91.6 s | 0 |
| 8b v3 | 0 | 8 of 10 | 2 | 130.4 s | 0 |
| **4b v3 + star rule** | **0** | **8 of 10** | **0** | 91.6 s | 0 |
| 8b v3 + star rule | 0 | 8 of 10 | 2 | 130.4 s | 0 |
| 4b + stars | 4 | 8 of 10 | 2 | 91.1 s | 0 |
| 8b + stars | 1 | 8 of 10 | 2 | 127.8 s | 0 |

Step 1 leaves off, 8b v3, 4b v3 + star rule and 8b v3 + star rule; step 2 ties them at 8; step 3 leaves only
**4b v3 + star rule** (0 kept but not postable; the other three keep both clumsy crashes). Steps 4 and 5 aren't needed.

- **Chosen config:** 4b v3 + star rule, `clip.py <video> --vlm qwen3-vl:4b-instruct --vlm-rule stars`, all other
  flags default.
- **Code:** commit `d335101` (main after PR 7 was merged). This commit changes only this file. Before the runs of
  step 4, `git diff d335101 HEAD -- clip.py vlm.py eval.py vlmeval.py prompts/` must print nothing.
- **Ollama:** 0.40.8.
- **Model digests** (Ollama `/api/tags`, the same in the dev runs' scores.json):
  - `qwen3-vl:4b-instruct` 84f7ed8042fa7cdbd902f7dcb7d0370e032095047cc3244179f263b77b5bc318
  - `qwen3-vl:8b-instruct` 70e58e37beb15464e19aa903dedb2cba828b89c0fb186f75ff2738c7347b96eb
- **Prompt hashes.** vlm.py turns CRLF into LF before hashing, and records that sha256 in scores.json
  (`vlm.prompt_sha256`) and in the cache key. It is also the sha256 of the file as committed (LF):
  - `prompts/veto_v3.txt` 66c0f0af0d91b14de7997f8c9c255247f89604258bec5a0c2435376b752ccc8c (the v3 and star-rule
    configs)
  - `prompts/stars_v2.txt` 095fa39fdc85147f39b5cf0163f8e3c582e403fb4377a7a9e047aa6b5c67cb56 (the + stars configs)

  A Windows checkout with `core.autocrlf` has CRLF files, so `sha256sum` on them gives other values (50ee1217... and
  700cadfb...). Those are not the prompt hashes.

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

## Run (PR 8, 2026-10-24)

Done once, in this order; each step's result was committed before the next started.

| Step | Commit | Result |
|---|---|---|
| 1. Freeze | `6e0a9f3` | 4b v3 + star rule, code `d335101` (above) |
| - | `0c735e9` | `make_rating_page.py`: the blind rating page, written and tested on dev runs before any held-out run |
| 2. Keys | `489906e` | `heldout1.json` (3 action, 6 never-pick ranges), `heldout2.json` (1 action, 4 never-pick) |
| 3. Blind ratings | `0b87f92` | 14 clips: heldout1 10 (7 postable), heldout2 4 (2 postable) |
| 4. Runs | - | 14 runs, all checks passed, no crash, no repeat (eval/BASELINE.md, v8) |

The result, the tables and the deviations from this plan (the folder listing, the rename, heldout2's 4 candidates, the
keys' provenance) are in eval/BASELINE.md, section v8. **From now on heldout1 and heldout2 are dev data.**
