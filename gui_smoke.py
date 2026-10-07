"""Owned-widget integration test, synthetic data only; no desktop automation."""
import bootstrap
import datetime as dt
import json
import os
from pathlib import Path
import time
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6 import QtWidgets as Q, QtCore, QtGui
from gui import Window, ROOT
from storage import immutable_json

app = Q.QApplication([])
app.setQuitOnLastWindowClosed(False)
for name in ['segoeui.ttf', 'segoeuib.ttf']:
    font = Path(r'C:\Windows\Fonts') / name
    QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(font.read_bytes()))
app.setFont(QtGui.QFont('Segoe UI', 10))
window = Window()
window.demo.setChecked(True)
window.interval.setValue(1)
window.days.setValue(0)
window.folder.setText(str(ROOT / 'validation' / 'gui_smoke_runs'))
window.label.setText('GUI integration test — synthetic input')
window.start_new()
run = window.run
process = window.recorder
started = time.monotonic()
phase = 0
second_window = None
report = {'mode': 'SIMULATED', 'run': str(run)}

def poll():
    global phase, second_window
    try:
        elapsed = time.monotonic() - started
        if elapsed > 45:
            raise TimeoutError('GUI smoke test deadline exceeded')
        if phase == 0 and elapsed >= 5:
            window.close()
            assert process.poll() is None, 'Closing GUI stopped recorder'
            second_window = Window(run)
            second_window.show()
            report['recording_survived_gui_close'] = True
            phase = 1
        elif phase == 1 and elapsed >= 11:
            second_window.refresh()
            second_window.stop_recording()
            phase = 2
        elif phase == 2 and process.poll() is not None:
            second_window.refresh()
            assert process.returncode == 0
            assert second_window.last_status['state'] == 'stopped'
            assert second_window.last_status['snapshots'] >= 5
            report['stop_button_saved_cleanly'] = True
            second_window.export()
            phase = 3
        elif phase == 3 and second_window.export_process and second_window.export_process.poll() is not None:
            code = second_window.export_process.returncode
            assert code == 0
            second_window.refresh()
            assert (second_window.export_destination / 'EXPORT_COMPLETE.json').exists()
            report['export_button_completed'] = True
            report['snapshots'] = second_window.last_status['snapshots']
            second_window.charts.draw()
            second_window.grab().save(str(ROOT / 'validation' / 'gui_smoke_preview_v01.png'))
            immutable_json(ROOT / 'validation' / 'gui_smoke_report_v01.json', report)
            print(json.dumps(report, indent=2), flush=True)
            timer.stop()
            second_window.close()
            app.quit()
    except BaseException as error:
        print(f'GUI_SMOKE_FAILED: {type(error).__name__}: {error}', flush=True)
        if run and (run / 'status.json').exists():
            s = json.loads((run / 'status.json').read_text())
            (run / f"stop_{s['boot_id']}.request").write_text('test cleanup')
        timer.stop()
        app.exit(1)

timer = QtCore.QTimer()
timer.timeout.connect(poll)
timer.start(200)
raise SystemExit(app.exec())
