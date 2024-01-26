#!/usr/bin/env python3
import configparser
import json
import logging
import pathlib
import re
import subprocess
import sys
import textwrap
from os import PathLike
from typing import Optional


# Klipper Quick Flash
# This tool uses information gathered from a klipper config file, as well as from its own config
# to automate building and flashing MCUs with klipper.

def process() -> None:
    """
    The main entrypoint for kqf.
    :return: None
    """
    logging.getLogger().setLevel(logging.DEBUG)
    if sys.version_info < (3, 7):
        logging.fatal("Python 3.7 or greater is required")
        sys.exit(1)

    kqf_conf = KQFConfig.get(path="test/kqf.cfg")
    mcus = {s: KlipperMCU.from_kqf_config(s, kqf_conf) for s in kqf_conf.mcus.keys()}

    if kqf_conf.klipper_config:
        logging.debug("Loading MCU definitions from klipper configs")
        klipper_conf = KlipperConf(kqf_conf.klipper_config)
        logging.debug(
            f"Loaded {len(klipper_conf.mcu_names())} MCUs definitions from Klipper: "
            f"[{', '.join(klipper_conf.mcu_names())}]")
        logging.debug('Augmenting mcu configs from klipper config')
        for mcu_name in mcus.keys() & klipper_conf.mcu_names():
            logging.debug(f'Augmenting {mcu_name} with klipper config')
            mcu = mcus[mcu_name]
            klipper_conf.extend_mcu(mcu)
    for m in mcus:
        mcus[m].self_extend()
    logging.log(logging.INFO, "MCU info dump:\n---\n" + "---\n".join([mcus[m].pretty_format() for m in mcus]) + "---")


class KlipperConf(object):
    def __init__(self, filename):
        self.data = None
        self.data = configparser.ConfigParser()
        self.data.read(filename)

        self.__mcu_sections = {(x if x == 'mcu' else x[4:]): self.data[x] for x in self.data.sections() if
                               x == 'mcu' or x.startswith('mcu ')}

    def mcu_names(self):
        return self.__mcu_sections.keys()

    def extend_mcu(self, mcu: 'KlipperMCU'):
        if mcu.name in self.mcu_names():
            mcu_conf = self.__mcu_sections[mcu.name]
            if 'serial' in mcu_conf:
                mcu.communication_type = mcu.communication_type or 'serial'
                # TODO Extract from udev
                mcu.communication_id = mcu.communication_id or mcu_conf.get('serial')
                mcu.communication_device = mcu.communication_device or mcu_conf.get('serial')
                mcu.communication_speed = mcu.communication_speed or mcu_conf.get('baud', '250000')
            elif 'canbus_uuid' in mcu_conf:
                mcu.communication_type = mcu.communication_type or 'can'
                mcu.communication_id = mcu.communication_id or mcu_conf.get('canbus_uuid')
                mcu.communication_device = mcu.communication_device or mcu_conf.get('canbus_interface', 'can0')
        pass


