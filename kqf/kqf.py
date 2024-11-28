import logging
import termios
import fcntl
import ctypes
import subprocess
import time
import os
import urllib
import urllib.request
import pathlib
import shutil
import contextlib
import struct
import json
from datetime import datetime
from typing import Union, Optional, List, Iterable, Dict
import sqlite3


from .config import KQFConfig, KlipperConf
from .util import get_can_interface_bitrate


class Notebook(object):
    def __init__(self, storage_location: pathlib.Path):
        self.storage_path = storage_location
        self._db = sqlite3.connect(self.storage_path)

    def db_init(self):
        self._db.execute("CREATE TABLE IF NOT EXISTS notebook (ctx varchar(31), key varchar(31) not null, val varchar(255) not null, primary key (ctx, key));")
        self._db.commit()
        pass

    def set(self, context: str, key: str, value: any):
        self._db.execute("INSERT OR REPLACE INTO notebook (ctx, key, val) VALUES (?,?,?)", (context, key, json.dumps(value)))
        self._db.commit()

    def get(self, context: str, key: str, default_value: Optional[any]=None):
        cur = self._db.execute("SELECT val FROM notebook WHERE ctx=? and val=?", (context, key))
        item = cur.fetchone()
        if not item:
            return default_value
        else:
            return json.loads(item)

    def cache_filter(self, context, key, value, default=None):
        if value:
            self.set(context, key, value)
            logging.debug(f"Wrote notebook {context}/{key}: {value}")
            return value
        else:
            notebook_value = self.get(context, key)
            if notebook_value:
                return notebook_value
            else:
                return default


