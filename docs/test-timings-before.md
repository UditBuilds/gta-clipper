# Test timings before PR 31

Main 31d6ab7 (PR 30), every suite once, one after another, nothing else heavy running, on the owner's laptop (i7-14650HX, 24 GB,
RTX 5060 Laptop, plugged in, Balanced power plan), Nov 23 2026, 21:45-23:01. Measured from outside: a clean copy of main, each
output line timestamped, each subprocess logged through a PYTHONPATH hook; no test file was touched. A check's seconds
are the time since the line before it, so a fixture that several checks share (a render, a video) counts for the first
check that asks for it.

**522 checks, all pass, 4532 s (75.5 min).**

## Where the time goes: ffmpeg's start

The suites start ffmpeg **2297 times**, and every start costs about **1.9 s** before it does anything:
`ffmpeg -version` takes 1.95 s, and the fastest of all these calls took 1.74 s. `ffprobe` (the same build, the same size)
takes 0.03 s. It is not ffmpeg: a byte-identical copy of the same ffmpeg.exe in another folder starts in 0.03 s from its
second start on (120 starts: median 0.033 s, the slowest 0.044 s). Most likely the virus scanner (McAfee; Windows Defender is
off) reads the installed 211 MB file again at every start, and remembers a fresh copy after one scan. It was not so on Nov 18,
when explainertest took 2.7 min.

| suite | ffmpeg starts | start-up alone (x 1.9 s) | share of the suite |
|---|---:|---:|---:|
| explainertest | 699 | 1328 s | 95% |
| hooktest | 596 | 1132 s | 97% |
| editortest | 299 | 568 s | 79% |
| shorttest | 203 | 382 s | 100% |
| topictest | 106 | 201 s | 85% |
| launchtest | 83 | 158 s | 80% |
| footagetest | 165 | 140 s | 92% |
| calibtest | 96 | 129 s | 88% |
| storytest | 28 | 53 s | 70% |
| selftest | 10 | 19 s | 57% |
| hudtest | 9 | 17 s | 91% |
| indextest | 3 | 6 s | 75% |
| **all** | **2297** | **4133 s** | **91%** |

Production renders pay the same: one v4 Short of the end-to-end check starts ffmpeg 58 times, about 110 s of it start-up.

## Suites

| suite | interpreter | checks | seconds |
|---|---|---:|---:|
| explainertest | .venv-tts | 88 | 1396.5 |
| hooktest | .venv | 51 | 1165.9 |
| editortest | .venv-tts | 45 | 714.6 |
| shorttest | .venv | 42 | 383.4 |
| topictest | .venv-tts | 24 | 237.8 |
| launchtest | .venv-tts | 48 | 196.5 |
| footagetest | .venv-tts | 31 | 151.9 |
| calibtest | .venv | 38 | 147.4 |
| storytest | .venv-tts | 16 | 76.4 |
| selftest | .venv | 3 | 33.2 |
| hudtest | .venv | 9 | 18.8 |
| indextest | .venv | 41 | 7.6 |
| vlmtest | .venv | 39 | 1.1 |
| varietytest | .venv | 13 | 0.8 |
| spreadtest | .venv | 13 | 0.3 |
| evaltest | .venv | 21 | 0.1 |
| **all 16** | | **522** | **4532.3** |

## The 3 slowest checks of each suite, and why

**explainertest** (1396 s)

- 156.2 s `look_render_has_no_black_screen`: 82 ffmpeg starts: a real Short with Kokoro (explainer.py), then a frame grabbed every quarter second, one ffmpeg each
- 148.1 s `beats_render_video_backgrounds_and_cards`: 75 ffmpeg starts: one per frame looked at
- 110.4 s `banner_and_pill_stay_in_the_safe_area`: 54 ffmpeg starts: one per frame of the pill drawn (libass)

**hooktest** (1166 s)

- 182.4 s `bigger_layout_wins_and_stays_in_the_box`: 97 ffmpeg starts: fit_banner tries sizes, one ffmpeg (libass) each
- 154.9 s `missing_hooks_txt_is_written_and_kept`: 79 ffmpeg starts: 5 clips made, clip.py --burn-hooks three times (the fixture 10 checks share)
- 78.0 s `cold_open_plays_the_moment_first_then_the_whole_clip`: 40 ffmpeg starts: 3 clips made, one --burn-hooks run, then single frames

**editortest** (715 s)

- 191.0 s `end_to_end_a_v4_short_with_its_replay`: 80 ffmpeg starts: a 40 s video made, then a whole v4 Short with vidstab, 1080x1920 x264 pieces, NVENC, and faster-whisper (model load + 17 s on the CPU)
- 144.8 s `end_to_end_a_v4_short_names_its_payoff`: 58 ffmpeg starts: the same video made again, a whole v4 Short, faster-whisper
- 134.6 s `no_subscribe_pill_and_no_click_in_a_v4_short`: 53 ffmpeg starts: the same Short rendered a third time

**shorttest** (383 s)

- 60.4 s `every_picture_on_the_picks_sheet_must_get_on_screen`: 32 ffmpeg starts (60 of its 60 s)
- 38.4 s `a_stale_fact_stops_the_render_unless_the_owner_allows_it`: 20 ffmpeg starts (38 of its 38 s)
- 32.0 s `tagger_skips_what_is_tagged_unless_forced`: 17 ffmpeg starts (32 of its 32 s)

