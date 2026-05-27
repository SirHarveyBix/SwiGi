import json
import os
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Mock swigi.gui avant import (effet de bord load_prefs)
if "swigi.gui" in sys.modules:
    _mock_gui = sys.modules["swigi.gui"]
else:
    _mock_gui = MagicMock()
    _mock_gui.notify = MagicMock()
    _mock_gui.prefs = {}
    _mock_gui.HAS_RUMPS = False
    _mock_gui.SwiGiMenuBar = None
    sys.modules["swigi.gui"] = _mock_gui

if "swigi.hidapi_loader" in sys.modules:
    _mock_loader = sys.modules["swigi.hidapi_loader"]
else:
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    sys.modules["swigi.hidapi_loader"] = _mock_loader

from swigi.bettermouse import (
    _find_global_app,
    _polling_hz,
    _safe_update,
    apply_profile,
    export_current,
    is_available,
    list_profiles,
    read_info,
)

# ── Helpers de construction plist ──────────────────────────────────────────────


def _make_mice_data(product="MX Master 4", vendor="Logitech"):
    """Construit le payload mice (inner plist)."""
    return plistlib.dumps(
        {
            "mice": [
                {
                    "name": {"product": product, "vendor": vendor},
                    "hiResWheel": True,
                    "ratchetMode": True,
                    "disengagePoint": 14,
                    "torque": 70,
                    "dpiEn": False,
                    "dpiIndex": 0,
                    "rpRate": 3,
                    "rpRateList": 0b00001000,
                }
            ]
        },
        fmt=plistlib.FMT_BINARY,
    )


def _make_appitems_data():
    """Construit le payload appitems (inner plist) avec profil global."""
    return plistlib.dumps(
        {
            "apps": {
                "global": {
                    "url": {"relative": "./"},
                    "scl": {
                        "smoothEn": True,
                        "sclThrough": True,
                        "duration": 10,
                        "brake": 10,
                        "panelLpf": 2,
                        "lpfDura": 14,
                        "vertInvEn": False,
                        "horiInvEn": False,
                        "horiSpeed": 8.0,
                    },
                }
            }
        },
        fmt=plistlib.FMT_BINARY,
    )


def _make_root_plist(product="MX Master 4", vendor="Logitech"):
    """Construit le plist BetterMouse racine complet."""
    return {
        "version": "8830",
        "mice": _make_mice_data(product=product, vendor=vendor),
        "appitems": _make_appitems_data(),
    }


def _write_root_plist(path, **kwargs):
    root = _make_root_plist(**kwargs)
    with open(path, "wb") as file:
        plistlib.dump(root, file, fmt=plistlib.FMT_BINARY)


# ── Helpers privés ─────────────────────────────────────────────────────────────


class TestFindGlobalApp(unittest.TestCase):
    def test_finds_global(self):
        """Trouve l'app globale par url.relative == './'."""
        applications = {"global_app": {"url": {"relative": "./"}, "scl": {}}}
        result = _find_global_app(applications)
        self.assertIsNotNone(result)
        self.assertIn("scl", result)

    def test_returns_none_when_missing(self):
        """Retourne None si aucune app globale."""
        applications = {"app1": {"url": {"relative": "/some/app"}}}
        self.assertIsNone(_find_global_app(applications))

    def test_empty_dict(self):
        """Retourne None sur dict vide."""
        self.assertIsNone(_find_global_app({}))


class TestPollingHz(unittest.TestCase):
    def test_1000hz(self):
        """Bit 3 seul disponible → 1000 Hz."""
        # bit 3 = 1000 Hz, reporting_rate=0 → seul bit disponible → 1000 Hz
        self.assertEqual(_polling_hz(0b00001000, 0), 1000)

    def test_500hz(self):
        """Bits 2+3 disponibles, index 0 → 500 Hz."""
        # bits 2+3 disponibles, reporting_rate=0 → 500 Hz
        self.assertEqual(_polling_hz(0b00001100, 0), 500)

    def test_out_of_range(self):
        """Index hors limites → 0."""
        # reporting_rate hors limites → 0
        self.assertEqual(_polling_hz(0b00000001, 5), 0)


class TestSafeUpdate(unittest.TestCase):
    def test_updates_non_none(self):
        """Met à jour uniquement les valeurs non-None."""
        target = {"a": 1}
        _safe_update(target, {"a": 99, "b": None, "c": 3})
        self.assertEqual(target["a"], 99)
        self.assertNotIn("b", target)
        self.assertEqual(target["c"], 3)


