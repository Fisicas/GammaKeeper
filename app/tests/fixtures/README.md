# Test fixtures

Byte-exact copies of workspace evidence, so the suite runs from any checkout (including CI) without sibling folders. The originals stay in place, unchanged.

| Fixture | Original (relative to `Radiacode Data\`) | SHA-256 |
|---|---|---|
| `decoder_fixture_batch_2537.json` | `investigation_20261007_v01\decoder_fixture_batch_2537.json` | `4cf7d42a6198e81b3986f9a0ad49cc2523eb5950ddd86de3afbcf2fe5940f295` |
| `v01_connection_174ad44e105f4749a9121ede64272f90.json` | `radiacode_logger_v01\runs\20261006_233228_177627_RC-103-006489\connections\174ad44e105f4749a9121ede64272f90.json` | `03eb4506fdb921a89a3e7ee135e52878720fcb6de71969b9e934914cb1b35851` |

The v0.1 run has two connection records with identical `configuration` text. The test previously took whichever one `glob` returned first, so pinning one changes nothing.
