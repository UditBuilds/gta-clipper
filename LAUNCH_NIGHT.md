# Launch night runbook (GTA 6, Wed Nov 19, 12:00 AM IST)

One page. Do the steps in order. Claude makes the Shorts; you play, review and upload.
Every PS5 menu path below was written from memory, not checked on a console: each says **(check on your PS5)**.

## Before midnight (once)

1. **PS5 capture settings** (Settings > Captures and Broadcasts > Captures; **check on your PS5**):
   - Video clip format: **WebM, 1080p**. HDR **off** while recording (the tools can't fix HDR colours).
   - **Include your microphone audio: Off. Include party audio: Off.** (In the same Captures menu; **check on your
     PS5**. Your earbuds' volume doesn't change what is recorded.)
   - **Max recording length**: start a test recording (Create button > Start New Recording; **check on your PS5**)
     and see how long the PS5 lets it run. Plan to stop and start a new recording **every 55 minutes or less**, so each
     part is its own file.
2. **In GTA 6's settings** (open them the first time you play):
   - Audio: **radio volume 0**, and voice chat or phone volume 0 if the game has them. Licensed songs in the game
     sound can get a Short blocked.
   - Display (or Accessibility): **SUBTITLES OFF**. Burned-in mission dialogue can swear. With subtitles on, /launch
     reads them and moves or drops a Short that swears in its first 7 s; with them off there is nothing to read.
     Either way, **spoken** swearing in the game sound is not checked: listen to each Short once (its SOUND flag).
3. **Laptop**: open the Ollama app. Close ComfyUI and anything else that uses the GPU. Keep 40 GB free on C:.
   In PowerShell:
   ```powershell
   cd C:\AI\gta-clipper
   git pull
   .\.venv-tts\Scripts\python.exe launchtest.py      # about 2 minutes; the last line must say RESULT: PASS
   ```

## While you play: what to try (it gives /launch good moments)

- Get a wanted level, then lose it by **switching cars** or **changing your outfit**.
- **First-hour moments**: the first drive, the first star, the first gun, the swamp, the beach, the neon.
- **Jason and Lucia**: play both, switch between them, get them both in the picture.
- **Big chases**: 3 or more stars, 30 seconds or longer, a helicopter, a crash with the police in view.
- Cutscenes, menus, maps and the phone are never used, so don't worry about them, but they don't help either.

## The split night (recommended)

1. **About 1:00 AM**: stop recording part 1. Start a new recording at once and keep playing.
2. Copy part 1 to the laptop (Media Gallery > select the clip > Copy to USB drive; **check on your PS5**). On the
   laptop (the folder the PS5 writes on the stick is `PS5\CREATE\Video Clips\<game>`; **check on your PS5**):
   ```powershell
   Copy-Item "E:\PS5\CREATE\Video Clips\*\*.webm" C:\AI\gta\input\     # E: = the USB stick
   ```
3. Run /launch on part 1 (step "Make the Shorts" below). It takes about 70 minutes for 1 hour of footage
   (about 40 of them the index), so part 1's Shorts are ready well before the 4:30 AM slot.
4. Later, stop part 2, copy it the same way and run /launch again. It **skips part 1** (already done), **never uses
   part 1's footage again**, and writes a new folder (`launch-2026-11-19-part2`).

One long run works too, but it takes about 1 hour 45 minutes for 2 hours of footage.

## Make the Shorts

```powershell
cd C:\AI\gta-clipper
claude
```
In Claude Code type `/model sonnet`, then `/launch`. It asks you nothing. When it is done it prints the review page:
`C:\AI\gta\shorts\launch-<date>\review.html` (or `...-part2\review.html`).

The index stays at one sheet every 6 s. One every 12 s was measured on Nov 17: about 40 % faster, but it found
only 3 of the 5 answer-key moments of test1 (6 s: 5 of 5), so it is not used.

## Review and upload (about 5 minutes per Short)

Open review.html and follow the steps at its top: watch with sound, read the red flags, read each narrated line next
to its evidence, fix a hook or a cold open with the command under it, then upload with the upload text. **Nothing is
uploaded automatically.** The Shorts are in order, best first.

Upload slots: **about 4:30 AM IST** and **about 10:30 PM IST**.

## If something goes wrong

- `ERROR: ... Ollama ...`: open the Ollama app, then type `/launch` again. It carries on where it stopped.
- `ERROR: no new recordings`: the files aren't in `C:\AI\gta\input` yet, or that part was already done.
- **NO-HUD MODE** on the page: the HUD couldn't be read, so nothing mentions wanted stars. Check the flags more
  carefully.
- A Short says **FAILED**: its reasons are on the page. The other Shorts are fine.
- By hand, without Claude: `.\.venv\Scripts\python.exe launch.py prepare`, write `shorts.json` yourself, then
  `.\.venv-tts\Scripts\python.exe launch.py render`.
