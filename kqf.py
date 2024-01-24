#!/usr/bin/env python3
import configparser
import logging
import pathlib
import textwrap
from typing import Optional, List
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
    logging.getLogger().setLevel(logging.INFO)
    for i in KlipperMCU.get_from_printer_cfg('test/test.printer.cfg'):
        print(f"Found {i.connection_type} mcu with id {i.connection_id}")
    pp(KQFConfig.get())


class KlipperMCU(object):
    """
    A representation of an individual MCU
    """
    @staticmethod
    def get_from_printer_cfg(filename: str):
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
        mcu.__from_klipper_config(cfg_section)
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

    def __from_klipper_config(self, config_block):
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
    For most mcus, this should be mosty Nones
    """
    config_flavor: str
    mcu_type: Optional[str]  # Overrides detection from KConfig flavor
    mcu_chip: Optional[str]  # Overrides detection from KConfig flavor
    communication_type: Optional[str]  # Overrides value detected from klipper
    communication_id: Optional[str]  # Overrides value detected from klipper
    bootloader: Optional[str]  # Indicates that a bootloader (other than the built-in DFU or picoboot) is present
    flash_method: Optional[str]  # Overrides value guessed from mcu_type, communication_*, and bootloader
    pass


class KQFConfig(object):
    config_flavors_path: PathLike
    firmware_storage_path: PathLike
    # Todo: also the string 'autodetect'
    klipper_config_path: Optional[PathLike]
    # Todo: also the string 'autodetect'
    klipper_repo_path: PathLike

    mcus: List[KQFMCUConfig]

    DEFAULT = textwrap.dedent("""\
        [KQF]
        # This is the configuration for Klipper Quick flash
        # Options in this section are for KQF itself
        # Options in other sections correspond to an mcu of that name
        # Note that section names are case sensitive
        
        # klipper_repo_path: Where to find the klipper repo that will be used to build firmware.
        #   the special value 'autodetect' will try a few common locations, and should work with KIAUH.
        klipper_repo_path: autodetect
        
        # klipper_config_path: The location of the klipper config to use when searching for mcus
        #   The special value 'autodetect' attempts to automatically find the klipper config
        #   If this value is not present, mcus will not be autodetected"
        klipper_config_path: autodetect
        
        # config_flavors_path: Path to a directory of klipper .config files, relative to the location of the script
        config_flavors_path: ./flavors
        
        # klipper_default_version: The default version of klipper to build
        # if none is specified, no pull will be performed
        #klipper_default_version:
        
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
        return kqf_conf

    def __load_from_conf(self, conf):
        pass


if __name__ == '__main__':
    process()
