"""Colors, Matplotlib text colors and the window stylesheet."""
import matplotlib


BG, PANEL, TEXT, MUTED, TEAL, GOLD = '#101923', '#182532', '#e6edf3', '#93a6b8', '#43d8bf', '#f5bc66'
matplotlib.rcParams.update({'text.color': TEXT, 'axes.titlecolor': TEXT, 'axes.titlesize': 10})


STYLESHEET = f'''
          QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
          QFrame#panel {{ background: {PANEL}; border: 1px solid #26384a; border-radius: 10px; }}
          QFrame#panel QLabel, QFrame#panel QCheckBox {{ background: transparent; }}
          QLabel#title {{ font-size: 23pt; font-weight: 600; }}
          QLabel#muted {{ color: {MUTED}; font-size: 9pt; }}
          QLabel#metric {{ font-size: 21pt; font-weight: 600; color: {TEAL}; }}
          QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{ background: #101c28; border: 1px solid #34495e; border-radius: 5px; padding: 6px; }}
          QPushButton {{ background: #273b4d; border: none; border-radius: 5px; padding: 9px 12px; font-weight: 600; }}
          QPushButton:hover {{ background: #354f65; }}
          QPushButton:disabled {{ color: #687b8c; background: #20303e; }}
          QPushButton#primary {{ background: {TEAL}; color: #10232a; }}
          QPushButton#stop {{ background: #78454a; }}
          QPushButton#primary:disabled, QPushButton#stop:disabled {{ color: #687b8c; background: #20303e; }}
          QPlainTextEdit {{ background: #101c28; border: 1px solid #26384a; color: {MUTED}; }}
'''
