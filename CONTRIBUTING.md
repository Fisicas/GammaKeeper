# Contributing to GammaKeeper

Agents follow [AGENTS.md](AGENTS.md) as well; this file describes the workflow.

## Repository layout
- Application source is at the repository root (the layout inherited from RadiaLog v0.5.1). The planned split of `gui.py`/`gui_analysis.py` into `ui/` modules is the first refactor.
- `tests/`: unit and synthetic GUI tests; `tests/fixtures/`: byte-exact copies of workspace evidence.
- `tools/`: `vendor_dependencies.py`, `deploy_release.py`.
- `docs/plan/`: improvement plans and benchmarks. `history/`: provenance of the v0.1–v0.5.1 import.
- Tags `v0.1.0`…`v0.5.1` are the imported RadiaLog releases, each byte-identical to its `radiacode_logger_v0*` folder. Those folders stay in the workspace as the archive.

## Workflow
1. Branch from `main` per work item, preferably in a separate worktree: `git worktree add ..\gk-feat-x -b feat/x`.
2. Once per clone: rebuild `dependencies/` (`tools\vendor_dependencies.py`) and install the pre-push hook (`git config core.hooksPath tools/hooks`). The repository is private on GitHub Free, which offers no branch protection there, so the hook stands in for it: it refuses direct pushes to `main` and runs the suite before every other push.
3. Run the suite (`python -X utf8 -m unittest discover -s tests`); CI runs the same suite on `windows-latest` for every pull request.
4. Open a pull request into `main`; merge only with a green suite.

## Releases
- A release is an annotated tag (`v0.5.2`, …). Install it side by side with `python -X utf8 tools\deploy_release.py <tag> --activate`; this exports the tag to `Radiacode Data\GammaKeeper Releases\<tag>\`, vendors dependencies, runs the tests in a throwaway copy, and only then points `current.txt` at it.
- Installed releases are never replaced or deleted by the tool, so a recorder from an older release keeps running. Rolling back means activating an older tag.
- Run data never live inside a release folder.

## Data and naming
- Raw scientific data never enter git (`.gitignore`).
- Display names say GammaKeeper; internal identifiers shared with RadiaLog releases (mutex names, run-folder layout, manifest fields) keep their original values.
