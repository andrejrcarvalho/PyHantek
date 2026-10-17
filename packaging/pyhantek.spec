# PyInstaller spec for PyHantek.
#   Windows: one self-contained PyHantek.exe
#   Linux:   a folder (build/dist/pyhantek/) that build_appimage.sh wraps into an AppImage
# Build with:  uv run pyinstaller --noconfirm packaging/pyhantek.spec
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WINDOWS = sys.platform == "win32"

datas = collect_data_files("pyhantek", includes=["firmware/*.hex", "assets/*"])
binaries = []
hiddenimports = (
    collect_submodules("uvicorn")  # uvicorn picks loops/protocols dynamically
    + collect_submodules("mcp.server")
    + ["pyhantek.mcp_server", "pyhantek.firmware", "pyhantek.assets", "usb.backend.libusb1"]
)

if WINDOWS or sys.platform == "darwin":
    datas += collect_data_files("libusb_package")
    binaries += collect_dynamic_libs("libusb_package")
else:
    # pyusb loads libusb with ctypes, which PyInstaller cannot see; bundle the system copy
    for cand in (
        "/lib/x86_64-linux-gnu/libusb-1.0.so.0",
        "/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0",
        "/usr/lib64/libusb-1.0.so.0",
        "/usr/lib/libusb-1.0.so.0",
    ):
        if os.path.exists(cand):
            binaries.append((cand, "."))
            break
    else:
        raise SystemExit("libusb-1.0.so.0 not found: install libusb-1.0-0")

excludes = [
    "tkinter", "PyQt5", "PyQt6", "PySide2", "matplotlib", "IPython", "pytest", "scipy", "pandas",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = os.path.join(ROOT, "pyhantek", "assets", "pyhantek.ico" if WINDOWS else "pyhantek.png")

if WINDOWS:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="PyHantek",
        console=False,
        icon=icon,
        upx=False,
        version=None,
    )
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pyhantek", console=False, icon=icon, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="pyhantek", upx=False)
