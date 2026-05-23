from datetime import datetime


DEBUG = 10
INFO = 20
WARN = 30
ERROR = 40

_LEVEL_NAMES = {
    DEBUG: "DEBUG",
    INFO: "INFO",
    WARN: "WARN",
    ERROR: "ERROR",
}

_current_level = INFO


def set_level(level):
    global _current_level
    _current_level = level


def _log(level, message):
    if level < _current_level:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_name = _LEVEL_NAMES[level]
    print(f"{timestamp} [{level_name}] {message}")


def debug(message):
    _log(DEBUG, message)


def info(message):
    _log(INFO, message)


def warn(message):
    _log(WARN, message)


def error(message):
    _log(ERROR, message)