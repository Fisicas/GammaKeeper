# GammaKeeper manual and release notes

GammaKeeper (formerly RadiaLog) records time-resolved gamma spectra from RadiaCode detectors over USB on Windows, for unattended multi-day experiments such as sealed-canister radon measurements on activated charcoal. It keeps immutable, hash-verified raw spectra, a crash-safe journal, SNTP-referenced timing, automatic reconnect with flagged gap aggregates, and resumable schedules. It is independent community software, not affiliated with RadiaCode / Scan-Electronics.

## Name and compatibility

- Releases v0.1–v0.5.1 were published as **RadiaLog**. Their folders, launchers and recorded runs keep that name, and the release notes below describe them as they were.
- Identifiers shared with those releases are unchanged so old and new versions stay compatible: the Windows mutex `Local\RadiaLog_<serial>` (old and new releases still exclude each other on the same device), the run-folder layout, manifest and journal field names, recorded `software_version` values, and the time-resolved export schema `radialog-time-resolved-v1`.
- Environment variables are now `GAMMAKEEPER_SCIENCE_PYTHON` and `GAMMAKEEPER_TEST_MODE`; the `RADIALOG_*` names still work.

## Release notes (current code: 0.5.1)

Paths beginning with `../` in these notes are relative to the original `radiacode_logger_v0*` release folder, i.e. they name folders in the `Radiacode Data` workspace. In this repository the application lives in `app/`.

## Linear and logarithmic display

Use the independent **Energy axis** and **Counts axis** selectors in **Spectrum / ROI**. Their linked controls also change the spectrogram energy axis and time-series count-rate axis. **Spectrogram intensity** independently selects linear or logarithmic colors. Choices are remembered on GUI reopening. Log views hide nonpositive values without adding pseudocounts; Linear shows measured zeros. Raw spectra, calibrated energies, ROI sums and scientific exports are unchanged. Acquisition and reconstruction rules are retained.


## Flexible time input and time-resolved export

Duration and count interval now accept expressions such as **60 minutes**, **1 hour**, and **1h 30min**. A new **Time-resolved export** tab writes original intervals, wider nonoverlapping bins, a chosen number of spectra, or moving averages. It provides CSV/NPZ, native RCSPG for nonoverlapping selections, and optional XLSX matrices for UFit. Actual bin timing, device exposure, source hashes, exclusions and overlap are preserved. See [TIME_RESOLVED.md](TIME_RESOLVED.md) for formats, usage and scientific limits.

This release preserves v0.4.2 and all acquired records in their original folders. Validation includes synthetic fault/GUI regressions and a real saved-run export with channel-by-channel count conservation. New RCSPG files have structural and roundtrip checks; native Windows import and UFit execution have not been tested. Capture defaults remain 10 s and seven days; shorter cadence requires experimental qualification. Reconnect supervision cannot survive a Windows reboot without relaunch; no startup task is installed.

Native Windows GUI and an independent experiment recorder for the connected RadiaCode-103. Defaults: **10-second cumulative-spectrum reads, one-week runs, one-second telemetry polling**. This reliability update has targeted fault-regression validation; a real seven-day soak remains required for week-long experimental qualification.

## Accumulated recording and collection spectra

**Spectrum / ROI** now defaults to **Accumulated recording**: it sums the experiment's recorded interval differences, excluding pre-recording cumulative counts, and grows as new spectra arrive. The selector also offers **Latest recorded interval**, **Selected heatmap interval / group**, and **Accumulated collection range (UTC)**.

To inspect a collection range, select that mode, set **UTC from** and **to**, uncheck **Until latest saved spectrum** for a fixed end, and click **Apply range**. With the checkbox checked, accumulation begins at the chosen start and continues through newly saved spectra. ROI controls apply to the displayed accumulated spectrum; counts remain integer original-channel counts.

Selection includes only complete recorded intervals inside the requested UTC bounds. The plot shows the actual included start/end and exposure, and the message reports excluded straddling intervals. Counts are not divided among unknown sub-interval photon times. Recovered gap aggregates are retained when fully included and explicitly flagged. Reset baselines contribute no new counts. Epochs with different calibration/channel grids cannot be silently combined; select one epoch instead. No smoothing, baseline subtraction, normalization, energy resampling or spectral reset is applied.

Accumulation runs in a background worker and processes subsequent records incrementally, preserving responsiveness and bounded result memory. The active recorder need not be stopped or restarted to use this viewer. Tests and live-journal renders in `validation` verify both recording totals and requested ranges against saved endpoint differences.

