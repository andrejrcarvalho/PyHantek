# PyHantek

A Python/Qt oscilloscope app for the **Hantek DSO-6022BE / 6022BL**, **Voltcraft DSO-2020** and **DSO-6021** USB oscilloscopes.

This project is based on the work done in [OpenHantek6022](https://github.com/OpenHantek/OpenHantek6022). The USB protocol, sample-rate and gain tables, calibration handling and the scope firmware all come from there. It uses the same calibration format, so an existing `~/.config/OpenHantek/<model>_<serial>_calibration.ini` keeps working.

Built with PySide6, pyqtgraph, pyusb and numpy.

## Run

```sh
git clone https://github.com/andrejrcarvalho/PyHantek.git && cd PyHantek
uv run pyhantek            # uses a connected scope, or the demo device if none is found
uv run pyhantek --demo     # always use the simulated demo device (toolbar: sine, UART, I²C, SPI, Lissajous)
uv run pyhantek -vv        # debug logging
```

Run the tests with `uv run pytest`.

### Standalone builds

| Platform | File | How to build |
| --- | --- | --- |
| Linux (x86-64) | `dist/PyHantek-<version>-x86_64.AppImage` | `./packaging/build_appimage.sh` |
| Windows (x64) | `dist\PyHantek-<version>-windows-x64.exe` | `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` (run on Windows) |

PyInstaller can't cross-compile, so the `.exe` has to be built on Windows. The GitHub Actions workflow (`.github/workflows/build.yml`) builds both files on every push. When you push a `v*` tag, it attaches them to a GitHub release.

To run the AppImage: `chmod +x PyHantek-*.AppImage && ./PyHantek-*.AppImage`. It needs a distro with a glibc at least as new as the build machine's (Ubuntu 22.04 for CI builds). For USB access, install the udev rule once:
`./PyHantek-*.AppImage --udev-rules | sudo tee /etc/udev/rules.d/60-openhantek.rules && sudo udevadm control --reload`

### USB access

* **Linux:** install the udev rule and replug the scope:
  `uv run pyhantek --udev-rules | sudo tee /etc/udev/rules.d/60-openhantek.rules && sudo udevadm control --reload`
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
* Math channel with formulas, for example `ch1 - ch2`, `ch1 * ch2`, `diff(ch1)`, `integ(ch1)`, `lowpass(ch1, 10e3)`, `sqrt(ch1**2 + ch2**2)` or `ch1 > 1.5`. Formulas are evaluated safely: only whitelisted names and functions are allowed
* Protocol decoding: UART (auto baud, parity, inverted), I²C (address, R/W, ACK/NAK, repeated start) and SPI (clock plus data, framed by clock gaps). Results appear as a bus lane on screen and in a "Decoded" table
* XY mode (CH2 against CH1) for Lissajous figures and component curves
* Persistence: a fading phosphor display from 0.2 s up to infinite, to catch jitter and rare glitches
* Zoom window: drag a region on the main screen to magnify it in a second view
* Spectrogram: a scrolling FFT waterfall, next to or instead of the spectrum
* Cursors (Δt, 1/Δt, ΔV), trace averaging, and linear, sin(x)/x or dot drawing
* The trigger has hysteresis, so noise on the opposite edge can't fire it
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

## AI control (MCP)

The app has a built-in [MCP](https://modelcontextprotocol.io) server, so assistants such as Claude can operate the scope. It listens on `http://127.0.0.1:8765/mcp` (localhost only), and the **MCP** toolbar button switches it on and off. The status bar shows whether it is running.

```sh
claude mcp add --transport http --scope user pyhantek http://127.0.0.1:8765/mcp
```

| Tool | What it does |
| --- | --- |
| `get_status` | Device, run state, sample rate, all settings and valid ranges |
| `set_channel` / `set_horizontal` / `set_trigger` | Change settings (values snap to valid 1-2-5 steps) |
| `set_math` | Math formula, scale and position |
| `set_display` | Spectrum, spectrogram, XY mode, persistence, zoom window, averaging, cursors |
| `run` / `stop` / `single` / `autoset` | Acquisition control |
| `get_measurements` | Frequency, Vpp, RMS, duty cycle, rise time… for each trace |
| `get_waveform` | Time/voltage samples of the screen or the whole record |
| `get_spectrum` | FFT peaks, noise floor and THD |
| `set_decoder` / `get_decoded` | Protocol decoding (UART/I²C/SPI) and the decoded bytes or text |
| `set_demo_signal` | Choose the demo device's test signals |
| `screenshot` | PNG of the scope screen |

Measurement tools wait for a frame captured with the latest settings, so "change something, then measure" never returns stale data.

Options: `--mcp-port 9000`, `--mcp-host 0.0.0.0` (to reach the app from another machine or from WSL; this exposes the scope to your network), and `--no-mcp`.

## Layout

```
pyhantek/
  models.py        scope specifications (gain steps, sample rates, USB IDs)
  device.py        USB discovery, FX2 firmware upload, transfers, demo device
  calibration.py   EEPROM calibration and the OpenHantek ini file
  acquisition.py   capture thread: hardware setup, conversion to volts, trigger, roll mode
  processing.py    trigger search, measurements, FFT, decimation
  mathexpr.py      safe math-channel formulas
  decoders.py      UART / I²C / SPI decoders
  settings.py      user settings (JSON)
  mcp_server.py    built-in MCP server (AI control over localhost HTTP)
  ui/              theme, scope/spectrum/zoom/spectrogram views, persistence, control panel, main window
  firmware/        FX2 firmware (from Hantek6022API)
```

## Not ported (yet)

* The DDS120, ISDS-205B and MDSO models, which use different firmware and scaling
* Writing calibration to the EEPROM, and the JSON export
* Translations and the histogram

## Credits

PyHantek is based on the work done in [OpenHantek6022](https://github.com/OpenHantek/OpenHantek6022) by its authors and contributors. The FX2 firmware and the udev rule come from [Hantek6022API](https://github.com/Ho-Ro/Hantek6022API). Both projects are GPL licensed, and so is PyHantek.
