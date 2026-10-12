# Time input and time-resolved spectra — GammaKeeper

Introduced in RadiaLog 0.5.0.

## Recording schedule

Enter `60 minutes`, `1 hour`, `1h 30min`, `5 days`, `1 week`, or `HH:MM:SS` in the interval and duration fields. Fractional values are accepted. A number without units uses the adjacent default-unit selector. An explicit unit in the text takes precedence. `0` or `continuous` means unlimited duration; it is invalid for a count interval. Unknown units, negative/nonfinite values and ambiguous `MM:SS` are rejected. The tooltip shows the interpreted seconds. Saved manifests retain seconds; reopening displays the same schedule in readable units. Resume retains the original deadline.

The spectrum/count interval is the **acquisition cadence**, from 1 s to 24 h. It is distinct from the averaging duration used after collection. The dose estimator is still polled on its existing cadence. Device spectrum elapsed time is quantized to whole seconds: one-second acquisition has not been qualified as an independent one-second measurement. Use 2–10 s when planning faster kinetics, validate the actual exposure/timing, and inspect exclusions. This release does not change the reconstruction rules for zero/nonincreasing duration or modify instrument calibration.

Choose a sufficiently short acquisition interval before the experiment. A ten-second cumulative read does not contain photon arrival times: genuine one-second spectra cannot be recovered afterward. Averaging more intervals reduces count noise at the expense of temporal resolution. No isotope-specific temporal response or detector calibration is established by software tests.

## Time-resolved export tab

Open a saved recording, choose **Time-resolved export**, select the mode and optionally a UTC range or epoch, then click **Export time-resolved spectra…** and choose an output parent. Each export creates a unique folder. Its job runs separately, below normal priority, and reports progress and completion in the tab. **Open export log** shows errors. Use results only when `COMPLETE.json` exists.

Modes:

* **Original recorded intervals**: one spectrum per complete usable saved difference.
* **Nonoverlapping bins by duration**: enter, for example, `1 minute` for six intervals from a ten-second run. The requested width must be a whole multiple of the saved cadence. The final group is retained with its shorter measured exposure. This is a source-aligned target duration, not a claim of exactly uniform wall-time edges.
* **Choose number of spectra**: partition one contiguous selection into the chosen number of groups, preserving all intervals. Group sizes may differ by one interval. More output spectra than input intervals, or crossing a discontinuity/calibration epoch in this mode, is rejected.
* **Moving average**: enter an averaging window and step, both whole multiples of the saved cadence. Only full windows are produced; unused trailing intervals are reported. Steps shorter than windows produce **correlated spectra** because the same photons appear in several rows. Such outputs omit RCSPG and carry a warning; they must not be fitted as independent observations with diagonal errors.

UTC selections contain **complete intervals only**, with straddling edges listed as exclusions. The default time zero is the first included interval start. Set an explicit experiment time zero to align records to source removal, an injection, or another event; negative relative times are retained. Time zero must be supplied from experimental evidence if it is meant to represent a physical trigger.

Resets, calibration/channel changes, recovered reconnect aggregates and suspect timing break contiguous segments. Their original integer differences and reasons, where recoverable, remain in `excluded_intervals.jsonl`. They are not distributed into invented time bins. Different calibration epochs are written separately. This exporter intentionally has stricter timing selection than the accumulated-count plot, which can include a flagged recovered gap aggregate.

## Output and scientific conventions

For each compatible contiguous segment:

