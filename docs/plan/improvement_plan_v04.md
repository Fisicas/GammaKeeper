# RadiaLog v0.5.1 — UI and backend improvement plan, v04

Revision of `improvement_plan_v03.md` (v01–v03 preserved unchanged). v04 adds Michael's answers of 2026-10-12 10:15 (D15–D20) and the **private git repository and code-organization plan** (§3.9), and moves repository setup to the front of the sequence. v03 added Michael's answers of 2026-10-12 10:09 (decisions D8–D14), the **Runs tab / one-job-per-device** design (§3.7), the closing-readout specification (§3.8), and a revised sequence. v02 recorded Michael's decisions of 2026-10-12, adds identity/icon/splash/notification work, defines the auto-resume rules, and replaces the resume-verification proposal with measured numbers. **No application code, run data, launchers or Windows settings were changed.** No scheduled task was created.

Evidence: `benchmark_results_v01.json` (preview/replay timings), `benchmark_results_v02.json` (resume-check breakdown). These are single measurements with a warm file cache. **Cold-cache times after a reboot were not measured.**

## 1. Decisions recorded

| # | Decision | Effect on plan |
|---|---|---|
| D1 | Overall assessment accepted. | Plan v01 tiers stand, except where changed below. |
| D2 | Scheduled-task changes must be handled carefully. | §3.3: per-run, opt-in, inspectable, removed automatically, never at system level. |
| D3 | Add a program icon and a splash/startup page, so the app is recognizable and its processes are identifiable in Task Manager. | New §3.1, §3.2. |
| D4 | Auto-resume at logon **only if the job is still in progress**. Never restart a run whose scheduled end has passed. | §3.3 eligibility rules. |
| D5 | Gap data are recovered from the device's cumulative counters after reconnect, provided the device was not reset in between. | §3.4: strengthen the continuity test that decides this. |
| D6 | Keep verification **before** resume if it takes only seconds. | §3.5: measured cost; a cheaper full pre-check is proposed. |
| D7 | Windows pop-up on run completion, with the RadiaLog icon visible in the pop-up. | §3.6. |
| D8 | **Confirmed:** never acquire before pre-resume verification finishes. | §2, §3.5: final; the background-verify option is dropped. |
| D9 | Auto-resume checkbox defaults to **off**. | §3.3. |
| D10 | Frozen/packaged executables: **later**. | §3.1 Phase B moved to the "Later" release. |
| D11 | Closing readout for expired interrupted runs: **yes**. | §3.8 specification. |
| D12 | Icon mock-ups: **yes**; produce them for approval before any icon enters the app. | §3.1; first deliverable of the identity work. |
| D13 | Test 2: **start fresh**; do not resume it. | Test 2 stays as an archived, interrupted run (see §3.7 "End run" for marking it). Qualification starts with a new run on the new release. |
| D15 | "Test run" flag: **yes**, default duration **1 h** (editable). | §3.7. |
| D16 | Long-running reminder: **daily** while a run is active with no window open. | §3.7, §3.6. |
| D17 | Run registry location: **`%LOCALAPPDATA%\RadiaLog\`**. | §3.7. |
| D18 | Test 2 and the v0.2 soak: **mark as Ended** (`ended_by_user` event; raw data untouched) once the Runs tab exists. | §3.7; done by the user from the Runs tab, not by a script. |
| D19 | **No second RadiaCode** available. | Multi-device recording is described as "backend-capable, not qualified". It is tested only with synthetic serials, and the GUI states this. |
| D20 | Move to a **private git repository**, one launcher, and split the two large window files. The aim is to let several agents work in parallel. | New §3.9; first step in §5. |
| D14 | Add a tab for ongoing/current runs with Resume and End actions. **One job per device at any time**; different devices may record simultaneously. Starting a new run must not leave forgotten jobs running. | New §3.7. |

## 2. Measured resume-check cost (answers D6)

The recorder currently makes two full passes before acquiring:

1. `RunStore.recover()`: read, SHA-256, JSON parse and full count reconstruction of every spectrum.
2. A second read and parse of every file, to rebuild the dose totals.

Test 2 (17,882 files, mean 9.5 KiB):

| Step | Time | Share |
|---|---|---|
| Read files | 2.09 s | 24% |
| SHA-256 | 0.37 s | 4% |
| JSON parse | 1.79 s | 20% |
| Reconstruct differences (pure Python, 1,024 channels) | 4.50 s | 51% |
| **Pass 1 total** | **8.83 s** (0.49 ms/file) | |
| Pass 2 (dose totals) | 3.88 s (0.22 ms/file) | |

Extrapolated warm-cache cost of the current two-pass design:

| Run | Spectra | Current (both passes) |
|---|---|---|
| 7 d at 10 s | 60,480 | ≈ 43 s |
| 7 d at 5 s | 120,960 | ≈ 86 s |
| 14 d at 10 s | 120,960 | ≈ 86 s |
| 7 d at 1 s | 604,800 | ≈ 7 min |

**Conclusion: a full check is a few seconds only for short runs.** For a week at 10 s it is closer to a minute, and probably longer on the cold cache right after a reboot (not measured).

### What the pre-resume gap actually costs

- **Spectral counts are not lost.** The device keeps accumulating, and the first spectrum after resume becomes one flagged aggregate interval covering the gap.
- **Time resolution is lost** inside that interval.
- **1-second telemetry is lost** for that period, apart from whatever the device buffer still holds when the reconnect backlog is read. The buffer's capacity is unverified.
- The gap already includes reboot and logon time, so a pre-check of 10–40 s adds proportionally little.

### Proposal (keeps D6's verify-first policy, but makes it cheaper)

**Pre-resume (blocking):**
- Read every raw file and check its SHA-256 against the index (sequence continuity, no missing or unindexed files, no `.partial` files).
- Then fully reconstruct only the tail after the last verified checkpoint.
- The checkpoint itself is protected by a hash chain over the indexed digests.
- The dose-total pass is merged into the same loop, and `reconstruct` is vectorized with NumPy.
- Expected 7 d / 10 s warm cost: about 5–10 s (the read + hash share of the measurement). **To be confirmed by benchmark after implementation.**

**Post-completion (unchanged):** the existing background completion audit still does the full re-reconstruction and export cross-check.

**Fallback rule:** if the checkpoint is missing or fails its hash, do the full current verification before resuming, however long it takes.

**Rule (confirmed, D8):** no acquisition before pre-resume verification, ever. There is no "acquire first, verify in background" path. This replaces v01 item 4.

## 3. New and revised work items

### 3.1 Program identity: icon, names, Task Manager (D3)

**Icon:** one original RadiaLog icon (not a manufacturer or third-party mark), delivered as a multi-size `.ico` (16, 20, 24, 32, 40, 48, 64, 256 px) plus a PNG master. It is used for:
- the window and taskbar
- the tray icon
- the splash page
- notifications
- the desktop/Start shortcut

Design is to be proposed separately for approval.

**Taskbar grouping:** set an explicit AppUserModelID (e.g. `GSU.RadiaLog`). Without it, the window groups under Python's icon.

**Task Manager — what the icon can and cannot do:**
- Background processes are listed by their executable. Today every RadiaLog process shows as **Python / pythonw.exe**: GUI, supervisor, recorder and the device worker. The venv launchers can add another Python process beneath each, so 4–8 entries is plausible. An icon file does not change this.
- **Phase A (no build-system change):**
  - A **Processes** panel in the GUI lists role, PID, start time, command line and parent for the supervisor, recorder and device worker.
  - It has a "Copy PIDs" action and a short README note on adding Task Manager's *Command line* column.
  - Each helper process gets an identifying first command-line argument, e.g. `--role recorder --run <id>`.
- **Phase B (decision needed):** a frozen build (PyInstaller, built in a separate build venv, never the shared science venv) with separate executables: `RadiaLog.exe`, `RadiaLog Supervisor.exe`, `RadiaLog Recorder.exe`, `RadiaLog Device Worker.exe`. Each carries the icon and version/FileDescription, so Task Manager shows names and icons directly.
  - Costs: a new build step; antivirus false positives are possible; the launcher and `runtime.py` would change from the science interpreter to the bundled one; dependency versions are frozen per release, which is good for reproducibility.
  - **Decision (D10): later.** Phase A ships first; Phase B goes in the "Later" release.
- **Mock-ups first (D12):** three or four original icon directions (e.g. stylized detector body, spectrum trace, spectrogram tile), each shown at 16/32/256 px on dark and light backgrounds and in a simulated toast and taskbar. Delivered as PNG plus an editable SVG master, for approval before any `.ico` goes into the app.

### 3.2 Splash and startup progress page (D3)

**GUI start:** a splash page showing the icon, version, release folder and, when a run is being reopened, its label and run ID. It shows startup steps as they finish:
1. loading the run index
2. reading supervisor health
3. restoring the display cache

**Run start/resume:** a startup-progress panel (not a modal dialog). It shows phases from the recorder's existing `recovery.sqlite` progress records and the journal:
1. checking schedule and UTC reference
2. verifying saved spectra n / N
3. opening the device
4. reconnect continuity check (§3.4)
5. first spectrum saved

Each phase shows its elapsed time.

**Logon auto-resume (§3.3) with no GUI open:** show the same compact progress window, with an action to open the full GUI.

The splash never blocks acquisition and never holds the device. It only reads journals.

### 3.3 Auto-resume at logon — rules and task safety (D2, D4)

**Eligibility.** All of these must hold, otherwise do nothing except log and notify:
1. The run was not stopped by the user. A user Stop removes eligibility permanently.
2. The recorder's last state is `interrupted_by_shutdown`, or `running` with a boot ID different from the current one (an unclean interruption, as in test 2).
3. The run is not completed, not a backup snapshot, and the existing `require_resume` preflight passes.
4. **The planned end has not passed**, judged by a verified SNTP time.
   - After logon, network can take minutes to come up. Retry the clock check with backoff for up to about 10 minutes.
   - If no verified time is obtained, do **not** resume. Notify "Schedule could not be verified; run not resumed."
   - Continuous runs (duration 0) have no end; only rules 1–3 apply.
5. No other RadiaLog recorder owns this serial number (the existing mutex).

**Expired runs are never restarted.** They are offered a **closing readout** instead (D11; specified in §3.8). It is never taken automatically at logon: it needs the user to act in the Runs tab.

**Task design:**
- Opt-in per run with a checkbox: "Resume automatically after a Windows restart (while this run is scheduled)". **Default: off (D9).** The task is created only after confirmation, showing the exact task name and command.
- Per-user, logon trigger, least privilege (`/RL LIMITED`), in a dedicated Task Scheduler folder `\RadiaLog\`, named after the run ID. No SYSTEM account, no stored password, no "run whether logged on or not": the device needs the user session.
- The action runs only `supervisor.py --auto-resume --run <path>` (or a dedicated `auto_resume.py`), which applies the eligibility rules itself. The task never changes the schedule.
- The task is **removed automatically** on completion, user Stop, expiry check, or when a run turns out ineligible. The GUI gets a "Scheduled tasks" view listing and removing RadiaLog tasks. The README documents the matching `schtasks /Query` and `/Delete` commands.
- Every creation and removal is logged as a run event, with the task XML saved in `<run>/logs/` for traceability.

**Limitation:** logon is required. After a Windows Update restart the machine sits at the sign-in screen and nothing resumes until you sign in. Windows' "automatically finish setting up after an update" sign-in option may cover this; it needs verifying on this machine and is not changed by RadiaLog.

### 3.4 Reconnect continuity test (D5)

You're right that cumulative counts and dose can be recovered after reconnect, provided the device was not reset. The current rule accepts continuity when four conditions hold:
- same serial
- same calibration and channel count
- device duration increased
- counts did not decrease

A **reset followed by enough new accumulation** can still pass all four (README limitation). Add one more condition:

- **Device elapsed time vs. wall-clock gap.** Across a gap, the device duration increment should equal the UTC gap (verified SNTP time before and after; after a reboot the monotonic clock is invalid) within a tolerance. Proposed tolerance: 1 s quantization + 2× the timing uncertainty + 0.1% of the gap. To be qualified empirically.
  - A spectrum reset restarts duration, so the increment would be much smaller than the gap. That case becomes a new epoch, not an aggregate.
  - Also compare the native accumulated-dose counter: it must not decrease, and its increase should be plausible against the pre-gap dose rate. Treat this as advisory only.
- Record the result as `continuity_test` (pass / fail / indeterminate) with all inputs in the event log, and show it in the startup-progress panel.

To verify on the device: whether the accumulated spectrum and its duration survive a device power-off or battery depletion, and which device actions (spectrum reset, dose reset, factory reset) reset which counters. Use a disposable test, never an experimental run.

### 3.5 Revised resume verification (D6)

Replaces v01 item 4 as described in §2:
- blocking full SHA-256 and sequence check
- tail reconstruction from a hash-chained checkpoint
- merged dose pass
- vectorized `reconstruct`
- full fallback if the checkpoint is invalid
- full re-reconstruction remains in the completion audit

Acceptance criteria:
- A 7-day / 10 s synthetic run resumes in ≤ 15 s warm.
- Cold-cache time is measured once on this PC after a reboot and reported.
- Tests corrupt a byte in an early file, a tail file and the checkpoint; each must block resume with *attention required*.

### 3.6 Notifications (D7)

**Events:**
- **completed** (required by D7)
- attention required
- interrupted → resumed
- auto-resume refused (expired or unverifiable schedule)
- spectrum stale beyond its threshold
- disk runway under 24 h

**Mechanism, Phase A:** a small notifier helper uses the already-installed PySide6 to show `QSystemTrayIcon.showMessage` with the RadiaLog icon. The supervisor launches it when an event occurs, so notifications work with the GUI closed. No new dependency and no registry changes.

**Icon in the pop-up:** Windows 11 builds the toast header from the sending app's identity. With an unregistered Python host, the header may show "Python" or a generic icon even when the tray icon is correct. Verify on this PC first.

If the icon is not shown, **Phase B** gives the toast its own identity:
- Register the AppUserModelID with a display name and icon, either through a Start-menu shortcut carrying that AUMID (preferred, easy to remove) or the per-user `HKCU\Software\Classes\AppUserModelId\<AUMID>` key.
- Optionally add `winrt-Windows.UI.Notifications` to the app-local dependencies.

Either option is a per-user Windows change. It will be proposed with exact entries and a removal step before anything is done.

**Do Not Disturb / Focus** suppresses toasts. Notifications are therefore also written to the run's event log, and the completion state shows in the tray icon tooltip and the GUI.

### 3.7 Runs tab and the one-job-per-device rule (D14)

**What the current code does (verified by reading it, not tested with two devices):**
- **Per-device exclusivity already exists at the lowest level.** Each recorder takes a Windows mutex named `Local\RadiaLog_<serial>`, and each supervisor takes `…_<serial>_supervisor`. The name is identical in every version from v0.1 to v0.5.1. The USB transport opens the device whose path contains that serial (`phyton.py`), and its handle is exclusive.
- **Recording from different RadiaCodes at the same time is therefore possible in the backend**: different serials, different mutexes, different run folders. It has **never been tested with two physical devices**; only one device (RC-103-006489) has been used.
- **The GUI does not support it and gives poor feedback:**
  - One window follows one run, and *Open saved run* is refused while that run is active.
  - *Start new recording* checks only the run currently selected in that window, not other runs on the same serial.
  - If another recorder holds the device, the new supervisor fails on its mutex and the window shows only "START FAILED — see diagnostics log".
- **Nothing finds running jobs the window is not showing.** Runs live in each version's `runs/` folder or any folder the user chose.
- **Interrupted runs hold no mutex** (their processes are gone), so a new run can take the device while an older run is still resumable. Auto-resume would then collide. Test 2 and the v0.2 soak both still read `running` in their journals.

**Policy:**
1. **One job per device**, where "job" includes a run waiting for resume (interrupted, schedule still open). A device is *claimed* by its active run or its pending-resume run.
2. **Different devices may record at the same time**, each with its own supervisor, recorder and worker.
3. **Starting a new run never stops anything silently.** If the chosen serial is claimed, the start dialog names the claiming run (label, start, planned end, state) and offers:
   - **End that run and start the new one.** This needs a second confirmation showing the label and remaining time, and the old run is ended through the same path as *End run* below.
   - **Cancel.**
4. Starting a run on an unclaimed serial while **other devices** are recording is allowed. The dialog lists those runs so nothing is forgotten.

**Run registry:**
- A small per-user index at `%LOCALAPPDATA%\RadiaLog\registry.sqlite` (**D17**) with run ID, path, serial, label, created/planned end, last known state, the auto-resume flag and the last-seen boot ID.
- Written when a run is created or changes state. Never holds scientific data. The journals stay authoritative, and the index can be rebuilt from them.
- Plus **"Scan folder for runs…"** to import existing runs: all version `runs/` folders, plus any folder you pick. This is how test 2 and the stale v0.2 soak appear.

**Runs tab (also reachable from the tray icon):** one row per non-archived run.

| Column | Content |
|---|---|
| State | Recording · Reconnecting · Starting/verifying · **Interrupted — resumable** · **Interrupted — expired** · Attention required · Completed · Ended |
| Device | serial, plus whether it is connected now |
| Label / folder | |
| Started / planned end | local time with UTC offset, and time remaining |
| Last spectrum | age; last telemetry age |
| Size & growth | current disk use, MB/day, projected size at planned end |
| Auto-resume | on/off, plus whether its scheduled task exists |
| Processes | supervisor/recorder/worker PIDs (verified by boot ID + creation time, §3.1/v01 item 2) |

**Row actions (each with confirmation where it changes anything):**
- **Open** (view in the main tabs; read-only for runs this window does not control)
- **Stop / End run**
- **Resume** (only if eligible under §3.3 rules 1–5, run manually)
- **Closing readout** (expired runs only, §3.8)
- **Toggle auto-resume**
- **Export / Validate / Show files**
- **Archive** (hides finished runs; no files are moved or deleted)

**What *End run* does:**
- *Running* run: the existing stop path, which takes a final short snapshot, then sets the state to `stopped (ended by user)`.
- *Interrupted* run: writes an `ended_by_user` event and sets the final state. It optionally takes a closing readout (§3.8) when the device is connected and unclaimed.
- In both cases it removes any auto-resume task and releases the device claim. The run can never be resumed afterwards. No raw data are changed.

**Guarding against forgotten long test runs:**
- At GUI start, and in the tray tooltip: a banner "N recordings active / M interrupted", linking to the Runs tab.
- The start dialog shows the planned end and the projected disk use, based on the measured ~0.5 GB/week of spectra and ~1.6 GB/week of journal at 10 s spectra with 1 s telemetry.
- An optional **"Test run"** flag shortens the default duration (**1 h, editable — D15**) and labels the run as a test in the Runs tab. Default durations for normal runs are unchanged.
- A notification **once a day (D16)** while a run is active with no window open, naming the run and its remaining time.
- Notifications for **interrupted — resumable** and **interrupted — expired** runs found at logon. Never an automatic action other than an opted-in resume.

**Multi-device monitoring:** the main window can follow one selected run. The Runs tab summarizes every run, and **Open** shows any of them read-only. A side-by-side live view of several devices goes in the "Later" release. **No second device is available (D19)**, so two-device operation stays "backend-capable, not qualified". It is tested only with synthetic serials, and the start dialog says so when a second serial is used. A physical two-device test is deferred until a second RadiaCode is available.

**Tests:**
- A second start on a claimed serial must be refused with the claiming run named.
- Two synthetic serials record concurrently without interference.
- *End run* on an interrupted run removes its task and its claim.
- A registry rebuild from journals matches the stored index.
- Stale `running` journals (the v0.2/test 2 pattern) are classified as interrupted.

### 3.8 Closing readout for expired interrupted runs (D11)

**Purpose:** when a run was interrupted and its scheduled end has since passed, recover the counts the device accumulated during the outage, **without restarting acquisition**.

**Preconditions:**
- The device is connected and not claimed by another run.
- The device serial matches the run.
- The run state is interrupted with an expired schedule.
- There is no previous closing readout.

**Procedure:**
1. Open the restricted client.
2. Read the cumulative spectrum and the latest status/dose records **once**, writing nothing to the device except the existing exchange initialization.
3. Run the §3.4 continuity test against the last saved spectrum.

**Storage:**
- One immutable record `closing_readout.json` in the run folder, with an index entry and an event.
- It is **not** appended as a spectrum sequence number and is never part of the in-schedule totals.
- Fields:
  - read time
  - the run's planned end
  - time elapsed after the planned end
  - device duration increment
  - count difference from the last saved spectrum
  - continuity-test verdict
  - native dose-counter difference and its age
  - an explicit statement that counts include time after the planned end and cannot be divided between before and after it

**Exports:** a separate table; the GUI shows it as a labeled post-schedule bar or spectrum. **If continuity fails** (device reset, recalibration), the readout is saved as `continuity_failed` with no difference computed.

### 3.9 Private git repository, single launcher, code organization (D20)

**Goals:**
- One authoritative source history, replacing the full-folder copies (`radiacode_logger_v01` … `v051`).
- Reviewable changes, so several agents can work in parallel on separate branches without overwriting each other.
- One launcher that always starts the current release.

**What goes in the repository:**
- Application source, tests, README/docs, `.ico`/SVG assets, release manifests and their scripts.
- `requirements-app.txt` and `dependencies-lock-v01.txt`, plus vendored third-party *license* files.

**What stays out (`.gitignore`):**
- `runs/`, any `*.sqlite*`, `logs/`, `cache/`, `validation/reliability_temp/`
- export/completion/backup folders, `gui_preferences.sqlite`, `__pycache__/`
- all experimental data, which stays where it is

Raw scientific data never enter git.

**Vendored `dependencies/` folder** (≈5 MB, including the compiled `.pyd`/`.dll` files):
- Proposed: don't commit it. Recreate it with a `tools/vendor_dependencies.py` script that runs `pip install --target dependencies` from the lock file. The script records SHA-256 hashes of the result in a committed `dependencies/MANIFEST.sha256`.
- This leaves the shared science venv untouched, as now.
- *Alternative:* commit it once via Git LFS. Simpler to reproduce offline, but it adds LFS.

**History import:**
- Import v0.1 → v0.5.1 as a sequence of commits, one per version folder, with tags `v0.1.0` … `v0.5.1` and the original folder dates in the commit messages. The source hashes are checked against each version's `release_manifest`.
- This keeps provenance and makes the past changes diff-able.
- The original folders stay in place, unchanged, as the archive. Mark them read-only only with your approval.

**Hosting:**
- Step 1: a local repository, e.g. `C:\Users\nelso\Desktop\Radiacode Data\RadiaLog` or another path you choose.
- Step 2: a **private** GitHub remote. This needs your explicit approval and your account. **Nothing is pushed until you approve.**
- This repository is separate from the flight-data repository already being staged.

**Working rules for multiple agents** (committed as `CONTRIBUTING.md` plus an `AGENTS.md`/`CLAUDE.md` with the same rules):
- Work on one branch per work item, e.g. `feat/runs-tab`, `fix/saved-status-import`. Never commit directly to `main`.
- Merge through a pull request only after the full test suite passes. Commits are small and touch one item each.
- Never touch run data, never create scheduled tasks or change the registry, and never install into the shared venv.
- Hardware-in-the-loop checks are run only by Michael.
- Every pull request states which files changed, the tests run, and any assumptions.
- **Continuous integration:** GitHub Actions on `windows-latest` runs the unit and synthetic GUI tests (offscreen Qt) on every pull request. Hardware and long-soak tests are not in CI. Private-repo Actions minutes are limited, so this needs checking against your plan.
- **Branch protection on `main`:** pull request required, CI must be green, no force-push.

**Releases and the single launcher:**
- A release is an annotated tag, e.g. `v0.5.2`. `tools/deploy_release.py <tag>` exports that tag into `%LOCALAPPDATA%\RadiaLog\releases\<tag>\` (or a folder you choose), builds or verifies `dependencies/`, runs the release checks, and writes the release manifest.
- One `Open_RadiaLog.cmd` (and later the shortcut with the icon) starts the release named in `releases\current.txt`. Rolling back means changing that one line.
- Older releases stay installed side by side, so a running recorder from an older release is never replaced underneath itself. Each run's manifest already records the software version that captured it.
- The 18 existing launchers at the workspace root stay put. Archive them into a subfolder only if you approve.

**Splitting the large window files** (behavior-preserving refactor; existing tests plus new view-level tests must pass):

| New module | Content taken from `gui.py` / `gui_analysis.py` |
|---|---|
| `ui/main_window.py` | window shell, tabs, menus, timers only |
| `ui/setup_panel.py` | new-run form, start/stop/resume controls, confirmations |
| `ui/runs_tab.py` | new Runs tab (§3.7) |
| `ui/health_strip.py` | status chips (v01 item 11) |
| `ui/charts.py` | `Charts` spectrogram/count/dose canvas |
| `ui/spectrum_tab.py` | `SpectrumCanvas`, spectrum source modes, ROI |
| `ui/summary_tab.py`, `ui/events_tab.py` | non-resetting summary; paged events |
| `ui/jobs.py` | export/validation/backup/time-resolved job launching and polling |
| `ui/theme.py` | colors, stylesheet, icon loading |
| `core/status.py` | `saved_status`, liveness (boot ID + creation time) |
| `core/registry.py` | per-user run registry |

- Backend modules (`recorder`, `supervisor`, `storage`, `device`, `analysis`, `export_run`) **move into `core/` without changes** in the same refactor, so the diff is only renames and imports.
- **Order:** the repository and history import come first. The refactor is its own pull request, before the v0.5.2 features, so every later feature lands in the new layout and agents can work on separate modules.
- Style is enforced from then on with `ruff` (format + lint), configured in `pyproject.toml`. It runs from a separate tool environment, not the shared venv. Existing code is reformatted only in the refactor pull request.


## 4. Unchanged from v01

The following are unchanged; see v01 for evidence and detail:
- **Shutdown awareness:** `ShutdownBlockReasonCreate` warning, `host_shutdown` event, `interrupted_by_shutdown` state.
- **Liveness:** boot ID and process creation time.
- **`saved_status` fix:** add the missing `sqlite3` import.
- **Per-run logs.**
- **Incremental whole-run overview:** the 116.8 s rebuild of test 2; profile first.
- **Follow-live throttling.**
- **Health strip** with disk runway.
- **Grouped left panel** and Stop confirmation.
- **Non-resetting Summary/Events tabs.**
- **Spectrogram interaction.**
- **Read-only second viewer.**

## 5. Revised sequencing

| Release | Contents |
|---|---|
| **Step 0 — repository (§3.9)** | Local private repo; history import v0.1–v0.5.1 with tags; `.gitignore`; dependency vendoring script; `CONTRIBUTING.md`/`AGENTS.md`; CI workflow; single launcher + `deploy_release.py`; then the behavior-preserving split of `gui.py`/`gui_analysis.py` (tests green, no feature changes). GitHub remote only after approval. |
| **v0.5.2** — small, no Windows changes | `saved_status` import fix; boot-ID/creation-time liveness and the "INTERRUPTED — resume available until …" state; Stop confirmation; non-resetting Summary/Events; per-run logs; **run registry + read-only Runs tab with folder scan; claimed-serial check at start with the claiming run named**; splash page; Processes panel and `--role` arguments; README operating notes. Icon: mock-ups for approval (D12), then the approved window icon + AppUserModelID |
| **v0.6** | Shutdown awareness; cheaper verify-first resume (§3.5); continuity test (§3.4); **Runs tab actions (Resume, End run, Archive), "end previous and start new" flow, active/interrupted banner, Test-run flag**; closing readout (§3.8); startup-progress panel; completion, attention and long-running notifications (Phase A); health strip; grouped panel; incremental overview |
| **v0.6.x** — only after explicit approval of the exact Windows changes | Opt-in per-run logon auto-resume tasks (§3.3); toast identity registration if Phase A lacks the icon (§3.6) |
| **Later** | Frozen executables (§3.1 Phase B, D10); side-by-side multi-device live view; two-device qualification when a second device exists (D19); spectrogram interaction |

**Qualification:** test 2 is not resumed (D13). Qualification starts fresh: a 24–48 h soak, then 7 days, on the release containing shutdown awareness. It should include:
- one deliberate restart with auto-resume enabled
- one restart after the planned end (must not resume; closing readout offered)
- one device spectrum reset during a gap on a disposable run (must create a new epoch)
- one attempted second start on the claimed device (must be refused, naming the claiming run)
- marking test 2 and the v0.2 soak as Ended from the Runs tab (D18), confirming their raw files and hashes are unchanged

## 6. Open items for Michael

**Resolved:** D8–D20 (§1).

**Remaining, all about the repository (§3.9):**
1. **Repository location** on disk (proposed: `C:\Users\nelso\Desktop\Radiacode Data\RadiaLog`).
2. **Vendored dependencies:** rebuild them from the lock file with a script (proposed), or commit them via Git LFS?
3. **History import:** import v0.1–v0.5.1 as tagged commits (proposed), or start fresh at v0.5.1?
4. **GitHub:** which account or organization will own the private remote, and is GitHub Actions on a private repository acceptable?
5. **Housekeeping:** archive the 18 root launchers into a subfolder, and mark the old version folders read-only? Both are optional; neither is needed for the plan.
6. **Git installation:** is Git for Windows already installed on this PC? To be checked at Step 0. Nothing will be installed system-wide without asking.
