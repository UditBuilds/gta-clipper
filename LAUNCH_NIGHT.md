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

## EXPERIMENTS TO PLAY (each one can become a Short that teaches one thing)

/launch only makes a Short when the footage has a point: something a stranger learns, a test with its answer, or a
clear payoff. Play a few of these on purpose. For every one: **keep the HUD on screen, keep the camera on what
matters, and wait for the result** (the Short shows it as proof). Cutscenes, menus, maps and the phone are never
used. The full list, with what must be on screen, is in `shotlist.json` (`experiments`).

**F2 Tests ("can you ...?", the HUD gives the answer)**
- **Swap cars to lose the cops:** get 2-3 stars, get out, take another car *on camera*, drive off, keep going until
  the stars are gone (or 60 s).
- **Change outfit to lose the cops:** 1-2 stars, change clothes if the game lets you, walk out, stand still 20 s.
- **Hide in the water:** 2 stars near water, jump or drive in, swim away from the shore.
- The Short can only say *why* the stars went if the change and the result are both on screen, in order. So film the
  swap, then the stars going.

**F4 First looks (what it looks like in GTA 6)**
- **First car:** camera behind it, 15 s on an open road, then a slow turn.
- **First chase:** your first wanted level in a car, 30 s, police in view.
- **First beach:** walk onto the sand and turn the camera slowly across the water and the skyline.
- **First Jason/Lucia switch:** stand still 3 s before it, then 10 s of the other character in view.

**F1 GTA 5 vs GTA 6 (the same kind of moment in both games)**: your GTA 5 recordings already have these, so play
the GTA 6 version:
- **A police chase at 3+ stars, police in view 15 s** (GTA 5: the rainy night chase, the river-truck chase).
- **Losing the stars after a chase, then 10 s of driving** (GTA 5: right after the river chase).
- **A night drive in the rain, city lights in view** (GTA 5: the rainy night drive).
- **A short shootout from cover** and **a sports car with a hard turn** (GTA 5 has both).

**F3 Three things**: one chase that shows three different things: a star rise, police in view, and a crash, a
helicopter or a jump.

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
In Claude Code type `/model sonnet`, then `/launch` (or `/launch --count 5` for more). It asks you nothing. It makes
**3** Shorts when 3 moments keep the rules and have a clear payoff (2/3 or more; fewer when fewer do, never a weak one to make up the number), every one like the posted sample (hook from frame 0, voice, white
captions, music and sound effects, setup -> payoff, close-ups of the HUD as proof); topic explainers come first,
action clips get a short voice too. A pick that can only be a plain clip is left out, and the report says why. When
it is done it prints the review page: `C:\AI\gta\shorts\launch-<date>\review.html` (or `...-part2\review.html`).

**Every night after launch:** copy the new recordings to `C:\AI\gta\input` and type `/launch` again. Only the new
recordings are indexed (the old ones stay in the library), and the picks come from the **content bank**: every moment of
every recording (`C:\AI\gta\bank\bank.html`), so it works with no new recordings too. Footage of a Short you **posted**
is never picked again; footage of one you only rendered may come back, after the new ones. So **after posting a Short, run
the line under "Posted it?"** on the review page.

The index stays at one sheet every 6 s. One every 12 s was measured on Nov 17: about 40 % faster, but it found
only 3 of the 5 answer-key moments of test1 (6 s: 5 of 5), so it is not used.

## Review and upload (about 5 minutes per Short)

Open review.html and follow the steps at its top: watch with sound, read the red flags, read each narrated line next
to its evidence and what it teaches (the format and its reason are under each Short's title), fix a hook or a cold open with the command under it, then upload with the upload text. **Nothing is
uploaded automatically.** The Shorts are in order, best first.

Upload slots: **about 4:30 AM IST** and **about 10:30 PM IST**.

## If something goes wrong

- `ERROR: ... Ollama ...`: open the Ollama app, then type `/launch` again. It carries on where it stopped.
- `ERROR: another heavy job is running: ...`: wait for that one to finish (one heavy job at a time on the laptop),
  then type the same command again.
- `ERROR: only ... GB of memory free`: close the browser and anything else big, then type `/launch` again. It
  carries on where it stopped.
- `ERROR: no new recordings`: the files aren't in `C:\AI\gta\input` yet, or that part was already done.
- **NO-HUD MODE** on the page: the HUD couldn't be read, so nothing mentions wanted stars. Check the flags more
  carefully.
- A Short says **FAILED**: its reasons are on the page. The other Shorts are fine.
- By hand, without Claude: `.\.venv\Scripts\python.exe launch.py prepare`, write `shorts.json` yourself, then
  `.\.venv-tts\Scripts\python.exe launch.py render`.
