"""Find RadiaCode detectors connected by USB.

Read-only SetupAPI enumeration of present devices with RadiaCode's USB ID; nothing is opened,
so this is safe while another process records from a detector. The serial number is the
device-instance segment of the interface path (\\\\?\\usb#vid_0483&pid_f123#<serial>#{guid}).
"""
import re

PATH_SERIAL = re.compile(r'vid_0483&pid_f123#([^#]+)#', re.IGNORECASE)


def serials_from_paths(paths):
    """Unique serial numbers (upper case, first-seen order) from device interface paths."""
    found = []
    for path in paths:
        match = PATH_SERIAL.search(path)
        if match and match.group(1).upper() not in found:
            found.append(match.group(1).upper())
    return found


def connected_serials():
    """Serial numbers of RadiaCode detectors currently present on USB; [] if none or unavailable."""
    try:
        from phyton import device_paths
        return serials_from_paths(device_paths())
    except (OSError, ImportError, AttributeError, ValueError):
        return []
