# PR 35: bank hardening, the log

An outside read-only review named these problems against main after PR 32. Checked against main b231a7d (PR 33); the lines are main's.

## Each claim

| item | verdict | where on main | check | fails on main? | fix |
|---|---|---|---|---|---|
| B1 | confirmed | index.py:553-557, footage_short.py:396-397, launch.py:800 (and the same pattern in launch.py:154-156, `load_runs`) | banktest `a_damaged_record_stops_everything_and_keeps_every_byte` | yes: save_used writes over the damaged file | `index.read_record`: only a file that isn't there is empty; anything else unreadable stops with a plain error |
| B2 | confirmed | bank.py:1059-1060 (replaced, then read back) and 1143-1146 (topic before the spans) | banktest `a_topic_is_checked_before_the_list_is_replaced`, `posting_locks_the_footage_before_it_writes_the_topic` | yes, both: a broken topic is in the file; the topic is added and the footage stays free | the temp file is read back before it replaces the list; the spans are locked before the topic is written |
| B3 | confirmed | bank.py:1017 (the crash stays in verified whatever the label), 751-754 (shelf), launch.py:1112-1116, 1520 | banktest `an_unsure_crash_is_no_crash_to_the_picker_or_the_words`, `an_unsure_impact_is_not_on_the_action_shelf` | yes, both: event crash, payoff tier 3, type crash, the word allowed; all shelved as action | an unsure label keeps no impact in verified; an unsure impact or shootout is a list item |
| B4 | confirmed | launch.py:96-103 and 1111 (no shootout kind), 842-846, 1506 (compare topics too), bank.py:725-731 | banktest `gun_words_need_shooting_the_frames_show`, `words_nobody_verified_name_no_topic` | yes, both: "shoot", "gun", "shootout" pass; "overpass" from an unseen crash's line counts | a line with truth.py's shooting words is marked not seen unless the frames show shooting; lines that name an unconfirmed event are left out |
| B5 | confirmed in part | banktest.py:868-869: `... and A or B) and C`: the window part was bypassed; the stretch part (C) could still fail | banktest `a_short_may_not_grow_into_locked_footage` (fixed) | n/a (the code was right; with the window code broken, main's check passes and the fixed one fails) | the parentheses; it also says what it got |
| B6 | confirmed | launchtest.py:1388-1389 (returns a tuple), 1442 (bool of it) | runnertest `a_check_that_returns_a_tuple_can_fail` (a scan of all 18 suites: this was the only one), launchtest's swearing check | yes: "can never fail: launchtest.py: spoken_swearing_in_the_first_7_s_is_found" | launchtest's main unpacks (ok, what it got), as banktest, topictest and varietytest do |
| B7 | confirmed | launch.py:179-187 against bank.py:912 | launchtest `only_gta_6_recordings_are_taken_for_a_gta_6_night` | yes: find_recordings takes `Grand Theft Auto V_...`, `test1.webm` and the GTA 6 file; the bank only the last | one prefix for both (`launch.GAME_FILES`); --videos is still used as given |
| B8 | confirmed | bank.py:773-783 (50 %), 968-973 (edges only) | banktest `footage_that_overlaps_posted_or_rejected_is_never_offered` | yes: a moment 27 % inside a rejected span is offered | any overlap over 0.1 s with posted or rejected footage keeps a moment out of the picker (its status on the page is as before) |
| B9 | confirmed | launch.py:499 and 1694 | banktest `a_proof_short_keeps_its_topic_as_its_type` | yes: category "star rise", picked right after a posted heat-icons Short | verify_terms keeps `topic: <id>`; `moment_type` uses the type the owner's own list gives that topic, and `bank.py posted` writes the same one |
| B10 | confirmed | bank.py:1166 (rejections by id), 387-390 and 496-500 (ids), 1078-1085 (Shorts by id) | banktest `rejecting_a_moment_keeps_every_earlier_rejection`, `ids_are_unique_and_stay_with_their_moment_on_a_rebuild`, `a_rendered_short_belongs_to_the_moment_it_shows` | yes: both earlier rejections gone; another id after a rebuild; (the third fails because shorts_of took only an id) | a rejection replaces only the one of the same footage; a rebuilt moment keeps the id of the old one on its seconds; a Short counts for a moment only when it shows it |
| B11 | confirmed, all three | bank.py:595 (`any(loud)`), 595-598 (returns at once), 603-604 (falls through) | banktest `a_crash_nobody_measured_the_sound_of_is_unsure`, `a_quiet_crash_does_not_hide_what_else_the_peak_shows`, `a_crash_that_is_only_ticked_names_nothing_else` | yes: sure crash without sound; five times unsure; reaction and chase, sure | unmeasured is not heard; a quiet or only-ticked impact leaves the peak in doubt: another impact, a fall, shooting or a HUD change may name it, nothing softer |
| B12 | confirmed | launch.py:1744-1747 and 1831, bank.py:884-903 | banktest `an_empty_build_never_replaces_a_full_bank` | yes: 0 moments written over the bank | build refuses to write an empty bank over one that isn't |
| B13 | half refuted, half confirmed | footage_short.py:271 and 400-404 | footagetest `rejected_footage_is_never_picked` | yes: there is no rejected list in it at all | Refuted: rendered footage isn't locked there, it is only left alone while other footage fits (footagetest pins that twice). Confirmed: rejected footage was ignored; it is never picked now. footage_short.py is live (/footage-short) |

## Deliberate breaks

Each one is one change to the fixed code; the suite that should notice was run, then the file put back.

First round: 34 breaks, 32 caught. The two that were not (marked) meant a check was too weak: the questions check never got as far as the line filter, and the id check had no moment that wanted a gone one's id. Both were made stricter, and their items run again. A 35th came from a last read of the diff (a damaged posted_topics.json ended `bank.py posted` in a traceback). **35 breaks, 35 caught**.

| item | break | file | caught by |
|---|---|---|---|
| B1 | a record that can't be read counts as empty again | index.py | `a_damaged_record_stops_everything_and_keeps_every_byte` |
| B1 | a record of the wrong shape counts as empty | index.py | `a_damaged_record_stops_everything_and_keeps_every_byte` |
| B1 | only load_posted reads the old way | launch.py | `a_damaged_record_stops_everything_and_keeps_every_byte` |
| B1 | only load_runs reads the old way | launch.py | `a_damaged_record_stops_everything_and_keeps_every_byte` |
| B2 | the topic's temp file isn't read back before it takes the list's place | bank.py | `a_topic_is_checked_before_the_list_is_replaced` |
| B2 | a refused topic leaves its temp file | bank.py | `a_topic_is_checked_before_the_list_is_replaced` |
| B2 | the topic is written before the footage is locked | bank.py | `marking_a_moment_posted_locks_it_and_adds_its_topic`, `marking_by_id_locks_all_the_short_showed`, `a_posted_shorts_type_is_never_guessed`, `posting_locks_the_footage_before_it_writes_the_topic` |
| B3 | an unsure label keeps the crash seen at its peak | bank.py | `an_unsure_crash_is_no_crash_to_the_picker_or_the_words` |
| B3 | an unsure impact is shelved as action | bank.py | `an_unsure_impact_is_not_on_the_action_shelf` |
| B4 | gun words are not marked as not seen | launch.py | `gun_words_need_shooting_the_frames_show` |
| B4 | moment_text uses lines nothing confirms | launch.py | `words_nobody_verified_name_no_topic` |
| B4 | unverified lines reach the compare topics | launch.py | `words_nobody_verified_name_no_topic` |
| B4 | a question is answered by what the moment was mined as | bank.py | `words_nobody_verified_name_no_topic` |
| B4 | a question's words may come from a line nothing confirms | bank.py | `words_nobody_verified_name_no_topic` (not caught in the first round) |
| B5 | an action clip's window runs past its stretch into posted footage | launch.py | `a_short_may_not_grow_into_locked_footage` |
| B6 | the swearing check finds nothing | speech.py | `spoken_swearing_in_the_first_7_s_is_found` |
| B6 | launchtest takes a tuple for true again | launchtest.py | `a_check_that_returns_a_tuple_can_fail` |
| B7 | /launch takes every video in the folder | launch.py | `only_gta_6_recordings_are_taken_for_a_gta_6_night` |
| B7 | the filter also refuses --videos | launch.py | `only_new_recordings_are_taken_and_held_out_never`, `only_gta_6_recordings_are_taken_for_a_gta_6_night` |
| B8 | overlaps with locked footage are only trimmed | bank.py | `footage_that_overlaps_posted_or_rejected_is_never_offered` |
| B8 | only posted footage counts | bank.py | `footage_that_overlaps_posted_or_rejected_is_never_offered` |
| B9 | verify_terms rewrites a topic's category | launch.py | `a_proof_short_keeps_its_topic_as_its_type` |
| B9 | a topic's type is its category's again | launch.py | `a_proof_short_keeps_its_topic_as_its_type` |
| B9 | a proof Short is written down without its topic | bank.py | `a_proof_short_keeps_its_topic_as_its_type` |
| B10 | a rejection replaces every earlier one of its id | bank.py | `rejecting_a_moment_keeps_every_earlier_rejection` |
| B10 | ids are the start second again | bank.py | `ids_are_unique_and_stay_with_their_moment_on_a_rebuild` |
| B10 | a gone moment's id is handed out again | bank.py | `ids_are_unique_and_stay_with_their_moment_on_a_rebuild` (not caught in the first round) |
| B10 | a Short counts by its id alone | bank.py | `a_rendered_short_belongs_to_the_moment_it_shows` |
| B11 | unmeasured sound counts as heard | bank.py | `a_crash_must_be_heard_and_a_wreck_is_not_one`, `a_crash_nobody_measured_the_sound_of_is_unsure` |
| B11 | a quiet crash is unsure at once | bank.py | `a_quiet_crash_does_not_hide_what_else_the_peak_shows`, `a_crash_that_is_only_ticked_names_nothing_else` |
| B11 | a crash that is only ticked falls through | bank.py | `a_label_the_peak_does_not_show_is_unsure_and_counts_for_nothing`, `a_crash_must_be_heard_and_a_wreck_is_not_one`, `a_crash_that_is_only_ticked_names_nothing_else` |
| B12 | an empty build is written | bank.py | `an_empty_build_never_replaces_a_full_bank` |
| B2 | add_topic reads the topics list without checking it | bank.py | `a_damaged_record_stops_everything_and_keeps_every_byte` |
| B13 | /footage-short ignores rejected footage | footage_short.py | `rejected_footage_is_never_picked` |
| B13 | a whole run doesn't read the rejected list | footage_short.py | `rejected_footage_is_never_picked` |

B5 the other way round: with the window code broken, main's version of the check still says True, the fixed one False.

## Checks whose assertions or fixtures changed

- banktest `a_crash_must_be_heard_and_a_wreck_is_not_one`: a crash whose sound was never measured was expected to be a crash; B11 asks for the opposite, so it is expected to be unsure. Stricter, not weaker.
- banktest `a_short_may_not_grow_into_locked_footage`: B5's parentheses.
- banktest `prepare_picks_from_the_bank_and_needs_no_new_recording`: fixtures only. Its new recordings are called `Grand Theft Auto VI_...` (B7) and get the made-up sound every other made-up recording has (B11); same assertions.
- launchtest: the three checks that run prepare() on new recordings call them `Grand Theft Auto VI_...` (B7); same assertions.

## What it changes on the real bank (read-only, no model call, Nov 24)

419 moments before and after, every id the same. 15 unsure moments move to the list shelf (9 from action, 6 from proof: what they were mined as is not what their peak shows). No moment with a payoff of 2/3 or more changes shelf, label or payoff.
