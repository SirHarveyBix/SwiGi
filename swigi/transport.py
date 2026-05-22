import ctypes
from swigi.constants import MAX_READ_SIZE
from swigi.hidapi_loader import lib, hid_err


class TransportError(Exception):
    pass


class HIDTransport:
    def __init__(self, path: bytes, pid: int):
        self.path = path
        self.pid = pid
        self._dev = lib.hid_open_path(path)
        if not self._dev:
            raise OSError(f"hid_open_path échoué : {hid_err()}")

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    def read(self, timeout: int = 500) -> bytes | None:
        if self._dev is None:
            raise TransportError("lecture sur transport fermé")
        buf = (ctypes.c_ubyte * MAX_READ_SIZE)()
        n = lib.hid_read_timeout(self._dev, buf, MAX_READ_SIZE, timeout)
        if n < 0:
            err = hid_err(self._dev) or ""
            if "success" in err.lower() or err == "":
                return None  # quirk BT macOS
            raise TransportError(f"hid_read échoué : {err}")
        return bytes(buf[:n]) if n > 0 else None

    def write(self, msg: bytes) -> None:
        if self._dev is None:
            raise TransportError("écriture sur transport fermé")
        buf = (ctypes.c_ubyte * len(msg))(*msg)
        n = lib.hid_write(self._dev, buf, len(msg))
        if n < 0:
            raise TransportError(f"hid_write échoué : {hid_err(self._dev)}")

    def close(self):
        if self._dev is not None:
            lib.hid_close(self._dev)
            self._dev = None

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
