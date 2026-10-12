# Ten-day experiments and scientific QC (GammaKeeper 0.6.0)

This release adds monitoring and derived analysis to the existing recorder. Native acquisition remains cumulative integer spectra, exact channel differences, measured device-reported duration, immutable raw files, and verify-before-acquire recovery. No physical detector or specimen qualification is implied by a green file audit.

## Starting and monitoring

1. Choose **10-day qualification • 10 s** in the new-recording panel. Confirm the sample label, serial, save folder and schedule.
2. Leave **Monitor after this window closes** enabled for local alerts. Choose an optional daily-backup parent on a separate volume. A blank parent disables automatic backups.
3. Start recording, then use **Sample sheet** in **Experiment / scientific QC** to record actual conditions. Each save appends a version to annotations.sqlite. It never rewrites the acquisition manifest.
4. Record the actual seal event in **Notes**, using **Mark seal time now** or a manually entered local time. Sealing can precede acquisition. For retrospective times, provide an event-time uncertainty when known. The click/entry clock reference does not independently calibrate a historical event.
5. Link a measured background through its folder in the sample sheet. The link freezes the background manifest hash and identifies its source horizon in QC outputs. Document matching geometry on both sample sheets. Linking files does not demonstrate stable background, matching efficiencies, or radon-free material.
6. Use **Experiment / scientific QC** for hourly count-window trends, peak-position/width QC, device temperature, and an advisory apparent-decay result. The plots retain actual included boundaries and device exposure.
7. The **Recordings** tab scans the configured data root when explicitly refreshed. **Open selected in another viewer** opens a read-only window, leaving current acquisition independent.
8. Closing the main GUI leaves acquisition and the optional monitor running. **Stop recording** asks for confirmation. Resume retains the original deadline.

The monitor is an ordinary local process with a tray icon; it creates no scheduled tasks or registry settings. It warns on sustained disconnection/stale state, stopped acquisition, integrity trouble, low disk space, failed backups/QC, qualified advisory gain drift, completion, and an audit requiring review. Alerts are logged in monitor.sqlite and deduplicated until the condition clears. Notification delivery depends on Windows tray availability; the event records whether a notification was delivered. Local monitoring cannot notify while the computer is powered off.

The monitor performs hourly derived QC while a physical recording is active. Its jobs run below normal process priority and independently of the recorder. Daily backups use fresh dated destinations; completed destinations are not overwritten. It attempts a final backup and waits for the completion audit before exiting. A failed final job is reported for inspection. Choose backup locations with sufficient space; raw recording, exports and backups have separate space requirements.

## Completion is more than integrity

VALIDATION.json retains passed as the file/reconstruction verdict. Its assessment separately reports:
- schedule endpoint coverage, missing start/tail and explicit tolerance;
- reconstructed device exposure, normal and aggregate exposure, invalid interval wall time;
- native versus variable-width timing resolution;
- reception-time telemetry coverage;
- scientific QC not evaluated by the file audit.

A reset or missing scheduled tail requires review even when every preserved file has an intact hash. Eligible recovered aggregates can preserve counts while losing fine temporal resolution. Device exposure is not independently calibrated live time, and endpoint coverage is not proof that every photon was retained.

## Seal-time and annotation evidence

Correction order uses monotonically increasing note IDs, not the potentially adjustable host entry clock. Earlier notes remain intact. Clock metadata retains quality, reference age and engineering uncertainty; unverified anchors are not labeled SNTP. Same-boot holdover remains distinct from a fresh reference. Event-time uncertainty and clock uncertainty describe different things.

Exports freeze the annotation horizon, selected seal ID and evidence hash. Annotation and acquisition databases have independent snapshot times; they are not an atomic combined transaction.

New backups preserve a frozen annotations.sqlite and its CSV/JSON representation with hashes. The Notes tab can reopen the seal and sample versions. Writes to backup annotations are refused. Legacy backups containing only CSV remain readable and explicitly report legacy note verification as unavailable.

## Time-resolved export

By default, time zero is the current seal when available. Otherwise the exporter uses the first included interval and warns that this is not experimental sealing. The GUI also offers a required current seal, explicit acquisition zero, and custom UTC. The selected basis and note ID are saved.

**Keep recovered aggregates as variable-width observations** / --keep-aggregates includes eligible continuity-inferred aggregates as isolated observations. It never spreads counts into artificial regular intervals. Resets, calibration boundaries and suspect timing remain excluded with reasons and counts preserved. Ordinary export still preserves all reconstructable counts.

The base schema identifier remains radialog-time-resolved-v1; format_revision=2 identifies chunked outputs. segments lists every NPZ/CSV/RCSPG chunk and its source segment, part, actual bounds and provenance. Large source lists reside in source_snapshots.jsonl. Default chunks contain at most 256 output spectra. File hashes and COMPLETE.json mark a completed export; partial output without the marker is not complete.

source_intervals and unused_sliding_tail_intervals describe the original source segment and are repeated across its parts; do not sum them over parts. intervals_used and unique_source_counts describe the individual chunk. Overlapping chunks can share source observations.

