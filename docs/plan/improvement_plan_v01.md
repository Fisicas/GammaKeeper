# RadiaLog v0.5.1 — UI and backend improvement plan

Review date: 2026-10-12. Scope: read-only review of `radiacode_logger_v051` (recorder, supervisor, storage, worker_client, gui, gui_analysis, preview, telemetry_analysis, saved_status, spectrum_view, run_policy, winfiles) and the saved run journals. **No application code, run data, or launchers were changed.** The only files written are in this folder.

Timings are from `benchmark_readonly_v01.py` → `benchmark_results_v01.json` (native Windows, science venv, warm file cache, uncached previews, no recorder running). They are single measurements, not a profiling study.

## 1. Findings

Tags: **[V]** verified from data or code; **[I]** inference from code, not observed in operation.

| # | Finding | Evidence |
|---|---|---|
| F1 | **Test 2 was ended by a manual Windows restart, and the journal still says "running".** Last spectrum seq 17,881 at 2026-10-10 18:08:57 UTC (14:08:57 EDT). Windows event 1074 (Start menu, "restart") was logged at 14:08:58 EDT. There is no `recorder_ended` event, and the recorder and supervisor states both remain `running`. The planned end is 2026-10-15 16:28 UTC, so the run can still be resumed. The v0.2 soak journal shows the same stale `running` state. | [V] run.sqlite, supervisor.sqlite, `time_units_export_20261011_v01/windows_restart_events_v01.json` |
| F2 | **No shutdown/logoff handling.** The recorder and supervisor run as `CREATE_NO_WINDOW` children. They have no `WM_QUERYENDSESSION`/`WM_ENDSESSION` handler, no `ShutdownBlockReasonCreate`, and no console control handler. A restart therefore gives no warning, and no final event is written. | [V] code |
| F3 | **GUI liveness checks use the PID only** (`winfiles.process_alive`). Health records do not store the boot ID or the process creation time. After a reboot, a reused PID can make a dead run look active, which disables Resume. | [I] code |
| F4 | **`saved_status.py` catches `sqlite3.Error` without importing `sqlite3`.** If `supervisor.sqlite` is locked when it is read, a `NameError` escapes `load_status`. `Window.refresh()` does not catch `NameError`, so that refresh tick is silently lost under `pythonw`. | [V] code; failure [I] not observed |
| F5 | **The whole-run view rebuilds everything from raw files.** `read_preview` re-reads, hashes and reconstructs every spectrum and decodes every telemetry batch in the window. Its cache is used only for closed runs. Uncached times: v0.3 run (10,369 spectra / 51,835 batches) **8.1 s**; test 2 (17,882 / 178,807) **116.8 s**. With *Whole run* + *Follow live*, a new rebuild is requested after each saved spectrum. During a multi-day run this would keep one core busy alongside acquisition. The growth is worse than linear (cause not isolated; candidates: re-sorting the `ObservationIndex` window for each group, one `SELECT` per row, telemetry JSON decoding). | [V] timing; continuous-load consequence [I] |
| F6 | **Resume cost grows with run length.** `RunStore.recover()` reads, hashes, parses and reconstructs every raw spectrum. The recorder then reads every file again to rebuild the dose totals. A single equivalent pass over test 2 took **9.2 s** (warm cache). By extrapolation, a 7-day run at 10 s is about 30 s per pass, ×2 passes, and longer with a cold cache. All of that is acquisition gap. | [V] timing; extrapolation [I] |
| F7 | Cold telemetry replay for the dose card is acceptable: 178,807 batches in 4.7 s over 36 GUI ticks, worst tick 0.15 s. **Not a priority.** | [V] timing |
| F8 | The **Run summary tab** is rewritten with `setPlainText` every 1 s, and the **Events table** is rebuilt every 5 s. Both reset scroll position and selection, so text cannot be copied reliably. Events shows only the latest 250 rows per source, and filtering happens client-side. | [V] code |
| F9 | **Stop recording asks for no confirmation.** | [V] code |
| F10 | **Diagnostics are stored per app version, not per run.** Recorder, completion and export logs go to `radiacode_logger_v051/logs` (export logs go to the app root). Unit-test fault logs land in the same folder: `logs/20261011_200349_943017_recorder.log` is a synthetic `bad_run`. A run resumed under a later version has its logs split across folders. | [V] |
| F11 | **Journal growth:** test 2's `run.sqlite` is 467.8 MB for about 50 h, roughly 1.6 GB/week at 1 s telemetry. That is on top of about 0.5 GB/week of spectrum JSON at 10 s. The README's space guidance mostly covers spectra. | [V] size; weekly rate extrapolated |
| F12 | **Workspace sprawl:** 18 launchers at the workspace root and 10 full app copies, each with vendored dependencies. This is a traceability and maintenance cost, not a runtime fault. | [V] |

Positive: the core architecture is sound. Most of the 2026-10-07/08 recommendations are already in place: a single-writer journal, a separate supervisor store, per-stream watchdogs, immutable raw files, a resume preflight, and background completion validation.

## 2. Plan

### Tier 1 — protect multi-day acquisitions (backend)

1. **Shutdown awareness (F1, F2).**
   - The recorder or supervisor creates a hidden message-only window.
   - On `WM_QUERYENDSESSION`, it calls `ShutdownBlockReasonCreate`. Windows then shows "RadiaLog is recording — restarting will create a gap" on a manual restart. This is advisory only: it cannot and should not block forced updates.
   - On `WM_ENDSESSION`, it writes a `host_shutdown` event, tries a final short snapshot within a bounded budget, and sets the recorder state to `interrupted_by_shutdown`.
   - Tests: synthetic session-end messages, and a check that raw data stay intact if the final read is skipped.