## Spectrum / ROI tab correction

Opening **Spectrum / ROI** now automatically shows the **latest recorded interval** and updates it as new spectra arrive. Previously, this tab stayed blank until an interval was clicked on the time-series heatmap. The empty/baseline states now have a styled explanation. Counts are successive cumulative-spectrum differences, so pre-recording counts are excluded; no smoothing or spectral resampling is applied.

The tab has its own ROI controls, synchronized with the time-series controls, and a spectrum-source selector. **Selected heatmap interval / group** preserves a deliberate selection while recording continues. Baselines, missing selections, invalid ROI bounds and integrity errors are explained instead of leaving a blank white plot. Changing recordings clears the previous spectrum. The active recorder can continue while this viewer is reopened.

The v0.4.1 release passes 72 regression tests, including six tests that actually open the tab, advance recorded data, change ROI controls, retain a historical selection, handle baselines and clear old selections. The live-data visual check is in `validation/spectrum_tab_live_v01.png`. Prior versions and original data remain preserved; acquisition behavior is unchanged.

## October 8 reliability and analysis release

This release implements the overnight-review recommendations. The supervisor writes health to its own SQLite store, watches spectrum and telemetry streams independently, and recognizes progress during lengthy raw-file recovery. Expired/completed/backup runs are rejected before a writer is opened. Spectrum deadlines have priority over telemetry. New raw telemetry records have payload hashes, and live count summaries are committed atomically with spectra.

UTC uses validated independent SNTP samples bound to Windows' shared monotonic performance counter. Updates slew continuously at at most 5 ms/s; network failure keeps the previous reference, including a same-boot recorder restart. Single-source mode, reference age, holdover, remaining correction and uncertainty are saved. The 100 ppm holdover-growth term is an explicit engineering assumption, not a certified oscillator bound. Normal checks occur every 15 minutes; failures retry with exponential spacing capped at 10 minutes. The program does not change the computer clock or Windows Time service. Device sample/filter latency and clock skew remain uncalibrated, and raw timestamps are never silently replaced.

The time-series tab offers recent/full/custom UTC windows, epoch selection, UTC/local axes, color limits and ROI rates. Click a heatmap interval for its original-channel spectrum; terminal-channel interpretation is separate. Whole-run display groups sum recorded integer differences and divide by summed device duration. Gaps/mixed groups remain blank. The run-summary tab shows counts, rate, counting-only uncertainty, dose coverage, counter age, integrity results and free space. Events/logs are searchable. Manual navigation disables live-follow; reopening remembers the selected run or setup.

Completed runs automatically launch a detached, low-priority validation/export job. Acquisition completion is preserved if optional export fails. **Create verified backup** copies a consistent journal horizon and verifies copied hashes. **Resume interrupted backup** continues that same horizon without overwriting changed files. Backup snapshots can be inspected but cannot be resumed as recordings. Optional derived caches/checkpoints accelerate reopened analysis; cache age/source-check time is labeled and full integrity validation remains separate.

Start the release as described under **Start** below. Previous versions and launchers are preserved. Close an older GUI and reopen to use v0.5.1; GUI closure does not stop acquisition. Current regressions are under `validation`; display renders are in `../display_scales_20261011_v01`, and earlier saved-run export evidence remains in `../time_units_export_20261011_v01`. See [DISPLAY_SCALES.md](DISPLAY_SCALES.md) for display semantics. Earlier connected-device pilot evidence remains in the original version directories. The historical sections below describe earlier releases and their checks.

## Saved-run settings correction

Opening or resuming a saved run restores its label, serial number, spectrum/count interval, original duration, recording mode and save folder from the immutable manifest. These controls are read-only while a run is selected. The summary above the plots shows the original start, planned end and time until that end, with explicit local UTC offsets; original UTC values are available in its tooltip. A continuous run is labeled as continuous. Resume retains the original end time and never restarts the duration.

After stopping or opening a stopped run, choose **Set up new recording** to use those settings as an editable template for a separate recording. This clears the selected run from the display without changing its files. Fractional intervals and short durations can be displayed without rounding them to integers or to zero days. Invalid manifests show an explicit settings warning instead of presenting new-run defaults as saved values.

This correction leaves acquisition and reconnection behavior unchanged. It passes 46 regression tests and synthetic GUI close/reopen, settings restoration, stop and export checks. A rendered preview against the current device run is in `../gui_settings_fix_20261007_v01/current_run_preview_v01.png`. Original v0.3.0 code and run data are preserved.


