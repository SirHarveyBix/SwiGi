import ctypes
import logging
import os
import sys
from swigi.constants import SYSTEM

log = logging.getLogger("swigi.hidapi")


class _DeviceInfo(ctypes.Structure):
    pass


_DeviceInfo._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", ctypes.POINTER(_DeviceInfo)),
]


def _load_hidapi() -> ctypes.CDLL:
    """Charge hidapi. Ordre de recherche : répertoire app, bundle PyInstaller, système."""
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    meipass = getattr(sys, "_MEIPASS", None)  # PyInstaller

    search_dirs = [app_dir]
    if meipass:
        search_dirs.append(meipass)

    if SYSTEM == "Darwin":
        local_names = ["libhidapi.dylib"]
        system_names = [
            "/opt/homebrew/lib/libhidapi.dylib",
            "/usr/local/lib/libhidapi.dylib",
            "libhidapi.dylib",
        ]
    elif SYSTEM == "Windows":
        local_names = ["hidapi.dll", "libhidapi-0.dll"]
        system_names = ["hidapi.dll", "libhidapi-0.dll"]
        for d in search_dirs:
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
        scripts_dir = os.path.join(sys.prefix, "Scripts")
        if os.path.isdir(scripts_dir):
            try:
                os.add_dll_directory(scripts_dir)
            except Exception:
                pass
    else:  # Linux
        local_names = ["libhidapi-hidraw.so.0", "libhidapi-hidraw.so", "libhidapi.so.0", "libhidapi.so"]
        system_names = local_names + ["libhidapi-libusb.so.0", "libhidapi-libusb.so"]

    for d in search_dirs:
        for name in local_names:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                try:
                    lib = ctypes.CDLL(path)
                    log.debug("hidapi: chargé %s (local)", path)
                    return lib
                except OSError:
                    continue

    for name in system_names:
        try:
            lib = ctypes.CDLL(name)
            log.debug("hidapi: chargé %s (système)", name)
            return lib
        except OSError:
            continue

    hints = {
        "Darwin": "brew install hidapi  OU  copier libhidapi.dylib dans le dossier de ce fichier",
        "Windows": "Télécharger hidapi.dll depuis github.com/libusb/hidapi/releases",
        "Linux": "sudo apt install libhidapi-hidraw0",
    }
    raise ImportError(f"hidapi introuvable — {hints.get(SYSTEM, 'installer hidapi')}")


# Charger hidapi
_lib = _load_hidapi()

# Initialiser hidapi
_lib.hid_init.restype = ctypes.c_int
_lib.hid_init.argtypes = []
_lib.hid_init()

# macOS : non-exclusif (coexiste avec Logi Options+)
if SYSTEM == "Darwin":
    _fn = getattr(_lib, "hid_darwin_set_open_exclusive", None)
    if _fn:
        _fn.argtypes = [ctypes.c_int]
        _fn.restype = None
        _fn(0)

# Liaisons hidapi
_lib.hid_enumerate.restype = ctypes.POINTER(_DeviceInfo)
_lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
_lib.hid_free_enumeration.restype = None
_lib.hid_free_enumeration.argtypes = [ctypes.POINTER(_DeviceInfo)]
_lib.hid_open_path.restype = ctypes.c_void_p
_lib.hid_open_path.argtypes = [ctypes.c_char_p]
_lib.hid_close.restype = None
_lib.hid_close.argtypes = [ctypes.c_void_p]
_lib.hid_read_timeout.restype = ctypes.c_int
_lib.hid_read_timeout.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_int]
_lib.hid_write.restype = ctypes.c_int
_lib.hid_write.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
_lib.hid_error.restype = ctypes.c_wchar_p
_lib.hid_error.argtypes = [ctypes.c_void_p]


def hid_err(dev=None):
    msg = _lib.hid_error(dev)
    return msg if msg else "erreur hidapi inconnue"


# Exposer _lib et _DeviceInfo pour les autres modules
lib = _lib
DeviceInfoStruct = _DeviceInfo
