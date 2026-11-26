import logging

from minitoo.i18n import set_language

# tests run in English; library warnings stay out of the console
set_language("en")
logging.getLogger("minitoo").addHandler(logging.NullHandler())
logging.getLogger("minitoo").propagate = False