class KlipperMCU(object):
    """
    A representation of an individual MCU
    """

    @staticmethod
    def get_name_from_section_name(in_str: str) -> str:
        if in_str == "mcu":
            return in_str
        elif in_str.startswith("mcu "):
            return in_str[4:]
        else:
            raise ValueError(f"Invalid MCU section name {in_str}")

    @classmethod
    def from_kqf_config(cls, name, kqf: "KQF", kqf_config: "KQFConfig"):
        mcu = cls(name, kqf, kqf_config)
        mcu.set_from_kqf_mcu_config(kqf_config.mcus[name])
        return mcu

    def __init__(self, name, kqf: "KQF", kqf_config: "KQFConfig"):
        self.kqf = kqf
        self.parent = kqf_config
        self.name: str = name  # The name of the MCU in klipper's config
        self.communication_type: Optional[str] = (
            None  # Type of connection to the printer
        )
        self.communication_id: Optional[str] = None  # The immutable ID of the MCU.
        self.communication_device: Optional[str] = None
        self.communication_speed: Optional[str] = None
        self.mcu_type: Optional[str] = None  # The type of the mcu (e.g. stm32, rp2040)
        self.mcu_chip: Optional[str] = (
            None  # The specific chip the MCU uses, used to generate args for DFU-util
        )
        self.bootloader: Optional[str] = (
            None  # The name of the bootloader used, None indicates chip-specific
        )
        self.flash_method: Optional[str] = (
            None  # The method that will be used to flash this mcu
        )
        self.flash_opts: Dict[str, str] = (
            {}
        )  # This dict contains flash/bootloader specific options
        self.flavor: Optional[str] = (
            None  # The name of the config 'flavor' used, this is the name of the
        )

    def note(self, key, val, default=None):
        return self.kqf.note(f'mcu:{self.name}', key, val, default)

    def get_comms_id(self, dev, val):
        return self.note('communications_id', val)

    def self_extend(self):
        """
        Gather information either from the local system, or make educated guesses based on other values
        This should preferably be called exactly once, after all known values from configs have been chosen
        """
        # Read the machine type from the flavor
        flavor = self.kqf.flavor(self.flavor)
        flavor_mcu_type = flavor["CONFIG_BOARD_DIRECTORY"]
        flavor_mcu_chip = flavor["CONFIG_MCU"]
        if not self.mcu_type:
            if flavor_mcu_type:
                self.mcu_type = flavor_mcu_type
            else:
                logging.warning(
                    f"Could not determine machine type for flavor '{self.flavor}'"
                )
        # If the chip is unset, then make a decision based on kconfig and mcu type
        if not self.mcu_chip:
            if self.mcu_type in ["linux", "pru", "ar110", "simulator"]:
                self.mcu_chip = self.mcu_type
            elif self.mcu_type in [
                "stm32",
                "avr",
                "atsam",
                "atsamd",
                "lpc1768",
                "hc32f460",
                "rp2040",
            ]:
                if flavor_mcu_chip:
                    self.mcu_chip = flavor_mcu_chip
                else:
                    logging.warning(
                        f"Could not determine mcu type for flavor '{self.flavor}' (missing from config)"
                    )
            else:
                logging.warning(
                    f"Unable to automatically determine chip type for mcu '{self.name}'"
                    " - KQF may still function"
                )
        # If the canbus bitrate is not already known, guess from the interface
        if (
            self.communication_type == "can"
            and self.communication_device
            and not self.communication_speed
        ):
            self.communication_speed = self.kqf.can_baud(
                self.communication_device
            )
            if not self.communication_speed:
                logging.warning(
                    f"Unable to automatically determine can bitrate for interface {self.communication_speed} "
                    f'please add a "connection_speed" to the [{"mcu" if self.name == "mcu" else "mcu " + self.name}] '
                    "config section - KQF may still function - run with DEBUG for more info"
                )
            pass

        is_canbridge = flavor["CONFIG_USBCANBUS"] == "y"
        is_usbserial = flavor["CONFIG_USBSERIAL"] == "y"
        is_serial = flavor["CONFIG_SERIAL"] == "y"
        if not self.flash_method:
            if self.bootloader == "katapult":
                self.flash_method = "katapult"
            elif self.mcu_type == "linux":
                # Bare make with no extra options
                self.flash_method = "make"
            elif self.mcu_type == "rp2040" and is_usbserial:
                # Bare make with no extra options will call rpiboot
                self.flash_method = "make"
            elif self.mcu_type == "stm32" and is_usbserial:
                # stm32 DFU (note not all chips have it)
                # TODO: Determine ID
                self.flash_method = "make"
            else:
                # fallback flash method: Make with no args
                logging.warning(
                    "Was not able to determine a specific flash process, defaulting to make"
                )
                self.flash_method = "make"

        if self.flash_method == "katapult" and "mode" not in self.flash_opts:
            if (self.communication_type == "can" and is_canbridge) or (
                self.communication_type == "serial" and is_usbserial
            ):
                self.flash_opts["mode"] = "usb_serial"
            elif self.communication_type == "serial" and is_serial:
                self.flash_opts["mode"] = "uart"
            elif self.communication_type == "can":
                self.flash_opts["mode"] = "can"

        # If its katapult in canbridge mode, we need to figure out the usb_id, otherwise we won't know the serial path
        if self.flash_method == "katapult" and is_canbridge:
            sysfs_real_device = (
                pathlib.Path("/sys/class/net")
                / pathlib.Path(self.communication_device).name
            ).resolve()
            # The parent of the network device is the USB device that it comes from
            device_usb_serial = (sysfs_real_device.parent.parent.parent / 'serial').read_text().strip()
            self.flash_opts['usb_id'] = device_usb_serial

        if "entry_mode" not in self.flash_opts:
            if self.communication_type == "can":
                self.flash_opts["entry_mode"] = "can"
            elif self.communication_type == "serial" and is_usbserial:
                self.flash_opts["entry_mode"] = "usb_serial"
            elif self.communication_type == "serial" and is_serial:
                self.flash_opts["entry_mode"] = "serial"

        # Notebook fallbacks (these should be last)
        self.communication_id = self.get_comms_id(self.communication_device, self.communication_id)

    def set_from_kqf_mcu_config(self, kqf_config):
        if kqf_config.config_flavor:
            self.flavor = kqf_config.config_flavor
        if kqf_config.mcu_type:
            self.mcu_type = kqf_config.mcu_type
        if kqf_config.mcu_chip:
            self.mcu_chip = kqf_config.mcu_chip
        if kqf_config.communication_type:
            self.communication_type = kqf_config.communication_type
        if kqf_config.communication_id:
            self.communication_id = kqf_config.communication_id
        if kqf_config.communication_device:
            self.communication_device = kqf_config.communication_device
        if kqf_config.communication_speed:
            self.communication_speed = kqf_config.communication_speed
        if kqf_config.flash_method:
            self.flash_method = kqf_config.flash_method
        if kqf_config.bootloader:
            self.bootloader = kqf_config.bootloader
        self.flash_opts = {**self.flash_opts, **kqf_config.flash_opts}
        if len(kqf_config.flash_opts) > 0 and not kqf_config.flash_method:
            logging.warning(
                f"config: mcu '{self.name}': Flash options specified without specifying the method. This "
                f"is unsafe. Please specify method"
            )

    def pretty_format(self):
        if len(self.flash_opts) > 0:
            opt_listing = (os.linesep + " " * 4).join(
                [f"{opt}: '{self.flash_opts[opt]}'" for opt in self.flash_opts]
            )
            opt_str = os.linesep + " " * 4 + opt_listing
        else:
            opt_str = ""
        return f"""\
name:      '{self.name}'
flavor:    '{self.flavor}'
mcu:
  type:    '{self.mcu_type}'
  chip:    '{self.mcu_chip}'
comms:
  type:    '{self.communication_type}'
  id:      '{self.communication_id}'
  device:  '{self.communication_device}'
  speed:   '{self.communication_speed if self.communication_speed is not None else "N/A"}'
flashing:
  method:  '{self.flash_method}'
  options:{opt_str}
  loader:  '{self.bootloader}'
"""


