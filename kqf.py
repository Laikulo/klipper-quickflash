#!/usr/bin/env python3
import configparser
import logging
import pathlib
import textwrap
from typing import Optional, List, Union
from pprint import pprint as pp
from os import PathLike


# Klipper Quick Flash
# This tool uses information gathered from a klipper config file, as well as from its own config
# to automate building and flashing MCUs with klipper.

def process() -> None:
    """
    The main entrypoint for kqf.
    :return: None
    """
    logging.getLogger().setLevel(logging.DEBUG)
    kqf_conf = KQFConfig.get(path="test/kqf.cfg")
    if kqf_conf.klipper_config:
        logging.debug("Loading MCU definitions from klipper configs")
        klipper_conf = KlipperConf(kqf_conf.klipper_config)
        logging.debug(
                        f"Loaded {len(klipper_conf.mcu_names())} MCUs definitions from Klipper: "
                        f"[{', '.join(klipper_conf.mcu_names())}]")


class KlipperConf(object):
    def __init__(self, filename):
        self.data = None
        self.data = configparser.ConfigParser()
        self.data.read(filename)

        self.__mcu_sections = {(x if x == 'mcu' else x[4:]): self.data[x] for x in self.data.sections() if
                               x == 'mcu' or x.startswith('mcu ')}

    def mcu_names(self):
        return self.__mcu_sections.keys()


class KlipperMCU(object):
    """
    A representation of an individual MCU
    """

    @staticmethod
    def get_from_printer_cfg(filename: PathLike):
        config = configparser.ConfigParser()
        # TODO: handle the import section
        config.read(filename)
        mcu_sections = [x for x in config.sections() if x == 'mcu' or x.startswith('mcu ')]
        return [KlipperMCU.from_cfg_section(x, config[x]) for x in mcu_sections]

    @staticmethod
    def from_cfg_section(section_name, cfg_section):
        if section_name == 'mcu':
            mcu = KlipperMCU('mcu')
        elif section_name.startswith('mcu '):
            mcu = KlipperMCU(section_name[4:])
        else:
            raise ValueError(f"mcu cfg section with invalid name {section_name}")
        mcu.from_klipper_config(cfg_section)
        return mcu

    def __init__(self, name):
        self.name: str = name  # The name of the MCU in klipper's config
        self.connection_type: str  # Type of connection to the printer
        self.connection_id: str  # The immutable ID of the MCU.
        # Can = can uuid
        # usb = serial number (not full path)
        # tty = device name
        self.mcu_type: str  # The type of the mcu (e.g. stm32, rp2040)
        self.mcu_chip: Optional[str]  # The specific chip the MCU uses, used to generate args for DFU-util
        self.bootloader: Optional[str]  # The name of the bootloader used, None indicates chip-specific
        self.flash_method: str  # The method that will be used to flash this mcu
        self.flavor: str  # The name of the config 'flavor' used, this is the name of the

    def from_klipper_config(self, config_block):
        if 'serial' in config_block:
            self.connection_type = 'serial'
            self.connection_id = config_block['serial']
            # TODO Use udev stuff to determine where this serial port comes from
        elif 'can_uuid' in config_block:
            self.connection_type = 'can'
            self.connection_id = config_block['can_uuid']
        else:
            raise ValueError(f"Unable to determine MCU info for mcu '{self.name}'")


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
    bootloader: Optional[str]  # Indicates that a bootloader (other than the built-in DFU or picoboot) is present
    flash_method: Optional[str]  # Overrides value guessed from mcu_type, communication_*, and bootloader
    pass

    @classmethod
    def from_config(cls, config_section):
        obj = KQFMCUConfig()
        obj.config_flavor = config_section.get('flavor')
        if not obj.config_flavor:
            raise ValueError("MCU firmware flavor not specified, try making one with 'kqf menuconfig'")
        obj.mcu_type = config_section.get('mcu_type')
        obj.mcu_chip = config_section.get('mcu_chip')
        obj.communication_type = config_section.get('communication_type')
        obj.communication_id = config_section.get('communication_id')
        obj.bootloader = config_section.get('bootloader')
        obj.flash_method = config_section.get('flash_method')
        pass