2. **Correct liveness (F3).** Store the boot ID and process creation time in recorder and supervisor state. The GUI then treats a process as alive only if (pid, creation time, boot ID) all match. When the boot ID differs, the run is labeled "INTERRUPTED — host restarted ≈ <time>; resume available until <planned end>".
3. **Fix the `saved_status` import (F4).** This is a one-line change. Also add a regression test with a locked `supervisor.sqlite`. Unexpected exceptions in `refresh()` should go to a GUI diagnostic log instead of being lost.
4. **Checkpointed resume (F6).**
   - Persist a verified checkpoint in `state`: last verified seq and its SHA-256, a running summary, dose integral and elapsed time, and a hash chain over the indexed digests.
   - On resume, verify only the checkpoint file plus the tail, then start acquiring.
   - Run the full verification as a low-priority background pass that raises *attention required* if it fails. Merge the two current scans into one.
   - **This is a policy change:** today everything is verified before acquisition resumes. Needs your decision.
5. **Per-run diagnostics (F10).** Write supervisor, recorder, completion, export and time-resolved logs to `<run>/logs/`. Tests should use only temporary directories. Keep the app-level log as a pointer only.
6. **Optional, opt-in auto-resume.** Add a per-user Task Scheduler "at logon" task that resumes runs marked `interrupted_by_shutdown`, but only if the resume preflight passes. It must never resume a completed, expired or backup run. This is a system change, so it needs your approval.
7. **Operating-procedure notes in the README.**
   - Set Windows Update active hours or pause updates for the experiment week.
   - Disable USB selective suspend for the run.
   - State that a lid close or power-button action can still sleep the machine despite `SetThreadExecutionState`.
   - Revise the space estimate to include the telemetry journal (F11).

### Tier 2 — GUI scalability

8. **Profile `read_preview`** to find the superlinear term (F5) before redesigning anything.
9. **Incremental overview store.**
   - Keep a derived, non-raw multi-resolution aggregate per run (for example 1 min / 10 min / 1 h groups). Each group stores channel sums, device seconds, flags and telemetry bins.
   - Append to it incrementally as spectra arrive. Record the verified-through seq and the source digests.
   - Add a "Rebuild from raw" action.
   - Targets: a 7-day whole-run view in under 2 s; under 50 ms of work per new spectrum.
10. **Throttle Whole run + Follow live.** Append only new groups, and never re-verify the whole run on every tick. Keep full verification in the completion/audit job, where it already lives.

### Tier 3 — UI/UX

11. **Health strip.** Replace the two text paragraphs with colored chips:
    - recorder state (recording / reconnecting / interrupted / attention)
    - spectrum age and telemetry age
    - clock quality
    - **disk runway** (free space ÷ measured growth rate → days)
    - elapsed and remaining time against the planned end
    - supervisor restart count
12. **Context-based left panel.** Group the controls as **New recording** / **Selected run** (Stop, Resume, Set up new) / **Data** (Export, Time-resolved export, Validate, Backup, Resume backup, Show files). Show only what applies to the current mode instead of 13 buttons that are always visible. **Stop** asks for confirmation, showing the run label and remaining time (F9).
13. **Summary and Events tabs (F8).**
    - Summary: a structured form updated in place, plus "Copy summary".
    - Events: SQL-side filtering, paging ("load older"), a severity column, and quick filters (gaps/reconnects, clock, late polls, decoder).
14. **Spectrogram interaction.**
    - Crosshair readout (UTC/local time, energy, channel, rate).
    - Drag a time range to accumulate its spectrum, reusing the existing collection-range engine.
    - Drag on the spectrum to set the ROI.
    - Overlay markers for reconnects, epoch changes and late polls.
15. **Notifications.** A tray icon showing state, and Windows toasts for *attention required*, *interrupted*, a spectrum stale beyond its threshold, and disk runway under 24 h. Remote notification (email, ntfy, etc.) is optional and needs a decision about network use.
16. **Read-only viewer while recording.** Let the user open another saved run in a second viewer window, instead of refusing *Open saved run* while a recording is active.

### Tier 4 — maintainability

17. Use one git repository with release tags instead of full-folder copies, and a single `Open_RadiaLog.cmd` pointing at the current release. Keep old folders read-only for provenance.
18. Gradually split `gui.py`/`gui_analysis.py` into views and controllers, and drop the semicolon-chained one-liners in core data paths. Behavior stays the same and the 104 tests must still pass.
19. Considered but **not recommended now**:
    - Batching telemetry commits: saves I/O but risks a few seconds of telemetry loss on a crash.
    - Packing spectra into daily segment files: about 60k files/week is fine on NTFS.

## 3. Suggested sequencing

- **v0.5.2 (small, low risk):** items 2, 3, 5, the Stop confirmation, the summary/events fixes from 13, and the README notes from 7.
- **v0.6:** items 1, 4, 8–10, 11 and 12.
- **v0.7:** items 14–16, 17 and 18.
- **Qualification:** a 24–48 h soak, then 7 days, on the release that contains item 1. A planned reboot test checks the new interruption path.

## 4. Decisions needed

1. Resume policy (item 4): may acquisition start before full background verification finishes?
2. Opt-in logon auto-resume task (item 6): yes or no?
3. Notifications: local toasts only, or also a remote channel?
4. Test 2: resume it before 2026-10-15 16:28 UTC, or start a fresh qualification run? If resumed, the gap since 2026-10-10 18:08:57 UTC becomes a flagged aggregate. Continuity across the reboot cannot be proven.