## October 7 reliability update

The overnight v0.1 run exited at 01:21:49 EDT when Windows denied replacement of `status.json`. The USB retry loop ended with the recorder process. This release fixes that failure path and adds an independent supervisor for automatic recorder restart/resume. The detailed evidence and limitations are in `../investigation_20261007_v01/failure_report_v02.md`. The old software and failed run are preserved.

The GUI reads the SQLite WAL journal and never opens the replaceable JSON cache. Compatibility-cache updates are isolated, bounded and nonfatal. The supervisor remains running after the window closes, restarts unexpected recorder exits, and monitors durable journal progress with a monotonic watchdog. Persistent raw-data/integrity/storage failures stop with an explicit diagnostic rather than retrying over damaged data.

The device declares group 4 as a six-byte dose-counter record; the earlier library expected sixteen-byte user data. Configuration-aware decoding fixes that exact recorded payload, retains valid prefixes on unknown/malformed frames, and preserves all raw bytes. Historical status records are kept in telemetry but do not replace newer live status or create false dose-reset detections. USB framing handles zero-length packets/split headers; blocking IPC cannot escape the parent's deadline. Short raw-file commit retries tolerate transient locks without overwriting raw data.

The release regression suite passes **38 tests**. Two real-device fault pilots reproduced a locked cache, USB reopening and forced recorder termination; both resumed automatically, conserved counts and retained calibration. GUI integration covers start, window closure/reopening, stop and export. A full overnight/24-hour soak remains a separate qualification; its request/result files are under `validation`. Original 655 overnight spectra remain hash-identical.


## October 7 live-review correction

The active v0.2 soak was left running. Its raw data are valid, but its one-second future-timestamp filter eventually excludes real-time telemetry as the projected device clock drifts ahead of host UTC. This can freeze the latest dose estimate and report false coverage losses. v0.3 stops using that projection as a reason to discard valid estimator observations.

Derived live dose uses **host reception UTC** as an explicit observation coordinate, retaining original device timestamps separately. Exact physical sample/filter latency is uncalibrated. The exporter independently recomputes dose bins from raw telemetry, and preserves the original snapshot coverage in a separate column. The monitor reprocesses old v0.2 runs without editing their raw files. Spectrum differences, calibration and channel order are unchanged.

The interface adds data ages, supervisor restart count, clock-projection lead, bounded incremental telemetry catch-up, and plot zoom/pan/save controls. New runs use the revised backend; the already-running v0.2 collector is not hot-patched. Use v0.3 for corrected monitoring and export of that run. See `../current_review_20261007_v01/review_and_improvement_plan_v01.md` for verified status and next priorities.

## Start

Install with `tools\install_gammakeeper.cmd` (see the README) and use the **GammaKeeper** Desktop or Start Menu shortcut, or double-click `app\Launch_GammaKeeper.cmd` in a checkout. The archived RadiaLog launchers in the workspace still start their original version folders. The launcher uses the existing science Python; dependencies added for this app are confined to `app/dependencies`. The shared environment and USB driver were not changed.

1. Close the manufacturer app and other device clients. The USB handle is exclusive.
2. Enter the sample/condition label, confirm serial `RC-103-006489`, interval **10 s**, duration **7 days**, and save folder.
3. Click **Start new recording**. Check that spectra and telemetry appear and that the footer reports a recent SNTP clock measurement.
4. The recorder continues if the GUI closes. Reopen the GUI and use **Open saved run** to monitor it. Use **Stop recording** to end it. Stopping attempts a final short snapshot, explicitly marked in the data.
5. Use **Export CSV + spectra** during or after a run. Each export gets a new folder. Unexpected recorder exits are restarted and resumed automatically by the supervisor. **Resume selected run** remains available for manually stopped or reboot-interrupted runs, until the original planned end. A Windows reboot requires reopening and resuming; no system startup task was installed.

Maintain power and prevent deliberate sleep/reboot during the experiment. The recorder requests inhibition of automatic system sleep while active; success is recorded. A power loss or disconnected device creates a real acquisition gap.

## Data and scientific meaning

