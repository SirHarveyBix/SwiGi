"""Tests pour swigi.prefs — chargement et sauvegarde des préférences."""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

if "swigi.hidapi_loader" not in sys.modules:
    from unittest.mock import MagicMock
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    sys.modules["swigi.hidapi_loader"] = _mock_loader

if "swigi.gui" not in sys.modules:
    from unittest.mock import MagicMock
    _mock_gui = MagicMock()
    sys.modules["swigi.gui"] = _mock_gui

from swigi.prefs import load_prefs, save_prefs


class TestLoadPrefs(unittest.TestCase):
    def test_success_with_existing_file(self):
        """Charge depuis un fichier JSON existant avec valeurs personnalisées."""
        data = {"notifications": False, "mouse_follow": False, "custom": 42}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(data, tmp)
            tmp_path = tmp.name
        try:
            with patch("swigi.prefs.PREFS_FILE", tmp_path):
                result = load_prefs()
        finally:
            os.unlink(tmp_path)
        self.assertEqual(result["notifications"], False)
        self.assertEqual(result["custom"], 42)

    def test_setdefault_adds_missing_keys(self):
        """setdefault injecte notifications et mouse_follow si absents du fichier."""
        data = {}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(data, tmp)
            tmp_path = tmp.name
        try:
            with patch("swigi.prefs.PREFS_FILE", tmp_path):
                result = load_prefs()
        finally:
            os.unlink(tmp_path)
        self.assertTrue(result["notifications"])
        self.assertTrue(result["mouse_follow"])

    def test_file_not_found_returns_defaults(self):
        """Fichier absent → retourne les valeurs par défaut sans lever."""
        with patch("swigi.prefs.PREFS_FILE", "/tmp/__swigi_prefs_does_not_exist__.json"):
            result = load_prefs()
        self.assertEqual(result, {"notifications": True, "mouse_follow": True})

    def test_invalid_json_returns_defaults(self):
        """JSON invalide → retourne les valeurs par défaut sans lever."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            tmp.write("not valid json {{{")
            tmp_path = tmp.name
        try:
            with patch("swigi.prefs.PREFS_FILE", tmp_path):
                result = load_prefs()
        finally:
            os.unlink(tmp_path)
        self.assertEqual(result, {"notifications": True, "mouse_follow": True})


class TestSavePrefs(unittest.TestCase):
    def test_saves_and_reloads(self):
        """save_prefs écrit le JSON, load_prefs peut le relire."""
        data = {"notifications": True, "mouse_follow": False, "test_key": 99}
        with tempfile.TemporaryDirectory() as tmpdir:
            prefs_path = os.path.join(tmpdir, "prefs.json")
            with patch("swigi.prefs.PREFS_FILE", prefs_path):
                save_prefs(data)
                result = load_prefs()
        self.assertEqual(result["test_key"], 99)
        self.assertFalse(result["mouse_follow"])

    def test_atomic_replace(self):
        """save_prefs utilise un fichier temporaire puis os.replace (atomique)."""
        data = {"notifications": True}
        with tempfile.TemporaryDirectory() as tmpdir:
            prefs_path = os.path.join(tmpdir, "prefs.json")
            with patch("swigi.prefs.PREFS_FILE", prefs_path):
                save_prefs(data)
            self.assertTrue(os.path.exists(prefs_path))
            with open(prefs_path) as f:
                saved = json.load(f)
        self.assertEqual(saved, data)

    def test_exception_silenced(self):
        """Erreur pendant la sauvegarde est loguée, pas propagée."""
        data = {"notifications": True}
        with patch("swigi.prefs.PREFS_FILE", "/nonexistent_dir/prefs.json"):
            save_prefs(data)  # ne doit pas lever


if __name__ == "__main__":
    unittest.main()