**topictest** (238 s)

- 124.0 s `engine_v3_makes_full_screen_shorts_and_checks_every_caption`: 55 ffmpeg starts (118 of its 124 s)
- 67.9 s `a_dark_blank_or_back_only_shot_or_one_with_text_at_the_bottom_is_rejected`: 34 ffmpeg starts (67 of its 68 s)
- 9.8 s `the_beat_question_does_not_see_our_own_inset`: 5 ffmpeg starts (10 of its 10 s)

**launchtest** (196 s)

- 140.9 s `an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak`: 60 ffmpeg starts (132 of its 141 s)
- 15.2 s `a_render_over_minus_1_dbtp_is_limited_and_measured_again`: 8 ffmpeg starts (15 of its 15 s)
- 14.8 s `a_short_that_breaks_a_rule_is_not_rendered_and_says_why`: 6 ffmpeg starts (12 of its 15 s)

**footagetest** (152 s)

- 97.4 s `guided_run_on_a_recording_without_stars_and_the_rules_about_overwriting`: 135 ffmpeg starts (97 of its 97 s)
- 33.6 s `a_finished_profile_is_never_written_again_by_hand_either`: 25 ffmpeg starts (33 of its 34 s)
- 8.6 s `bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time`: pure Python: no ffmpeg (made-up pictures and numbers)

**calibtest** (147 s)

- 48.4 s `end_to_end_bars_found_and_guesses_confirmed`: 37 ffmpeg starts (48 of its 48 s)
- 46.3 s `end_to_end_stars_found_in_a_made_up_video`: 34 ffmpeg starts (45 of its 46 s)
- 36.5 s `end_to_end_on_a_made_up_video`: 25 ffmpeg starts (36 of its 36 s)

**storytest** (76 s)

- 54.2 s `a_real_story_short_renders`: 22 ffmpeg starts (52 of its 54 s)
- 8.7 s `the_v2_render_checks_timing_and_logs_the_story`: 3 ffmpeg starts (6 of its 9 s)
- 8.4 s `shots_are_slow_cuts_of_one_moment_in_time_order`: 1 ffmpeg starts (8 of its 8 s)

**selftest** (33 s)

- 27.2 s `there is no GTA V HUD here`: 7 ffmpeg starts (27 of its 27 s), 1 run(s) of explainer.py or clip.py
- 2.0 s `clip_02_2m14s.mp4`: 1 ffmpeg starts (2 of its 2 s)
- 2.0 s `clip_03_1m24s.mp4`: 1 ffmpeg starts (2 of its 2 s)

**hudtest** (19 s)

- 2.3 s `test1 3`: 2 ffmpeg starts (4 of its 2 s)
- 2.1 s `test1 1`: 1 ffmpeg starts (2 of its 2 s)
- 2.1 s `test1 8`: 1 ffmpeg starts (2 of its 2 s)

**indextest** (8 s)

- 6.9 s `e2e_sheets_are_cut_at_the_right_times`: 3 ffmpeg starts (7 of its 7 s)
- 0.3 s `drawn_stars_are_counted`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.2 s `schema_is_strict_and_describes_first`: pure Python: no ffmpeg (made-up pictures and numbers)

**vlmtest** (1 s)

- 0.8 s `stars_options_stop_clip_py_at_once`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.1 s `no_veto_keeps_the_first_five`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.0 s `veto_step_star_rule_and_v6_shape`: pure Python: no ffmpeg (made-up pictures and numbers)

**varietytest** (1 s)

- 0.4 s `repick_runs_on_cached_data_and_leaves_the_run_alone`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.3 s `the_nov20_case_puts_the_bike_crash_on_top_and_no_near_duplicates`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.0 s `the_posted_list_is_read_only_and_checked`: pure Python: no ffmpeg (made-up pictures and numbers)

**spreadtest** (0 s)

- 0.2 s `short_wall_splits_v9_not_v10`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.1 s `v10_caps_the_chase_and_spreads`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.0 s `without_walls_v9_and_v10_are_the_same`: pure Python: no ffmpeg (made-up pictures and numbers)

**evaltest** (0 s)

- 0.1 s `touch_at_clip_end_is_not_a_hit`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.0 s `every_rating_in_the_repo_has_a_peak`: pure Python: no ffmpeg (made-up pictures and numbers)
- 0.0 s `test1_v3_clip_4_keeps_its_own_rating`: pure Python: no ffmpeg (made-up pictures and numbers)

## Every check

<details><summary>seconds per check, in the order they run</summary>

**explainertest**

