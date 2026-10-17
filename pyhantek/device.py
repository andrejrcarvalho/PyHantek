"""USB access for Hantek 6022-family scopes, plus a simulated demo device.

Protocol summary (from OpenHantek6022):
  * vendor control OUT requests 0xE0..0xE6 configure gain, sample rate,
    channel count, coupling and calibration frequency
  * 0xE3 [1] starts sampling, 0xE3 [0] stops it
  * samples arrive on bulk endpoint 0x86 as unsigned bytes ("binary offset",
    0x80 = 0 V), interleaved CH1/CH2 when two channels are active
  * calibration bytes live in the EEPROM at offset 8 (request 0xA2)
"""

from __future__ import annotations

import array
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from importlib import resources
from typing import Callable

import numpy as np

from .models import DEMO_MODEL, FIRMWARE_VERSION, MODELS, ScopeModel

log = logging.getLogger(__name__)

# Control codes
CTRL_EEPROM = 0xA2
SET_GAIN_CH1 = 0xE0
SET_GAIN_CH2 = 0xE1
SET_SAMPLERATE = 0xE2
START_SAMPLING = 0xE3
SET_NUM_CHANNELS = 0xE4
SET_COUPLING = 0xE5
SET_CALFREQ = 0xE6

EP_IN = 0x86
REQ_OUT = 0x40  # vendor | host-to-device
REQ_IN = 0xC0  # vendor | device-to-host
CALIBRATION_SIZE = 80  # offsets ls/hs (32) + gain (16) + fine offsets ls/hs (32)
FX2_CPUCS = 0xE600


class DeviceError(RuntimeError):
    pass


class DeviceGone(DeviceError):
    """The scope was unplugged."""


_BACKEND = None


