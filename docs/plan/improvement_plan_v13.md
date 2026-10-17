# GammaKeeper improvement plan v13: plot interaction draft

Date: 2026-10-17. Supersedes v12 for plot interaction only. Branch: `feat/plot-interaction-v13`, based on `origin/main` at `86d0cb2`. This is a source change for the next release; no installed release or recording is changed.

## Evidence and display diagnosis

The local charcoal analysis notes (`charcoal_decay_20261013_v01/README.md`, update v02, and `handoff_prompt_v01.md`) report that the 78 keV feature's centroid stayed at 78.2–78.3 keV and its FWHM at 17.8–18.0 keV over the analyzed period. Their recorded display diagnosis is a **linear 99.5th-percentile color limit near 12 counts/s/channel**: the peak channel fell from about 21 to 12 counts/s/channel, changing the visible saturated core and shoulders. These are results of that earlier analysis, not a new fit or a claim about the remainder of the ongoing experiment. The current branch does not read or alter the run.

The plot previously recomputed its color maximum from the chosen time window. The new default uses all displayed positive intensities (100th percentile) and holds the largest upper color limit seen during the current viewing session for that run. It resets when a different run is opened. The colorbar says when the upper limit is held. Explicit percentile clipping remains available and is labelled as clipping; it can still alter apparent widths. Logarithmic colors still mask measured zero bins without adding counts. The lower log color bound can change when a newly displayed lower positive intensity appears; colors are therefore a visual aid, not a calibrated cross-session intensity comparison or a measurement of FWHM.

## Implemented source behavior

| Area | Behavior |
|---|---|
| Layout | Spectrogram and accumulated-spectrum axes use equal-width columns. A horizontal heatmap colorbar preserves that width. F11 shows the dashboard plots with status cards, setup and diagnostics hidden; Escape restores the previous view. |
| Color | Cividis and unclipped log intensity are the new defaults. The 100% color ceiling is held within one viewing session for a run. Existing saved display preferences are still respected. |
| Time | A two-handle UTC slider and editable start/end boxes select the viewed window. The adjacent spectrum accumulates **complete intervals only** within those bounds; straddling intervals are excluded, not split or interpolated. Time remains a linear UTC axis. |
| Energy | Two-handle energy sliders and editable keV boxes are linked across dashboard and Spectrum / ROI. Dragging on either spectrum selects/highlights a spectral region. The terminal channel remains excluded from ROI totals unless selected explicitly. |
| Scale/navigation | Visible buttons and clickable plot axis labels toggle linear/log energy, spectrum counts, count rate and dose rate; a separate button toggles heatmap intensity. Fit Y uses counts inside the selected energy region; Zoom to region also changes X; Reset X/Y returns to the full plot. Nonpositive values stay masked in log views. |
| Status | Elapsed wall-clock span since the first saved spectrum and time until the planned end are prominent cards. Elapsed includes gaps and stops at the last saved spectrum for stopped or stale runs; the remaining card is schedule time, not measured exposure. Saved-spectrum count moves into diagnostics. |

All plotting changes operate on derived views. Raw cumulative spectra, channel differences, original calibration, device exposure, quality flags, journal and exports retain their existing rules. A selected time window can take time to verify from source records; until verification completes, status remains visible.

## Verification and release gates

The GUI tests use synthetic spectra and temporary run directories. They check color-limit behavior as intensity falls, equal top-axis widths, drag selection, linked controls, visible range errors, full-screen restoration, fractional-UTC slider endpoints, and complete-interval accumulation with two deliberately straddling intervals. The final Windows suite passed (167 tests, project-local TEMP/TMP). Offscreen synthetic-data screenshots were reviewed at normal and full-screen sizes; plot titles and full-screen height were adjusted after that review. Hardware operation, behavior on the active experiment and physical peak-width analysis remain outside this software test.

No physical RadiaCode device is contacted. No run folder, installed release, scheduled task or shared Python environment is modified.
