# Test timings after PR 31

Written by run_tests.py: each tier's part is replaced by its latest complete run. The seconds of a check that makes something other checks share (a render, a video) hold that too.

<!-- quick -->
## --quick: 615 checks in 2.6 min (Nov 28, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| runnertest | .venv | 11/11 | 0.7 | a_check_that_returns_a_tuple_can_fail (0.3 s) |
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.0 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.6 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 14/14 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| banktest | .venv | 69/69 | 16.8 | prepare_picks_from_the_bank_and_needs_no_new_recording (2.1 s) |
| memetest | .venv | 43/43 | 21.7 | a_full_bleed_render_has_its_caption_on_the_first_and_last_frames (4.6 s) |
| indextest | .venv | 49/49 | 3.5 | e2e_sheets_are_cut_at_the_right_times (1.3 s) |
| shorttest | .venv | 42/42 | 9.4 | every_picture_on_the_picks_sheet_must_get_on_screen (1.5 s) |
| calibtest | .venv | 35/35 | 8.1 | end_to_end_bars_found_and_guesses_confirmed (2.1 s) |
| hooktest | .venv | 33/33 | 19.8 | missing_hooks_txt_is_written_and_kept (9.9 s) |
| hudtest | .venv | 9/9 | 2.5 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 27/27 | 4.5 | a_finished_profile_is_never_written_again_by_hand_either (1.6 s) |
| launchtest | .venv-tts | 48/48 | 7.0 | subtitle_sheets_cut_the_band_at_full_size (1.9 s) |
| topictest | .venv-tts | 20/20 | 5.6 | a_full_screen_clip_has_its_inset_only_in_its_window (1.3 s) |
| storytest | .venv-tts | 15/15 | 8.2 | shots_are_slow_cuts_of_one_moment_in_time_order (6.7 s) |
| editortest | .venv-tts | 71/71 | 16.2 | render_punches_in_on_the_payoffs_shot_and_holds_it_over_the_replay (2.3 s) |
| explainertest | .venv-tts | 56/56 | 28.6 | text_stays_in_the_safe_area (3.7 s) |
| **all** | | **615** | **154.1** | |

Slowest checks:

- 9.9 s hooktest: missing_hooks_txt_is_written_and_kept
- 6.7 s storytest: shots_are_slow_cuts_of_one_moment_in_time_order
- 4.6 s memetest: a_full_bleed_render_has_its_caption_on_the_first_and_last_frames
- 4.0 s hooktest: bigger_layout_wins_and_stays_in_the_box
- 4.0 s memetest: a_length_that_is_not_whole_frames_keeps_every_frame
- 3.9 s memetest: a_letterbox_render_keeps_black_bars_and_the_caption_in_the_top_bar
- 3.8 s memetest: a_replay_jumps_back_and_plays_at_half_speed
- 3.8 s memetest: a_punch_in_zooms_toward_the_subject_by_its_factor
- 3.7 s explainertest: text_stays_in_the_safe_area
- 2.5 s explainertest: visuals_are_read
<!-- /quick -->

<!-- full -->
## --full: 695 checks in 10.2 min (Nov 28, 2026)

| suite | interpreter | checks | seconds | slowest check |
|---|---|---:|---:|---|
| runnertest | .venv | 11/11 | 0.7 | a_check_that_returns_a_tuple_can_fail (0.3 s) |
| evaltest | .venv | 21/21 | 0.1 | touch_at_clip_end_is_not_a_hit (0.0 s) |
| spreadtest | .venv | 13/13 | 0.2 | short_wall_splits_v9_not_v10 (0.1 s) |
| vlmtest | .venv | 39/39 | 0.6 | stars_options_stop_clip_py_at_once (0.5 s) |
| varietytest | .venv | 14/14 | 0.6 | repick_runs_on_cached_data_and_leaves_the_run_alone (0.4 s) |
| banktest | .venv | 69/69 | 16.9 | prepare_picks_from_the_bank_and_needs_no_new_recording (2.1 s) |
| memetest | .venv | 44/44 | 30.9 | the_measured_first_second_fires_on_a_real_dead_start_and_passes_samples_1_and_2 (9.4 s) |
| indextest | .venv | 49/49 | 3.5 | e2e_sheets_are_cut_at_the_right_times (1.3 s) |
| shorttest | .venv | 42/42 | 9.4 | every_picture_on_the_picks_sheet_must_get_on_screen (1.4 s) |
| calibtest | .venv | 38/38 | 23.4 | stars_found_in_made_up_star_rows (6.4 s) |
| hooktest | .venv | 51/51 | 70.0 | missing_hooks_txt_is_written_and_kept (9.9 s) |
| hudtest | .venv | 9/9 | 2.4 | test1 3 (0.5 s) |
| footagetest | .venv-tts | 32/32 | 18.2 | bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time (8.6 s) |
| launchtest | .venv-tts | 49/49 | 27.9 | an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak (20.7 s) |
| topictest | .venv-tts | 24/24 | 39.3 | engine_v3_makes_full_screen_shorts_and_checks_every_caption (19.7 s) |
| storytest | .venv-tts | 16/16 | 21.9 | a_real_story_short_renders (13.9 s) |
| editortest | .venv-tts | 81/81 | 160.0 | end_to_end_a_v4_short_with_its_replay (45.4 s) |
| explainertest | .venv-tts | 90/90 | 171.1 | look_render_has_no_black_screen (19.9 s) |
| selftest | .venv | 3/3 | 14.6 | 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s) (14.0 s) |
| **all** | | **695** | **611.7** | |

Slowest checks:

- 45.4 s editortest: end_to_end_a_v4_short_with_its_replay
- 42.7 s editortest: end_to_end_a_v4_short_names_its_payoff
- 20.7 s launchtest: an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak
- 19.9 s explainertest: look_render_has_no_black_screen
- 19.7 s topictest: engine_v3_makes_full_screen_shorts_and_checks_every_caption
- 19.6 s editortest: the_render_fails_on_the_clean_voice_and_only_flags_the_mix
- 19.1 s editortest: a_short_whose_hook_is_misheard_fails_and_one_heard_right_is_ready
- 15.8 s explainertest: beats_render_lock_on_follows_the_zoom
- 14.0 s selftest: 40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s)
- 13.9 s storytest: a_real_story_short_renders
<!-- /full -->
