"""Read-only Windows boot identity for persisted monotonic deadlines."""
from functools import lru_cache


@lru_cache(maxsize=1)
def boot_identity():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters') as key:
            return str(winreg.QueryValueEx(key,'BootId')[0])
    except (OSError,ImportError):
        return None
