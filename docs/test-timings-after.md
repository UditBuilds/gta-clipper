# Test timings after PR 31

Written by run_tests.py: each tier's part is replaced by its latest complete run. The seconds of a check that makes something other checks share (a render, a video) hold that too.

<!-- quick -->
## --quick: 452 checks in 1.7 min (Nov 23, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.1 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.7 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 13/13 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| indextest | .venv | 41/41 | 2.1 | e2e_sheets_are_cut_at_the_right_times (1.4 s) |
| shorttest | .venv | 42/42 | 9.7 | every_picture_on_the_picks_sheet_must_get_on_screen (1.5 s) |
| calibtest | .venv | 35/35 | 7.9 | end_to_end_bars_found_and_guesses_confirmed (2.1 s) |
| hooktest | .venv | 33/33 | 20.5 | missing_hooks_txt_is_written_and_kept (10.2 s) |
| hudtest | .venv | 9/9 | 2.5 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 26/26 | 4.6 | a_finished_profile_is_never_written_again_by_hand_either (1.6 s) |
| launchtest | .venv-tts | 47/47 | 7.3 | subtitle_sheets_cut_the_band_at_full_size (1.9 s) |
| topictest | .venv-tts | 20/20 | 5.7 | a_full_screen_clip_has_its_inset_only_in_its_window (1.3 s) |
| storytest | .venv-tts | 15/15 | 8.9 | shots_are_slow_cuts_of_one_moment_in_time_order (7.4 s) |
| editortest | .venv-tts | 44/44 | 6.6 | a_daytime_shot_is_graded_exactly_as_before (1.9 s) |
| explainertest | .venv-tts | 54/54 | 26.2 | text_stays_in_the_safe_area (3.7 s) |
| **all** | | **452** | **103.5** | |

Slowest checks:

- 10.2 s hooktest: missing_hooks_txt_is_written_and_kept
- 7.4 s storytest: shots_are_slow_cuts_of_one_moment_in_time_order
- 4.1 s hooktest: bigger_layout_wins_and_stays_in_the_box
- 3.7 s explainertest: text_stays_in_the_safe_area
- 2.6 s explainertest: visuals_are_read
- 2.1 s calibtest: end_to_end_bars_found_and_guesses_confirmed
- 1.9 s launchtest: subtitle_sheets_cut_the_band_at_full_size
- 1.9 s editortest: a_daytime_shot_is_graded_exactly_as_before
- 1.9 s launchtest: safety_sheets_are_4_frames_a_second_apart
- 1.9 s explainertest: focus_centres_the_subject
<!-- /quick -->

<!-- full -->
## --full: 528 checks in 8.4 min (Nov 23, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.1 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.7 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 13/13 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| indextest | .venv | 41/41 | 2.0 | e2e_sheets_are_cut_at_the_right_times (1.4 s) |
| shorttest | .venv | 42/42 | 9.8 | every_picture_on_the_picks_sheet_must_get_on_screen (1.5 s) |
| calibtest | .venv | 38/38 | 26.9 | stars_found_in_made_up_star_rows (8.0 s) |
| hooktest | .venv | 51/51 | 72.4 | missing_hooks_txt_is_written_and_kept (10.3 s) |
| hudtest | .venv | 9/9 | 2.5 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 31/31 | 18.7 | bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time (8.8 s) |
| launchtest | .venv-tts | 48/48 | 28.2 | an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak (20.8 s) |
| topictest | .venv-tts | 24/24 | 40.4 | engine_v3_makes_full_screen_shorts_and_checks_every_caption (20.3 s) |
| storytest | .venv-tts | 16/16 | 23.1 | a_real_story_short_renders (14.1 s) |
| editortest | .venv-tts | 51/51 | 97.0 | end_to_end_a_v4_short_with_its_replay (41.3 s) |
| explainertest | .venv-tts | 88/88 | 167.3 | look_render_has_no_black_screen (20.1 s) |
| selftest | .venv | 3/3 | 14.7 | 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s) (14.1 s) |
| **all** | | **528** | **504.5** | |

Slowest checks:

- 41.3 s editortest: end_to_end_a_v4_short_with_its_replay
- 40.0 s editortest: end_to_end_a_v4_short_names_its_payoff
- 20.8 s launchtest: an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak
- 20.3 s topictest: engine_v3_makes_full_screen_shorts_and_checks_every_caption
- 20.1 s explainertest: look_render_has_no_black_screen
- 16.2 s explainertest: beats_render_lock_on_follows_the_zoom
- 14.1 s selftest: 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s)
- 14.1 s storytest: a_real_story_short_renders
- 11.3 s explainertest: banner_and_pill_stay_in_the_safe_area
- 11.0 s explainertest: beats_render_video_backgrounds_and_cards
<!-- /full -->
