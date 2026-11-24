# Test timings after PR 31

Written by run_tests.py: each tier's part is replaced by its latest complete run. The seconds of a check that makes something other checks share (a render, a video) hold that too.

<!-- quick -->
## --quick: 522 checks in 1.8 min (Nov 24, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| runnertest | .venv | 11/11 | 0.7 | a_check_that_returns_a_tuple_can_fail (0.3 s) |
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.1 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.7 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 14/14 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| banktest | .venv | 50/50 | 10.9 | prepare_picks_from_the_bank_and_needs_no_new_recording (2.1 s) |
| indextest | .venv | 41/41 | 1.9 | e2e_sheets_are_cut_at_the_right_times (1.3 s) |
| shorttest | .venv | 42/42 | 9.2 | every_picture_on_the_picks_sheet_must_get_on_screen (1.4 s) |
| calibtest | .venv | 35/35 | 7.1 | end_to_end_bars_found_and_guesses_confirmed (1.7 s) |
| hooktest | .venv | 33/33 | 19.3 | missing_hooks_txt_is_written_and_kept (9.7 s) |
| hudtest | .venv | 9/9 | 2.4 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 27/27 | 4.1 | a_finished_profile_is_never_written_again_by_hand_either (1.3 s) |
| launchtest | .venv-tts | 48/48 | 5.9 | subtitle_sheets_cut_the_band_at_full_size (1.5 s) |
| topictest | .venv-tts | 20/20 | 5.1 | a_full_screen_clip_has_its_inset_only_in_its_window (1.2 s) |
| storytest | .venv-tts | 15/15 | 7.4 | shots_are_slow_cuts_of_one_moment_in_time_order (6.0 s) |
| editortest | .venv-tts | 50/50 | 7.2 | a_daytime_shot_is_graded_exactly_as_before (1.8 s) |
| explainertest | .venv-tts | 54/54 | 26.8 | text_stays_in_the_safe_area (3.5 s) |
| **all** | | **522** | **109.7** | |

Slowest checks:

- 9.7 s hooktest: missing_hooks_txt_is_written_and_kept
- 6.0 s storytest: shots_are_slow_cuts_of_one_moment_in_time_order
- 3.9 s hooktest: bigger_layout_wins_and_stays_in_the_box
- 3.5 s explainertest: text_stays_in_the_safe_area
- 2.2 s explainertest: visuals_are_read
- 2.1 s banktest: prepare_picks_from_the_bank_and_needs_no_new_recording
- 1.8 s explainertest: focus_centres_the_subject
- 1.8 s editortest: a_daytime_shot_is_graded_exactly_as_before
- 1.7 s calibtest: end_to_end_bars_found_and_guesses_confirmed
- 1.5 s launchtest: subtitle_sheets_cut_the_band_at_full_size
<!-- /quick -->

<!-- full -->
## --full: 607 checks in 8.5 min (Nov 24, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| runnertest | .venv | 11/11 | 0.8 | a_bad_suite_fails_whatever_it_prints (0.3 s) |
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.1 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.7 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 14/14 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| banktest | .venv | 50/50 | 10.4 | prepare_picks_from_the_bank_and_needs_no_new_recording (1.9 s) |
| indextest | .venv | 49/49 | 3.5 | e2e_sheets_are_cut_at_the_right_times (1.3 s) |
| shorttest | .venv | 42/42 | 9.2 | every_picture_on_the_picks_sheet_must_get_on_screen (1.4 s) |
| calibtest | .venv | 38/38 | 23.2 | stars_found_in_made_up_star_rows (6.5 s) |
| hooktest | .venv | 51/51 | 70.2 | missing_hooks_txt_is_written_and_kept (9.9 s) |
| hudtest | .venv | 9/9 | 2.4 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 32/32 | 18.0 | bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time (8.5 s) |
| launchtest | .venv-tts | 49/49 | 27.4 | an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak (20.4 s) |
| topictest | .venv-tts | 24/24 | 38.8 | engine_v3_makes_full_screen_shorts_and_checks_every_caption (19.6 s) |
| storytest | .venv-tts | 16/16 | 21.5 | a_real_story_short_renders (13.8 s) |
| editortest | .venv-tts | 58/58 | 99.6 | end_to_end_a_v4_short_with_its_replay (39.8 s) |
| explainertest | .venv-tts | 88/88 | 166.4 | look_render_has_no_black_screen (20.0 s) |
| selftest | .venv | 3/3 | 14.4 | 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s) (13.8 s) |
| **all** | | **607** | **507.4** | |

Slowest checks:

- 39.8 s editortest: end_to_end_a_v4_short_with_its_replay
- 38.0 s editortest: end_to_end_a_v4_short_names_its_payoff
- 20.4 s launchtest: an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak
- 20.0 s explainertest: look_render_has_no_black_screen
- 19.6 s topictest: engine_v3_makes_full_screen_shorts_and_checks_every_caption
- 16.0 s explainertest: beats_render_lock_on_follows_the_zoom
- 13.8 s selftest: 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s)
- 13.8 s storytest: a_real_story_short_renders
- 11.1 s explainertest: banner_and_pill_stay_in_the_safe_area
- 10.3 s explainertest: music_and_sound_effects_render
<!-- /full -->
