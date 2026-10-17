"""Entry point for the frozen (PyInstaller) build."""

import sys


def _smart_app_control_on() -> bool:
    """True if Windows 11 Smart App Control is enforcing (it blocks unsigned DLLs)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as key:
            return winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")[0] == 1
    except OSError:
        return False


def _report_blocked(exc: BaseException) -> None:
    import ctypes

    msg = (
        "PyHantek could not start because Windows blocked one of its libraries.\n\n"
        "Smart App Control is turned on, and it refuses to load unsigned program files "
        "(here: the NumPy/Qt libraries bundled with PyHantek).\n\n"
        "To run PyHantek, turn Smart App Control off in Windows Security > App & browser control "
        "> Smart App Control, or run PyHantek from source with Python (see the README).\n\n"
        f"Details: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    )
    ctypes.windll.user32.MessageBoxW(None, msg, "PyHantek: blocked by Smart App Control", 0x10)


if __name__ == "__main__":
    try:
        from pyhantek.app import main
    except ImportError as exc:
        if sys.platform == "win32" and _smart_app_control_on():
            _report_blocked(exc)
            sys.exit(1)
        raise
    sys.exit(main())
