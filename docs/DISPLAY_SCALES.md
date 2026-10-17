# Linear and logarithmic views — GammaKeeper

Introduced in RadiaLog 0.5.1.

In **Spectrum / ROI**, choose **Energy axis** and **Counts axis** independently. All four combinations are available: linear/linear, log/linear, linear/log and log/log. The controls apply to the latest interval, selected interval/group, accumulated recording and accumulated UTC collection range.

The dashboard's **Energy axis** and **Spectrum counts** controls are linked to the corresponding controls in **Spectrum / ROI**. Independent **Count-rate axis** and **Dose-rate axis** controls act on their respective time-series ordinate axes and default to linear. **Spectrogram intensity** changes only the heatmap color scale. Energy, spectrum counts and spectrogram intensity default to logarithmic display. UTC time remains linear. Buttons beside the plots and clickable axis labels provide direct scale switching; Plot options retains the selectors. The GUI remembers these choices when closed and reopened.

These are display coordinates, not transformations of the scientific records. Original calibrated energies remain in keV, channel counts remain integers, count rates remain cps, and ROI boundaries/sums and numerical exports are unchanged. Log spectra use small markers so isolated nonzero channels remain visible rather than disappearing between masked zero channels.

Zero/nonpositive values cannot be represented on logarithmic axes. They are hidden without adding a pseudocount, and a plot note explains this. Returning to Linear restores them. Log energy omits nonpositive channel energies; log-energy heatmaps omit bins whose left energy edge is nonpositive. Linear heatmap colors include measured zeros; missing intervals stay masked. Positive intensities below 0.001 cps/channel retain a valid logarithmic color range. The next-release default is the full displayed intensity range with Cividis colors; its upper limit is held at the highest value seen during the current viewing session for that run. The colorbar labels the held limit. Optional color-percentile clipping is labelled because saturation can change the apparent width of a fading band. The lower log limit can still change, and the upper limit resets on reopening. Plot colors do not establish a physical peak width.

An all-zero log spectrum shows an explanation and a positive display domain, without inventing counts. The navigation toolbar resets its Home history when scales change so it cannot restore incompatible linear limits onto a log axis. A single saved time interval uses its actual start/end as the time-axis extent.

Scale toggles redraw already displayed data. They do not connect to the device, restart acquisition or trigger another accumulated-spectrum reconstruction. Close the old GUI and reopen it (see **Start** in the README); closing the GUI does not stop its independent recorder. The initial new-release launch can read the previous release's GUI preferences, preserving the last selected recording or setup.

Validation evidence is in `app/validation/release_manifest_v*.json` and the workspace folder `display_scales_20261011_v01`. Original v0.5.0 source and all raw recordings remain preserved. Saved-run renders are owned Qt-widget snapshots, not automated control of an existing desktop window.
