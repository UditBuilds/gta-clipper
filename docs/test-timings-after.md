# Test timings after PR 31

Written by run_tests.py: each tier's part is replaced by its latest complete run. The seconds of a check that makes something other checks share (a render, a video) hold that too.

<!-- quick -->
## --quick: 550 checks in 2.0 min (Nov 26, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| runnertest | .venv | 11/11 | 0.6 | a_check_that_returns_a_tuple_can_fail (0.3 s) |
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.0 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.6 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 14/14 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| banktest | .venv | 52/52 | 10.8 | prepare_picks_from_the_bank_and_needs_no_new_recording (2.0 s) |
| indextest | .venv | 49/49 | 3.4 | e2e_sheets_are_cut_at_the_right_times (1.2 s) |
| shorttest | .venv | 42/42 | 9.0 | every_picture_on_the_picks_sheet_must_get_on_screen (1.4 s) |
| calibtest | .venv | 35/35 | 7.2 | end_to_end_bars_found_and_guesses_confirmed (1.7 s) |
| hooktest | .venv | 33/33 | 19.1 | missing_hooks_txt_is_written_and_kept (9.5 s) |
| hudtest | .venv | 9/9 | 2.3 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 27/27 | 4.1 | a_finished_profile_is_never_written_again_by_hand_either (1.3 s) |
| launchtest | .venv-tts | 48/48 | 6.0 | subtitle_sheets_cut_the_band_at_full_size (1.5 s) |
| topictest | .venv-tts | 20/20 | 5.1 | a_full_screen_clip_has_its_inset_only_in_its_window (1.2 s) |
| storytest | .venv-tts | 15/15 | 7.4 | shots_are_slow_cuts_of_one_moment_in_time_order (5.9 s) |
| editortest | .venv-tts | 67/67 | 14.9 | render_punches_in_on_the_payoffs_shot_and_holds_it_over_the_replay (2.2 s) |
| explainertest | .venv-tts | 55/55 | 26.8 | text_stays_in_the_safe_area (3.6 s) |
| **all** | | **550** | **118.4** | |

Slowest checks:

- 9.5 s hooktest: missing_hooks_txt_is_written_and_kept
- 5.9 s storytest: shots_are_slow_cuts_of_one_moment_in_time_order
- 3.9 s hooktest: bigger_layout_wins_and_stays_in_the_box
- 3.6 s explainertest: text_stays_in_the_safe_area
- 2.2 s explainertest: visuals_are_read
- 2.2 s editortest: render_punches_in_on_the_payoffs_shot_and_holds_it_over_the_replay
- 2.0 s banktest: prepare_picks_from_the_bank_and_needs_no_new_recording
- 1.9 s editortest: the_first_second_must_show_the_subject
- 1.8 s editortest: a_daytime_shot_is_graded_exactly_as_before
- 1.8 s explainertest: focus_centres_the_subject
<!-- /quick -->

<!-- full -->
## --full: 629 checks in 8.8 min (Nov 26, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| runnertest | .venv | 11/11 | 0.6 | a_check_that_returns_a_tuple_can_fail (0.3 s) |
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.0 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.6 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 14/14 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| banktest | .venv | 52/52 | 11.5 | prepare_picks_from_the_bank_and_needs_no_new_recording (2.2 s) |
| indextest | .venv | 49/49 | 3.4 | e2e_sheets_are_cut_at_the_right_times (1.2 s) |
| shorttest | .venv | 42/42 | 9.1 | every_picture_on_the_picks_sheet_must_get_on_screen (1.4 s) |
| calibtest | .venv | 38/38 | 22.4 | stars_found_in_made_up_star_rows (6.5 s) |
| hooktest | .venv | 51/51 | 67.4 | missing_hooks_txt_is_written_and_kept (9.6 s) |
| hudtest | .venv | 9/9 | 2.3 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 32/32 | 17.0 | bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time (8.5 s) |
| launchtest | .venv-tts | 49/49 | 25.7 | an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak (19.7 s) |
| topictest | .venv-tts | 24/24 | 36.6 | engine_v3_makes_full_screen_shorts_and_checks_every_caption (18.2 s) |
| storytest | .venv-tts | 16/16 | 20.2 | a_real_story_short_renders (12.9 s) |
| editortest | .venv-tts | 77/77 | 139.8 | end_to_end_a_v4_short_with_its_replay (39.8 s) |
| explainertest | .venv-tts | 89/89 | 154.1 | look_render_has_no_black_screen (17.9 s) |
| selftest | .venv | 3/3 | 14.3 | 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s) (13.8 s) |
| **all** | | **629** | **526.0** | |

Slowest checks:

- 39.8 s editortest: end_to_end_a_v4_short_with_its_replay
- 38.2 s editortest: end_to_end_a_v4_short_names_its_payoff
- 19.7 s launchtest: an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak
- 18.2 s topictest: engine_v3_makes_full_screen_shorts_and_checks_every_caption
- 17.9 s explainertest: look_render_has_no_black_screen
- 16.4 s editortest: the_render_fails_on_the_clean_voice_and_only_flags_the_mix
- 16.3 s editortest: a_short_whose_hook_is_misheard_fails_and_one_heard_right_is_ready
- 13.8 s explainertest: beats_render_lock_on_follows_the_zoom
- 13.8 s selftest: 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s)
- 12.9 s storytest: a_real_story_short_renders
<!-- /full -->
