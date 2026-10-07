"""
Test untuk app.logger — setup dan konfigurasi logging.
"""

import logging
import os
from unittest.mock import patch

from app.logger import setup_logger


class TestSetupLogger:
    """Pengujian konfigurasi logger Open ADE."""

    def test_returns_logger(self):
        lgr = setup_logger("test_logger_1")
        assert isinstance(lgr, logging.Logger)
        assert lgr.name == "test_logger_1"

    def test_default_level_info(self):
        lgr = setup_logger("test_logger_2")
        assert lgr.level == logging.INFO

    def test_level_override_debug(self):
        lgr = setup_logger("test_logger_3", level="DEBUG")
        assert lgr.level == logging.DEBUG

    def test_level_override_warning(self):
        lgr = setup_logger("test_logger_4", level="WARNING")
        assert lgr.level == logging.WARNING

    @patch.dict(os.environ, {"LOG_LEVEL": "ERROR"})
    def test_level_from_env(self):
        lgr = setup_logger("test_logger_5")
        assert lgr.level == logging.ERROR

    def test_no_duplicate_handlers(self):
        """Memanggil setup_logger dua kali dengan nama yang sama tidak menambah handler."""
        name = "test_logger_dup"
        lgr1 = setup_logger(name)
        handler_count = len(lgr1.handlers)
        lgr2 = setup_logger(name)
        assert lgr1 is lgr2
        assert len(lgr2.handlers) == handler_count

    def test_propagate_false(self):
        """Logger tidak meng-propagate ke root logger (menghindari duplikasi output)."""
        lgr = setup_logger("test_logger_no_prop")
        assert lgr.propagate is False

    def test_singleton_import(self):
        """Import `logger` dari modul menghasilkan instance yang sama."""
        from app.logger import logger as logger_a
        from app.logger import logger as logger_b

        assert logger_a is logger_b
