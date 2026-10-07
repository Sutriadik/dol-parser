"""
Open ADE — Centralized Logging Module
Menggantikan print() statements dengan proper logging framework.
"""

import logging
import os
import sys


def setup_logger(name: str = "open_ade", level: str | None = None) -> logging.Logger:
    """
    Membuat dan mengkonfigurasi logger untuk Open ADE Engine.

    Args:
        name: Nama logger
        level: Log level override (DEBUG, INFO, WARNING, ERROR)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Hindari duplicate handlers saat di-reload
    if logger.handlers:
        return logger

    log_level = level or os.getenv("LOG_LEVEL", "INFO")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler dengan format yang clean
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logger.level)

    formatter = logging.Formatter(
        fmt="%(asctime)s │ %(levelname)-8s │ %(message)s", datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # Paddle memasang handler di root logger; tanpa ini setiap log tercetak dua kali.
    logger.propagate = False

    return logger


# Singleton logger instance untuk seluruh aplikasi
logger = setup_logger()
