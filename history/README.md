# Import provenance (2026-10-12)

The first nine commits are the RadiaLog releases v0.1.0–v0.5.1, imported by `import_history_v01.py` from the `radiacode_logger_v0*` folders, which remain unchanged in the workspace.

- **One commit per folder**, tagged `v0.1.0` … `v0.5.1`. Each tree mirrors the folder's selected files exactly; files absent from a version are absent from its commit. Commit dates are each folder's latest source mtime.
- **Selected:** all top-level source/docs/launcher files, `tests/` and `THIRD_PARTY_LICENSES/`, and the top-level files of `validation/` (JSON, text, PNG renders and logs referenced by the READMEs).
- **Not imported:** `runs/`, `dependencies/` (rebuilt by `tools/vendor_dependencies.py`), `logs/`, `cache/`, `__pycache__/`, v0.1's unused `.venv/`, all `validation/` subfolders (run data and temporary test output), `*.sqlite*`, and root `export_*.log`.
- **Verification:** after every commit, each blob's SHA-256 was compared with the folder file, and the file sets compared both ways. The v0.5.0 and v0.5.1 release manifests (59 and 64 per-file hashes) matched. Any mismatch would have stopped the import; none occurred.
- `import_manifest.json` lists every imported file's SHA-256 and size per version, plus what was skipped and why.
- `launchers_archive_20261012_MANIFEST.json` is a copy of the manifest written when the 21 legacy root launchers moved to `Radiacode Data\launchers_archive_20261012\`. It records each original's bytes (base64), its SHA-256, and the only edit (`%~dp0` → `%~dp0..\`).

The repository was created with repo-local settings only (`core.autocrlf=false`, `core.longpaths=true`, commit identity per plan D26) and `.git/info/attributes` set to `* -text` during the import.