- `manifest.json`: immutable configuration, software/library versions, units and processing rules.
- `spectra/YYYY-MM-DD/#########.json`: immutable cumulative spectra, original 1,024 integer channels, device elapsed time, polynomial calibration, original binary payload, UTC read bounds, original host timestamps, connection identity and status samples. There is no rolling retention limit or 24-hour overwrite.
- `run.sqlite`: append-only telemetry batches including complete raw binary payloads, all decoded flags/error percentages/events, spectrum index with SHA-256, and connection/retry/clock audit history. SQLite uses WAL and `synchronous=FULL`. Mutable state and index are separate from immutable raw spectra. Back up the whole run folder; while active, also retain `run.sqlite-wal` and `run.sqlite-shm`, or use the app's consistent export.
- `connections`: immutable device configuration, firmware and exchange-clock initialization provenance. Pre-anchor buffered data is retained with its prior time mapping explicitly unknown.
- `status.json`: optional replaceable compatibility cache. The GUI reads `ui_status` and supervisor metadata from SQLite WAL; cache errors cannot stop recording.

The first spectrum is a **baseline**, even if the device already holds millions of counts. Interval counts are `C(t2) - C(t1)` channel by channel. Spectrum-derived cps is the summed difference divided by the **actual device elapsed-time increment**, not blindly by 10. Nominal 10-second requests can yield other durations; the device duration is quantized to whole seconds. The displayed device count-rate estimator has a different averaging history and is saved separately.

Calibration is `E(ch) = a0 + a1*ch + a2*ch²` in keV. Channel order is preserved and energy increases to the right in the GUI. Time increases downward in the spectrogram. Preview uses the most recent 240 intervals and original channels; raw retention is unlimited. Logarithmic color is display only. There is **no smoothing, normalization, background subtraction, energy resampling, or change to device calibration**.

The device configuration verified raw dose-rate units **R/h** and dose units **R**. Display/export µSv uses the instrument convention `1 R -> 0.01 Sv`, i.e. raw R values times 10,000. This is the instrument's conversion, not a universal physical equivalence. No constant conversion from count rate to dose is introduced.

Dose-rate bins use time-weighted device estimates with zero-order hold capped at 3 seconds; longer gaps are left missing. `integrated_dose_uSv` is provided only when a bin has >=99.9% telemetry time coverage. `observed_integrated_dose_uSv` and its cumulative counterpart integrate only the observed periods and include coverage fractions. The GUI's **Integrated dose • observed** card shows this derived quantity. The native accumulated-dose counter is saved separately, with its timestamp, age, baseline and reset indicator. Buffered counter updates are sparse at background levels; repeated counter values are not fresh measurements. Initial status records with future protocol timestamps retain the original timestamp and use explicitly labeled reception time for the status observation.

`poisson_sigma_cps = sqrt(N)/duration` is a counting-only approximation, not a confidence interval or a total uncertainty budget. Device count/dose error percentages and flags remain separately available in raw telemetry. Clock, calibration, dead time and detector systematics are not folded into this sigma.

## Time and reconnection

Scheduling uses monotonic deadlines rather than sleeping 10 seconds after a read. A separate USB worker has bounded operation deadlines; the recorder can terminate a stalled worker and reopen the transport. Acquisition and durable storage do not depend on GUI redraws.

Retries occur at **1, 2, 4, 8, 16, 32, 60, 60… seconds after each failed attempt**. These are waiting intervals; opening/timeout time is additional. Backoff resets only after 60 seconds of connection stability and at least three saved spectra. Every failure/retry is recorded. No spectrum, dose, alarm or calibration reset commands are permitted by the restricted client. Only exchange setup, device civil-time synchronization and exchange timestamp initialization are allowed writes; these are logged.

After reconnection, matching serial/calibration, increasing duration and nondecreasing counts permit a **flagged aggregate difference** across the gap. Continuity is inferred; a reset followed by enough new accumulation during a long outage cannot always be ruled out. Aggregate counts are retained but never presented as fabricated 10-second spectra. Resets, power events, counter decreases and calibration/channel changes establish a new baseline/epoch. GUI gaps remain blank. `valid` and `nominal_time_bin` in exports have different meanings.

SNTP queries to `time.windows.com` and `time.cloudflare.com` measure the host offset at start and hourly. They do not change the Windows clock. UTC read bounds and buffered telemetry use a monotonic UTC reference anchored to the saved measurement; original host UTC is retained. Server origin/mode/stratum checks, round-trip delay and root dispersion are saved. Hourly corrections and clock reference shifts are audited. The reported uncertainty is an estimate for the synchronization measurement; device-time quantization, subsequent oscillator drift and spectral latch timing remain separate. If no server is available, the app records an explicitly unverified host-clock reference rather than dropping measurement data. The Windows Time service was stopped during development, which is why independent queries were added.

