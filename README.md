<p align="center">
  <a href="https://github.com/Fisicas/GammaKeeper"><img src="docs/images/splash.png" alt="GammaKeeper splash screen: the gamma 'keeper' icon, wordmark and startup steps over a radon-progeny gamma spectrum" width="760"></a>
</p>

<p align="center">
  <b>Unattended, verifiable time-resolved gamma spectroscopy with RadiaCode detectors on Windows.</b>
</p>

[GammaKeeper](https://github.com/Fisicas/GammaKeeper) (formerly RadiaLog) reads cumulative gamma spectra at a chosen interval for days at a time and retains the raw records: SHA-256 indexed spectra, a crash-safe journal, SNTP-referenced timing, and automatic reconnection with explicit gaps or recovered aggregates. It was built for sealed-canister measurements of radon (Rn-222) adsorbed on activated charcoal, where changes in the Pb-214 and Bi-214 photopeaks can be followed over several days. The faint spectrum behind the splash screen is that radon-progeny spectrum.

## What it does

- **Time-resolved spectra without a rolling buffer.** Each interval's 1,024-channel spectrum is the exact integer difference of the device's cumulative spectra, normalized by the measured device exposure. Nothing is overwritten, smoothed or resampled.
- **Automatic recovery.** A supervisor restarts the recorder, USB faults back off and reconnect, and counts recovered across a gap are kept as one flagged aggregate rather than spread over invented bins. Interrupted runs can be resumed until their planned end.
- **Verifiable records.** Raw records are immutable and SHA-256 indexed; resume verifies them before acquiring; completed runs get a background audit and export.
- **Honest timing.** UTC comes from validated SNTP measurements bound to the monotonic clock, with the uncertainty and reference age saved alongside the data.
- **Analysis-ready exports.** CSV/NPZ time bins, nonoverlapping or moving-average spectra, native RCSPG, and XLSX matrices for fitting tools, each with full provenance.
- **One job per device.** A per-serial lock keeps two recorders from sharing a detector, across old and new releases.

<p align="center">
  <a href="docs/images/screenshot-live-dashboard-v073-20261013.svg"><img src="docs/images/screenshot-live-dashboard-v073-20261013.svg" alt="GammaKeeper 0.7.3 live dashboard: charcoal spectrogram, accumulated spectrum, and count and dose rates with larger header readings" width="100%"></a>
</p>

GammaKeeper 0.7.3 viewing a real ongoing charcoal recording, captured October 13, 2026. The viewer observes the independently running recorder; its stop control is disabled. Only the Windows title bar is omitted from the image; plotted data and readings are unchanged. Click for full size. [Screenshot provenance](docs/images/README.md).

<details>
<summary>Earlier recording view with linear energy axes</summary>

<p align="center">
  <a href="docs/images/screenshot-live-dashboard-linear-20261013.png"><img src="docs/images/screenshot-live-dashboard-linear-20261013.png" alt="Earlier GammaKeeper 0.7.2 view of the same charcoal recording using linear energy axes" width="100%"></a>
</p>

Captured earlier with GammaKeeper 0.7.2. The readings differ because acquisition continued between captures. Display scales do not alter original channel counts. These ongoing-run screenshots do not establish completed multi-day or decay-model qualification.

</details>

## Quick start

You need Windows 10/11, a RadiaCode detector on USB (tested with a RadiaCode 103), and Python 3.12 with PySide6, NumPy and Matplotlib (versions in [`app/requirements-app.txt`](app/requirements-app.txt)).

**Install (recommended).** Double-click [`tools\install_gammakeeper.cmd`](tools/install_gammakeeper.cmd) in Explorer. It fetches the latest `main` (or the tag you pass), exports a clean copy into `GammaKeeper Releases\` next to the repository, rebuilds the device libraries, runs the full test suite, activates the release only if every test passes, and creates **GammaKeeper** shortcuts on the Desktop and in the Start Menu. Run it again to update: each install sits beside the previous ones, so a recording started by an older build keeps running, and rolling back is one command. The repository's working files are only read. [`tools\uninstall_gammakeeper.cmd`](tools/uninstall_gammakeeper.cmd) removes the shortcuts and leaves releases and data alone.

**Or run from the checkout.** `python -X utf8 tools\vendor_dependencies.py` once, then `app\Launch_GammaKeeper.cmd`.

**Record.** Close the manufacturer's app (the USB handle is exclusive), enter a label, confirm the serial number, interval and duration, and click **Start new recording**. New runs go to `Desktop\Radiacode Data\GammaKeeper Runs` unless you choose another folder (or set `GAMMAKEEPER_DATA_ROOT`). Closing the window does not stop the recording.

The [manual](docs/manual.md) covers operation, data formats, scientific conventions and limits in detail.

## Documentation

GammaKeeper 0.7.3 adds cached-first reopening, a clearer live dashboard, larger readings and controls,
and a stable render-status area. Earlier releases provide persistent monitoring, verified backups,
versioned sample/background metadata, scientific QC and seal-referenced exports.
See the [ten-day experiment guide](docs/TEN_DAY_EXPERIMENTS.md) for operation, provenance and qualification limits.

| Document | Contents |
|---|---|
| [Manual and release notes](docs/manual.md) | operation, data and scientific meaning, timing and reconnection, exports, validation, release history |
| [Time-resolved export](docs/TIME_RESOLVED.md) | schedule input, export modes, file formats, fitting caveats |
| [Display scales](docs/DISPLAY_SCALES.md) | linear/log axes and what they do and do not change |
| [Public release preparation](docs/PUBLIC_RELEASE.md) | publication scope, screenshot provenance and release checks |
| [Contributing](CONTRIBUTING.md) · [Agent rules](AGENTS.md) | repository layout, workflow, releases |
| [Development plan](docs/plan/) | current plan (highest version) and its evidence |

## Repository layout

| Path | Contents |
|---|---|
| `app/` | the application: recorder, supervisor, storage, analysis and export modules, the `ui/` window, `tests/`, launcher |
| `docs/` | manual, topic guides, images, development plan |
| `tools/` | installer and shortcuts, release deployment, dependency vendoring, identity artwork, privacy audit, git hooks |
| `history/` | provenance of the imported RadiaLog v0.1–v0.5.1 releases |

## Status

GammaKeeper is pre-1.0. The automated suite runs on every pull request (unit and synthetic GUI tests on Windows). A real multi-day charcoal experiment is ongoing. Completed long-run qualification, native RCSPG import and UFit execution remain unverified; see the manual and development plan. This release is for experimental use.

## Name, credits and license

Releases v0.1–v0.5.1 were published as **RadiaLog**; identifiers they share with GammaKeeper (the device lock, run-folder and manifest formats) are unchanged, so old runs open and old and new releases never record from the same detector at once.

GammaKeeper builds on the MIT-licensed [cdump/radiacode](https://github.com/cdump/radiacode) protocol library, and its recording approach was informed by [ckuethe/radiacode-tools](https://github.com/ckuethe/radiacode-tools). Third-party notices are in [`app/THIRD_PARTY.md`](app/THIRD_PARTY.md). It is independent community software, not affiliated with RadiaCode / Scan-Electronics.

Licensed under the [MIT License](LICENSE).
