"""Logger condiviso per gli script di audit."""
from __future__ import annotations
import logging
import sys

def get_logger(name: str = "audit") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                     datefmt="%H:%M:%S"))
    log.addHandler(h)
    return log
