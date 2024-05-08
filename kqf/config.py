# Configuration of KQF itself is handled here
import configparser
import logging
from os import PathLike
from typing import Optional
import pathlib
import textwrap
import re

from .klipper import KlipperMCU


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
    def get(path="~/.kqf/kqf.cfg") -> 'KQFConfig':
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
            KlipperMCU.get_name_from_section_name(
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
    pass

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
