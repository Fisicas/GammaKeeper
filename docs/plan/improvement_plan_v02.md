# RadiaLog v0.5.1 — UI and backend improvement plan, v02

Revision of `improvement_plan_v01.md` (preserved unchanged). v02 records Michael's decisions of 2026-10-12, adds identity/icon/splash/notification work, defines the auto-resume rules, and replaces the resume-verification proposal with measured numbers. **No application code, run data, launchers or Windows settings were changed.** No scheduled task was created.

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

**Proposed rule, for confirmation:** no acquisition before pre-resume verification ever. That means no "acquire first, verify in background" path. This follows D6 and replaces v01 item 4.

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
  - Recommendation: Phase A now; Phase B once the reliability items are qualified.

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

**Expired runs are never restarted.** Optional, default off, needs D-decision 4: a one-shot **closing readout** for an expired interrupted run. It reads the cumulative spectrum and dose counter once and stores them as a separate, explicitly post-schedule record, without reopening acquisition. Its counts include time after the planned end, so it is labeled and never merged into the run's in-schedule totals.

**Task design:**
- Opt-in per run with a checkbox: "Resume automatically after a Windows restart (while this run is scheduled)". Default proposed: off. The task is created only after confirmation, showing the exact task name and command.
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
- **Git repository and single launcher.**
- **GUI refactor.**

## 5. Revised sequencing

| Release | Contents |
|---|---|
| **v0.5.2** — small, no Windows changes | `saved_status` import fix; boot-ID/creation-time liveness and the "INTERRUPTED — resume available until …" state; Stop confirmation; non-resetting Summary/Events; per-run logs; window icon + AppUserModelID; splash page; Processes panel and `--role` arguments; README operating notes |
| **v0.6** | Shutdown awareness; cheaper verify-first resume (§3.5); continuity test (§3.4); startup-progress panel; completion and attention notifications (Phase A); health strip; grouped panel; incremental overview |
| **v0.6.x** — only after explicit approval of the exact Windows changes | Opt-in per-run logon auto-resume tasks (§3.3); toast identity registration if Phase A lacks the icon (§3.6) |
| **Later** | Frozen executables (§3.1 Phase B); spectrogram interaction; second viewer; refactor |

**Qualification:** a 24–48 h soak, then 7 days, on the release containing shutdown awareness. It should include one deliberate restart with auto-resume enabled, one restart after the planned end (must not resume), and one device spectrum reset during a gap on a disposable run (must create a new epoch).

## 6. Open items for Michael

1. Confirm the §2 rule: no acquisition before pre-resume verification, ever.
2. Default for the auto-resume checkbox: off (proposed) or on?
3. Frozen executables (§3.1 Phase B): later, or never?
4. Optional closing readout for expired interrupted runs (§3.3): yes or no?
5. Icon direction: e.g. a stylized detector or spectrum-trace motif in the existing teal/gold palette. Mock-ups to be produced for approval before any file goes into the app.
6. Test 2: resume before 2026-10-15 16:28 UTC, or start fresh. This is still open from v01.
