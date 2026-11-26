"""MiniToo Mirror - start the program.

    python app.py                  normal start
    python app.py --simulator      with a virtual device (no hardware needed)
    python app.py --lang de        force a UI language for this run
    python app.py --screenshot F   save a screenshot of the window and quit (testing)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys


def resource_path(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def setup_logging() -> str:
    from minitoo.settings import app_dir

    path = os.path.join(app_dir(), "minitoo.log")
    root = logging.getLogger("minitoo")
    root.setLevel(logging.INFO)
    try:
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2,
                                                  encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)
    except OSError:
        pass
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MiniToo Mirror")
    ap.add_argument("--simulator", action="store_true", help="virtual device (no hardware)")
    ap.add_argument("--lang", help="UI language code for this run (en, ka, de, ...)")
    ap.add_argument("--screenshot", metavar="PNG", help="save a window screenshot and quit")
    ap.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--first-run", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.demo:
        args.simulator = True

    log_path = setup_logging()
    log = logging.getLogger("minitoo")

    def excepthook(exc_type, exc, tb):
        log.critical("unhandled error", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MiniTooMirror.App")
        except Exception:
            pass

    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from minitoo import APP_NAME
    from minitoo.gui.theme import apply_palette, build_qss
    from minitoo.i18n import installer_language, set_language, system_language
    from minitoo.settings import AppSettings

    app = QApplication(sys.argv if argv is None else [sys.argv[0]] + list(argv))
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    apply_palette(app)
    app.setStyleSheet(build_qss(resource_path("assets")))
    icon_path = resource_path("assets", "icon.png")
    if os.path.exists(resource_path("assets", "icon.ico")):
        app.setWindowIcon(QIcon(resource_path("assets", "icon.ico")))

    settings = AppSettings.load()
    persist = not args.simulator
    if args.simulator:
        settings.connection = "simulator"

    # --- language: chosen in the installer -> use it; first run without installer -> picker
    reg_lang = installer_language()
    if reg_lang and reg_lang != settings.installer_language and not args.first_run:
        settings.language = reg_lang            # (re)installed with a language choice
        settings.installer_language = reg_lang
        if persist:
            settings.save()
    if args.lang:
        set_language(args.lang)
    elif settings.language and not args.first_run:
        set_language(settings.language)
    else:
        from minitoo.gui.language_dialog import LanguageDialog

        guess = reg_lang or system_language()
        dlg = LanguageDialog(guess, icon_path)
        if args.screenshot and args.first_run:
            QTimer.singleShot(1200, lambda: (dlg.grab().save(args.screenshot), dlg.accept()))
        if not dlg.exec():
            return 0
        settings.language = set_language(dlg.chosen)
        if persist:
            settings.save()
        if args.screenshot and args.first_run:
            return 0

    from minitoo.gui.main_window import MainWindow

    win = MainWindow(settings, persist=persist, icon_path=icon_path)
    win.show()
    log.info("log file: %s", log_path)

    delay = 2500
    if args.demo:
        # testing: connect to the simulator and start mirroring
        QTimer.singleShot(300, win._connect)
        QTimer.singleShot(1500, win._start_mirror)
        delay = 7000
    if args.screenshot:
        def shot():
            win.grab().save(args.screenshot)
            win.close()
        QTimer.singleShot(delay, shot)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