# ── is_available ───────────────────────────────────────────────────────────────


class TestIsAvailable(unittest.TestCase):
    def test_true_when_plist_exists_darwin(self):
        """True si le plist BetterMouse existe sur Darwin."""
        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as temp_file:
            plist_path = temp_file.name
        try:
            with (
                patch("swigi.bettermouse.BM_PLIST", plist_path),
                patch("swigi.bettermouse.SYSTEM", "Darwin"),
            ):
                self.assertTrue(is_available())
        finally:
            os.unlink(plist_path)

    def test_false_when_missing(self):
        """False si le plist n'existe pas."""
        with (
            patch("swigi.bettermouse.BM_PLIST", "/nonexistent/path.plist"),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            self.assertFalse(is_available())

    def test_false_on_non_darwin(self):
        """False sur système non-Darwin."""
        with patch("swigi.bettermouse.SYSTEM", "Windows"):
            self.assertFalse(is_available())


# ── list_profiles ──────────────────────────────────────────────────────────────


class TestListProfiles(unittest.TestCase):
    def test_lists_json_files_sorted(self):
        """Liste les fichiers .json triés alphabétiquement sans extension."""
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ("b-prof.json", "a-prof.json", "c-prof.json"):
                open(os.path.join(temp_dir, name), "w").close()
            with patch("swigi.bettermouse.PROFILES_DIR", temp_dir):
                result = list_profiles()
        self.assertEqual(result, ["a-prof", "b-prof", "c-prof"])

    def test_ignores_non_json(self):
        """Ignore les fichiers non-.json."""
        with tempfile.TemporaryDirectory() as temp_dir:
            open(os.path.join(temp_dir, "prof.json"), "w").close()
            open(os.path.join(temp_dir, "notes.txt"), "w").close()
            with patch("swigi.bettermouse.PROFILES_DIR", temp_dir):
                result = list_profiles()
        self.assertEqual(result, ["prof"])

    def test_empty_dir(self):
        """Dossier vide → liste vide."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("swigi.bettermouse.PROFILES_DIR", temp_dir):
                self.assertEqual(list_profiles(), [])


# ── export_current ─────────────────────────────────────────────────────────────


class TestExportCurrent(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plist_path = os.path.join(self.tmpdir, "BetterMouse.plist")
        _write_root_plist(self.plist_path)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_export_creates_json(self):
        """Export crée un fichier JSON avec meta, scroll et mouse_hw."""
        profiles_dir = os.path.join(self.tmpdir, "profiles")
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            path = export_current("test-export")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        self.assertEqual(data["meta"]["name"], "test-export")
        self.assertEqual(data["meta"]["mouse"], "MX Master 4")
        self.assertIn("scroll", data)
        self.assertIn("mouse_hw", data)

    def test_export_scroll_fields(self):
        """Export contient les champs scroll corrects."""
        profiles_dir = os.path.join(self.tmpdir, "profiles")
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            path = export_current("scroll-test")
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        scroll = data["scroll"]
        self.assertTrue(scroll["smooth_en"])
        self.assertEqual(scroll["duration"], 10)
        self.assertEqual(scroll["brake"], 10)
        self.assertFalse(scroll["vert_inv"])

    def test_export_raises_when_unavailable(self):
        """FileNotFoundError si plist absent."""
        with (
            patch("swigi.bettermouse.BM_PLIST", "/nonexistent.plist"),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            with self.assertRaises(FileNotFoundError):
                export_current("x")

    def test_export_auto_name_when_none(self):
        """name=None génère un nom automatique préfixé 'profil-'."""
        profiles_dir = os.path.join(self.tmpdir, "profiles")
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            path = export_current(None)
        self.assertTrue(os.path.basename(path).startswith("profil-"))


# ── apply_profile ──────────────────────────────────────────────────────────────


class TestApplyProfile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plist_path = os.path.join(self.tmpdir, "BetterMouse.plist")
        _write_root_plist(self.plist_path)
        self.profiles_dir = os.path.join(self.tmpdir, "profiles")
        os.makedirs(self.profiles_dir)
        # Profil de test
        profile = {
            "meta": {
                "name": "test",
                "mouse": "MX Master 4",
                "bm_version": "8830",
                "exported_at": "",
            },
            "scroll": {
                "smooth_en": False,
                "scl_through": True,
                "duration": 20,
                "brake": 5,
                "panel_lpf": 2,
                "lpf_dura": 14,
                "vert_inv": True,
                "hori_inv": False,
                "hori_speed": 6.0,
            },
            "mouse_hw": {
                "ratchet": False,
                "hireswheel": False,
                "disengage_point": 10,
                "torque": 50,
                "dpi_en": False,
                "dpi_index": 0,
                "polling_rate": 2,
            },
        }
        with open(os.path.join(self.profiles_dir, "test.json"), "w") as file:
            json.dump(profile, file)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _apply(self, profile_name="test", mouse_name=None):
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", self.profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
            patch("subprocess.run"),
            patch("subprocess.Popen"),
        ):
            apply_profile(profile_name, mouse_name=mouse_name)

    def test_apply_patches_scroll(self):
        """Applique les paramètres scroll du profil au plist."""
        self._apply()
        with open(self.plist_path, "rb") as file:
            root = plistlib.load(file)
        appitems = plistlib.loads(root["appitems"])
        scroll_settings = _find_global_app(appitems["apps"])["scl"]
        self.assertFalse(scroll_settings["smoothEn"])
        self.assertEqual(scroll_settings["duration"], 20)
        self.assertEqual(scroll_settings["brake"], 5)
        self.assertTrue(scroll_settings["vertInvEn"])

    def test_apply_patches_mouse_hw(self):
        """Applique les paramètres hardware du profil au plist."""
        self._apply()
        with open(self.plist_path, "rb") as file:
            root = plistlib.load(file)
        mice = plistlib.loads(root["mice"])["mice"]
        mouse = next(
            mouse for mouse in mice if mouse["name"]["vendor"].lower() == "logitech"
        )
        self.assertFalse(mouse["ratchetMode"])
        self.assertFalse(mouse["hiResWheel"])
        self.assertEqual(mouse["disengagePoint"], 10)
        self.assertEqual(mouse["torque"], 50)
        self.assertEqual(mouse["rpRate"], 2)

    def test_apply_raises_for_wrong_mouse(self):
        """ValueError si mouse_name ne correspond pas au profil."""
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", self.profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
            patch("subprocess.run"),
            patch("subprocess.Popen"),
        ):
            with self.assertRaises(ValueError):
                apply_profile("test", mouse_name="MX Anywhere 3")

    def test_apply_missing_profile_raises(self):
        """FileNotFoundError si profil inexistant."""
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", self.profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            with self.assertRaises(FileNotFoundError):
                apply_profile("inexistant")

    def test_apply_rollback_on_patch_error(self):
        """Plist corrompu → rollback vers backup."""
        with open(self.plist_path, "rb") as file_handle:
            original_data = file_handle.read()

        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.PROFILES_DIR", self.profiles_dir),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
            patch(
                "swigi.bettermouse._patch_appitems", side_effect=RuntimeError("boom")
            ),
            patch("subprocess.run"),
            patch("subprocess.Popen"),
        ):
            with self.assertRaises(RuntimeError):
                apply_profile("test")
        # Plist doit être restauré
        with open(self.plist_path, "rb") as file_handle:
            self.assertEqual(file_handle.read(), original_data)

    def test_apply_accepts_none_mouse_name(self):
        """mouse_name=None → pas de vérification souris."""
        self._apply(mouse_name=None)


# ── read_info ──────────────────────────────────────────────────────────────────


class TestReadInfo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plist_path = os.path.join(self.tmpdir, "BetterMouse.plist")
        _write_root_plist(self.plist_path)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_mouse_info(self):
        """Retourne les infos de la souris Logitech détectée."""
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            info = read_info()
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "MX Master 4")
        self.assertTrue(info["hireswheel"])
        self.assertTrue(info["ratchet"])

    def test_returns_none_when_unavailable(self):
        """None si BetterMouse non disponible."""
        with (
            patch("swigi.bettermouse.BM_PLIST", "/nonexistent.plist"),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            self.assertIsNone(read_info())

    def test_returns_none_when_no_logitech(self):
        """None si aucune souris Logitech dans le plist."""
        _write_root_plist(self.plist_path, vendor="Razer")
        with (
            patch("swigi.bettermouse.BM_PLIST", self.plist_path),
            patch("swigi.bettermouse.SYSTEM", "Darwin"),
        ):
            self.assertIsNone(read_info())


if __name__ == "__main__":
    unittest.main()
