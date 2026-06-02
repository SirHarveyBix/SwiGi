import ctypes
import threading

from swigi.constants import MAX_READ_SIZE
from swigi.hidapi_loader import hid_error, lib


class TransportError(Exception):
    pass


class HIDTransport:
    def __init__(self, path: bytes, product_id: int):
        self.path = path
        self.product_id = product_id
        self._lock = threading.Lock()
        self._device = lib.hid_open_path(path)
        if not self._device:
            raise OSError(f"hid_open_path échoué : {hid_error()}")

    @property
    def is_open(self) -> bool:
        return self._device is not None

    def read(self, timeout: int = 500) -> bytes | None:
        with self._lock:
            device = self._device
            if device is None:
                raise TransportError("lecture sur transport fermé")
            buffer = (ctypes.c_ubyte * MAX_READ_SIZE)()
            bytes_read = lib.hid_read_timeout(device, buffer, MAX_READ_SIZE, timeout)
            if bytes_read < 0:
                error_message = hid_error(device) or ""
                if "success" in error_message.lower() or error_message == "":
                    return None  # quirk BT macOS
                raise TransportError(f"hid_read échoué : {error_message}")
            return bytes(buffer[:bytes_read]) if bytes_read > 0 else None

    def write(self, message: bytes) -> None:
        with self._lock:
            device = self._device
            if device is None:
                raise TransportError("écriture sur transport fermé")
            buffer = (ctypes.c_ubyte * len(message))(*message)
            bytes_written = lib.hid_write(device, buffer, len(message))
            if bytes_written < 0:
                raise TransportError(f"hid_write échoué : {hid_error(device)}")
            if bytes_written != len(message):
                raise TransportError(
                    f"hid_write partiel : {bytes_written}/{len(message)} octets écrits"
                )

    def close(self):
        with self._lock:
            device, self._device = self._device, None
        if device is not None:
            lib.hid_close(device)

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exceptions):
        self.close()
