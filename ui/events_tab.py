"""Events / logs tab: recorder and supervisor events with a text filter."""
import time

from PySide6 import QtCore, QtGui, QtWidgets as Q
from export_run import readonly


class EventsMixin:
    def _build_events_tab(self):
        event_page=Q.QWidget();event_layout=Q.QVBoxLayout(event_page)
        self.event_search=Q.QLineEdit();self.event_search.setPlaceholderText('Filter events, warnings or errors')
        self.event_search.textChanged.connect(lambda:self.refresh_events(True))
        event_layout.addWidget(self.event_search)
        self.event_table=Q.QTableWidget(0,4);self.event_table.setHorizontalHeaderLabels(['UTC','Source','Event','Details'])
        self.event_table.setEditTriggers(Q.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.horizontalHeader().setSectionResizeMode(3,Q.QHeaderView.ResizeMode.Stretch)
        event_layout.addWidget(self.event_table)
        logs=Q.QPushButton('Open recorder diagnostics');logs.clicked.connect(self.open_diagnostics);event_layout.addWidget(logs)
        self.tabs.addTab(event_page,'Events / logs')

    def refresh_events(self,force=False):
        if not self.run or (not force and time.monotonic()-self._last_events<5):return
        self._last_events=time.monotonic();events=[]
        for source,name in (('recorder','run.sqlite'),('supervisor','supervisor.sqlite')):
            if not (self.run/name).exists():continue
            db=readonly(self.run/name)
            try:
                events.extend((utc,source,kind,payload) for utc,kind,payload in db.execute('SELECT utc,kind,payload FROM events ORDER BY id DESC LIMIT 250'))
            finally:db.close()
        query=self.event_search.text().casefold()
        events=sorted(events,reverse=True)
        events=[row for row in events if query in ' '.join(row).casefold()][:300]
        self.event_table.setRowCount(len(events))
        for i,row in enumerate(events):
            for j,value in enumerate(row):
                item=Q.QTableWidgetItem(value if j!=3 else value[:300]);item.setToolTip(value)
                self.event_table.setItem(i,j,item)

    def open_diagnostics(self):
        path=(self.last_status.get('supervisor') or {}).get('child_log') or self.last_launch_log
        if path:QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
