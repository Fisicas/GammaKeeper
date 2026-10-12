# Rules for agents working on GammaKeeper

GammaKeeper (formerly RadiaLog) records time-resolved gamma spectra from RadiaCode detectors over USB on Windows. Its scientific use is sealed-canister measurements of radon (Rn-222 progeny) in activated charcoal. The current plan is `docs/plan/` (highest version number).

## Never
- Touch run data: anything under a `runs/` folder, `GammaKeeper Runs\`, the `radiacode_logger_v0*` folders, or any `run.sqlite`, `spectra/` or `manifest.json` that belongs to a recording. Tests use temporary directories only.
- Send commands to a physical device, or start a recorder against one. Hardware-in-the-loop checks are run by Michael only.
- Create scheduled tasks, change the registry or other Windows settings, or install anything into the shared science environment (`C:\Users\nelso\.venvs\codex-science-v1`).
- Rename internal identifiers that old releases share: the mutex names `Local\RadiaLog_<serial>` and `…_supervisor`, run-folder layout, manifest/journal field names and schema values.
- Rewrite pushed history, force-push, commit directly to `main`, or bypass the pre-push hook (`--no-verify`, `GAMMAKEEPER_ALLOW_MAIN_PUSH`). GitHub Free gives this private repository no branch protection; the hook in `tools/hooks/` (installed with `git config core.hooksPath tools/hooks`) is the only guard.

## Always
- Work on one branch per work item (`feat/…`, `fix/…`, `docs/…`), in your own git worktree, so parallel agents never share a working copy.
- Keep commits small and about one thing. Files are stored byte-for-byte (`.gitattributes`: `* -text`); don't change line endings except in a commit that does only that.
- Run the full suite before proposing a merge:
  ```powershell
  & 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 tools\vendor_dependencies.py   # once per checkout
  & 'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe' -X utf8 -m unittest discover -s app	ests
  ```
- Use `GAMMAKEEPER_SCIENCE_PYTHON` / `GAMMAKEEPER_TEST_MODE` in new code and docs; the `RADIALOG_*` names remain only as fallbacks.
- State in every pull request: the files changed, the tests run and their result, and any assumptions or untested paths.
- Keep acquisition rules intact unless the work item is explicitly about them: raw records stay immutable, counts are integer channel differences, no smoothing/resampling/background subtraction in recorded data, verify before acquiring on resume.

## Windows notes
- Processes started inside a packaged app (such as the Claude desktop app) have new `%LOCALAPPDATA%` folders redirected into the package's private cache. Don't store anything there that the user's own launchers must find; releases go to `Radiacode Data\GammaKeeper Releases\` (`tools/deploy_release.py`).
- Use the science interpreter with `-X utf8`. The application lives in `app/` (modules, `ui/`, `tests/`, launcher); app-only dependencies live in `app/dependencies/`, rebuilt from `app/dependencies-lock-v01.txt`. Keep the repository root for documentation and project files.