```
     0.2  times_are_read
    18.9  visuals_are_read
     0.2  every_problem_stops_before_rendering
     0.0  shot_objects_are_read
     0.3  bad_shots_stop_before_rendering
     0.5  bad_json_and_bad_settings_stop
     3.8  sound_settings_are_read
     0.5  bad_sound_settings_stop
     0.0  auto_music_picks_from_the_music_folder
     0.0  music_dips_under_speech_and_lifts_in_long_pauses
     0.0  sound_effects_follow_lines_pans_and_cards
    11.3  sound_effects_sit_under_the_voice
     0.0  dollar_amounts_warn
     0.0  speed_and_length_warn
     0.0  words_line_up_with_the_sentence
     0.0  unrelated_phonemes_are_refused
     0.0  chunks_follow_the_rules
     0.0  tagged_phrase_stays_on_one_line
     0.0  tags_colour_the_right_words
   109.1  text_stays_in_the_safe_area
     9.6  outline_and_shadow_stay_in_the_safe_area_and_off_the_cards
     5.5  anton_is_really_used
     0.0  slots_cover_the_voice_exactly
     0.0  the_loop_ending_never_takes_a_pictures_whole_time
     0.0  a_short_last_line_keeps_the_pacing_rules
     0.0  motions_vary_and_repeat_exactly
    35.4  ken_burns_moves_the_way_it_says
    52.9  image_loop_ending_lands_on_the_first_frame
    12.0  framing_picks_the_part_of_the_photo
    30.0  focus_centres_the_subject
    17.9  video_focus_moves_the_crop
    13.9  pan_slides_evenly_edge_to_edge
    13.3  pan_opening_loops_back
    70.8  video_opening_loops_back
     0.0  weights_share_a_line
     0.0  pacing_is_counted_and_warned
     6.2  crossed_out_card_draws_a_bar_over_its_text
    12.0  short_clip_that_cannot_loop_holds
    12.3  short_clip_that_can_loop_loops
     0.0  clip_long_enough_just_plays
    45.9  run_folder_is_as_asked
     0.4  short_is_1080x1920_at_30_fps
     0.4  video_length_matches_the_voice
     0.0  captions_hold_the_tag_colours
     6.0  colours_really_show_on_screen
     3.9  opening_card_shows_then_goes
     1.9  video_fills_the_frame_with_no_blur_or_bars
     9.8  last_frames_show_the_first_visual
     9.8  no_game_audio_gets_through
     4.1  voice_is_about_minus_14_lufs
     0.0  voice_has_no_slow_drift
     0.0  dead_air_is_gone
     0.0  sentence_tail_cut_is_careful
     0.0  captions_follow_the_real_speech
     0.0  log_says_what_happened
    48.0  pacing_render_joins_and_loops
    43.6  music_stays_well_under_the_voice
     8.2  spoken_word_is_highlighted
    54.1  music_and_sound_effects_render
    33.4  auto_with_no_music_folder_still_renders
     0.0  new_fields_are_read
     2.5  bad_new_fields_stop
     1.8  cards_and_labels_find_their_place
    13.9  word_captions_follow_each_word
     0.0  phrase_captions_step_aside_for_cards
    15.7  numbers_count_up_and_hold_their_place
     6.0  labels_are_pills_on_the_card_edge
     1.9  marks_land_on_their_spot
    12.2  grade_adds_contrast_and_a_vignette
   156.2  look_render_has_no_black_screen
    10.0  look_render_frames_labels_and_marks
    13.5  look_render_hides_captions_over_cards
     1.4  old_example_scripts_still_read
     8.3  beats_are_read
     4.4  bad_beats_stop
     3.8  lock_on_keeps_its_box_in_the_safe_area
     2.0  compare_cards_stack_without_overlap
    40.2  snippet_text_fits_its_card
   110.4  banner_and_pill_stay_in_the_safe_area
    11.2  beat_sounds_are_planned_and_sit_under_the_voice
     0.0  pacing_counts_changes_and_warns
    14.4  ps5_video_gets_the_range_fix
    69.0  beats_render_lock_on_follows_the_zoom
     6.1  beats_render_compare_slides_in_and_captions_move
   148.1  beats_render_video_backgrounds_and_cards
    17.3  beats_render_never_ends_frozen_and_still_loops
     0.1  beats_render_prints_the_pacing_check
    19.6  beat_example_scripts_read
```

**hooktest**

