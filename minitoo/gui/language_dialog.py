"""First-run language picker."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout)

from .. import APP_NAME
from ..i18n import LANGUAGES, _, set_language


class LanguageDialog(QDialog):
    def __init__(self, preselect: str, icon_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(440, 560)
        self.chosen = preselect if preselect in LANGUAGES else "en"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)

        top = QHBoxLayout()
        if icon_path:
            logo = QLabel()
            logo.setPixmap(QPixmap(icon_path).scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
            top.addWidget(logo)
            self.setWindowIcon(QIcon(icon_path))
        titles = QVBoxLayout()
        name = QLabel(APP_NAME)
        name.setProperty("heading", True)
        self.subtitle = QLabel()
        self.subtitle.setProperty("muted", True)
        titles.addWidget(name)
        titles.addWidget(self.subtitle)
        top.addLayout(titles, 1)
        lay.addLayout(top)

        self.prompt = QLabel()
        self.prompt.setStyleSheet("font-size: 11pt; font-weight: 600; margin-top: 6px;")
        lay.addWidget(self.prompt)

        self.list = QListWidget()
        self.list.setIconSize(QSize(1, 1))
        self.list.setStyleSheet("QListWidget::item { padding: 9px 10px; font-size: 10.5pt; }")
        for code, (native, english) in LANGUAGES.items():
            text = native if native == english else f"{native}   —   {english}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.list.addItem(item)
            if code == self.chosen:
                self.list.setCurrentItem(item)
        self.list.currentItemChanged.connect(self._changed)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list, 1)

        self.note = QLabel()
        self.note.setProperty("muted", True)
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        row = QHBoxLayout()
        row.addStretch(1)
        self.ok = QPushButton()
        self.ok.setProperty("primary", True)
        self.ok.setMinimumWidth(140)
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        lay.addLayout(row)
        self._retranslate()

    def _changed(self, item, _prev=None) -> None:
        if item is None:
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        set_language(self.chosen)
        self._retranslate()

    def _retranslate(self) -> None:
        set_language(self.chosen)
        self.subtitle.setText(_("Screen mirroring for the Divoom MiniToo"))
        self.prompt.setText(_("Choose your language"))
        self.note.setText(_("You can change the language later in Settings."))
        self.ok.setText(_("Continue"))
