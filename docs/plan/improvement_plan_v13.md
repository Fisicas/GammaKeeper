# GammaKeeper improvement plan v13

Date: 2026-10-13. Branch: `feat/live-run-dashboard`, based on `origin/main` at `31b75a8`. Planned application version: 0.7.0.

## Implemented in this change

- Make the live dashboard the default view for a selected run. Show spectrogram and accumulated recording spectrum above a single shared-time count-rate/dose-rate trend plot.
- Put spectrum-derived cps on the left and device dose-rate µSv/h on the right. Color each axis with its trace. Move all series controls outside the plot rectangle so the estimator legend cannot obscure data.
- Keep whole-run accumulated spectra moving in their own background worker using the current checksum, sequence, reconstruction, complete-interval and calibration-grid checks. Preserve the Spectrum / ROI tools for individual intervals and requested collection ranges.
- Keep run label, acquisition interval, planned duration/end, latest spectrum rate, dose rate, saved-spectrum count, data ages and supervisor restart count visible in a compact header.
- Collapse run setup and long diagnostics by default after a run is selected. Keep analysis pages under **More**, add a direct return to live plots, and support full-screen dashboard with F11 and Escape.
- Preserve raw data and acquisition code paths. Plot grouping is labeled independently from the recorded interval.

## Validation

Added synthetic GUI and plot checks for separate color-coded axes, external legend behavior, supplied original-channel accumulation, background loading of a run's accumulated spectrum, and collapsed setup/details. Run the full unittest suite and release integrity checks before proposing merge. Synthetic and software checks do not constitute physical device qualification.

## Release

Follow the existing pull-request, Windows CI, review and side-by-side release workflow. Do not restart or modify any active recorder or run data while deploying this display-only update.