```
     0.1  hooks_txt_has_one_line_per_clip
     0.0  typed_hooks_come_back
     0.0  notepad_encodings_are_read
     0.0  bad_hooks_txt_stops
     0.0  emoji_go_and_text_stays
     0.0  escaping_matches_what_libass_needs
     0.0  ass_file_keeps_the_text_in_the_last_field
     0.0  outline_style_is_the_pr12_banner
     0.0  a_peak_after_the_hook_is_read
     0.0  a_peak_that_is_no_number_stops
     0.0  cold_open_window_stays_in_the_clip
     0.0  zoom_stays_after_the_cold_open_and_inside_the_clip
     0.0  two_lines_split_evenly
   182.4  bigger_layout_wins_and_stays_in_the_box
    59.9  too_long_does_not_fit
     7.6  missing_font_is_reported
     0.0  voice_span_ignores_silence_at_the_ends
     0.0  duck_gain_has_the_right_shape
   154.9  missing_hooks_txt_is_written_and_kept
     0.0  burn_survives_plain_windows_settings
     0.0  emoji_are_stripped_and_logged
     0.0  no_typed_peak_means_no_cold_open_and_no_zoom
     0.0  originals_are_untouched_and_finals_are_complete
     0.0  nothing_to_do_is_a_byte_exact_copy
     7.5  only_what_changes_is_reencoded
    11.6  banner_is_there_from_frame_0_and_gone_after_3_s
     3.8  game_ducks_only_while_the_voice_sounds
     1.9  voice_is_on_top_and_on_time
     1.9  clip_without_game_audio_gets_the_voice_alone
    24.8  too_long_hook_stops_before_any_encode
     6.5  an_open_final_and_stray_voice_files_are_explained
    78.0  cold_open_plays_the_moment_first_then_the_whole_clip
     1.8  cold_open_sound_stays_with_its_picture
     4.0  banner_is_there_from_frame_0_of_the_cold_open
    73.2  zoom_happens_once_around_the_peak
     2.1  no_game_audio_still_gets_cold_open_and_zoom
    63.0  no_cold_open_keeps_the_length_and_still_zooms
    66.7  no_zoom_keeps_the_cold_open_and_does_not_zoom
    57.1  both_off_is_the_pr12_path
    60.4  banner_stays_still_during_the_zoom
     3.8  full_banner_reaches_the_last_frame_of_a_cold_open_clip
    34.8  voice_starts_at_frame_0_of_the_final_clip
     2.0  sound_dips_briefly_at_the_cut
    36.8  box_style_is_a_white_rounded_box_with_black_text
    36.8  shadow_style_has_no_box
    41.2  outline_style_still_draws
    75.3  hook_duration_sets_how_long_the_banner_stays
     2.7  bad_flags_stop
    51.7  bad_peak_stops_before_any_encode
     8.9  early_peak_warns_and_still_burns
     2.3  a_folder_with_no_scores_stops
```

**editortest**

```
     0.3  a_good_plan_passes
     0.0  no_payoff_no_short
     0.0  the_payoff_plays_whole_in_one_shot
     0.0  short3_payoff_must_hold_the_hit
     0.0  the_setup_comes_first
     0.0  no_second_is_shown_twice
     0.0  too_little_footage_makes_it_shorter_not_repeated
     0.0  v4_has_no_loop_ending
     0.0  assembled_visuals_show_each_shot_once
     0.0  the_window_is_fixed_and_the_same_size
     0.0  stabilise_only_when_it_helps
     0.0  pieces_split_at_the_game_camera_cuts
     0.0  a_fast_camera_swing_is_not_a_cut_and_the_payoff_is_judged_apart
     9.0  shake_is_measured_and_judder_scores_high
    28.9  a_blurred_first_frame_is_found_and_the_start_moves
     0.0  a_hook_about_the_hud_needs_its_proof_at_frame_0
     0.0  a_line_ends_before_its_cut
     0.0  a_cut_lands_on_a_sentence_end_or_an_event
     0.0  a_cut_on_the_6_s_sampling_grid_is_not_an_event
     0.0  no_voice_over_an_action_payoff
     0.0  an_action_short_may_replay_its_impact_once_right_after_the_payoff
     0.0  a_replay_is_refused_when_it_breaks_the_owners_terms
     0.0  no_voice_over_the_replay_and_its_cuts_need_no_line
     0.0  a_slowed_shot_splits_at_camera_cuts_on_its_own_timeline
     0.0  punchy_lines_and_hype
     0.0  the_voice_speed_is_1_20
     0.0  words_a_minute_with_the_pauses_and_voiced_only
   144.8  end_to_end_a_v4_short_names_its_payoff
   191.0  end_to_end_a_v4_short_with_its_replay
     0.0  a_short_without_a_payoff_is_rejected_on_the_page
   130.1  early_swearing_fails_with_the_shorts_metadata
     0.0  render_and_render_one_default_to_engine_v4_like_the_command_line
     0.0  the_first_word_by_0_2_s_the_hook_by_3_5_s_line_2_by_4_s
     0.0  kokoros_lead_in_is_trimmed_so_the_line_starts_on_its_first_word
     0.0  a_hook_title_or_open_card_that_tells_the_result_is_refused
     0.0  the_hooks_subject_is_in_the_evidence_at_frame_0
     0.0  a_moved_first_frame_is_checked_again_for_the_hooks_subject
     3.8  the_open_card_is_5_words_at_most_small_and_above_the_action
     0.0  the_short_ends_as_the_talking_ends_or_on_the_replay
   134.6  no_subscribe_pill_and_no_click_in_a_v4_short
    34.8  a_dark_shot_is_not_darkened
    12.8  a_daytime_shot_is_graded_exactly_as_before
     2.0  the_lift_is_capped_and_black_stays_black
     0.0  a_shot_that_ends_in_the_dark_is_dark_and_a_flash_of_dark_is_not
    22.4  render_grades_each_shot_by_its_own_light_and_the_replay_like_its_payoff
```

**shorttest**

