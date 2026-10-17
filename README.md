# PyHantek

A Python/Qt oscilloscope app for the **Hantek DSO-6022BE / 6022BL**, **Voltcraft DSO-2020** and **DSO-6021** USB oscilloscopes.

This project is based on the work done in [OpenHantek6022](https://github.com/OpenHantek/OpenHantek6022). The USB protocol, sample-rate and gain tables, calibration handling and the scope firmware all come from there. It uses the same calibration format, so an existing `~/.config/OpenHantek/<model>_<serial>_calibration.ini` keeps working.

Built with PySide6, pyqtgraph, pyusb and numpy.

## Run

```sh
cd pyhantek
uv run pyhantek            # uses a connected scope, or the demo device if none is found
uv run pyhantek --demo     # always use the simulated demo device
uv run pyhantek -vv        # debug logging
```

Run the tests with `uv run pytest`.

### USB access

* **Linux:** install the udev rule and replug the scope:
  `sudo cp ../utils/udev_rules/60-openhantek.rules /etc/udev/rules.d/ && sudo udevadm control --reload`
* **Windows:** the scope needs the WinUSB driver (for example installed with Zadig) for both the "no firmware" (04B4) and the "firmware loaded" (04B5) IDs. The libusb DLL comes with the `libusb-package` dependency.
* **macOS:** works without a driver. `libusb-package` provides libusb.
* **WSL:** USB devices have to be attached with `usbipd`.

The firmware is uploaded automatically when the scope is plugged in. Outdated firmware is replaced too.

## Features

* Live trace with a 10 × 8 division graticule, CH1 / CH2 / Math traces, and draggable markers for trigger level, trigger position and channel offsets
* Trigger modes Auto, Normal, Single and Roll; rising or falling slope; noise reject
* Timebase from 100 ns/div to 10 s/div with automatic sample-rate selection (or a fixed rate). Low rates use oversampling for less noise
* Roll mode for slow signals: new samples scroll in from the right while the capture is still running
* Measurements: frequency, period, Vpp, max, min, mean, RMS, AC RMS, duty cycle, rise and fall time
* FFT spectrum (dBV, log frequency axis) with Hann, Hamming, Blackman, flat-top or rectangular window, and a peak marker
* Math channel: CH1 + CH2, CH1 − CH2, CH2 − CH1, CH1 × CH2
* Cursors (Δt, 1/Δt, ΔV), trace averaging, and linear, sin(x)/x or dot drawing
* Autoset, and an adjustable calibration output frequency (32 Hz to 100 kHz)
* Offset calibration with grounded inputs. It writes the OpenHantek-compatible ini file and never the EEPROM
* PNG screenshots (also copied to the clipboard) and CSV export of the visible traces
* Settings and window layout are restored on the next start

### Keyboard

| Key | Action |
| --- | --- |
| Space | Run / stop |
| S | Single shot |
| A | Autoset |
| ← / → | Timebase |
| ↑ / ↓ | V/div of the trigger source channel |
| C | Toggle cursors |
| Mouse wheel / Shift + wheel | Timebase / V/div |
| Ctrl+P / Ctrl+E | Screenshot / CSV export |

## Layout

```
pyhantek/
  models.py        scope specifications (gain steps, sample rates, USB IDs)
  device.py        USB discovery, FX2 firmware upload, transfers, demo device
  calibration.py   EEPROM calibration and the OpenHantek ini file
  acquisition.py   capture thread: hardware setup, conversion to volts, trigger, roll mode
  processing.py    trigger search, measurements, FFT, math, decimation
  settings.py      user settings (JSON)
  ui/              theme, scope and spectrum views, control panel, main window
  firmware/        FX2 firmware (from Hantek6022API)
```

## Not ported (yet)

* The DDS120, ISDS-205B and MDSO models, which use different firmware and scaling
* Writing calibration to the EEPROM, and the JSON export
* Translations, the zoom view, and the histogram

## Credits

PyHantek is based on the work done in [OpenHantek6022](https://github.com/OpenHantek/OpenHantek6022) by its authors and contributors. The FX2 firmware and the udev rule come from [Hantek6022API](https://github.com/Ho-Ro/Hantek6022API). Both projects are GPL licensed, and so is PyHantek.
