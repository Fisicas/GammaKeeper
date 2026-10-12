# Contributing to GammaKeeper

Agents follow [AGENTS.md](AGENTS.md) as well; this file describes the workflow.

## Repository layout
- The repository root holds only documentation and project files. The whole application lives in `app/`, laid out exactly as a RadiaLog release folder was: its modules import each other by name and start helpers as scripts by path, so they move together.
- Backend modules (recorder, supervisor, storage, device, analysis, exports) are at the top of `app/`.
- The window lives in `app/ui/`, one mixin per concern, composed in `app/ui/main_window.py`:

  | Module | Content |
  |---|---|
  | `main_window.py` | `Window` shell: header, layout, timers, preferences, `main()` |
  | `setup_panel.py` | new-run form; start/stop/resume/open; saved-settings restore |
  | `run_status.py` | 1 s refresh: state banner, metric cards, health, clock footer |
  | `analysis_panel.py` | `AnalysisUI`: builds the tabs from the tab mixins and wires shared signals |
  | `timeseries_tab.py`, `charts.py` | time series tab, previews, heatmap selection, display scales; `Charts` canvas |
  | `spectrum_tab.py` | Spectrum / ROI tab and `SpectrumCanvas` |
  | `summary_tab.py`, `events_tab.py` | run summary; events/logs |
  | `jobs.py` | export, completion audit, backup and time-resolved jobs |
  | `tasks.py`, `theme.py`, `paths.py` | background tasks; colors and stylesheet; app root |

  `gui.py` (the launch target) and `gui_analysis.py` re-export the old names for compatibility.
- `app/tests/`: unit and synthetic GUI tests; `app/tests/fixtures/`: byte-exact copies of workspace evidence. `app/validation/`: release evidence.
- `tools/`: `vendor_dependencies.py`, `deploy_release.py`.
- `docs/`: the manual (`manual.md`, including release notes), topic guides and images; `docs/plan/`: improvement plans and benchmarks. `history/`: provenance of the v0.1–v0.5.1 import.
- Tags `v0.1.0`…`v0.5.1` are the imported RadiaLog releases, each byte-identical to its `radiacode_logger_v0*` folder. Those folders stay in the workspace as the archive.

## Workflow
1. Branch from `main` per work item, preferably in a separate worktree: `git worktree add ..\gk-feat-x -b feat/x`.
2. Once per clone: name the Python that has PySide6, NumPy and Matplotlib (`git config gammakeeper.python "C:\path\to\python.exe"`; stored only in this clone, used by the installer, launcher and hook), rebuild `app/dependencies/` (`tools\vendor_dependencies.py`) and install the pre-push hook (`git config core.hooksPath tools/hooks`). The repository is private on GitHub Free, which offers no branch protection there, so the hook stands in for it: it refuses direct pushes to `main` and runs the suite before every other push.
3. Run the suite (`python -X utf8 -m unittest discover -s app\tests`); CI runs the same suite on `windows-latest` for every pull request.
4. Open a pull request into `main`; merge only with a green suite.

## Releases
- A release is an annotated tag (`v0.5.2`, …). Install it side by side with `python -X utf8 tools\deploy_release.py <tag> --activate`; this exports the tag to `Radiacode Data\GammaKeeper Releases\<tag>\`, vendors dependencies, runs the tests in a throwaway copy, and only then points `current.txt` at it. A branch such as `origin/main` installs as `<branch>-<commit>` for testing.
- Activation also writes the stable launcher `GammaKeeper.pyw` and `gammakeeper.ico` beside `current.txt`; the Desktop and Start Menu shortcuts (`tools\shortcuts.py`, normally via `tools\install_gammakeeper.cmd`) point at that launcher, so they never change between releases. Create shortcuts from Explorer, not from a packaged app such as the Claude desktop app, whose AppData writes may be redirected.
- Installed releases are never replaced or deleted by the tool, so a recorder from an older release keeps running. Rolling back means activating an older tag.
- Run data never live inside a release folder.

## Data and naming
- Raw scientific data never enter git (`.gitignore`).
- Display names say GammaKeeper; internal identifiers shared with RadiaLog releases (mutex names, run-folder layout, manifest fields) keep their original values.