```
     0.1  tagger_cleans_the_models_answer
     0.0  tagger_refuses_an_answer_it_cant_use
     0.0  region_and_names_come_from_the_folder
     0.0  type_shape_and_group_come_from_the_file
    25.0  text_and_logos_are_saved_for_every_file
    27.9  tags_made_before_the_text_check_are_kept
     3.6  a_long_video_starts_after_its_rating_card
    20.7  tagger_tags_pictures_and_two_frames_of_a_video
    32.0  tagger_skips_what_is_tagged_unless_forced
    28.4  override_file_wins_over_the_model
    20.5  a_broken_override_file_stops_with_a_reason
    22.9  an_unusable_answer_leaves_the_file_untagged
    20.8  check_sheet_has_every_file_and_its_thumbnails
     0.0  picker_finds_a_picture_by_what_it_shows
     0.0  a_query_becomes_a_file_explainer_can_render
     0.0  picker_never_repeats_a_picture_in_one_short
     0.0  a_poor_match_is_refused_and_names_the_closest
     0.0  pictures_of_the_last_five_shorts_are_avoided
     0.0  used_media_log_keeps_each_short_once
     0.0  text_logos_and_rating_cards_stay_out
     0.0  a_full_screen_shot_prefers_a_phone_shaped_picture
     0.0  a_video_is_picked_only_when_asked_and_starts_after_its_rating_card
     0.0  a_region_label_needs_a_picture_from_that_region
     0.0  a_region_label_steers_the_pick_and_an_override_can_move_a_picture
     0.0  picker_refuses_what_it_cant_do
     0.0  a_line_must_list_real_checked_facts
     0.0  a_reported_fact_must_use_its_safe_wording
     0.0  every_number_must_come_from_a_listed_fact
     0.0  claims_sheet_lists_each_fact_with_its_source
     0.0  the_real_facts_file_reads_and_keeps_the_owners_statuses
     0.0  own_footage_facts_are_added_and_confirmed_without_touching_the_rest
     0.0  every_fact_says_if_it_changes_and_when_it_was_checked
     0.0  a_stale_fact_is_one_that_changes_and_was_checked_over_three_days_ago
     0.0  title_rules
     0.0  upload_text_needs_a_description_and_three_to_five_hashtags
     7.5  word_count_is_checked_before_anything_renders
    14.8  make_short_writes_every_file_and_strips_its_own_settings
     7.4  a_bad_fact_or_picture_stops_before_the_render
    38.4  a_stale_fact_stops_the_render_unless_the_owner_allows_it
    22.6  a_short_outside_the_length_or_pacing_is_not_ready
    60.4  every_picture_on_the_picks_sheet_must_get_on_screen
    30.3  the_next_short_avoids_the_last_ones_pictures
```

**topictest**

```
     0.3  close_ups_are_16_9_and_inside_the_picture
     4.5  a_close_up_is_cut_from_the_recording_at_1280x720
     0.3  heat_icons_and_star_looks_are_read_on_made_up_huds
     9.3  hud6_reads_a_made_up_recording_and_finds_the_changes
     0.0  hud6_events_ignore_misreads_and_unknown_samples
     0.0  a_label_stands_only_where_the_frames_show_it
     0.0  police_with_stars_stay_and_mission_dialogue_costs_points
     0.0  the_safety_answers_give_what_was_seen_where
     0.0  the_pool_spreads_over_the_recordings_and_every_reject_says_why
     0.4  topics_come_from_the_shot_list_and_the_hud
     0.4  a_topic_pick_is_narrated_and_gets_the_hud_evidence
     0.2  a_topic_passes_the_quality_gate_with_its_topic
   124.0  engine_v3_makes_full_screen_shorts_and_checks_every_caption
     5.0  engine_v3_refuses_cards_plain_clips_late_payoffs_and_used_footage
    67.9  a_dark_blank_or_back_only_shot_or_one_with_text_at_the_bottom_is_rejected
     9.8  the_beat_question_does_not_see_our_own_inset
     6.6  every_caption_word_is_checked_against_its_frame
     0.1  the_crop_follows_the_action_and_stays_in_the_middle_otherwise
     8.7  a_full_screen_clip_has_its_inset_only_in_its_window
     0.2  close_ups_keep_to_the_zoom_cap_and_box_the_detail
     0.0  anything_we_put_on_screen_stays_in_the_safe_zone
     0.0  insets_are_timed_to_the_words_that_name_them
     0.0  the_v3_rules_in_truth
     0.0  the_hook_card_and_beats_for_lines_that_name_none
```

**launchtest**