## Offline exports

Exports copy the SQLite index consistently, verify raw SHA-256 hashes, and independently reconstruct spectrum differences.

- `time_bins.csv`: UTC midpoint boundaries, actual durations, counts/cps, Poisson sigma, quality/epoch, dose-rate means and integration coverage, native counter age, original host timestamps and clock metadata.
- `telemetry.csv`: all decoded telemetry records. **Its `dose_rate` and `dose` columns remain in raw R/h and R**; see export metadata.
- `spectra_#####.npz`: chunks of at most 1,000 spectra, signed 64-bit channel differences, channel indices, per-row calibration, duration, epoch, quality and validity. `-1` denotes a baseline/invalid row; consult `valid`. Recovered aggregates have `valid=True` and `nominal_time_bin=False`. Load with `allow_pickle=False`.
- `events.jsonl`, `source_index.sqlite`, `EXPORT_COMPLETE.json`: audit history, raw telemetry backup, provenance and completion check. An incomplete export has no completion marker.

Do not concatenate cumulative spectra directly as if they were independent time bins. Use the differences, baseline handling, duration and quality flags above. A week requires about 60,480 nominal intervals, with actual count depending on startup, final partial bin and gaps. Reserve several GB and check actual run growth; the recorder stops if free space falls below 256 MiB and never deletes prior data.

## Development and validation

```powershell
& 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 -m unittest discover -s '.\tests' -v
& 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 '.\gui.py'
& 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 '.\supervisor.py' --run '.\runs\example_unique_run' --seconds 604800 --interval 10
& 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 '.\export_run.py' '.\runs\example_unique_run'
```

Run these commands from the `app` folder. Simulation requires `--demo` and is prominently labeled. Original spectra elsewhere in the workspace were preserved. The latest numbered `validation/release_manifest_v*.json`, produced by `release_checks.py`, records current automated/integration/export checks and source hashes. Earlier pilot reports remain in their original version directories.

The v0.5.0 suite has 94 checks, including flexible-unit parsing, real GUI export arguments, time-bin conservation, range selection, overlap, integrity and XLSX orientation. Inherited coverage includes baseline/duration/conservation, locking, automatic restart, decoder, historical-status and integrity checks. Original checks cover existing-count baselines, actual duration, unsigned underflow/resets, calibration/events, gaps, count conservation over 60,480 simulated intervals, bounded worker failure, monotonic UTC, SNTP validation, atomic-write recovery, raw integrity, multiple-writer exclusion, forward/backward UTC day transitions, duplicate telemetry timestamps, export round-trip and blocked destructive device commands. GUI integration checks start, closure with recorder survival, reopening, stopping and exporting using synthetic data. Short real-device pilots test the installed Phyton USB driver and software transport close/reopen. These do **not** establish seven-day stability, physical cable-removal recovery, power-loss behavior of the SSD, or independent detector/calibration accuracy.

Before the experiment: perform an unattended 24–48 hour soak, physically unplug/replug USB once, verify an app stop/resume and a Windows restart/resume on a disposable run, and audit exported counts and gap flags. Then complete a representative week if the experiment requires that qualification. Do not use experimental raw data as a fault-injection target.

## Sources and licenses

The application uses the MIT-licensed [cdump/radiacode](https://github.com/cdump/radiacode) 0.4.0 protocol and decoders (reviewed at commit `2b217916f49f5eedddf8f0d9116fcfd3c6b8832e`). Its upstream license is preserved in `THIRD_PARTY_LICENSES/radiacode-LICENSE.txt`; the PyPI wheel did not include a separate license file. The restricted client adapts its command framing while replacing assertion-only checks with explicit exceptions. The Windows transport is new code using standard SetupAPI and overlapped ReadFile/WriteFile through the existing Phyton driver; no private IOCTLs or driver replacement are used.

The recording/reconstruction approach was informed by [ckuethe/radiacode-tools](https://github.com/ckuethe/radiacode-tools) (MIT, reviewed commit `41af71b3f17af01677dfd151855e72d144581d97`); its Unix signal handling, abort-on-USB-error logic and first-bin baseline behavior were not copied. PySide6 supplies Qt, NumPy supplies arrays, and Matplotlib supplies plots. No BecqMoni GPL code was copied. See `THIRD_PARTY.md` and dependency license files. This is independent community software, not a manufacturer product.
