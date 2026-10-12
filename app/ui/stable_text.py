"""Refresh read-only text without repeatedly resetting scroll or selection."""
from PySide6 import QtGui


def set_text(widget,text):
    if widget.toPlainText()==text:return
    old=widget.textCursor();position,anchor=old.position(),old.anchor()
    scroll=widget.verticalScrollBar().value()
    widget.setPlainText(text)
    cursor=QtGui.QTextCursor(widget.document())
    end=max(0,widget.document().characterCount()-1)
    cursor.setPosition(min(anchor,end));cursor.setPosition(min(position,end),QtGui.QTextCursor.MoveMode.KeepAnchor)
    widget.setTextCursor(cursor);widget.verticalScrollBar().setValue(scroll)