```
     0.1  ps5_names_give_the_session_order
     0.0  only_new_recordings_are_taken_and_held_out_never
     0.0  a_second_run_the_same_night_gets_its_own_folder
     0.1  the_profile_is_calibrated_with_every_guess_accepted_or_the_run_goes_without_hud
     0.0  auto_accept_takes_every_guess_and_reports_a_profile_it_cannot_save
     0.0  the_hud_must_agree_with_the_model_or_the_recording_goes_no_hud
     0.0  a_window_gets_points_for_each_thing_it_shows
     0.0  moments_never_overlap_never_use_footage_or_what_earlier_shorts_showed
     0.0  three_picks_come_from_three_parts_of_the_session
     0.0  the_type_follows_what_the_moment_has
     0.0  all_picks_one_type_switches_the_closest_one
     0.0  the_safety_checks_unload_the_model_when_memory_runs_low
     0.0  safety_answers_must_be_complete
     5.5  safety_sheets_are_4_frames_a_second_apart
     0.1  a_phone_menu_cutscene_or_loading_screen_drops_the_footage_it_is_in
     0.1  blood_in_the_first_7_s_moves_the_start_once_or_drops_the_type
     0.1  a_child_near_guns_or_a_body_with_blood_is_never_shown
     0.0  swear_words_are_found_and_mild_words_are_not
     0.1  swearing_in_the_first_7_s_moves_the_start_or_drops_the_type
     5.5  subtitle_sheets_cut_the_band_at_full_size
     0.0  the_index_saying_blood_counts_as_gore_too
     0.1  no_hud_mode_asks_twice_as_often
     0.0  the_type_keeps_to_what_the_safety_checks_left
     0.0  footage_an_earlier_short_showed_never_comes_back
    15.2  a_render_over_minus_1_dbtp_is_limited_and_measured_again
     0.0  a_narrated_line_must_point_at_what_is_on_screen
     0.0  words_and_numbers_need_their_evidence
     0.0  claims_about_how_the_game_works_need_a_fact
     0.0  only_jason_and_lucia_may_be_named_and_no_spoilers
     0.0  titles_and_hooks_may_use_title_case
     0.0  hooks_titles_and_descriptions_follow_the_rules
     0.0  a_short_may_start_later_inside_its_window
     0.0  an_auto_peak_must_come_from_evidence
     8.6  a_narrated_short_plays_its_moment_on_and_is_logged
    14.8  a_short_that_breaks_a_rule_is_not_rendered_and_says_why
   140.9  an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak
     0.0  the_no_hud_profiles_never_see_a_hud
     0.1  a_failed_calibration_runs_the_whole_night_without_hud
     0.5  a_recording_under_15_s_is_skipped_with_a_plain_line
     0.1  prepare_writes_candidates_with_evidence_and_logs_the_run
     4.4  a_frame_sheet_shows_the_moment_with_its_times
     0.0  one_heavy_job_at_a_time
     0.0  a_stopped_prepare_uses_the_finished_clip_runs_again
     0.0  a_short_no_longer_in_shorts_json_leaves_the_review_page
     0.0  spoken_swearing_in_the_first_7_s_is_found
     0.0  the_preflight_unloads_our_own_model_before_it_measures_memory
     0.0  running_out_of_memory_in_the_safety_checks_stops_with_a_plain_error
     0.0  a_safety_shifted_window_keeps_the_vision_checks_verdict
```

**calibtest**

```
     0.2  times_parse
     0.0  star_geometry_from_four_clicks
     0.0  star_geometry_any_bottom_tip_and_inner_corner
     0.0  star_geometry_refuses_bad_clicks
     0.0  star_clicks_on_the_wrong_spots_name_the_point
     0.0  star_outlines_past_the_box_are_refused
     0.0  snapping_to_the_cell_grid
     0.0  a_phone_area_outside_the_box_is_refused
     0.0  bar_row_from_bar_boxes
     0.0  limits_halfway_or_one_side
     0.0  bar_colour_rule_from_frames
     0.0  a_bar_that_changes_colour_gets_two_groups
     0.0  a_yellow_bar_tests_both_strong_channels
     0.0  dim_frames_of_a_bar_are_never_left_without_a_rule
     0.0  every_coloured_frame_lands_in_one_group
     0.0  a_black_frame_ticked_shown_does_not_wreck_the_limit
     0.0  a_wrong_frame_says_why
     0.0  a_grey_bar_has_no_rule
     0.0  an_override_wins
     0.0  drawing_makes_a_profile_clip_loads
     0.1  boxes_past_the_picture_edge_are_cut_to_it
     0.0  rules_switched_off
     5.4  bars_found_on_made_up_huds
     0.4  no_bars_or_one_bar_is_refused
     0.1  a_part_empty_bar_under_a_map_icon_stays_one_bar
     0.2  hud_guess_from_the_found_bars
     0.0  label_summary_counts_guesses
    48.4  end_to_end_bars_found_and_guesses_confirmed
    36.5  end_to_end_on_a_made_up_video
     6.5  stars_found_in_made_up_star_rows
     0.6  star_slots_typed_in_are_used
     0.3  too_few_star_pictures_are_refused
     0.2  a_found_star_row_is_used_once_confirmed
    46.3  end_to_end_stars_found_in_a_made_up_video
     0.2  gta6_profiles_load_and_gta5_reads_as_before
     1.0  gta6_arrow_and_stars_are_read_on_made_up_huds
     1.0  gta6_guided_scan_finds_a_made_up_arrow
     0.0  gta6_drawing_makes_an_arrow_profile
```

**footagetest**

