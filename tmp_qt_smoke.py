import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtWidgets import QApplication, QTextBrowser
from ui.panels.gmail import _sanitize_email_html_for_qt

app = QApplication([])
html = '<div style="color:rgba(0, 0, 0, 0.2);">x</div>'
browser = QTextBrowser()
browser.setHtml(_sanitize_email_html_for_qt(html))
print('ok', browser.toPlainText())