* `segment_*_bins.jsonl`: each bin's actual UTC start/end, relative midpoint/start/end in seconds, source sequence range, number of source intervals, summed device exposure, wall duration, quality flags, clock quality/error estimate and read half-width. The clock estimate is not a certified total timing uncertainty; device/filter latency remains uncalibrated.
* `segment_*.npz`: full original-channel integer `counts`, `rate_cps`, counting-only `poisson_sigma_cps`, `energy_keV`, `channel`, calibration polynomial, actual timing/exposure and source sequence endpoints. Load with `numpy.load(path, allow_pickle=False)`.
* `segment_*_rate_cps.csv`: time in **seconds** as rows, calibrated energy as columns, and measured rates in **counts/s/channel**. The energy column headers are explicitly **eV**, obtained by multiplying keV by 1000 without spectral resampling.
* Optional `segment_*_UFit_rate_cps.xlsx`: the same matrix orientation, with `Time (s)` in its first column and explicit eV spectral headers. Select **seconds** or header-based **auto** for input time units and **energy** or auto for the spectral axis in UFit. Its default microsecond setting must not be used for these records.
* Nonoverlapping `segment_*.rcspg`: the native Windows JSON schema, original channel counts and polynomial coefficients in **keV**. `timestamp` is the actual bin-end read time in epoch milliseconds; `collectTime` is measured device exposure in seconds. Millisecond rounding is confined to this native format. Full timestamp precision, exclusions and processing provenance are preserved in the sidecars.

An optional energy range restricts only the fitting CSV/XLSX matrices. The NPZ and RCSPG retain every original channel, including the terminal channel; its possible overflow interpretation is not resolved by the export. Channel order remains ascending on the verified calibration. Nonmonotonic calibration is rejected rather than silently reordered. There is no background subtraction, spectral baseline correction, smoothing, interpolation or energy resampling.

Rates are normalized by **summed measured device exposure**, not requested or nominal bin width. The NPZ counting-only error estimate is `sqrt(N)/exposure`; it is neither a confidence interval nor a complete error budget. In particular, zero counts do not imply zero uncertainty, and sparse count data require an appropriate Poisson likelihood or other justified treatment.

`COMPLETE.json` records source manifest, source horizon and every raw snapshot SHA-256, selection recipe, output hashes, segment information, overlap and omissions. Acquisition may continue after the captured source horizon. The exporter only opens the original database read-only and verifies immutable raw spectra. Failed or interrupted exports have no completion marker and can be recreated in another folder. Large fine-cadence selections require memory and disk space; coarser bins and an energy selection can reduce fitting-file size. Raw recordings remain the authoritative data.

## Decay-associated spectra

These files are time-resolved measurement matrices, not fitted decay-associated spectra. A subsequent fit must specify the kinetic model, time zero, backgrounds, response and error treatment. A radon decay chain can contain both ingrowth and decay, so independent exponentials may not describe it adequately. Bin-integrated predictions are needed when averaging windows are appreciable relative to the kinetics; a midpoint matrix alone does not remove that bias. Overlapping windows require covariance-aware fitting.

The local UFit import interface was inspected, but UFit was not executed or copied. XLSX orientation and units were checked with an independent reader. The RCSPG writer was checked against an existing native file and roundtripped channel by channel; import of a newly generated file in the native Windows application remains unverified.

## Reproducible commands

From this release directory, using PowerShell:

```powershell
& 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 '.\supervisor.py' --run '.\runs\unique_new_run' --interval '10 seconds' --duration '1 hour' --label 'sample / conditions'
& 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 '.\time_resolved.py' 'C:\path\to\saved_run' --mode width --width '1 minute' --xlsx --energy-min-kev 200 --energy-max-kev 700
```

The second command writes a unique export directory within the source run; `--target` can specify a different new directory. `--mode number --number 100` chooses 100 spectra. `--mode sliding --width '1 minute' --step '10 seconds'` produces correlated moving averages. UTC CLI selection and time-zero values are epoch seconds (`--start-utc`, `--end-utc`, `--time-zero-utc`); the GUI provides date/time selectors.

Closing the GUI does not stop acquisition, but a Windows restart or shutdown does. This release does not install a Windows startup task. After a restart, open the saved run and explicitly resume it if the original deadline still permits recording. Its existing counts cannot reconstruct individual spectra during an outage.