def _backend():
    """libusb backend: the libusb-package DLL (Windows/macOS), a copy bundled in a frozen app, or the system one."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import libusb_package

        _BACKEND = libusb_package.get_libusb1_backend()
        if _BACKEND is not None:
            return _BACKEND
    except Exception:
        pass
    base = getattr(sys, "_MEIPASS", None)  # PyInstaller bundle
    if base:
        for name in ("libusb-1.0.so.0", "libusb-1.0.dll", "libusb-1.0.dylib"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                import usb.backend.libusb1 as libusb1

                _BACKEND = libusb1.get_backend(find_library=lambda _name, p=path: p)
                return _BACKEND
    return None


def _usb():
    try:
        import usb.core
        import usb.util
    except ImportError as exc:  # pragma: no cover
        raise DeviceError("pyusb is not installed") from exc
    return usb


@dataclass
class FoundDevice:
    model: ScopeModel
    usb_dev: object
    needs_firmware: bool

    @property
    def label(self) -> str:
        state = "firmware needed" if self.needs_firmware else "ready"
        return f"{self.model.name} (bus {self.usb_dev.bus}, addr {self.usb_dev.address}) — {state}"


def find_devices() -> list[FoundDevice]:
    """List all connected supported scopes."""
    try:
        usb = _usb()
        devices = list(usb.core.find(find_all=True, backend=_backend()) or [])
    except Exception as exc:
        log.warning("USB scan failed: %s", exc)
        return []
    found: list[FoundDevice] = []
    for dev in devices:
        ids = (dev.idVendor, dev.idProduct)
        for model in MODELS:
            if ids == (model.vid_nofw, model.pid_nofw):
                found.append(FoundDevice(model, dev, True))
                break
            if ids == (model.vid, model.pid):
                outdated = dev.bcdDevice < FIRMWARE_VERSION
                found.append(FoundDevice(model, dev, outdated))
                break
    return found


# ---------------------------------------------------------------- firmware


def parse_intel_hex(text: str) -> list[tuple[int, bytes]]:
    """Return contiguous (address, data) blocks from an Intel HEX file."""
    records: list[tuple[int, bytes]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise DeviceError(f"invalid hex line {line_no}")
        raw = bytes.fromhex(line[1:])
        if sum(raw) & 0xFF:
            raise DeviceError(f"hex checksum error in line {line_no}")
        length, addr, rtype = raw[0], (raw[1] << 8) | raw[2], raw[3]
        if rtype == 0x00:
            records.append((addr, raw[4 : 4 + length]))
        elif rtype == 0x01:
            break
    # merge adjacent records into blocks of up to 1 KiB
    blocks: list[tuple[int, bytearray]] = []
    for addr, data in sorted(records):
        if blocks and blocks[-1][0] + len(blocks[-1][1]) == addr and len(blocks[-1][1]) + len(data) <= 1024:
            blocks[-1][1].extend(data)
        else:
            blocks.append((addr, bytearray(data)))
    return [(a, bytes(d)) for a, d in blocks]


def upload_firmware(found: FoundDevice, progress: Callable[[str], None] = lambda s: None, timeout: float = 10.0) -> FoundDevice:
    """Upload firmware into the FX2 RAM and wait for the scope to re-enumerate."""
    usb = _usb()
    hex_text = resources.files("pyhantek.firmware").joinpath(f"{found.model.firmware}-firmware.hex").read_text()
    blocks = parse_intel_hex(hex_text)
    dev = found.usb_dev
    progress(f"Uploading firmware to {found.model.name}…")
    try:
        dev.ctrl_transfer(REQ_OUT, 0xA0, FX2_CPUCS, 0, b"\x01", 1000)  # halt CPU
        for addr, data in blocks:
            if addr + len(data) > 0x4000:
                raise DeviceError("firmware exceeds FX2LP internal RAM")
            dev.ctrl_transfer(REQ_OUT, 0xA0, addr, 0, data, 1000)
        try:
            dev.ctrl_transfer(REQ_OUT, 0xA0, FX2_CPUCS, 0, b"\x00", 1000)  # run
        except usb.core.USBError:
            pass  # the device may drop off the bus immediately
    except usb.core.USBError as exc:
        raise DeviceError(f"firmware upload failed: {exc}") from exc
    finally:
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass

    progress("Waiting for the scope to restart…")
    time.sleep(1.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for cand in find_devices():
            if cand.model.vid == found.model.vid and cand.model.pid == found.model.pid and not cand.needs_firmware:
                return cand
        time.sleep(0.25)
    raise DeviceError("scope did not come back after firmware upload")


# ---------------------------------------------------------------- devices


class ScopeDevice:
    """A real scope with firmware loaded."""

    is_demo = False

    def __init__(self, found: FoundDevice):
        usb = _usb()
        self._usb = usb
        self.model = found.model
        self.dev = found.usb_dev
        try:
            try:
                if self.dev.is_kernel_driver_active(0):
                    self.dev.detach_kernel_driver(0)
            except (NotImplementedError, usb.core.USBError):
                pass
            try:
                self.dev.set_configuration()
            except usb.core.USBError:
                pass  # already configured
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError as exc:
            raise DeviceError(self._permission_hint(exc)) from exc
        try:
            self.serial = self.dev.serial_number or "unknown"
        except Exception:
            self.serial = "unknown"
        self.firmware_version = self.dev.bcdDevice
        self._lock = threading.Lock()

    @staticmethod
    def _permission_hint(exc) -> str:
        msg = f"cannot open the scope: {exc}"
        if getattr(exc, "errno", None) == 13 or "Access denied" in str(exc):
            msg += "\nOn Linux install the udev rule (utils/udev_rules/60-openhantek.rules) and replug the scope."
        return msg

    @property
    def name(self) -> str:
        return self.model.name

    def _wrap(self, exc) -> DeviceError:
        if getattr(exc, "errno", None) in (19, 5) or "No such device" in str(exc):
            return DeviceGone(str(exc))
        return DeviceError(str(exc))

    def control(self, code: int, data: bytes) -> None:
        with self._lock:
            for attempt in range(3):
                try:
                    self.dev.ctrl_transfer(REQ_OUT, code, 0, 0, data, 500)
                    return
                except self._usb.core.USBTimeoutError:
                    if attempt == 2:
                        raise DeviceError(f"timeout sending command 0x{code:02x}")
                except self._usb.core.USBError as exc:
                    raise self._wrap(exc) from exc

    def read_calibration(self) -> bytes | None:
        if not self.model.has_eeprom_calibration:
            return None
        try:
            with self._lock:
                data = self.dev.ctrl_transfer(REQ_IN, CTRL_EEPROM, 8, 0, CALIBRATION_SIZE, 500)
            return bytes(data)
        except Exception as exc:
            log.warning("could not read calibration EEPROM: %s", exc)
            return None

    def read(self, nbytes: int, rate_bytes: float, stop: threading.Event, chunk: Callable[[np.ndarray, int], None] | None = None) -> np.ndarray | None:
        """Start sampling and read `nbytes` from the bulk endpoint.

        `rate_bytes` is the expected data rate (B/s) and is used for timeouts.
        Slow captures are read in pieces so that `chunk(buffer, received)` can
        show progress and the capture can be interrupted via `stop`.
        """
        self.control(START_SAMPLING, b"\x01")
        buf = np.empty(nbytes, dtype=np.uint8)
        expected_s = nbytes / max(rate_bytes, 1)
        piece = nbytes if expected_s < 0.25 else max(512, int(rate_bytes * 0.1) // 512 * 512)
        received = 0
        try:
            while received < nbytes:
                if stop.is_set():
                    return None
                n = min(piece, nbytes - received)
                timeout = int(1000 + 3000 * n / max(rate_bytes, 1))
                tmp = array.array("B", bytes(n))
                with self._lock:
                    got = self.dev.read(EP_IN, tmp, timeout)
                if got <= 0:
                    break
                buf[received : received + got] = np.frombuffer(tmp, dtype=np.uint8, count=got)
                received += got
                if chunk is not None and piece < nbytes:
                    chunk(buf, received)
        except self._usb.core.USBTimeoutError:
            log.debug("bulk read timeout after %d/%d bytes", received, nbytes)
            return None
        except self._usb.core.USBError as exc:
            raise self._wrap(exc) from exc
        return buf if received == nbytes else None

    def stop_sampling(self) -> None:
        try:
            self.control(START_SAMPLING, b"\x00")
        except DeviceError:
            pass

    def close(self) -> None:
        self.stop_sampling()
        try:
            self._usb.util.release_interface(self.dev, 0)
            self._usb.util.dispose_resources(self.dev)
        except Exception:
            pass


class DemoDevice:
    """Simulated scope: CH1 is a slightly distorted 1 kHz sine, CH2 the calibration square wave."""

    is_demo = True
    serial = "demo"
    firmware_version = 0

    def __init__(self):
        self.model = DEMO_MODEL
        self.gain = [1, 1]
        self.gain_index = [7, 7]
        self.rate_id = 10
        self.channels = 2
        self.calfreq = 1000.0
        self.t = 0.0
        self.rng = np.random.default_rng()

    @property
    def name(self) -> str:
        return "Demo device"

    def control(self, code: int, data: bytes) -> None:
        if code in (SET_GAIN_CH1, SET_GAIN_CH2):
            ch = code - SET_GAIN_CH1
            self.gain[ch], self.gain_index[ch] = data[0], data[1]
        elif code == SET_SAMPLERATE:
            self.rate_id = data[0]
        elif code == SET_NUM_CHANNELS:
            self.channels = data[0]
        elif code == SET_CALFREQ:
            cf = data[0]
            if cf == 0:
                self.calfreq = 100.0
            elif cf > 200:
                self.calfreq = (cf - 200) * 100.0
            elif cf > 100:
                self.calfreq = (cf - 100) * 10.0
            else:
                self.calfreq = cf * 1000.0

    def read_calibration(self) -> bytes | None:
        return None

    SIGNALS = ("sine", "uart", "i2c", "spi", "lissajous")
    signal = "sine"
    _patterns: dict = {}

    def _pattern(self, name):
        """Repeating logic patterns for the protocol demos (built once)."""
        if name not in self._patterns:
            if name == "uart":
                bits = [1] * 20
                for byte in b"Hello from PyHantek!\r\n":
                    bits += [0] + [(byte >> k) & 1 for k in range(8)] + [1, 1]
                bits += [1] * 200
                self._patterns[name] = (np.array(bits, np.uint8),)
            elif name == "i2c":
                scl, sda = [], []

                def put(c, d, k=1):
                    scl.extend([c] * k)
                    sda.extend([d] * k)

                put(1, 1, 8)
                for addr, data, read in ((0x48, b"\x00", False), (0x48, b"\x1a\x80", True)):
                    put(1, 0, 2)  # START
                    put(0, 0, 1)
                    for byte in [(addr << 1) | int(read)] + list(data):
                        for bit in [(byte >> (7 - k)) & 1 for k in range(8)] + [0]:
                            put(0, bit)
                            put(1, bit, 2)
                            put(0, bit)
                    put(0, 0)
                    put(1, 0, 2)
                    put(1, 1, 6)  # STOP
                put(1, 1, 60)
                self._patterns[name] = (np.array(scl, np.uint8), np.array(sda, np.uint8))
            elif name == "spi":
                clk, mosi = [], []
                for byte in b"\x9f\x00\xef\x40\x18":
                    for k in range(8):
                        b = (byte >> (7 - k)) & 1
                        clk += [0, 1]
                        mosi += [b, b]
                    clk += [0] * 4
                    mosi += [0] * 4
                clk += [0] * 60
                mosi += [0] * 60
                self._patterns[name] = (np.array(clk, np.uint8), np.array(mosi, np.uint8))
        return self._patterns[name]

    def _signals(self, t):
        """Analog test signals in volts for CH1/CH2."""
        square = np.where((t * self.calfreq) % 1.0 < 0.5, 2.0, 0.0)
        if self.signal == "uart":
            (pat,) = self._pattern("uart")
            idx = (t * 115200).astype(np.int64) % len(pat)
            return pat[idx] * 3.3, square
        if self.signal == "i2c":
            scl, sda = self._pattern("i2c")
            idx = (t * 100e3 * 4).astype(np.int64) % len(scl)  # 4 steps per 100 kHz clock
            return scl[idx] * 3.3, sda[idx] * 3.3
        if self.signal == "spi":
            clk, mosi = self._pattern("spi")
            idx = (t * 1e6 * 2).astype(np.int64) % len(clk)  # 2 steps per 1 MHz clock
            return clk[idx] * 3.3, mosi[idx] * 3.3
        if self.signal == "lissajous":
            return 1.5 * np.sin(2 * np.pi * 1000 * t), 1.5 * np.sin(2 * np.pi * 1500 * t + 0.6)
        sine = 1.5 * np.sin(2 * np.pi * 1000 * t) + 0.25 * np.sin(2 * np.pi * 3000 * t + 0.4)
        return sine, square

    def _raw_rate(self) -> float:
        rid = self.rate_id
        return rid * 1e6 if rid < 100 else (rid - 100) * 1e4

    def read(self, nbytes, rate_bytes, stop, chunk=None):
        rate = self._raw_rate()
        n = nbytes // self.channels
        # advance a random amount so that the trigger has something to do
        t0 = self.t + self.rng.uniform(0, 1e-3)
        t = t0 + np.arange(n) / rate
        self.t = t[-1]
        out = np.empty((n, self.channels), dtype=np.uint8)
        for ch, sig in enumerate(self._signals(t)[: self.channels]):
            scale = self.model.voltage_scale[self.gain_index[ch]]
            noisy = sig + self.rng.normal(0, 0.012, n)
            out[:, ch] = np.clip(np.round(noisy * scale + 128), 0, 255).astype(np.uint8)
        flat = out.reshape(-1)
        # pace like real hardware, but faster for very slow rates
        duration = n / rate
        end = time.monotonic() + min(duration, 0.5)
        steps = max(1, int(min(duration, 0.5) / 0.05))
        for i in range(steps):
            if stop.is_set():
                return None
            time.sleep(max(0.0, (end - time.monotonic()) / (steps - i)))
            if chunk is not None and steps > 1:
                chunk(flat, int(len(flat) * (i + 1) / steps) // self.channels * self.channels)
        return flat

    def stop_sampling(self) -> None:
        pass

    def close(self) -> None:
        pass


def calfreq_code(freq: float) -> int:
    """Encode the calibration output frequency the way the firmware expects."""
    if freq < 1000:
        cf = 100 + round(freq / 10)
        return 0 if cf == 110 else cf
    if freq <= 5500 and int(freq) % 1000:
        return 200 + round(freq / 100)
    return round(freq / 1000)


def raw_rate(sample_id: int) -> float:
    return sample_id * 1e6 if sample_id < 100 else (sample_id - 100) * 1e4


__all__ = [
    "DemoDevice",
    "DeviceError",
    "DeviceGone",
    "FoundDevice",
    "ScopeDevice",
    "calfreq_code",
    "find_devices",
    "raw_rate",
    "upload_firmware",
]
