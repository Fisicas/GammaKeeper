# GammaKeeper (formerly RadiaLog) — improvement plan v09

Date: 2026-10-12. Supersedes v08; v01–v08 are preserved unchanged. "RadiaLog" means the existing releases v0.1–v0.5.1 and their data.

v09, like v08, is written as the **current specification**: decisions, verified state, requirements, work items, sequence and open items. The revision history is in §9. Where a work item's detailed design is unchanged, the item names the earlier section that holds it, so nothing is lost.

**Status:** Step 0 steps 1–9 are **done** (D36, D42): local repository, history import, tooling, and the push of `main` and tags `v0.1.0`…`v0.5.1` to the private `Fisicas/GammaKeeper`. First CI run **passed** (104/104 tests on windows-latest). No run data, version folders, Windows settings or scheduled tasks were changed. Remaining: Step 10 (GUI split).

---

## 1. Purpose and scientific requirements

**Measurement (D33):** sealed-canister decay of radon (Rn-222) adsorbed on activated charcoal, recorded as time-resolved gamma spectra with a RadiaCode 103 (RC-103-006489, 1,024 channels).

| Nuclide | Half-life | Lines used (keV) | Role |
|---|---|---|---|
| Rn-222 | 3.8235 d | none usable | quantity of interest; seen only through its progeny |
| Pb-214 | ≈27 min | 242.0, 295.2, 351.9 | progeny, equilibrium with Rn ≈3 h after sealing |
| Bi-214 | 19.9 min | 609.3, 1120.3, 1764.5 | progeny |
| Pb-212 (thoron chain) | 10.64 h | 238.6 | interference; **not resolvable from Pb-214 242 keV** with CsI(Tl); separated by half-life |
| Tl-208 (thoron chain) | 3.05 min (fed by Pb-212) | 583.2, 2614.5 | thoron indicator |
| K-40 | stable | 1460.8 | background; energy-scale reference |

**Measured background:** test 3 (v0.5.0, 1 h) averaged **4.15 cps** total, i.e. about 42 counts per 10 s spectrum spread over 1,024 channels. Room-air radon progeny produce the **same lines** in the background, and they vary over the day.

**Consequences for the software:**

