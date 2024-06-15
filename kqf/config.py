# Configuration of KQF itself is handled here
import configparser
import logging
import re
from os import PathLike
from typing import Optional, TYPE_CHECKING
import pathlib
import textwrap

if TYPE_CHECKING:
    from .kqf import KlipperMCU


class KQFConfig(object):
    config_flavors_path: Optional[pathlib.Path]
    firmware_storage_path: Optional[pathlib.Path]
    _klipper_config: Optional[pathlib.Path]
    _klipper_config_path: Optional[PathLike]
    _klipper_config_auto: bool
    _klipper_repo: Optional[pathlib.Path]
    _klipper_repo_path: Optional[PathLike]
    _klipper_repo_auto: bool

    def __init__(self):
        self.config_flavors_path = None
        self.firmware_storage_path = None
        self._klipper_config = None
        self._klipper_config_path = None
        self._klipper_config_auto = False
        self._klipper_repo = None
        self._klipper_repo_path = None
        self._klipper_config_auto = False
        self._mcus = {}

    DEFAULT = textwrap.dedent(
        """\
        [KQF]
        # This is the configuration for Klipper Quick flash
        # Options in this section are for KQF itself
        # Options in other sections correspond to an mcu of that name
        # Note that section names are case sensitive

        # klipper_repo_path: Where to find the klipper repo that will be used to build firmware.
        #   the special value 'autodetect' will try a few common locations, and should work with KIAUH.
        klipper_repo_path: autodetect

        # klipper_config_path: The location of the klipper config to use when searching for MCUs
        #   The special value 'autodetect' attempts to automatically find the klipper config
        #   If this value is not present, MCUs will not be autodetected"
        klipper_config_path: autodetect

        # config_flavors_path: Path to a directory of klipper .config files, relative to the location of the script
        config_flavors_path: ~/.kqf/flavors

        # firmware_storage: The path of where to store compiled firmware
        firmware_storage: ~/.kqf/firmware

        [mcu]
        # place configuration for your primary MCU here

        #[mcu secondary_mcu]
        # place configuration for another MCU here, and uncomment the above.
        #   The section name should match the name in your printer.cfg (e.g. [mcu somemcu].
        """
    )

    @staticmethod
    def get(path="~/.kqf/kqf.cfg") -> "KQFConfig":
        logging.debug(f"Loading configuration from {path}")
        conf_path = pathlib.Path(path).expanduser()
        if not conf_path.exists():
            conf_path.parent.mkdir(parents=True, exist_ok=True)
            with conf_path.open(mode="w+") as conf_file:
                conf_file.write(KQFConfig.DEFAULT)
            logging.warning(
                f"KQF Configuration does not exist, it has been created at {path}"
            )
        kqf_conf_parser = configparser.ConfigParser()
        kqf_conf_parser.read(conf_path)
        kqf_conf = KQFConfig()
        kqf_conf.__load_from_conf(kqf_conf_parser)
        logging.debug(f"Loaded configuration from {path}")
        return kqf_conf

    def __load_from_conf(self, conf) -> None:
        if "KQF" not in conf.sections():
            raise ValueError(
                "KQF section is missing from the configuration. (It's case sensitive)"
            )
        kqf_section = conf["KQF"]

        repo_path_str = kqf_section.get("klipper_repo_path")
        if not repo_path_str:
            raise ValueError(
                "Klipper repo path is not specified in configuration. It is required"
            )
        if repo_path_str == "autodetect":
            self._klipper_repo_path = None
            self._klipper_repo_auto = True
        elif pathlib.Path(repo_path_str).expanduser().is_dir():
            self._klipper_repo_path = pathlib.Path(repo_path_str).expanduser()
            self._klipper_repo_auto = False
        else:
            raise ValueError(
                f"Klipper repo path {repo_path_str} is invalid or does not exist"
            )

        config_path_str = kqf_section.get("klipper_config_path")
        if not config_path_str:
            self._klipper_config_path = None
            self._klipper_config_auto = False
        elif config_path_str == "autodetect":
            self._klipper_config_path = None
            self._klipper_config_auto = True
        elif pathlib.Path(config_path_str).expanduser().is_file():
            self._klipper_config_path = pathlib.Path(config_path_str).expanduser()
            self._klipper_config_auto = False
        else:
            raise ValueError(
                f"Klipper repo path {repo_path_str} is invalid or does not exist"
            )

        self.config_flavors_path = pathlib.Path(
            kqf_section.get("config_flavors_path", "~/.kqf/flavors")
        ).expanduser()
        self.firmware_storage_path = pathlib.Path(
            kqf_section.get("firmware_storage_path", "~/.kqf/firmware")
        ).expanduser()
        self.mcus = {
            KQFMCUConfig.get_name_from_section_name(
                conf_section
            ): KQFMCUConfig.from_config(conf[conf_section])
            for conf_section in conf.sections()
            if conf_section == "mcu" or conf_section.startswith("mcu ")
        }

    @property
    def klipper_config(self) -> Optional[pathlib.Path]:
        if self._klipper_config:
            return self._klipper_config
        elif self._klipper_config_path:
            self._klipper_config = pathlib.Path(self._klipper_config_path)
            return self._klipper_config
        elif self._klipper_config_auto:
            config_path = self._find_klipper_config()
            if config_path:
                return config_path
            else:
                raise ValueError(
                    "Could not autodetect klipper config location. Please set klipper_config_path in the KQF"
                    "section of the KQF configuration"
                )
        else:
            # Klipper config parsing is disabled
            return None

    __CONFIG_PATHS_TO_TRY = [
        "~/printer_data/config/printer.cfg",
        "/etc/klipper/printer.cfg",
    ]

    def _find_klipper_config(self) -> Optional[pathlib.Path]:
        for path_to_try in self.__CONFIG_PATHS_TO_TRY:
            path = pathlib.Path(path_to_try).expanduser()
            if path.is_file():
                return path
        return None

    @property
    def klipper_repo(self) -> Optional[pathlib.Path]:
        if self._klipper_repo:
            return self._klipper_repo
        elif self._klipper_repo_path:
            self._klipper_repo = pathlib.Path(self._klipper_repo_path)
            return self._klipper_repo
        elif self._klipper_config_auto:
            repo_path = self._find_klipper_repo()
            if repo_path:
                return repo_path
            else:
                raise ValueError(
                    "Could not autodetect klipper config location. Please set klipper_config_path in the KQF"
                    "section of the KQF configuration"
                )
        else:
            # Klipper config parsing is disabled
            return None

    __REPO_PATHS_TO_TRY = ["~/klipper", "~/src/klipper", "~/vcs/klipper"]

    def _find_klipper_repo(self) -> pathlib.Path:
        for path_to_try in self.__REPO_PATHS_TO_TRY:
            path = pathlib.Path(path_to_try).expanduser()
            if (path / ".git").is_dir() and (path / "klippy" / "klippy.py").is_file():
                return path
        raise ValueError("Could not autodetect klipper config location")


