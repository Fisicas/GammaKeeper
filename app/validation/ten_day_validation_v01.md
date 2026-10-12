# GammaKeeper 0.6.0 validation — 12 October 2026

This evidence covers software and disposable synthetic data. No detector was opened and no existing recorded data or archived release was changed. The implementation is on feat/ten-day-workflow in its own worktree.

## Reproduction

Use the configured Windows science interpreter with -X utf8:
```powershell
$py = git config --get gammakeeper.python
& $py -X utf8 -m unittest discover -s app\tests -v
& $py -X utf8 tools\benchmark_ten_day.py --output app\validation\capacity-new.json --screenshot app\validation\capacity-new.png
& $py -X utf8 tools\benchmark_recovery.py
```

All evidence files use exclusive creation. Choose new output names or an incremented script/output name for repeated measurements. The benchmarks create and delete only their own disposable fixtures under app/validation/capacity_temp. Raw spectra and journals are excluded from Git.

## Software checks

The suite covers original recording/reliability behavior plus:
- reset and missing-tail classification independent of file integrity;
- corrected seal order under a host-clock backstep and unverified clock labeling;
- frozen/reopened backup notes and annotation corruption detection;
- chunked channel-count conservation, aggregate isolation and seal-zero evidence;
- unknown timing uncertainty retained as unknown in mixed groups;
- streaming telemetry agreement with the original integration rule;
- known synthetic blended-peak shift and integrated Poisson decay with shared background;
- incremental count-cache conservation and altered endpoint detection;
- GUI-free verified backup, alert debounce/recovery, versioned sample sheets and read-only viewing;
- negative net rates, shared background variance and changed background-manifest refusal;
- corruption in an early or tail file refusing acquisition before a client is constructed.

The final suite count and its result are in ten_day_tests_v03.txt and the branch's pre-push/CI check. Earlier test evidence is retained locally; a discovered completion hash-helper name collision was repaired before the final suite.

Dependency vendoring matched the committed package-code reference. Differences were restricted to the same 28 tolerated pip metadata files. The shared environment was not installed into or upgraded. Numerical/QC requirements include SciPy 1.18.1.

## Full synthetic capacity fixture

capacity_ten_day_v01.json identifies the tested build and all app-source hashes. It used 86,400 intervals (86,401 cumulative snapshots), 1,024 channels, 864,001 synthetic telemetry batches and 864,000 s device exposure.

| Measurement | Result |
|---|---:|
| Fixture preparation | 97.97 s |
| Native streamed export | 196.82 s |
| Export chunks (max 256 spectra) | 338 |
| Export peak process working set | 173.72 MiB |
| Fresh count overview | 78.70 s |
| Cached count overview | 0.0384 s |
| Fresh live preview | 92.01 s |
| Cached live preview | 0.0647 s |
| Unique exported counts vs cumulative endpoint differences | exact in every channel |

The fixture is deterministic and intentionally synthetic; the separate known-signal tests use seeded Poisson data. Native-export measurements precede metadata/scope review fixes that do not change count arithmetic or chunk bounds. The measured source build is retained rather than relabeled as a later commit.

## Recovery follow-up

Initial full-byte recovery was 502.24 s. The 1,000-interval profile found 5.30 of 6.78 s in file opening. Bounded prefetch overlaps opening/reading across eight read-only threads, with at most sixteen pending payloads. Reconstruction, every hash, sequence and epoch remain checked in order before acquisition.

recovery_ten_day_v01.json identifies the follow-up source build. On a fresh fixture it measured 79.04 s recovery, with preparation 81.47 s separately reported. It verified 86,401 snapshots, 5,807,626 expected interval counts and 864,000 s device exposure. This follow-up uses sparse telemetry (241 batches) because it measures spectrum recovery, not export. Filesystem caching and external machine load were uncontrolled; do not infer a universal speedup or a cold-cache bound.

A full ten-day recovery is therefore a visible wait, not instantaneous. The existing progress/cancellation mechanism remains necessary.

## Visual QA and qualification boundary

scientific_qc_ten_day_v01.png is the inspected 1500×1080 offscreen Windows GUI capture, visibly marked SIMULATED. It shows hourly rate trends, advisory relative gain checks, device temperature, recorded seal elapsed time, exposure and source provenance. Legend contrast and overview density were adjusted after the earlier smoke capture.

Synthetic software tests do not qualify physical USB reliability, real tray notification delivery, actual shutdown behavior, multi-day hardware operation, absolute energy/efficiency, containment/geometry, temperature sensors, dead time, background stationarity or seal timing. Physical checks remain Michael's responsibility under AGENTS.md. Reported fit standard errors are conditional on their models and assumptions.