class KlipperMCU(object):
    """
    A representation of an individual MCU
    """

    @staticmethod
    def get_name_from_section_name(in_str: str) -> str:
        if in_str == 'mcu':
            return in_str
        elif in_str.startswith('mcu '):
            return in_str[4:]
        else:
            raise ValueError(f"Invalid MCU section name {in_str}")

    @classmethod
    def from_kqf_config(cls, name, kqf_config: 'KQFConfig'):
        mcu = cls(name, kqf_config)
        mcu.set_from_kqf_mcu_config(kqf_config.mcus[name])
        return mcu

    def __init__(self, name, kqf_config: 'KQFConfig'):
        self.parent = kqf_config
        self.name: str = name  # The name of the MCU in klipper's config
        self.communication_type: Optional[str] = None  # Type of connection to the printer
        self.communication_id: Optional[str] = None  # The immutable ID of the MCU.
        self.communication_device: Optional[str] = None
        self.communication_speed: Optional[str] = None
        self.mcu_type: Optional[str] = None  # The type of the mcu (e.g. stm32, rp2040)
        self.mcu_chip: Optional[str] = None  # The specific chip the MCU uses, used to generate args for DFU-util
        self.bootloader: Optional[str] = None  # The name of the bootloader used, None indicates chip-specific
        self.flash_method: Optional[str] = None  # The method that will be used to flash this mcu
        self.flavor: Optional[str] = None  # The name of the config 'flavor' used, this is the name of the

    RE_MACHINE_TYPE = re.compile('^CONFIG_BOARD_DIRECTORY="([a-zA-Z0-9]+)"$')
    RE_MCU = re.compile('^CONFIG_MCU="([a-zA-Z0-9]+)"$')

    def self_extend(self):
        """
        Gather information either from the local system, or make educated guesses based on other values
        This should preferably be called exactly once, after all known values from configs have been chosen
        """
        # Read the machine type from the flavor
        flavor_path = (self.parent.config_flavors_path / self.flavor).with_suffix('.config').expanduser()
        # TODO: Gather all notable info about the flavor here, so we don't churn the file
        if not self.mcu_type and flavor_path.is_file():
            with flavor_path.open("r") as flavor_file:
                for line in flavor_file.readlines():
                    matches = KlipperMCU.RE_MACHINE_TYPE.match(line)
                    if matches:
                        self.mcu_type = matches[1]
                        break
            # No dice
            if not self.mcu_type:
                logging.warning(f"Could not determine machine type for flavor '{self.flavor}'")
        # If the chip is unset, then make a decision based on kconfig and mcu type
        if not self.mcu_chip and self.mcu_type:
            if self.mcu_type in ['linux']:
                self.mcu_chip = "N/A"
            elif self.mcu_type in ['stm32']:  # TODO: Check what other types use this same method
                with flavor_path.open("r") as flavor_file:
                    for line in flavor_file.readlines():
                        matches = KlipperMCU.RE_MCU.match(line)
                        if matches:
                            self.mcu_chip = matches[1]
                            break
                if not self.mcu_chip:
                    logging.warning(f"Could not determine mcu type for flavor '{self.flavor}'")
            else:
                logging.warning(
                                f"Unable to automatically determine chip type for mcu '{self.name}'"
                                " - KQF may still function")
        # If the canbus bitrate is not already known, guess from the interface
        if (
                self.communication_type == 'can' and
                self.communication_device and
                not self.communication_speed):
            self.communication_speed = get_can_interface_bitrate(self.communication_device)
            if not self.communication_speed:
                logging.warning(
                    f'Unable to automatically determine can bitrate for interface {self.communication_speed} '
                    f'please add a "connection_speed" to the [{"mcu" if self.name == "mcu" else "mcu " + self.name}] '
                    'config section - KQF may still function - run with DEBUG for more info')
            pass

        pass

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

    def pretty_format(self):
        return textwrap.dedent(f"""\
            name:     '{self.name}'
            flavor:   '{self.flavor}'
            mcu:
              type:   '{self.mcu_type}'
              chip:   '{self.mcu_chip}'
            comms:
              type:   '{self.communication_type}'
              id:     '{self.communication_id}'
              device: '{self.communication_device}'
              speed:  '{self.communication_speed if self.communication_speed is not None else "N/A"}'
            flashing:
              method: '{self.flash_method}'
              loader: '{self.bootloader}' 
        """)


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
    communication_speed: Optional[str]  # Overrides value detected from system configration
    bootloader: Optional[str]  # Indicates that a bootloader (other than the built-in DFU or picoboot) is present
    flash_method: Optional[str]  # Overrides value guessed from mcu_type, communication_*, and bootloader
    pass

    @classmethod
    def from_config(cls, config_section) -> 'KQFMCUConfig':
        obj = KQFMCUConfig()
        obj.config_flavor = config_section.get('flavor')
        if not obj.config_flavor:
            raise ValueError("MCU firmware flavor not specified, try making one with 'kqf menuconfig'")
        obj.mcu_type = config_section.get('mcu_type')
        obj.mcu_chip = config_section.get('mcu_chip')
        obj.communication_type = config_section.get('communication_type')
        obj.communication_id = config_section.get('communication_id')
        obj.communication_device = config_section.get('communication_device')
        obj.communication_speed = config_section.get('communication_speed')
        obj.bootloader = config_section.get('bootloader')
        obj.flash_method = config_section.get('flash_method')
        return obj


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
        kqf_section = conf['KQF']

        repo_path_str = kqf_section.get('klipper_repo_path')
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

        config_path_str = kqf_section.get('klipper_config_path')
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

        self.config_flavors_path = pathlib.Path(kqf_section.get('config_flavors_path', '~/.kqf/flavors')).expanduser()
        self.mcus = {KlipperMCU.get_name_from_section_name(conf_section): KQFMCUConfig.from_config(conf[conf_section])
                     for conf_section in conf.sections() if
                     conf_section == 'mcu' or conf_section.startswith('mcu ')}

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


def get_can_interface_bitrate(ifname: str) -> Optional[str]:
    # noinspection PyBroadException
    try:
        ipl = subprocess.run(['ip', '-details', '-json', 'link', 'show', ifname], capture_output=True, check=True)
        net_json = json.loads(ipl.stdout.decode('UTF-8'))
        bitrate = net_json[0]['linkinfo']['info_data']['bittiming']['bitrate']
    except Exception:
        logging.debug(f"Unable to determine bitrate for can interface {ifname}", exc_info=True)
        return None
    return bitrate


if __name__ == '__main__':
    process()