```
     0.1  scan_times_cover_the_recording
     8.6  bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time
     0.7  no_bars_means_no_guesses
     0.0  the_star_row_is_found_and_text_is_not
     0.1  the_outline_is_sized_so_the_stars_can_be_counted
     1.0  no_stars_in_a_recording_is_said_plainly
     0.0  about_a_dozen_pictures_go_on_the_page
     0.0  the_verdict_is_decided_in_code
    97.4  guided_run_on_a_recording_without_stars_and_the_rules_about_overwriting
    33.6  a_finished_profile_is_never_written_again_by_hand_either
     0.0  a_denied_word_is_not_a_match
     0.0  text_search_skips_denied_lines_and_says_so
     0.0  structured_filters_run_before_the_text_search
     0.0  a_star_change_needs_a_steady_count_on_both_sides
     0.0  a_star_rise_window_starts_2_s_before_and_is_confirmed_at_both_ends
     0.0  stars_rise_is_a_query_like_any_other
     0.3  the_command_line_prints_star_rises_as_json
     0.0  footage_is_cut_on_clean_edges
     0.0  a_star_rise_beat_keeps_the_change_inside_it
     0.0  a_short_star_rise_beat_still_gets_its_rise
     0.0  a_moment_pinned_with_file_and_at_gets_its_own_thumbnail
     0.0  the_best_unused_footage_is_picked
     0.0  footage_an_earlier_short_showed_is_avoided_while_there_is_other
     0.0  a_footage_short_is_built_from_the_library_end_to_end
     0.0  pinned_footage_is_logged_as_used_too
     0.0  never_use_footage_never_gets_on_screen
     0.0  a_second_short_does_not_repeat_the_first_ones_footage
     0.0  a_script_that_cannot_be_a_footage_short_is_refused_with_reasons
     0.0  shot_list_ids_and_fallback_words
     4.0  explainer_reads_sound_on_a_video_shot
     6.0  game_sound_sits_under_the_voice_only_where_its_shot_is
```

**storytest**

```
     8.4  shots_are_slow_cuts_of_one_moment_in_time_order
     0.0  a_named_thing_is_on_screen_within_half_a_second_of_its_word
     0.0  the_first_two_seconds_show_the_setup
     0.0  pops_land_on_their_words_and_every_long_gap_is_logged
     0.0  overlays_and_breaks_must_explain
     4.9  a_circle_follows_its_target_and_stops_when_lost
    54.2  a_real_story_short_renders
     0.0  a_story_short_that_keeps_the_rules_passes
     0.0  play_by_play_and_missing_story_parts_are_refused
     0.0  overlays_must_name_what_they_show
     0.0  a_test_says_why_only_when_both_are_on_screen
     0.0  the_quality_gate_skips_a_moment_with_no_point
     0.0  a_dry_run_stand_in_is_flagged_and_never_used_for_real
     0.0  only_an_f1_short_uses_the_other_side
     8.7  the_v2_render_checks_timing_and_logs_the_story
     0.0  the_launch_night_experiments_cover_every_format
```

**selftest**

```
    27.2  there is no GTA V HUD here
     0.0  40.0 s -> clip 1 34.80-49.80 s, peak 40.00 s (off by +0.00 s)
     0.0  90.0 s -> clip 3 84.80-99.80 s, peak 90.00 s (off by +0.00 s)
     0.0  140.0 s -> clip 2 134.80-149.80 s, peak 140.00 s (off by +0.00 s)
     0.0  clip_01_0m34s.mp4
     2.0  clip_02_2m14s.mp4
     2.0  clip_03_1m24s.mp4
```

**hudtest**

```
     2.3  test1 3
     2.1  test1 2
     2.1  test1 8
     2.0  test1 8
     2.0  test2 18
     2.1  test1 1
     2.0  test1 4
     2.0  test2 4
     2.0  test2 12
```

**indextest**

```
     0.2  schema_is_strict_and_describes_first
     0.0  a_good_answer_parses
     0.0  bad_answers_are_refused
     0.0  notable_is_cut_to_15_words_and_values_are_tidied
     0.0  prompt_is_filled_with_the_profiles_game
     0.0  ask_retries_once_then_reports_the_error
     0.0  star_masks_cover_five_slots_without_overlap
     0.3  drawn_stars_are_counted
     0.0  stars_count_from_the_filling_end_only
     0.0  a_profile_can_override_the_count_rule
     0.0  short_star_gaps_are_bridged
     0.0  hud_rules_switch_off_without_a_minimap
     0.0  no_minimap_and_phone_stretches_are_separate
     0.0  hud_rules_win_over_the_model
     0.0  only_a_lone_other_is_smoothed
     0.0  short_gameplay_pieces_join_a_neighbour_but_never_use_is_left_alone
     0.0  segments_cover_the_recording_in_order
     0.0  segments_have_the_expected_labels
     0.0  no_usable_segment_holds_hud_excluded_time
     0.0  segment_details_come_from_its_sheets
     0.0  a_star_count_needs_a_full_second
     0.0  answers_are_cached_and_a_rerun_asks_nothing
     0.0  a_stopped_run_carries_on_and_errors_are_asked_again
     0.0  the_cache_key_covers_what_decides_the_answer
     0.0  low_memory_unloads_the_model_then_stops_the_run
     0.0  memory_falling_fast_unloads_the_model_without_waiting_10_calls
     0.0  held_out_footage_is_refused
     0.0  the_indexer_never_reads_the_answer_keys
     6.9  e2e_sheets_are_cut_at_the_right_times
     0.0  e2e_the_hud_is_read_from_the_picture
     0.0  e2e_segments_follow_the_hud_and_the_model
     0.0  e2e_files_are_written
     0.0  e2e_a_rerun_is_cached_and_keeps_the_first_runs_timing
     0.0  e2e_search_finds_the_chase
     0.0  find_filters_by_every_key
     0.0  text_search_reads_every_sheet_of_a_segment
     0.0  never_use_is_left_out_by_default
     0.0  a_wrong_query_is_an_error
     0.0  the_shotlist_holds_only_shots
     0.0  a_shotlist_topic_must_be_marked_verify_after_launch
     0.0  coverage_counts_segments_per_topic
```

