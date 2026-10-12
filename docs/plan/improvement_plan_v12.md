# GammaKeeper improvement plan v12

Date: 2026-10-12. Supersedes v11 as the implementation status; earlier plans remain unchanged. This work targets the upcoming ten-day sealed-charcoal experiment. Branch: feat/ten-day-workflow, based on main 741dfdb. Application version: 0.6.0, pre-1.0.

## Implemented

| Area | Result |
|---|---|
| Integrity and coverage | Independent file/reconstruction, schedule, spectrum exposure, aggregate timing, telemetry and scientific-QC verdicts. Resets and missing scheduled tails require review. |
| S1 seal/annotations | Append-only IDs select corrections; clock evidence is evaluated at entry, with holdover/unknown uncertainty retained. Export freezes seal ID and annotation horizon. |
| S2 sample sheet | Versioned structured sample, exposure, geometry, environmental and background metadata in annotation sidecar. Geometry-description changes require review for advisory fitting. |
| S3 background | Manifest-hash linkage, source horizon, gross/net window trends, shared background variance and joint count likelihood. Geometry match remains documentary rather than physical proof. |
| Experiment setup | Explicit 10-day / 10 s preset; existing default and archived schedules retained. |
| S6 monitoring windows | Hourly native-channel sums; broad, explicitly mixed energy windows. Conditional apparent-decay advice uses integrated predictions, sufficient data and stated ingrowth exclusion. |
| S7 gain/temperature | Poisson Gaussian/continuum QC with a shared-shift 609/583 model, weak-fit rejection, 5 keV provisional drift threshold, and device-temperature provenance. No calibration correction. |
| S8 aggregate export | Eligible recovered counts become isolated variable-width observations when selected; resets/suspect timing remain excluded. No invented regular bins. |
| Long-run responsiveness | Incremental live counts/mature telemetry cache, one-minute whole-run plot throttle, bounded spectral exports and SQLite telemetry integration. |
| Recovery | Dose restoration merged into the existing verified raw pass; bounded read-only prefetch overlaps open latency. Every raw hash and reconstruction is still checked before acquisition. |
| Operation | Experiment overview, approximate disk headroom, versioned preferences, local tray monitor, hourly QC jobs, optional dated daily/final verified backups, stop confirmation, stable text/event selection, and separate read-only viewers. |
| Reproducibility | Run/analysis source hashes, deployment/Git identity and dirty status; resumed builds recorded separately. |
| Scientific export | Complete QC report with window definitions, initial guesses, bounds, conditional standard errors, assumptions, background covariance and raw-source horizon. No absolute-activity estimate. |

Detailed conventions and user operation: [TEN_DAY_EXPERIMENTS.md](../TEN_DAY_EXPERIMENTS.md).

## Current evidence

The existing suite plus new integrity, streaming, known-signal and GUI-free monitor tests passed locally before the final review fixes. Final suite/CI and benchmark evidence are recorded in the validation note delivered with this branch.

A full synthetic ten-day fixture contained 86,400 intervals, 1,024 channels and 864,001 telemetry batches. Every exported channel sum matched its cumulative endpoint difference. The streamed native export used 173.72 MiB peak working set in that run. Cached full preview was 0.0647 s; fresh preview was 92.01 s. Fixture preparation (97.97 s) is reported separately.

Initial complete-byte recovery took 502.24 s. A 1,000-interval profile attributed 5.30 of 6.78 s to file opening. This motivated bounded parallel file reads rather than weakening integrity checks or changing Windows security settings. The follow-up fresh-fixture recovery measured 79.04 s, with every hash and endpoint count checked. It used sparse telemetry for spectrum-recovery measurement; filesystem caching was uncontrolled. See app/validation/ten_day_validation_v01.md and recovery_ten_day_v01.json.

These are synthetic capacity/numerical checks. Filesystem cache and external system load were not controlled. The capacity evidence identifies its source build; metadata/scope fixes and the recovery prefetch are covered by subsequent targeted/full checks.

## Remaining physical qualification and limits

- Michael performs all device commands, real USB fault/restart exercises, notification delivery checks on the interactive desktop and the actual multi-day experiment.
- Absolute energy/efficiency calibration, dead-time behavior, geometry/containment, background stationarity, room/canister sensing and seal-time uncertainty require experimental evidence.
- Three-hour ingrowth exclusion and fixed-geometry/constant-background exponential advice are assumptions. Full early-time chain analysis and interfering components remain offline scientific work; do not equate an advisory fitted half-life with physical qualification.
- The evaluated nuclear reference is explicitly ENSDF September 2023, 3.8222(9) d. Retain the evaluation and uncertainty in comparisons rather than treating an older plan's 3.8235 d as exact.
- Cold filesystem/OS-cache performance is not established by the synthetic benchmark.
- Multi-device operation remains backend-capable and physically unqualified.
- Logon auto-resume, scheduled tasks, Windows setting changes and packaging are deferred. Local processes cannot notify while the computer is off.
- Publishing/privacy scrub remains as specified in v11; the repository remains private.

## Preservation and workflow

Raw data, old release folders, the primary checkout and pushed history are preserved. Development uses an isolated worktree and small commits. Dependencies were rebuilt from the committed device-library lock without altering the shared science environment. No physical device was opened during development.

Changes reach main through a pull request after the full suite and CI. The pre-push guard remains enabled. Installation/activation is a separate action through the existing side-by-side installer after review.
