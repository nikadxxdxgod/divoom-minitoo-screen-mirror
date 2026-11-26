"""Make docs/screenshot.png: the main window (English, simulator, test pattern).
Shows no private screen content.

    python tools/docs_screenshot.py [lang] [output.png]
"""

import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "docs", "screenshot.png")
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    import app as appmod
    from minitoo.gui.main_window import MainWindow
    from minitoo.gui.theme import apply_palette, build_qss
    from minitoo.i18n import set_language
    from minitoo.settings import AppSettings

    qa = QApplication(sys.argv[:1])
    qa.setStyle("Fusion")
    apply_palette(qa)
    qa.setStyleSheet(build_qss(appmod.resource_path("assets")))
    set_language(lang)
    s = AppSettings()
    s.connection = "simulator"
    s.source_kind = "image"
    w = MainWindow(s, persist=False, icon_path=appmod.resource_path("assets", "icon.png"))
    w.resize(1500, 900)
    w.show()

    def step1():
        w._connect()

    def step2():
        w._use_test_pattern()
        w._send_still()

    def step3():
        w.dock_device.raise_()

    def shot():
        w.grab().save(out)
        print("saved", out)
        w.close()
        qa.quit()

    QTimer.singleShot(300, step1)
    QTimer.singleShot(1200, step2)
    QTimer.singleShot(2600, step3)
    QTimer.singleShot(3200, shot)
    qa.exec()


if __name__ == "__main__":
    main()
