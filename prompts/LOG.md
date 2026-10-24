# Veto prompt versions

Every prompt tried on the dev set (test1, test2) is kept here with its results, failed ones included. At most 3
versions may be tried; the last one is frozen for the held-out test.

**Stopping rule (approved by the owner before any result):** a new version is written only to fix a mistake the
previous version made, and its reason is logged here before it runs. Stop at the first version where neither model
vetoes a clip the owner rated postable; otherwise v3 is final, whatever its results. No version may describe a
test1/test2 moment.

All runs: contact sheet version 2 (8 frames, 1536x480, the frame nearest the peak moved onto it), temperature 0,
seed 42, num_ctx 4096, thinking off (the Instruct builds have no thinking mode). Table B counts the 10 candidates
with an owner rating (test1 1-5, all postable; test2 1-5, of which 1 and 3 are not postable).

## v1 (`veto_v1.txt`), 2026-10-24

The text approved in the plan, plus one sentence for the PEAK label (decision 3 came after the plan).

| Model | Run test1 | Run test2 | Vetoed but postable | Kept but not postable | Agreements |
|---|---|---|---|---|---|
| qwen3-vl:4b-instruct | 20261024-140946 | 20261024-140721 | **2** (test1 #1 3:10.2, #3 3:50.2) | 1 (test2 #1) | 7 |
| qwen3-vl:8b-instruct | 20261024-141511 | 20261024-141231 | **1 + 1** (test1 #1 3:10.2, test2 #2 4:40.0) | 2 (test2 #1, #3) | 6 |

Result: fails the stopping rule (both models veto postable clips).

What went wrong: all four costly vetoes are chases (police with wanted stars in test1, a gang chase in test2) that
the model called `boring_driving_or_walking`, with reasons like "drives through city streets ... with no chase or
combat". Still frames can't show speed or who is behind, and the wanted stars are about 30x8 px in a 384x216
frame. The 4b also wrote "wanted stars" for two test2 clips, which have none.

## v2 (`veto_v2.txt`), reason logged before running

Fixes v1's only costly mistake, chases called boring driving. Two changes, nothing else:
- `boring_driving_or_walking` now needs calm, ordinary play in every frame, and says that driving alone is not
  enough because stills can't show speed: possible racing, escaping or following goes to chase_or_shootout or
  other_action.
- The unsure sentence now also says that every candidate was picked for unusually busy sound and picture, so a calm
  clip should be rare (true for every candidate: each has a step scoring 2.0 or more).

The clumsy-crash misses (kept but not postable) are not addressed in v2: they don't count for the stopping rule,
and a stricter clumsy rule could add costly vetoes.