| # | Requirement | Why |
|---|---|---|
| R1 | Counts and exposure are preserved through every gap (already true: flagged aggregates). | The useful time resolution for a 3.8-d decay is ~10 min–1 h. A gap of minutes costs almost nothing if aggregates are fitted as wider bins (R7). |
| R2 | The **seal time** (time zero) and other experimental events are recorded as timestamped, SNTP-referenced annotations. | TIME_RESOLVED.md requires time zero from experimental evidence. Progeny grow in for ≈3 h after sealing, so this window must be modeled or excluded. |
| R3 | A **sample sheet** per run: charcoal type and mass, exposure start/end and location, seal time, canister/geometry, room temperature/RH, linked background run. | Needed to turn counts into a radon result and to compare runs. |
| R4 | **Background runs** with the same geometry and radon-free charcoal (sealed > 30 d or baked), linkable to sample runs. | The background contains the analysis lines. |
| R5 | **Gain and temperature QC**: hourly peak positions of 609 keV and K-40 1461 keV against device temperature (already recorded in `RareData`). | CsI(Tl) gain drifts with temperature; fixed energy windows over days need checking. In a sealed canister, temperature also shifts Rn between the charcoal bed and headspace, which changes counting efficiency. |
| R6 | **Energy-window panel** with net rates and a running half-life readout for sealed runs; rigorous fitting offline. | Fast sanity check during a run; the fit stays outside the acquisition app. |
| R7 | **Export option that keeps gap aggregates** as variable-width bins, for bin-integrated model fitting. | The time-resolved export currently drops recovered aggregates as segment breaks. |
| R8 | **Run data live in `C:\Users\nelso\Desktop\Radiacode Data\GammaKeeper Runs\` (D34)**, never inside a release folder. | v0.5.1 defaults new runs to `<release>\runs` (`gui.py` lines 255, 364, 541). |
| R9 | **Physical qualification**: a sealed-charcoal run whose fitted half-life must return 3.82 d within its uncertainty. | Checks timing, gap handling, gain stability and seal integrity (a leak shortens the apparent half-life) end to end. |

**Radon preset (D35, D39):** default duration **3 days** and spectrum interval **10 s**, both editable. 3 d covers 0.78 half-lives, which suits an activity measurement with decay correction. For the R9 half-life qualification, set the run to 10–14 d (2.6–3.7 half-lives). 10 s spectra are summed into wider bins at analysis time (time-resolved export); 3 d at 10 s is 25,920 spectra (≈ 0.25 GB of spectrum files at the measured 9.5 KiB each, plus ≈ 0.7 GB of journal at 1 s telemetry).

---

## 2. Decisions

| # | Decision |
|---|---|
| D1 | Plan v01 assessment accepted; tiers stand except where changed. |
| D2 | Scheduled-task changes handled carefully: per run, opt-in, inspectable, removed automatically, never system-level. |
| D3 | Program icon and splash/startup page; processes identifiable in Task Manager. |
| D4 | Auto-resume at logon only while the job is still in progress; never restart a run whose planned end has passed. |
| D5 | Gap data are recovered from the device's cumulative counters after reconnect, provided the device was not reset. |
| D6 | Verification before resume, if it takes only seconds. |
| D7 | Windows pop-up on run completion, showing the GammaKeeper icon. |
| D8 | Never acquire before pre-resume verification finishes. |
| D9 | Auto-resume checkbox defaults to off. |
| D10 | Frozen/packaged executables: later. |
| D11 | Closing readout for expired interrupted runs: yes. |
| D12 | Icon mock-ups for approval before any icon enters the app. |
| D13 | Test 2 is not resumed; qualification starts fresh. |
| D14 | Runs tab with Resume and End; one job per device at any time; different devices may record simultaneously; starting a run never leaves forgotten jobs. |
| D15 | "Test run" flag, default duration 1 h (editable). |
| D16 | Daily reminder while a run is active with no window open. |
| D17 | Run registry at `%LOCALAPPDATA%\GammaKeeper\`. **Location superseded by D40.** |
| D18 | Test 2 and the v0.2 soak are marked Ended from the Runs tab (raw data untouched). |
| D19 | No second RadiaCode: multi-device stays "backend-capable, not qualified". |
| D20 | Private git repository, one launcher, split the two large window files, to let agents work in parallel. |
| D21 | Repository at `C:\Users\nelso\Desktop\Radiacode Data\GammaKeeper`. |
| D22 | Vendored dependencies rebuilt by script from the lock file, not committed. |
| D23 | v0.1–v0.5.1 imported as tagged history. |
| D24 | GitHub owner Fisicas, private, created empty (name: see D30). |
| D25 | Old launchers moved into a subfolder; folders not marked read-only. |
| D26 | Commit identity Michael R. Nelson <18431493+Fisicas@users.noreply.github.com>, set in the repository only. |
| D27 | License holder "<project> contributors" (resolved by D30). |
| D28, D31 | Step 0 documented only until separately approved (superseded by D36). |
| D29, D32 | Michael updated Git for Windows; verified 2.55.0.windows.5 with configuration intact. |
| D30 | Public name GammaKeeper; repository `Fisicas/GammaKeeper`; license holder "GammaKeeper contributors". |
| **D33** | **Experiment: sealed-canister decay.** |
| **D34** | **Run data root: `Radiacode Data\GammaKeeper Runs\`.** |
| **D35** | **Radon preset default duration: 3 days.** |
| **D36** | **Step 0 approved and executed; fixes applied as found** (Michael, 2026-10-12). |
| **D37** | **GitHub repository created by Michael:** `Fisicas/GammaKeeper`, Private, empty; created 2026-10-12 14:38 UTC; description "Reliable time-resolved gamma spectra logger for Radiacode". |
| **D38** | **Releases install to `Radiacode Data\GammaKeeper Releases\`**, not `%LOCALAPPDATA%` (applied under D36; reason §3.2 E5). Overridable with `--releases` or `GAMMAKEEPER_RELEASES`. |
| **D39** | **Radon preset spectrum interval: 10 s.** |
| **D40** | **Run registry next to the data it indexes: `Radiacode Data\GammaKeeper Runs\_registry\registry.sqlite`** (supersedes D17's location; avoids the redirection in §3.2 E5). |
| **D41** | **GitHub Free, repository private:** no server-side branch protection. Replaced by AGENTS.md rules plus a repo-local pre-push hook (`tools/hooks/pre-push`, `core.hooksPath`) that refuses direct pushes to `main` and runs the suite before other pushes. |
| **D42** | **Push approved;** `main` and tags pushed 2026-10-12. |

---

## 3. Verified state after Step 0 (2026-10-12)

### 3.1 What was done

| Step | Result |
|---|---|
| Preflight | No RadiaLog recorder, supervisor or GUI process running (only MCP Python servers). Test 3 had completed 2026-10-11 21:34 UTC. |
| 1. Launchers | 21 launchers moved to `launchers_archive_20261012\`; only `%~dp0` → `%~dp0..\` changed; every target path checked; originals' bytes and hashes in `MANIFEST.json`. `Open_RadiaLog.cmd` stays at the root (still starts v0.5.1). |
| 2. Repository | `git init -b main`; repo-local `user.name`/`user.email` (D26), `core.autocrlf=false`, `core.longpaths=true`. Nothing global changed. |
| 3–4. History import | 9 commits, annotated tags `v0.1.0`…`v0.5.1`, dates = each folder's latest source mtime (monotonic). **Every blob's SHA-256 matched its folder file**, file sets matched both ways, and the v0.5.0/v0.5.1 release manifests (59/64 hashes) matched. Repository size 3.5 MiB. |
| 5. Documents | `AGENTS.md` (+ `CLAUDE.md` importing it), `CONTRIBUTING.md`, `history/` (import script, per-file import manifest, launcher-archive manifest), `docs/plan/` (this folder). |
| 6. Dependencies | `tools/vendor_dependencies.py` + `dependencies-lock-v01.sha256` (reference: v0.5.1 `dependencies/`, 242 files). Rebuild from PyPI: **all package files identical**; 28 differences limited to pip's RECORD/REQUESTED metadata. The shared venv was not modified. |
| 7. CI | `.github/workflows/tests.yml` (windows-latest, Python 3.12, `requirements-ci.txt` pinned to the shared environment's versions, offscreen Qt, `RADIALOG_SCIENCE_PYTHON` set to the runner's interpreter). **Not yet run** (needs the push). |
| 8. Releases | `tools/deploy_release.py`; tag v0.5.1 installed at `Radiacode Data\GammaKeeper Releases\v0.5.1\`: **71 files byte-identical** to `radiacode_logger_v051`. **Not activated** (R8). |
| Tests | 104/104 pass in the repository checkout (54 s). 104/104 pass on a fresh export of HEAD with no sibling workspace folders (the CI-equivalent check). |
| 9. GitHub | Remote `origin` = `https://github.com/Fisicas/GammaKeeper.git`; `main` (`9655aef`) and 9 tags pushed. First CI run (#38268562091, push to `main`): **passed**, 104/104 tests in 50 s, job 2 min 3 s (15:12:48–15:14:51 UTC); dependency rebuild matched with the same 28 metadata-only differences. Branch protection: not available (D41); pre-push hook instead. |

