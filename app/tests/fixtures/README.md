# Test fixtures

Byte-exact copies of workspace evidence, so the suite runs from any checkout (including CI) without sibling folders. The originals stay in place, unchanged.

| Fixture | Original (relative to `Radiacode Data\`) | SHA-256 |
|---|---|---|
| `decoder_fixture_batch_2537.json` | `investigation_20261007_v01\decoder_fixture_batch_2537.json` | `4cf7d42a6198e81b3986f9a0ad49cc2523eb5950ddd86de3afbcf2fe5940f295` |
| `v01_connection_174ad44e105f4749a9121ede64272f90.json` | `radiacode_logger_v01\runs\20261006_233228_177627_RC-XXX-XXXXXX\connections\174ad44e105f4749a9121ede64272f90.json` | `eb1204956329a819df075cb5b790d0efad764ba09956ecda8c7219c506fedf7d` (original `03eb4506…b35851`) |

Privacy scrub (2026-10-12): the detector serial in the connection record was replaced by `RC-000-000000`; the test uses only its `configuration` text, which is unchanged. The original file is untouched in the workspace.

The v0.1 run has two connection records with identical `configuration` text. The test previously took whichever one `glob` returned first, so pinning one changes nothing.
