# Third-party dependencies — GammaKeeper

The `dependencies` folder contains application-local `radiacode` 0.4.0 and its installed dependencies. Their original license/metadata files are retained under `dependencies/*dist-info`. The shared environment supplies PySide6 6.11.2, NumPy 2.5.3 and Matplotlib 3.11.2; it was not modified. The local installed-version list is in `dependencies-lock-v01.txt`.

- radiacode: https://github.com/cdump/radiacode — MIT; protocol client/decoders.
- pyusb: https://github.com/pyusb/pyusb — BSD; installed dependency, unused by Phyton transport.
- libusb-package: https://github.com/pyusb/libusb-package — BSD packaging; bundled libusb has its separate LGPL license. Used for diagnostic enumeration only.
- Bleak/WinRT: https://github.com/hbldh/bleak — installed upstream dependencies; no BLE transport is used by this release.
- PySide6/Qt: https://doc.qt.io/qtforpython-6/ — LGPL/GPL/commercial options; dynamically loaded from the shared environment.
- NumPy: https://numpy.org/ — BSD.
- Matplotlib: https://matplotlib.org/ — Matplotlib license, based on PSF.

The new recorder, GUI, transport, storage and reconstruction code is under `LICENSE`. No manufacturer binaries or USB drivers are redistributed. This local source-based application is not a self-contained installer; a redistributed executable would need its own complete dependency/license packaging review.
