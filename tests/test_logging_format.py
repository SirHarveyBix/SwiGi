"""Tests pour swigi.logging_format."""

import logging
import unittest
from unittest.mock import patch

from swigi.logging_format import ColoredFormatter, PlainFormatter


class TestColoredFormatter(unittest.TestCase):
    def _make_record(self, level, message):
        record = logging.LogRecord(
            name="swigi.test",
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        return record

    @patch("swigi.logging_format.sys")
    def test_no_color_when_not_tty(self, mock_sys):
        """Pas de codes ANSI quand stderr n'est pas un TTY."""
        mock_sys.stderr.isatty.return_value = False
        formatter = ColoredFormatter()
        record = self._make_record(logging.INFO, "test message")
        result = formatter.format(record)
        self.assertIn("→", result)
        self.assertIn("test message", result)
        self.assertNotIn("\033[", result)

    @patch("swigi.logging_format.sys")
    def test_color_when_tty(self, mock_sys):
        """Codes ANSI présents quand stderr est un TTY."""
        mock_sys.stderr.isatty.return_value = True
        mock_sys.stderr.__class__ = type(mock_sys.stderr)
        formatter = ColoredFormatter()
        record = self._make_record(logging.WARNING, "attention")
        result = formatter.format(record)
        self.assertIn("\033[", result)
        self.assertIn("attention", result)

    @patch("swigi.logging_format.sys")
    def test_debug_dim(self, mock_sys):
        """Messages DEBUG formatés avec style atténué."""
        mock_sys.stderr.isatty.return_value = True
        formatter = ColoredFormatter()
        record = self._make_record(logging.DEBUG, "debug msg")
        result = formatter.format(record)
        self.assertIn("debug msg", result)

    @patch("swigi.logging_format.sys")
    def test_error_colored(self, mock_sys):
        """Messages ERROR formatés avec couleur rouge."""
        mock_sys.stderr.isatty.return_value = True
        formatter = ColoredFormatter()
        record = self._make_record(logging.ERROR, "erreur")
        result = formatter.format(record)
        self.assertIn("erreur", result)

    @patch("swigi.logging_format.sys")
    def test_critical(self, mock_sys):
        """Messages CRITICAL formatés avec fond rouge."""
        mock_sys.stderr.isatty.return_value = True
        formatter = ColoredFormatter()
        record = self._make_record(logging.CRITICAL, "fatal")
        result = formatter.format(record)
        self.assertIn("fatal", result)


class TestPlainFormatter(unittest.TestCase):
    def test_plain_format(self):
        """Format sans couleur avec niveau et message en clair."""
        formatter = PlainFormatter()
        record = logging.LogRecord(
            name="swigi.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        self.assertIn("INFO", result)
        self.assertIn("hello", result)
        self.assertNotIn("\033[", result)


if __name__ == "__main__":
    unittest.main()