class KQFMCUConfig(object):
    """
    This represents KQF configuration for a specific MCU it affects how KQF gathers info about the MCU
    For most MCUs, this should be mostly Nones
    """

    config_flavor: str
    mcu_type: Optional[str]  # Overrides detection from KConfig flavor
    mcu_chip: Optional[str]  # Overrides detection from KConfig flavor
    communication_type: Optional[str]  # Overrides value detected from klipper
    communication_id: Optional[str]  # Overrides value detected from klipper
    communication_device: Optional[str]  # Overrides value detected from klipper
    communication_speed: Optional[
        str
    ]  # Overrides value detected from system configration
    bootloader: Optional[
        str
    ]  # Indicates that a bootloader (other than the built-in DFU or picoboot) is present
    flash_method: Optional[
        str
    ]  # Overrides value guessed from mcu_type, communication_*, and bootloader
    flash_opts: Optional[str]  # Overrides individual values for flash configuration

    @staticmethod
    def get_name_from_section_name(in_str: str) -> str:
        if in_str == "mcu":
            return in_str
        elif in_str.startswith("mcu "):
            return in_str[4:]
        else:
            raise ValueError(f"Invalid MCU section name {in_str}")

    @classmethod
    def from_config(cls, config_section) -> "KQFMCUConfig":
        obj = KQFMCUConfig()
        obj.config_flavor = config_section.get("flavor")
        if not obj.config_flavor:
            logging.warning(f"There is no flavor defined for [{config_section.name}].")
        obj.mcu_type = config_section.get("mcu_type")
        obj.mcu_chip = config_section.get("mcu_chip")
        obj.communication_type = config_section.get("communication_type")
        obj.communication_id = config_section.get("communication_id")
        obj.communication_device = config_section.get("communication_device")
        obj.communication_speed = config_section.get("communication_speed")
        obj.bootloader = config_section.get("bootloader")
        obj.flash_method = config_section.get("flash_method")
        obj.flash_opts = {
            opt[6:]: config_section[opt]
            for opt in config_section
            if opt.startswith("flash_") and opt != "flash_method"
        }
        return obj