NPZ retains all channels and integer counts, device exposure, bin starts/ends, midpoint-relative time, aggregate flags, calibration and counting-only sigma. Fitting CSV/XLSX uses ascending channel-center energy in eV; NPZ energy is keV. Energy selection affects fitting matrices, not full NPZ/RCSPG counts. No axis reordering or spectral interpolation occurs.

Overlapping windows reuse photons. Their errors are correlated and RCSPG is omitted; use nonoverlapping bins for ordinary independent-error fitting. Fit integrated predictions to actual interval bounds. Device exposure uniformly weighting UTC intervals is an assumption, not a calibrated dead-time map.

Example (after setting the configured interpreter):
```powershell
$py = git config --get gammakeeper.python
& $py -X utf8 app\time_resolved.py 'C:\path\to\recording' --target 'D:\new-export-folder' --mode width --width '1 h' --time-zero-basis seal --keep-aggregates
& $py -X utf8 app\scientific_qc.py 'C:\path\to\recording' --target 'D:\new-qc-folder' --ingrowth-exclusion-hours 3
```

Both commands require a fresh output folder and open the acquisition journal read-only.

## Scientific conventions and limits

Monitoring windows are explicit inclusive channel-center ranges and exclude the terminal channel, whose overflow semantics are unqualified. They are broad spectral regions, not automatically isolated isotope photopeak areas. The 239/242 and 609/583 regions are explicitly mixed. Window definitions are included in SCIENTIFIC_QC.json.

Gross rates are N/T with device exposure T. Independent sample/background counting-only net variance is N_s/T_s^2 + N_b/T_b^2. Reusing the same background introduces covariance between net bins; its shared variance is exported. Negative net rates remain visible. No subtraction changes recorded counts.

Peak QC fits bin-integrated Gaussian peak(s) plus a flat continuum through Poisson likelihood. The 609-region model includes a 583.2 keV component with shared shift/width. This constraint is an assumption and does not establish isotope separation. Fit ranges, initial guesses, bounds, deviance, status and conditional standard errors are retained. Sparse, boundary-limited, poorly shaped or unidentified fits are not forced into a centroid. The provisional alert threshold is 5 keV relative to the first qualified bin. No calibration correction is applied.

Temperature is the detector's reported status temperature. Mean/min/max, record count and maximum timestamp age are retained. Values with timestamp age above 300 s are flagged and omitted from the plot; a projected device timestamp can itself be uncertain. Detector temperature does not measure canister temperature or room RH.

Advisory apparent decay uses gross counts, bin-integrated exponential plus constant background, and a joint Poisson term for linked background counts. The default three-hour ingrowth exclusion is a project assumption requiring validation, not a universal equilibrium guarantee. Recovery/timing flags are excluded from this advisory fit; variable-width aggregates remain available in the fitting export. Fits need sufficient span/counts, identifiable curvature, acceptable model deviance and interior parameter estimates. Uncertainty is a conditional observed-curvature standard error, not a full confidence interval or systematic error budget.

The nuclear reference is explicitly ENSDF September 2023: Rn-222 half-life 3.8222(9) d, [NNDC evaluation](https://www.nndc.bnl.gov/ensnds/222/Rn/adopted.pdf). A reference-like apparent half-life does not establish containment, absolute counting efficiency, absence of interfering components, or unbiased background. Absolute Bq/Bq per mass requires geometry-specific efficiency and sample calibration. Early-time chain fitting, environmental background changes and dead-time/geometry systematics require offline investigation.

## Cache and build provenance

The live whole-run overview and hourly QC update from newly saved intervals. Cache policy includes source path, manifest, period, epoch and source-code identity. New raw records and the cached endpoint are hash checked. A previously verified prefix is reused; a cache is not a fresh audit of every old file. The completed audit and verify-before-resume recovery still read the raw source. Qualified mature telemetry bins are cached; provisional final bins are recomputed. Whole-run follow-live plotting is throttled to one minute, while health refresh remains one second. Explicit refresh bypasses the plot throttle; turning off follow-live preserves navigation.

Resume restores dose totals during the same verified raw pass. Stored channel differences and raw acquisition behavior are unchanged.

Each new manifest's versions.build contains source hashes, Git/deployment identity and dirty status. Recorder-start events identify each resumed build. Exports identify their analysis build separately. An installed deployment modified after packaging is marked dirty when source hashes differ. Environment/library versions remain explicit, including SciPy 1.18.1. The shared environment is not automatically installed or upgraded.

## Validation and capacity

Unit, synthetic GUI, count conservation, corruption, clock/backstep, backup, cache, known-peak and known-decay checks are included in app/tests. The reproducible capacity script is tools/benchmark_ten_day.py. It generates disposable synthetic 1,024-channel data with one-second telemetry; it never opens a detector. It reports preparation cost separately, verifies every exported channel sum, and measures recovery, fresh/warm views, native export and process peak working set.

```powershell
$py = git config --get gammakeeper.python
& $py -X utf8 tools\benchmark_ten_day.py --output app\validation\capacity-ten-day-new.json --screenshot app\validation\capacity-ten-day-new.png
```

Use new output names on every run. At least 8 GiB free is required. Timings represent fresh derived caches with a warm filesystem after fixture creation. OS cold-cache, physical USB faults, real notification delivery, actual shutdown behavior and multi-day hardware/sample stability are separate qualification work for Michael.
