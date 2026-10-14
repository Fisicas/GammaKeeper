# GammaKeeper 0.7.3 - experimental release

Opening a large recording now restores its derived display checkpoint first, then checks the cached source endpoint and verifies new records in the background. The previous plots remain visible while updates render. Shared, disposable display caches survive side-by-side upgrades; a complete raw-data audit remains an explicit operation.

The live dashboard combines a spectrogram, accumulated spectrum and count/dose trends. Larger, color-matched readings and buttons improve readability. Rendering status stays beside Refresh plots without shifting the figures. Linear/log switching retains calibrated limits; countdowns use whole seconds. The GammaKeeper wordmark links to the project. Advanced pages scroll at smaller window sizes.

## Validation and limits

- 163 local unit and synthetic GUI tests passed, including corrupt-cache rejection, endpoint tampering, legacy-cache migration, appended-tail verification, count conservation and display geometry.
- Eight GUI tests also passed at 150% Qt scaling; synthetic layouts were inspected at 1280 x 800, 1920 x 1080 and 2560 x 1440.
- The owner tested the new UI against an independently running RadiaCode-103 charcoal recording. README screenshots are actual user captures with provenance.
- Acquisition format, immutable raw records, channel counts, timing conventions and detector exclusion locks are unchanged. No smoothing, background subtraction or resampling is introduced.
- Full multi-day qualification, native RCSPG import and UFit execution are not established by these checks. Shorter acquisition intervals require device qualification. Windows reboot recovery requires relaunch.

## Install or update

Follow the README quick start. The installer builds into a new release directory and tests it before activation. Existing recorder processes keep their original release. Installing or publishing this version does not migrate or restart an ongoing recording.

This is a pre-1.0 source release for Windows 10/11 with Python 3.12. Third-party notices remain in app/THIRD_PARTY.md. GitHub Windows CI is required before merging.
