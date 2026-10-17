"""Offscreen visual check with synthetic values only; no run or device access."""
import datetime as dt
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['GAMMAKEEPER_TEST_MODE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap
from PySide6 import QtCore, QtGui, QtWidgets as Q
from gui import Window


app = Q.QApplication([])
for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
    font = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / name
    if font.exists():
        QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(font.read_bytes()))
app.setFont(QtGui.QFont('Segoe UI', 10))
app.setStyle('Fusion')
window = Window(read_only=True)
window.timer.stop()
window.resize(1600, 900)
window.show()
base = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc).timestamp()
saved = dict(spectrum=dict(counts=[100] * 64, calibration_keV=[1., 8., 0.]),
             telemetry_bin=dict(coverage_fraction=1., dose_rate_uSv_h=.041,
                                device_count_rate_cps=8.))
rows = []
total = [0] * 64
for index in range(30):
    counts = [max(0, int(5 + 140 * __import__('math').exp(-((i - 24) / 5) ** 2) *
                             __import__('math').exp(-index / 30))) for i in range(64)]
    counts[-1] = 0
    total = [old + value for old, value in zip(total, counts)]
    interval = dict(counts_delta=counts, device_duration_s=10., wall_duration_s=10.,
                    count_rate_cps=sum(counts) / 10., quality=['normal'])
    rows.append((base + 10 * (index + 1), 0, interval, saved))
window.charts.set_source('synthetic')
window.charts.show_rows(rows, saved, 'demo')
window.charts.set_accumulated((base + 300, [0], dict(counts_delta=total),
                               dict(spectrum=saved['spectrum'], collection=dict(
                                   included_intervals=30, requested_start_utc=None,
                                   selection_rule='Complete recorded intervals only'))))
for index, text in enumerate(('8.00 cps', '0.041 µSv/h', '0.0021 µSv', '5 min', '9 d 23 h')):
    window.metrics[index].setText(text)
app.processEvents()
window.charts.draw()
window.grab().save(str(Path(__file__).with_suffix('.png')))
window.toggle_fullscreen()
app.processEvents()
window.charts.draw()
window.grab().save(str(Path(__file__).with_name('plot_interaction_v13_fullscreen.png')))
window.close()