class KQF(object):
    """
    Program state and other such
    """

    __INSTANCE = None

    def __init__(self, config_path: str, logger=logging.getLogger()):
        if not KQF.__INSTANCE:
            KQF.__INSTANCE = self
        self._logger = logger
        self._config_path = pathlib.Path(config_path).expanduser()
        # TODO Make this take a path
        self._config = KQFConfig.get(config_path)
        self._mcus = {
            s: KlipperMCU.from_kqf_config(s, self, self._config)
            for s in self._config.mcus.keys()
        }
        self._notebook = Notebook(self._config.config_flavors_path.parent / 'notebook.db')
        self._notebook.db_init()

    def note(self, ctx, key, val, default=None):
        return self._notebook.cache_filter(ctx, key, val, default)

    def can_baud(self, dev):
        return self.note(f"canif:{dev}", 'baud', get_can_interface_bitrate(dev))

    @classmethod
    def get(cls) -> "KQF":
        if not cls.__INSTANCE:
            raise ValueError("Tried to get KQF before it was initaialized")
        return cls.__INSTANCE

    def _log(self, *args, **kwargs):
        self._logger.log(*args, **kwargs)

    @property
    def logger(self):
        return self._logger

    @property
    def config(self):
        return self._config

    @property
    def config_path(self):
        return self._config_path

    def flavor_path(self, flavor: str) -> pathlib.Path:
        return (self._config.config_flavors_path / flavor).with_suffix(".conf")

    def dump_mcu_info(self, mcu_names: Optional[List[str]] = None):
        mcu_target_list = mcu_names if mcu_names else self._mcus
        mcu_info_log = logging.getLogger("kqf.mcu_info")
        mcu_info_log.setLevel(logging.INFO)
        mcu_info_log.log(
            logging.INFO,
            "\n"
            + "---\n".join([self._mcus[m].pretty_format() for m in mcu_target_list])
            + "---",
        )

    def inventory(self, self_extend: bool = True):
        if self._config.klipper_config:
            self._logger.debug("Loading MCU definitions from klipper configs")
            klipper_conf = KlipperConf(self._config.klipper_config)
            self._logger.debug(
                f"Loaded {len(klipper_conf.mcu_names())} MCUs definitions from Klipper: "
                f"[{', '.join(klipper_conf.mcu_names())}]"
            )
            self._logger.debug("Augmenting mcu configs from klipper config")
            for mcu_name in self._mcus.keys() & klipper_conf.mcu_names():
                self._logger.debug(f"Augmenting {mcu_name} with klipper config")
                klipper_conf.extend_mcu(self._mcus[mcu_name])
        if self_extend:
            for mcu in self._mcus:
                self._mcus[mcu].self_extend()

    def flavor_exists(self, flavor) -> bool:
        return self.flavor_path(flavor).is_file()

    def flavor(self, name: str, must_exist: bool = False):
        return KQFFlavor(self, self._config, name, must_exist=must_exist)

    def menuconfig(self, flavor: Union[str, "KQFFlavor"]) -> None:
        if isinstance(flavor, str):
            flavor = KQFFlavor(self, self.config, flavor)
            ctx = flavor
        elif isinstance(flavor, KQFFlavor):
            ctx = contextlib.nullcontext()
        else:
            raise ValueError("Invalid flavor")
        with ctx:
            subprocess.run(
                ["make", "clean", "menuconfig"],
                cwd=self._config.klipper_repo,
                check=True,
            )

    def build(self, flavor: Union[str, "KQFFlavor"]) -> bool:
        if isinstance(flavor, str):
            flavor = KQFFlavor(self, self.config, flavor)
            ctx = flavor
        elif isinstance(flavor, KQFFlavor):
            ctx = contextlib.nullcontext()
        else:
            raise ValueError("Invalid flavor")
        with ctx:
            # noinspection PyBroadException
            try:
                # Note: Klipper does not handle `make clean all` properly
                # TODO: Check if klipper implements distclean, and if that should be done instead
                subprocess.run(
                    ["make", "clean"], cwd=self._config.klipper_repo, check=True
                )
                subprocess.run(
                    ["make", "all"], cwd=self._config.klipper_repo, check=True
                )
                firmware_path = flavor.firmware_path(
                    "latest"
                )  # TODO: make this a parameter
                firmware_path.mkdir(parents=True, exist_ok=True)
                for f in (self._config.klipper_repo / "out").iterdir():
                    if f.stem == "klipper":
                        self.logger.debug(
                            f"Archiving firmware artifact {f.absolute()} to {(firmware_path / f.name).absolute()}"
                        )
                        shutil.copy(f, firmware_path / f.name)
                return True
            except Exception:
                self.logger.exception(f"An error occurred when building {flavor.name}")
                return False

    def flash(
        self, mcu: KlipperMCU, ver: str = "latest", permit_bootloader_entry: bool = True
    ) -> None:
        if not mcu.flash_method:
            raise ValueError(
                f"Flashing method for mcu {mcu.name} could not be automatically determined."
                "Please add it to the KQF config"
            )
        # Check that latest firmware exists
        flavor = KQFFlavor(self, self._config, mcu.flavor)
        fw_path = flavor.firmware_path(ver)
        if not fw_path.exists():
            raise ValueError(
                f'Firmware version "{ver}" does not exist for flavor "{flavor}" as required by mcu "{mcu.name}"'
            )
        if permit_bootloader_entry and ("entry_mode" in mcu.flash_opts):
            self.logger.debug(f"Preparing to enter bootloader on {mcu.name}")
            self.enter_bootloader(mcu)
        self.logger.info(
            f"Flashing {mcu.name} with flavor {flavor.name} to version {ver} with method {mcu.flash_method}"
        )
        if mcu.flash_method == "make":
            self.flash_make(mcu, ver)
        elif mcu.flash_method == "katapult":
            self.flash_katapult(mcu, ver)
        elif mcu.flash_method == "sdcard":
            self.flash_sdcard(mcu, ver)
        elif mcu.flash_method == "none":
            logging.info("NOOP - Flash mode 'none'")
        else:
            raise ValueError(
                f"Invalid flash method {mcu.flash_method} for mcu {mcu.name}"
            )
        pass

    def enter_bootloader(self, mcu: KlipperMCU) -> None:
        post_delay = 2
        entry_method = mcu.flash_opts.get("entry_mode")
        if entry_method == "usb_serial":
            # Open the serial port at 1200 baud, and send a DTR pulse
            serial_path = pathlib.Path(
                mcu.flash_opts.get(
                    "entry_serial",
                    f"/dev/serial/by-id/"
                    f'usb-{mcu.flash_opts.get("entry_usb_product", "Klipper")}_'
                    f'{mcu.mcu_chip}_{mcu.flash_opts.get("entry_usb_id", mcu.communication_id)}'
                    "-if00",
                )
            )
            if not serial_path.exists():
                raise ValueError(
                    f"Serial port {serial_path} does not exist for rebooting into bootloader"
                )

            with serial_path.open("ab+", buffering=0) as serial_port:
                delay = 0.1
                file_no = serial_port.fileno()
                attrs = termios.tcgetattr(file_no)
                self.logger.debug("Setting baud to 1200")
                attrs[4] = attrs[5] = termios.B1200
                self.logger.debug("Disabling automatic flow control")
                attrs[2] &= ~termios.CRTSCTS
                termios.tcsetattr(file_no, termios.TCSADRAIN, attrs)
                termios.tcdrain(file_no)
                time.sleep(0.250)  # Time to let the baud rate switch take effect
                serial_status = ctypes.c_int()
                fcntl.ioctl(file_no, termios.TIOCMGET, serial_status)
                dtr = struct.pack("I", termios.TIOCM_DTR)
                try:
                    if not serial_status.value & termios.TIOCM_DTR:
                        self.logger.debug("DTR OFF")
                        fcntl.ioctl(file_no, termios.TIOCMBIC, dtr)
                        termios.tcdrain(file_no)
                        time.sleep(delay)
                    # DTR is on at this point
                    self.logger.debug("DTR ON")
                    fcntl.ioctl(file_no, termios.TIOCMBIS, dtr)
                    termios.tcdrain(file_no)
                    time.sleep(delay)
                    # Turn DTR back on
                    self.logger.debug("DTR OFF")
                    fcntl.ioctl(file_no, termios.TIOCMBIC, dtr)
                    termios.tcdrain(file_no)
                    time.sleep(delay)
                    termios.tcdrain(file_no)
                except (BrokenPipeError, termios.error, OSError):
                    self.logger.debug(
                        "Device has disconnected, assuming reboot in progress"
                    )
                self.logger.debug("Waiting for reboot")
                time.sleep(post_delay)
        elif entry_method == "serial":
            serial_path = pathlib.Path(
                mcu.flash_opts.get(
                    "entry_serial",
                    mcu.flash_opts.get("serial", mcu.communication_device),
                )
            )
            # Get the constant from the termios module
            baud_c = getattr(
                termios,
                f'B{mcu.flash_opts.get("entry_baud", mcu.flash_opts.get("baud",mcu.communication_speed))}',
            )
            with serial_path.open("ab+", buffering=0) as serial_port:
                file_no = serial_port.fileno()
                old_attrs = termios.tcgetattr(file_no)
                attrs = old_attrs.copy()
                attrs[4] = attrs[5] = baud_c
                termios.tcsetattr(file_no, termios.TCSADRAIN, attrs)
                termios.tcdrain(file_no)

                serial_port.write(b"~ \x1c Request Serial Bootloader!! ~")
                termios.tcdrain(file_no)

                # Reset the terminal to old baud
                termios.tcsetattr(file_no, termios.TCSADRAIN, old_attrs)
                termios.tcdrain(file_no)
            time.sleep(post_delay)
        elif entry_method == "can":
            self._invoke_katapult(
                mcu.flash_opts,
                [
                    "-i",
                    mcu.flash_opts.get("interface", mcu.communication_device),
                    "-u",
                    mcu.flash_opts.get("uuid", mcu.communication_id),
                    "-r",
                ],
            )
            time.sleep(post_delay)
        elif entry_method == "noop":
            # Cases where bootloader entry is irrelevant, such as flash-sdcard
            pass
        else:
            raise ValueError(f"Unknown bootloader entry method: {entry_method}")

    def flash_make(self, mcu, ver: str) -> None:
        ignore_failure: Union[bool, str]
        if mcu.mcu_type == "stm32":
            ignore_failure = "Klipper misreports successes as failures when using dfu-util on some stm32 parts"
        else:
            ignore_failure = False
        flavor = KQFFlavor(self, self._config, mcu.flavor)
        opts = mcu.flash_opts
        with flavor:
            flavor.restore_artifacts(ver)
            if not (self._config.klipper_repo / "out" / "klipper.elf").exists():
                raise ValueError(
                    f"Previously-compiled klipper is missing klipper.elf for flavor {flavor}"
                )
            make_args = [
                "make",
                "--old-file=out/klipper.elf",  # Prevent klipper from rebuilding when flashing
                "--old-file=out/klipper.uf2",
            ]
            if opts.get("debug"):
                make_args.append("-d")
            make_args += [opts[var] for var in opts if var.startswith("var_")]
            make_args.append(opts.get("target", "flash"))
            try:
                subprocess.run(make_args, cwd=self._config.klipper_repo, check=True)
            except subprocess.CalledProcessError as e:
                if ignore_failure:
                    self.logger.warning(f"Ignoring flash failure: {ignore_failure}")
                else:
                    raise e

    KATAPULT_FLASHTOOL_URL = (
        "https://raw.githubusercontent.com/Arksine/katapult/master/scripts/flashtool.py"
    )

    def _ensure_katapult(self) -> pathlib.Path:
        flash_can_script = pathlib.Path("~/.kqf/lib/flashtool.py").expanduser()
        if not flash_can_script.exists():
            logging.debug(
                f"Downloaded katapult flashtool from {KQF.KATAPULT_FLASHTOOL_URL}"
            )
            flash_can_script.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(KQF.KATAPULT_FLASHTOOL_URL, flash_can_script)

        cur_mode = flash_can_script.stat().st_mode
        if not cur_mode & 0o111 == 0o111:
            logging.debug("Marked flashtool as excecutable")
            flash_can_script.chmod(cur_mode | 0o111)
        return flash_can_script

    def _invoke_katapult(self, opts, extra_args) -> None:
        environ = os.environ
        args = []
        interp: Optional[str] = None
        if "venv" in opts:
            venv_dir = pathlib.Path(opts["venv"]).expanduser()
            interp = str((venv_dir / "bin" / "python3").absolute())
            environ["VIRTUAL_ENV"] = str(venv_dir.absolute())
        if "interpreter" in opts:
            interp = args.append(opts["interpreter"])
        if interp:
            args.append(interp)
        args.append(self._ensure_katapult())
        args += extra_args
        subprocess.run(args, check=True)

    def flash_katapult(self, mcu: KlipperMCU, ver: str) -> None:
        opts = mcu.flash_opts

        katapult_mode = opts.get("mode", "can")
        args = []
        logging.debug(f"Katapult flash in {katapult_mode} mode")
        if katapult_mode == "can":
            args += [
                "-i",
                opts.get("interface", mcu.communication_device),
                "-u",
                opts.get("uuid", mcu.communication_id),
            ]
        elif katapult_mode == "usb_serial":
            args += [
                "-d",
                opts.get(
                    "serial",
                    f"/dev/serial/by-id/"
                    f'usb-{opts.get("usb_product", "katapult")}_'
                    f'{mcu.mcu_chip}_{opts.get("usb_id", mcu.communication_id)}-if00',
                ),
                "-b",
                str(opts.get("serial_baud", mcu.communication_speed)),
            ]
        elif katapult_mode == "uart":
            args += [
                "-d",
                opts.get("serial", mcu.communication_device),
                "-b",
                opts.get("serial_baud", mcu.communication_speed),
            ]
        else:
            raise ValueError(f"Katapult mode {katapult_mode} invalid.")

        if opts.get("verbose"):
            args.append(["-v"])

        flavor = KQFFlavor(self, self._config, mcu.flavor, True)
        logging.debug(f"Launching katapult flashtool: {args}")
        args += ["-f", flavor.firmware_path(ver) / "klipper.bin"]

        if "venv" not in opts and "interpreter" not in opts:
            # Make an educated guess
            standard_kippy_venv = pathlib.Path("~/klippy-env/").expanduser()
            if (standard_kippy_venv / "bin" / "python").exists():
                opts["venv"] = str(standard_kippy_venv)

        self._invoke_katapult(opts, args)

    def flash_sdcard(self, mcu: KlipperMCU, ver):
        flavor = KQFFlavor(self, self._config, mcu.flavor, True)
        flash_opts = mcu.flash_opts
        if "board" not in flash_opts:
            raise ValueError("Board not specified for sdcard flash")
        sdcard_board_config = flash_opts["board"]
        flash_sdcard_path = self._config.klipper_repo / "scripts" / "flash-sdcard.sh"
        if not flash_sdcard_path.is_file():
            raise ValueError(f"{flash_sdcard_path} is not a file")
        # Get the list of supported board definitions
        sdcard_list_board = subprocess.run(
            [flash_sdcard_path, "-l"], capture_output=True, check=True
        )
        supported_boards = []
        for output_line in sdcard_list_board.stdout.decode("utf-8").splitlines(False):
            if output_line == "Available Boards:":
                continue
            else:
                supported_boards.append(output_line.strip())
        if sdcard_board_config not in supported_boards:
            raise ValueError(
                f"{sdcard_board_config} is not supported by this version of Klipper flash-sdcard.sh"
            )
        flash_sdcard_args = [
            flash_sdcard_path,
            "-f",
            flavor.firmware_path(ver) / "klipper.bin",
            "-d",
            flavor.firmware_path(ver) / "klipper.dict",
            mcu.communication_device,
            sdcard_board_config,
        ]
        subprocess.run(flash_sdcard_args, check=True)

    def list_mcus(self) -> Iterable[str]:
        return self._mcus.keys()

    def get_mcu(self, mcu_name) -> Optional["KlipperMCU"]:
        try:
            return self._mcus[mcu_name]
        except KeyError:
            return None