Post-import commits on `main`: `.gitattributes`/`.gitignore`; test-fixture fix; vendoring tool; CI workflow; deploy tool; release location (D38); contributor/agent rules; license holder → "GammaKeeper contributors"; provenance records; plan documents.

### 3.2 Problems found and fixed during Step 0

| # | Problem | Fix |
|---|---|---|
| E1 | `tests/test_reliability.py` read `investigation_20261007_v01\` and a v0.1 run's `connections\` from outside the app folder; it would fail in CI and in any deployed release. | Byte-exact copies in `tests/fixtures/` with provenance (commit `06f2b25`). |
| E2 | Release manifests list `tests\…` with backslashes. | Import normalizes path separators before comparing. |
| E3 | v0.1 contains an unused `.venv\` (0.5 MB, created from the science interpreter). | Not imported; recorded in `history/README.md`. |
| E4 | Plan v07 §3.10 imported only `validation/*.json` and `*.txt`, but the READMEs reference PNG renders in `validation/`. | Top-level `validation/` files of every type are imported (PNG renders ~4 MB in total); subfolders (run data, temp output) are not. |
| E5 | **Packaged-app redirection:** a process started inside the Claude desktop app (an MSIX package) that creates a new folder in `%LOCALAPPDATA%` has it redirected to `AppData\Local\Packages\Claude_…\LocalCache\Local\…`. The first v0.5.1 deployment landed there and was invisible to Explorer-started launchers. | Removed; releases now install to `Radiacode Data\GammaKeeper Releases\` (D38). See §3.3 for the registry. |

### 3.3 Problems found that remain open

| # | Problem | Plan |
|---|---|---|
| O1 | v0.5.1 defaults new runs to `<release>\runs`. Activated from `GammaKeeper Releases\`, it would put experimental data inside a release folder. | v0.5.2 adds the data root (R8, D34). `Open_GammaKeeper.cmd` and the first `--activate` come with v0.5.2, not before. Until then `Open_RadiaLog.cmd` remains the launcher. |
| O2 | `release_checks.py` line 20 and `gui.py` line 184 read a sibling `radiacode_logger_v05` folder. | `gui.py`: harmless (returns no preferences). Release checks: `deploy_release.py` gates on the test suite instead; per-release check scripts that compare against sibling folders are retired in v0.5.2. |
| O3 | ~~D17 run registry in `%LOCALAPPDATA%`~~ had the same redirection problem as E5. | **Resolved by D40:** `GammaKeeper Runs\_registry\registry.sqlite` (rebuildable from journals; never scientific data). |
| O4 | ~~Branch protection on a private repository~~ is unavailable on Michael's GitHub Free plan. Windows runners count double against the private-repo Actions minutes. | **Resolved by D41** (pre-push hook + AGENTS.md). Keep CI to pull requests and pushes to `main`; watch the monthly minutes. |

---

## 4. Resume-verification cost and the cadence question

Measured on test 2 (17,882 files, warm cache): pass 1 (read + SHA-256 + JSON + reconstruct) 0.49 ms/file, pass 2 (dose totals) 0.22 ms/file; reconstruct alone is 51% of pass 1. Cold-cache time after a reboot is not measured. Details: v07 §2.

| Run | Spectra | Current two-pass cost (warm, extrapolated) |
|---|---|---|
| 3 d at 10 s (radon preset if 10 s is kept) | 25,920 | ≈ 18 s |
| 3 d at 60 s | 4,320 | ≈ 3 s |
| 7 d at 10 s | 60,480 | ≈ 43 s |
| 14 d at 10 s (half-life qualification) | 120,960 | ≈ 86 s |

**Plan (cadence 10 s, D39):** merge the two passes and vectorize `reconstruct` with NumPy first (low risk), then benchmark. The 3-day preset (≈ 18 s today) should fall below 10 s from that alone; the 14-day qualification run (≈ 86 s today) is the case most likely to need more. Build the hash-chained checkpoint (v07 §3.5) **only if** the benchmark misses D6 ("seconds") for runs actually planned. D8 is unchanged: no acquisition before verification. Acceptance: a synthetic 3 d / 10 s run resumes in ≤ 10 s warm, and a 14 d / 10 s run is measured and reported; cold-cache time measured once; corrupting an early file, a tail file or (if built) the checkpoint blocks resume with *attention required*.

---

## 5. Work items

### 5.1 Science features (new in v08)

- **S1 Annotations and time zero (R2).** Append-only `annotations` table in the run journal (own writer path, never touching raw records): UTC (SNTP-referenced, with clock quality), local time, kind (`seal`, `canister_placed`, `canister_removed`, `background_start`, `note`, …), text, author. GUI: "Add note…" plus a one-click "Mark seal time" with an editable time for marks entered afterwards (the entry time is stored separately). Shown as markers on the spectrogram and time series; exported as `annotations.csv`; `time_resolved.py` offers "time zero = seal annotation".
- **S2 Sample sheet (R3).** Structured fields stored as versioned annotations (the manifest stays immutable): charcoal type and mass, exposure start/end, location, seal time, canister and geometry, room temperature/RH, linked background run ID. Editable during and after the run; every edit is a new version.
- **S3 Background linkage (R4).** Mark a run as background; link sample runs to a background run (by run ID). Analysis subtracts the background rate per energy window with its counting uncertainty.
- **S4 Radon preset (D35, D39).** "Radon (sealed charcoal)" in the start dialog: duration 3 d and interval 10 s, both editable; prompts for the sample sheet and the seal time.
- **S5 Data root (R8, D34, D40).** Default save folder `Radiacode Data\GammaKeeper Runs\`, overridable by `GAMMAKEEPER_DATA_ROOT`. The run registry lives in `<data root>\_registry\registry.sqlite`; its scan covers the data root and the legacy `radiacode_logger_v0*\runs` folders.
- **S6 Energy-window panel (R6).** Predefined windows: Pb-214 295 and 352; Bi-214 609, 1120, 1764; mixed 239/242 (labeled as mixed); Tl-208 583 and 2614; K-40 1461. Gross and net (background-linked) rates per hour with counting uncertainty; running exponential fit of Pb-214/Bi-214 net rates after seal + 3 h showing half-life ± uncertainty against 3.8235 d. Display only; recorded data unchanged.
- **S7 Gain/temperature QC (R5).** Hourly centroid of 609 and 1461 keV from summed spectra vs device temperature; alert if a centroid moves by more than a set fraction of the window width. Display and export only.
- **S8 Aggregate-inclusive export (R7).** `time_resolved.py --keep-aggregates`: recovered gap aggregates become variable-width bins with `aggregate=True`, their actual start/end and exposure; the README states that only bin-integrated models may use them.
- **S9 Offline analysis (`analysis/radon/` in the repository).** Reads time-resolved exports: Bateman chain Rn-222 → Pb-214 → Bi-214 from the seal time with fitted initial progeny, Poisson likelihood, bin-integrated predictions, background subtraction, decay-corrected rate at seal time. Absolute activity needs an efficiency calibration in the canister geometry; the Ra-226 source in the workspace has the same lines and checks the energy scale before each experiment, but a point source cannot give efficiency for a distributed charcoal bed.

### 5.2 Reliability, operation and identity (designs unchanged unless noted)

| Item | Design reference | Change in v08 |
|---|---|---|
| Shutdown awareness (`ShutdownBlockReasonCreate`, `host_shutdown`, `interrupted_by_shutdown`) | v01 item 1 | **Moved to v0.5.2**: the failure that ended test 2 |
| Liveness by boot ID + process creation time; "INTERRUPTED — resume available until …" | v01 item 2 | — |
| `saved_status` missing `sqlite3` import + locked-DB regression test | v01 item 3 | — |
| Per-run logs in `<run>/logs/` | v01 item 5 | — |
| Verify-first resume | §4 above; v07 §3.5 | Checkpoint only if needed |
| Reconnect continuity test (device elapsed vs UTC gap) | v07 §3.4 | — |
| Closing readout for expired interrupted runs | v07 §3.8 | — |
| Opt-in logon auto-resume task | v07 §3.3 | Task folder `\GammaKeeper\` unchanged |
| Runs tab, one job per device, claimed-serial check, End run, Test-run flag | v07 §3.7 | Registry location D40 |
| Notifications (Phase A tray toast; Phase B AUMID registration) | v07 §3.6 | — |
| Icon mock-ups, AppUserModelID, splash, startup-progress panel, Processes panel, `--role` arguments | v07 §3.1–3.2 | Processes panel deferred |
| Health strip, grouped left panel, Stop confirmation, non-resetting Summary/Events | v01 items 11–13 | — |
| Incremental whole-run overview (profile first) and follow-live throttling | v01 items 8–10 | — |
| Spectrogram interaction, read-only second viewer | v01 items 14, 16 | — |
| README operating notes (Windows Update active hours, USB selective suspend, lid/power button, journal growth 1.6 GB/week at 1 s telemetry) | v01 item 7 | — |

---

## 6. Repository and releases

- **Layout now:** application at the repository root as in v0.5.1; `tests/` (+ `fixtures/`), `tools/`, `history/`, `docs/plan/`, `.github/workflows/`.
- **First refactor (Step 10), changed from v07:** split **only** `gui.py` and `gui_analysis.py` into `ui/` modules (v07 §3.9 table). Backend modules stay at the root: helpers are started as scripts by path and the files are moderate in size, so moving them adds risk without helping parallel work. `core/status.py` and `core/registry.py` arrive as new modules with their features. Line-ending normalization (LF for `.py`/`.md`, CRLF for `.cmd`) is its own commit. `ruff` from a separate tool environment, reformatting only in that pull request.
- **Parallel agents:** one branch and one git worktree per work item (AGENTS.md). Hotspots after the GUI split: `recorder.py`, `storage.py`; sequence work items that touch them.
- **Releases:** annotated tag → `tools/deploy_release.py <tag> --activate` → `Radiacode Data\GammaKeeper Releases\<tag>\` + `current.txt`. Releases are never replaced; rollback = activate an older tag.
- **Launcher:** `Open_GammaKeeper.cmd` at the workspace root, starting `GammaKeeper Releases\<current.txt>`, is created with v0.5.2 (first release with S5). `Open_RadiaLog.cmd` then moves to the launcher archive with the same prefix edit and a manifest entry.
- **GitHub (Step 9, done):** private `Fisicas/GammaKeeper`, `main` + tags pushed. No branch protection on GitHub Free (D41): every clone installs the pre-push hook (`git config core.hooksPath tools/hooks`), and all changes reach `main` by pull request. Optional: `gh repo edit --description` to the planned "…for RadiaCode detectors".

---

## 7. Sequence

| Release | Contents |
|---|---|
| **Step 0 (done)** | §3.1, including the push. Remaining: Step 10 GUI split (first code pull request). |
| **v0.5.2 — ready for experiments** | S5 data root + `Open_GammaKeeper.cmd`; S1 annotations/seal time; S2 sample sheet; S4 radon preset; shutdown awareness; liveness; `saved_status` fix; per-run logs; Stop confirmation; non-resetting Summary/Events; registry + read-only Runs tab + claimed-serial check; display names → GammaKeeper; README operating notes. |
| **v0.6** | S3 background linkage; S6 energy-window panel; S7 gain/temperature QC; S8 aggregate export; merged/vectorized resume verification (+ checkpoint only if needed); continuity test; Runs tab actions, end-previous-and-start-new, banner, Test-run flag; closing readout; notifications Phase A; health strip; grouped panel; incremental overview; splash/startup panel; approved icon + AUMID. |
| **v0.6.x** — after explicit approval of the exact Windows changes | Opt-in logon auto-resume tasks; toast identity registration if needed. |
| **Later** | Frozen executables; Processes panel; side-by-side multi-device view; two-device qualification; spectrogram interaction. |
| **Parallel, any time** | Icon mock-ups (D12); S9 offline analysis package (works on existing exports). |

**Qualification** (on the first release with shutdown awareness, i.e. v0.5.2):
1. Engineering: 24–48 h soak; one deliberate restart (resume; with auto-resume once v0.6.x exists); one restart after the planned end (no resume; closing readout offered once v0.6 exists); a spectrum reset during a gap on a disposable run (new epoch); a second start on the claimed device (refused, naming the claiming run); test 2 and the v0.2 soak marked Ended with raw hashes unchanged.
2. Physical (R9): Ra-226 source line check; then a sealed-charcoal run of 10–14 d with seal-time annotation and a linked background run; fitted half-life consistent with 3.8235 d; peak-position drift within limits across the temperature range seen.

---

## 8. Open items for Michael

1. Merge the pull request that adds this plan and the pre-push hook once CI is green (or ask the agent to merge it).
2. Before any public release (unchanged): trademark search for "GammaKeeper" and a check of the RadiaCode community for similar tools.

---

## 9. Changelog

- **v09:** Michael's answers: radon preset interval 10 s (D39), registry beside the data (D40), GitHub Free private repository with a pre-push hook instead of branch protection (D41), push approved and done (D42). Updated §3 (Step 9 and first CI run), the resume-cost plan for 10 s, S4/S5, §6–§8.
- **v08:** consolidated specification. Added the scientific requirements (§1), D33–D38, the Step 0 results and the problems found (§3), the science work items S1–S9, the cadence-dependent resume table, and a revised sequence (shutdown awareness and the science basics in v0.5.2; checkpoint conditional; GUI-only refactor; launcher with v0.5.2). Corrected stale v07 text: the copied plan version (v05 → all), the Git version notes in §3.13, "18 launchers stay put", "or another path you choose", the "GammaKog" typo, and the decision-table order.
- **v07:** public name GammaKeeper (D30); Step 0 still documented only (D31); Git 2.55.0.windows.5 verified (D32).
- **v06:** D26–D29; GitHub name and prior-art search (RadiaLog name conflict); Git update instructions.
- **v05:** D21–D25; verified Git/GitHub tooling; Step 0 runbook.
- **v04:** D15–D20; private repository and code organization; repository work moved first.
- **v03:** D8–D14; Runs tab and one job per device; closing readout.
- **v02:** D1–D7; icon/splash/notifications; auto-resume rules; measured resume cost.
- **v01:** read-only review of v0.5.1: findings F1–F12 and the tiered plan.