class KQFConfig(object):
    config_flavors_path: Optional[pathlib.Path]
    firmware_storage_path: Optional[pathlib.Path]
    _klipper_config: Optional[pathlib.Path]
    _klipper_config_path: Optional[PathLike]
    _klipper_config_auto: bool
    _klipper_repo: Optional[pathlib.Path]
    _klipper_repo_path: Optional[PathLike]
    _klipper_repo_auto: bool

    mcus: List[KQFMCUConfig]

    def __init__(self):
        self.config_flavors_path = None
        self.firmware_storage_path = None
        self._klipper_config = None
        self._klipper_config_path = None
        self._klipper_config_auto = False
        self._klipper_repo = None
        self._klipper_repo_path = None
        self._klipper_config_auto = False

    DEFAULT = textwrap.dedent("""\
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
        
        #[secondary_mcu]
        # place configuration for another MCU here, and uncomment the above.
        #   The section name should match the name in your printer.cfg. ([mcu nameHere] = [nameHere])
        """)

    @staticmethod
    def get(path="~/.kqf/kqf.cfg"):
        logging.debug(f"Loading configuration from {path}")
        conf_path = pathlib.Path(path).expanduser()
        if not conf_path.exists():
            conf_path.parent.mkdir(parents=True, exist_ok=True)
            with conf_path.open(mode="w+") as conf_file:
                conf_file.write(KQFConfig.DEFAULT)
            logging.warning(f"KQF Configuration does not exist, it has been created at {path}")
        kqf_conf_parser = configparser.ConfigParser()
        kqf_conf_parser.read(conf_path)
        kqf_conf = KQFConfig()
        kqf_conf.__load_from_conf(kqf_conf_parser)
        logging.debug(f"Loaded configuration from {path}")
        return kqf_conf

    def __load_from_conf(self, conf):
        if 'KQF' not in conf.sections():
            raise ValueError("KQF section is missing from the configuration. (It's case sensitive)")

        repo_path_str = conf.get('KQF', 'klipper_repo_path')
        if not repo_path_str:
            raise ValueError("Klipper repo path is not specified in configuration. It is required")
        if repo_path_str == 'autodetect':
            self._klipper_repo_path = None
            self._klipper_repo_auto = True
        elif pathlib.Path(repo_path_str).expanduser().is_dir():
            self._klipper_repo_path = pathlib.Path(repo_path_str).expanduser()
            self._klipper_repo_auto = False
        else:
            raise ValueError(f"Klipper repo path {repo_path_str} is invalid or does not exist")

        config_path_str = conf.get('KQF', 'klipper_config_path')
        if not config_path_str:
            self._klipper_config_path = None
            self._klipper_config_auto = False
        elif config_path_str == 'autodetect':
            self._klipper_config_path = None
            self._klipper_config_auto = True
        elif pathlib.Path(config_path_str).expanduser().is_file():
            self._klipper_config_path = pathlib.Path(config_path_str).expanduser()
            self._klipper_config_auto = False
        else:
            raise ValueError(f"Klipper repo path {repo_path_str} is invalid or does not exist")
        mcus = [KQFMCUConfig.from_config(conf[conf_section]) for conf_section in conf.sections() if
                conf_section == 'mcu' or conf_section.startswith('mcu ')]

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
                    "section of the KQF configuration")
        else:
            # Klipper config parsing is disabled
            return None

    __CONFIG_PATHS_TO_TRY = [
        "~/printer_data/config/printer.cfg",
        "/etc/klipper/printer.cfg"
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
                    "section of the KQF configuration")
        else:
            # Klipper config parsing is disabled
            return None

    __REPO_PATHS_TO_TRY = [
        "~/klipper",
        "~/src/klipper",
        "~/vcs/klipper"
    ]

    def _find_klipper_repo(self) -> pathlib.Path:
        for path_to_try in self.__REPO_PATHS_TO_TRY:
            path = pathlib.Path(path_to_try).expanduser()
            if (path / ".git").is_dir() and (path / 'klippy' / 'klippy.py').is_file():
                return path
        raise ValueError(
            "Could not autodetect klipper config location")


if __name__ == '__main__':
    process()
