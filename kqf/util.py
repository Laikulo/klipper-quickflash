import logging
import os
import pathlib
import shutil
import subprocess
import json
import pkgutil
from importlib.metadata import distribution
from enum import Enum
from typing import Optional


def get_can_interface_bitrate(ifname: str) -> Optional[str]:
    # noinspection PyBroadException
    try:
        ipl = subprocess.run(
            ["ip", "-details", "-json", "link", "show", ifname],
            capture_output=True,
            check=True,
        )
        net_json = json.loads(ipl.stdout.decode("UTF-8"))
        bitrate = net_json[0]["linkinfo"]["info_data"]["bittiming"]["bitrate"]
    except Exception:
        logging.debug(
            f"Unable to determine bitrate for can interface {ifname}", exc_info=True
        )
        return None
    return bitrate


def get_comms_id(serial_dev):
    # The concept of a communication ID varies based on what the end-user specified.
    # There is a good chance that the end user specified a symlink, and we need to find the
    # device node.
    serial_path = pathlib.Path(serial_dev)
    if not serial_path.exists():
        # It's not up right now, so give back null
        logging.debug("Serial port not present, not trying to identify it")
        return None
    elif serial_path.is_symlink():
        serial_path = serial_path.resolve()
    if not serial_path.is_char_device():
        raise ValueError(
            f"Specified serial port {serial_path} was not a character device"
        )
    if serial_path.parent == pathlib.Path("/dev/pts"):
        logging.debug(f"Found PTY device at {serial_path} with id {serial_dev}")
        return serial_dev

    serial_dev_name = serial_path.name
    serial_sysfs_path = pathlib.Path("/sys/class/tty") / serial_dev_name
    if not serial_sysfs_path.exists():
        raise ValueError(
            f"Unable to access information about serial device {serial_dev_name}:{serial_path}"
        )

    # /sys/devices/..../usbX/X-Y/X-Y.Z/....
    serial_device_path = (serial_sysfs_path / "device").resolve()
    device_driver = (serial_device_path / "driver").readlink().name
    if device_driver == "cdc_acm":
        # Not all cdc_acm's are glong to be klipper
        usb_mfr = (serial_device_path.parent / "manufacturer").read_text().strip()
        if usb_mfr == "Klipper":
            # A klipper-managed virutal serial port (not a uart on a chip, or an external adapter)
            serial_device_serial = (
                (serial_device_path.parent / "serial").read_text().strip()
            )
            logging.debug(
                f"Found Klipper-USB serial at {serial_dev_name}:{serial_path} with id {serial_device_serial}"
            )
            return serial_device_serial
        else:
            logging.debug(
                f"Found non-Klipper usb serial at {serial_dev_name} with id {serial_dev}"
            )
            return serial_dev
    else:
        # Assume non-usb serial
        logging.debug(f"Found non-usb serial at {serial_dev_name} with id {serial_dev}")
        return serial_dev


def launch_editor(filename: os.PathLike, editor: Optional[str] = None) -> None:
    editor = editor or find_editor()
    subprocess.run([editor, filename])


EDITOR_ENV_TO_TRY = ["KQF_EDITOR", "VISUAL", "EDITOR"]
EDITORS_TO_TRY = ["sensible-editor", "editor", "vim", "vi", "emacs", "nano", "pico"]


def find_editor() -> str:
    for i in EDITOR_ENV_TO_TRY:
        if i in os.environ and shutil.which(os.environ[i]):
            return os.environ[i]
    for i in EDITORS_TO_TRY:
        if shutil.which(i):
            return i
    raise RuntimeError(
        "Unable to find an editor, please set the KQF_EDITOR, EDITOR, or VISUAL envvar"
    )


class ServiceManager(Enum):
    UNKNOWN = 0
    SYSTEMD = 1
    REDHAT_RC = 2
    DEBIAN_RC = 3
    BUSYBOX_RC = 4
    OPEN_RC = 5


def proc_get_name(pid: int) -> str:
    """
    Returns the name of the process given by id.
    Processes can set this, so it isn't a highly trusted thing
    """
    with open(f"/proc/{str(pid)}/stat") as stat_file:
        stat_line = stat_file.readline()
    # We need to find the first character of the name, it's the second token
    start_pos = 0
    while stat_line[start_pos] != " ":
        start_pos += 1
    # Position of the first space
    start_pos += 1

    if stat_line[start_pos] == "(":
        start_pos += 1
        end_char = ")"
    else:
        end_char = " "

    end_pos = start_pos + 1

    while stat_line[end_pos] != end_char:
        end_pos += 1

    ps_name = stat_line[start_pos:end_pos]

    return ps_name


def get_system_service_manager(required: bool = False) -> ServiceManager:
    """
    Determine the system-wide service manager, this may not be the only service manager, or even the one that is
    managing klippy
    """
    # If pid1 is systemd, we can assume we are on a systemd-manged system.
    pid1_name = proc_get_name(1)
    if pid1_name == "systemd":
        return ServiceManager.SYSTEMD
    elif pathlib.Path("/etc/inittab").exists() and pid1_name == "init":
        # RC Style init.
        # if we have a /var/lock/subsys, we are redhat style
        if pathlib.Path("/var/lock/subsys").exists():
            return ServiceManager.REDHAT_RC
        # If we only have init.d and no rc.d, we are busybox runlevel-less rc
        elif (
            pathlib.Path("/etc/init.d").exists()
            and not pathlib.Path("/etc/rc.d").exists()
        ):
            return ServiceManager.BUSYBOX_RC
        elif (
            pathlib.Path("/etc/init.d").exists() and pathlib.Path("/etc/rc.d").exists()
        ):
            return ServiceManager.DEBIAN_RC
        # if none of the above, fall through and let the UNKNOWN behavior take place
    elif pid1_name == "openrc-init":
        return ServiceManager.OPEN_RC

    if required:
        raise RuntimeError("Could not determine the system service manager")
    return ServiceManager.UNKNOWN


def get_license_text() -> Optional[str]:
    try:
        return distribution("klipper_quick_flash").read_text("COPYING")
    except Exception:
        pass

    try:
        data = pkgutil.get_data("kqf", "GPL3.txt")
        return data.decode("ASCII")
    except Exception:
        pass

    return None