**varietytest**

```
     0.3  the_nov20_case_puts_the_bike_crash_on_top_and_no_near_duplicates
     0.0  payoff_strength_ranks_a_clear_event_first
     0.0  posted_topics_cost_points_and_match_only_their_own_moments
     0.0  the_posted_list_is_read_only_and_checked
     0.0  two_picks_of_one_recording_must_differ_and_three_only_when_nothing_else_qualifie
     0.0  a_run_gets_both_an_action_clip_and_a_topic
     0.0  every_pick_says_why_it_beat_the_others
     0.0  the_used_footage_is_read_as_of_the_run
     0.4  repick_runs_on_cached_data_and_leaves_the_run_alone
     0.0  no_crash_right_after_a_posted_crash_unless_nothing_else_meets_the_bar
     0.0  the_crash_rule_holds_for_real_moment_labels
     0.0  a_payoff_below_2_of_3_is_never_picked_by_any_route
     0.0  a_moment_that_failed_its_shot_check_at_render_is_not_picked_again
```

**vlmtest**

```
     0.1  no_veto_keeps_the_first_five
     0.0  vetoed_candidates_are_skipped
     0.0  fillers_come_after_the_survivors
     0.0  all_vetoed_gives_the_first_five_as_fillers
     0.0  fewer_candidates_than_count
     0.0  errors_never_veto
     0.0  categories_and_veto_set
     0.0  a_good_answer_parses
     0.0  bad_answers_are_errors
     0.0  request_body
     0.0  ask_records_errors_and_thinking
     0.0  sheet_times_put_one_frame_on_the_peak
     0.0  sheet_peak_before_the_clip_is_held_inside
     0.0  sheet_times_stay_in_order_for_any_peak
     0.0  sheet_filter_labels_the_peak
     0.0  cache_key_changes_with_every_part
     0.0  cached_answers_skip_the_model
     0.0  errors_are_not_cached
     0.0  cache_file_with_other_fields_is_ignored
     0.0  unload_waits_until_the_model_is_gone
     0.0  missing_model_or_ollama_stops_the_run
     0.0  the_prompt_file_exists
     0.0  crlf_checkout_gives_the_same_prompt
     0.0  judge_outcomes
     0.0  first_five_of_ten_candidates_are_the_five_picks
     0.0  stars_are_cut_at_the_clip_edges
     0.0  stars_never_exceed_the_clip
     0.0  stars_line_text
     0.0  fill_prompt_needs_the_placeholder_once
     0.0  the_stars_prompt_file
     0.0  hint_none_cache_key_is_v6s
     0.0  stars_cache_key_includes_the_hint_text
     0.0  judge_sends_each_candidate_its_line
     0.0  judge_without_hint_sends_the_prompt_unchanged
     0.0  star_rule_cancels_only_boring_vetoes
     0.0  stars_options_need_the_hud
     0.8  stars_options_stop_clip_py_at_once
     0.0  veto_step_star_rule_and_v6_shape
     0.0  veto_step_hint_lines
```

**spreadtest**

```
     0.2  short_wall_splits_v9_not_v10
     0.0  long_wall_ends_a_sequence_in_both
     0.0  wall_counts_as_quiet_in_v10
     0.0  without_walls_v9_and_v10_are_the_same
     0.0  excluded_steps_have_no_sequence
     0.0  v9_lets_one_chase_take_every_pick
     0.1  v10_caps_the_chase_and_spreads
     0.0  first_five_of_ten_are_the_five_picks_in_v10
     0.0  a_clip_in_two_runs_comes_once
     0.0  unrated_only_leaves_out_rated_clips
     0.0  every_clip_stays_without_unrated_only
     0.0  videos_stay_apart
     0.0  page_shows_nothing_about_the_pick
```

**evaltest**

```
     0.1  touch_at_clip_end_is_not_a_hit
     0.0  touch_at_clip_start_is_not_a_hit
     0.0  one_second_overlap_is_a_hit
     0.0  clip_inside_range_is_a_hit
     0.0  touch_does_not_count_for_recall
     0.0  zero_length_range_is_an_error
     0.0  shorter_cut_of_same_peak_takes_the_rating
     0.0  peaks_4_s_apart_match_4_5_s_do_not
     0.0  rated_clips_under_15_s_do_not_count
     0.0  picked_clip_must_be_15_to_30_s
     0.0  newest_rating_wins
     0.0  same_clip_rating_beats_a_newer_peak_match
     0.0  same_clip_rating_applies_even_if_peaks_drift
     0.0  test1_v3_clip_4_keeps_its_own_rating
     0.0  rating_without_a_peak_is_an_error
     0.0  every_rating_in_the_repo_has_a_peak
     0.0  owner_ratings_of_test1_4b_v6_picks
     0.0  owner_rating_of_test1_v4_clip_2_wins
     0.0  postable_at5_counts_the_first_five_ranks
     0.0  fewer_than_five_picks_count_out_of_five
     0.0  unrated_is_never_postable
```

</details>
