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
from datetime import datetime
from typing import Union, Optional

from .config import KQFConfig
from .klipper import KlipperConf, KlipperMCU


class KQF(object):
    """
    Program state and other such
    """

    def __init__(self, config_path: str, logger=logging.getLogger()):
        self._logger = logger
        self._config_path = pathlib.Path(config_path).expanduser()
        # TODO Make this take a path
        self._config = KQFConfig.get(config_path)
        self._mcus = {
            s: KlipperMCU.from_kqf_config(s, self._config)
            for s in self._config.mcus.keys()
        }

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

    def dump_mcu_info(self):
        mcu_info_log = logging.getLogger("kqf.mcu_info")
        mcu_info_log.setLevel(logging.INFO)
        mcu_info_log.log(
            logging.INFO,
            "\n"
            + "---\n".join([self._mcus[m].pretty_format() for m in self._mcus])
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

    def flavor_exists(self, flavor):
        return self.flavor_path(flavor).is_file()

    def menuconfig(self, flavor: Union[str, "KQFFlavor"]):
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
    ):
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
        elif mcu.flash_method == "none":
            logging.info("NOOP - Flash mode 'none'")
        else:
            raise ValueError(
                f"Invalid flash method {mcu.flash_method} for mcu {mcu.name}"
            )
        pass

    def enter_bootloader(self, mcu: KlipperMCU):
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
                post_delay = 2
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
        else:
            raise ValueError(f"Unknown bootloader entry method: {entry_method}")

    def flash_make(self, mcu, ver: str):
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

    def _ensure_katapult(self):
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

    def _invoke_katapult(self, opts, extra_args):
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

    def flash_katapult(self, mcu: KlipperMCU, ver: str):
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
                opts.get("serial_baud", mcu.communication_speed),
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
        self._invoke_katapult(opts, args)

    def list_mcus(self):
        return self._mcus.keys()

    def get_mcu(self, mcu_name):
        try:
            return self._mcus[mcu_name]
        except KeyError:
            return None


class KQFFlavor(object):
    ACTIVE_FLAVOR = None

    @staticmethod
    def list_existing(kqf: KQF):
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

    def firmware_path(self, ver: Optional[str] = None):
        if ver:
            return self._config.firmware_storage_path / self._flavor / ver
        else:
            return self._config.firmware_storage_path / self._flavor

    def list_firmware_versions(self):
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

    def restore_artifacts(self, ver):
        for f in self.firmware_path(ver).iterdir():
            if f.stem == "klipper":
                self._parent.logger.debug(f"Restoring artifact {f}")
                shutil.copy(f, self._config.klipper_repo / "out" / f.name)