class KlipperConf(object):
    def __init__(self, filename):
        self.data = None
        self.data = configparser.ConfigParser(strict=False)
        self.data.read_file(IncludingConfigSource(filename))

        self.__mcu_sections = {
            (x if x == "mcu" else x[4:]): self.data[x]
            for x in self.data.sections()
            if x == "mcu" or x.startswith("mcu ")
        }

    def mcu_names(self):
        return self.__mcu_sections.keys()

    def extend_mcu(self, mcu: "KlipperMCU"):
        if mcu.name in self.mcu_names():
            mcu_conf = self.__mcu_sections[mcu.name]
            if "serial" in mcu_conf:
                mcu.communication_type = mcu.communication_type or "serial"
                # TODO Extract from udev
                mcu.communication_id = mcu.communication_id or mcu_conf.get("serial")
                mcu.communication_device = mcu.communication_device or mcu_conf.get(
                    "serial"
                )
                mcu.communication_speed = mcu.communication_speed or mcu_conf.get(
                    "baud", "250000"
                )
            elif "canbus_uuid" in mcu_conf:
                mcu.communication_type = mcu.communication_type or "can"
                mcu.communication_id = mcu.communication_id or mcu_conf.get(
                    "canbus_uuid"
                )
                mcu.communication_device = mcu.communication_device or mcu_conf.get(
                    "canbus_interface", "can0"
                )
        pass


class IncludingConfigSource(object):
    # The full path of all visited files, used to bail if the config is already included
    VISITED_FILES = []
    INCLUDE_RE = re.compile("\\[include (.*)]")

    def __init__(self, source_path, source_dir=None):
        # The file we are reading from. The position of this in the file is used to track ordering.
        self.__base_path = pathlib.Path(source_path)
        self.__base_file = self.__base_path.open("r")
        if source_dir:
            self.__source_dir = source_dir
        else:
            self.__source_dir = self.__base_path.parent
        # A child ICS
        self.__include_queue = []
        # A line buffer, used to allow us to compare with config file semantics
        self.__line_buffer = ""

    def get_line(self):
        # Gets a single line of config, with a terminating newline.
        # Returns None at end of file
        # If there is a queued child parser, but there is not one open
        for child_parser in self.__include_queue.copy():
            child_line = child_parser.get_line()
            if child_line is not None:
                return child_line
            else:
                self.__include_queue.remove(child_parser)

        config_line = self.__base_file.readline()
        if not config_line:
            # We have reached the end of the file
            return None
        # Check if current line is an include
        include_matches = IncludingConfigSource.INCLUDE_RE.match(config_line)
        if include_matches:
            # Variance from klipper behavior. Hidden files will match globs w/o leading dot
            include_spec = include_matches[1]
            paths_to_include = sorted(self.__base_path.parent.glob(include_spec))
            # This is a variance from klipper behavior. It allows globs to be empty
            if not paths_to_include:
                print(self.__base_path)
                print(include_spec)
                raise ValueError("Config file referenced does not exist")

            self.__include_queue += [
                IncludingConfigSource(path_to_include)
                for path_to_include in paths_to_include
            ]
            return self.get_line()
        else:
            return config_line

    def readable(self):
        return True

    def readline(self):
        line = self.get_line()
        if line is not None:
            return line
        else:
            return ""

    def readlines(self):
        return list(self)

    def __next__(self):
        line = self.get_line()
        if line is not None:
            return line
        else:
            raise StopIteration

    def __iter__(self):
        return self