class KQFFlavor(object):
    ACTIVE_FLAVOR = None

    @staticmethod
    def list_existing(kqf: KQF) -> List[str]:
        flavor_path = kqf.config.config_flavors_path
        if not flavor_path.is_dir():
            return []
        else:
            return [
                f.stem
                for f in flavor_path.iterdir()
                if f.is_file() and f.suffix == ".config"
            ]

    def __init__(
        self, kqf: KQF, kqf_config: KQFConfig, name: str, must_exist: bool = False
    ):
        self._parent = kqf
        self._flavor = name
        self._config = kqf_config
        self.__kconfig_vars: Optional[Dict[str, str]] = None
        self.__kconfig_path = self._config.klipper_repo / ".config"
        if must_exist and not self.exists():
            raise ValueError(
                f"kConfig for flavor '{name}' does not exist, try running 'menuconfig {name}'"
            )

    def exists(self) -> bool:
        return self.path.exists()

    @property
    def name(self) -> str:
        return self._flavor

    @property
    def path(self) -> pathlib.Path:
        return (self._config.config_flavors_path / self._flavor).with_suffix(".config")

    def __load_kconfig(self, force=False):
        if force:
            self.__kconfig_vars = None
        elif self.__kconfig_vars:
            return
        self.__kconfig_vars = {}
        with self.path.open("r") as conf_file:
            for line in conf_file.read().splitlines():
                if len(line) == 0:
                    continue
                if line[0] == "#":
                    continue
                segments = line.split("=", 1)
                if len(segments) < 2:
                    logging.debug(f"Unparsable line {line}")
                    continue
                self.__kconfig_vars[segments[0]] = segments[1]

    def get_kconfig_keys(self) -> List[str]:
        if self.exists():
            self.__load_kconfig()
            return list(self.__kconfig_vars.keys())
        return []

    def get_kconfig_var(self, key) -> Optional[str]:
        if not self.exists():
            return None
        self.__load_kconfig()
        val = self.__kconfig_vars.get(key)
        if val:
            val = val.strip('"')
        return val

    def firmware_path(self, ver: Optional[str] = None) -> pathlib.Path:
        if ver:
            return self._config.firmware_storage_path / self._flavor / ver
        else:
            return self._config.firmware_storage_path / self._flavor

    def list_firmware_versions(self) -> List[str]:
        if self.firmware_path().is_dir():
            return [
                f.name
                for f in self.firmware_path().iterdir()
                if (f / "klipper.dict").is_file()
            ]

    def __enter__(self):
        self._parent.logger.debug(f"activating flavor {self.name}")
        if KQFFlavor.ACTIVE_FLAVOR:
            if KQFFlavor.ACTIVE_FLAVOR == self:
                # This flavor is already active, so this is a no-op
                return
            else:
                raise RuntimeError(
                    "Tried to activate a flavor while one was still in use"
                )
        KQFFlavor.ACTIVE_FLAVOR = self

        if self.__kconfig_path.is_file():
            self._parent.logger.warning("kConfig file already exists")
            kconfig_modified_time = datetime.fromtimestamp(
                self.__kconfig_path.stat().st_mtime
            )
            kconfig_modified_time_slug = kconfig_modified_time.strftime("%Y%m%dT%H%M")
            kconfig_backup_time_slug = datetime.now().strftime("%Y%m%dT%H%M")
            backup_suffix = f"-mod_{kconfig_modified_time_slug}-saved_-{kconfig_backup_time_slug}-{os.getpid()}.bak"
            backup_path = self.__kconfig_path.with_name(
                self.__kconfig_path.name + backup_suffix
            )
            self._parent.logger.info(
                f"Renaming previous kConfig (last modified at {kconfig_modified_time_slug}:"
            )
            self._parent.logger.info(
                f"{self.__kconfig_path.absolute()} -> {backup_path.absolute()}"
            )
            shutil.move(self.__kconfig_path, backup_path)

        self._parent.logger.debug("cleaning workspace")
        subprocess.run(
            ["make", "clean"], cwd=self._config.klipper_repo, capture_output=True
        )
        if self.exists():
            self._parent.logger.debug(f"loading .config for flavor {self.name}")
            shutil.copy(self.path, self.__kconfig_path)
            self._parent.logger.debug("running olddefconfig")
            subprocess.run(
                ["make", "olddefconfig"],
                cwd=self._config.klipper_repo,
                capture_output=True,
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._config.config_flavors_path.exists():
            self._parent.logger.info(
                f"Created flavor directory at {self._config.config_flavors_path.absolute()}"
            )
            self._config.config_flavors_path.mkdir(exist_ok=True, parents=True)
        if self.__kconfig_path.exists():
            shutil.move(self.__kconfig_path, self.path)
            self._parent.logger.debug(f"Saved kConfig for flavor '{self._flavor}'")
        subprocess.run(
            ["make", "distclean"], cwd=self._config.klipper_repo, capture_output=True
        )
        KQFFlavor.ACTIVE_FLAVOR = None

    def __getitem__(self, item):
        return self.get_kconfig_var(item)

    def restore_artifacts(self, ver) -> None:
        for f in self.firmware_path(ver).iterdir():
            if f.stem == "klipper":
                self._parent.logger.debug(f"Restoring artifact {f}")
                shutil.copy(f, self._config.klipper_repo / "out" / f.name)
