"""Credentials and identity from environment variables only (I1 constraint 4). A variable missing from the process
environment is also looked up in the Windows user environment (registry key HKEY_CURRENT_USER, subkey Environment), where `setx` or
[Environment]::SetEnvironmentVariable(..., 'User') put it, so a value set after this app started is found without
a restart. Values are never printed or logged."""
import os


def _user_env(name):
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value, _ = winreg.QueryValueEx(k, name)
            return str(value).strip() or None
    except OSError:
        return None


def get(name):
    v = os.environ.get(name, "").strip()
    if not v:
        v = _user_env(name) or ""
        if v:
            os.environ[name] = v
    return v or None


def require(*names):
    missing = [n for n in names if not get(n)]
    if missing:
        raise EnvironmentError(f"not set: {', '.join(missing)}")


def status(*names):
    """{name: 'set' | 'unset'} without values."""
    return {n: "set" if get(n) else "unset" for n in names}
