#!/usr/bin/env python3
import argparse
import configparser
import contextlib
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from os import PathLike
from typing import Optional, Callable, Union


# Klipper Quick Flash
# This tool uses information gathered from a klipper config file, as well as from its own config
# to automate building and flashing MCUs with klipper.

def entrypoint() -> None:
    if sys.version_info < (3, 7):
        logging.fatal("Python 3.7 or greater is required")
        sys.exit(1)

    logging.basicConfig()
    kqf_log = logging.getLogger('kqf')
    kqf_log.setLevel(logging.INFO)

    ap = argparse.ArgumentParser()
    ap.add_argument('-v', action='store_true', help="Enable verbose output")
    ap.add_argument("-c", metavar="CONFIG_FILE", help="Config file to use", default="~/.kqf/kqf.cfg")
    ap.set_defaults(cmd_action=None)

    commands = ap.add_subparsers(metavar='ACTION', help="The action to perform")

    add_cmd(commands, 'dump_mcu', cmd_dump_mcu, help="Prints info about MCUs, for debugging")

    menuconfig_cmd = add_cmd(commands, 'menuconfig', cmd_menuconfig, help="Launch menuconfig for a flavor")
    menuconfig_cmd.add_argument('flavor', metavar='FLAVOR', help="The flavor to run menuconfig for")
    menuconfig_cmd.add_argument('--build', action='store_true', default=False, help="Build firmware after configuring")

    build_cmd = add_cmd(commands, 'build', cmd_build, help="Build firmware for a flavor")
    build_flavor_spec = build_cmd.add_mutually_exclusive_group(required=True)
    build_flavor_spec.add_argument('flavor', metavar='FLAVOR', help="The flavor to build firmware for", nargs='?'),
    build_flavor_spec.add_argument('--all', dest='build_all', action='store_true', help="Build all")

    args = ap.parse_args()

    logging.basicConfig()
    if args.v:
        logging.getLogger().setLevel(logging.DEBUG)
        kqf_log.setLevel(logging.DEBUG)

    kqf = KQF(config_path=args.c)

    if args.cmd_action:
        args.cmd_action(kqf, args)
    else:
        logging.fatal("No action was specified, kqf will now exit")
        ap.print_help()


def add_cmd(sp, name: str, act: Callable, *args, **kwargs):
    command = sp.add_parser(name=name, *args, **kwargs)
    command.set_defaults(cmd_action=act)
    return command


def cmd_dump_mcu(kqf, _):
    kqf.inventory()
    kqf.dump_mcu_info()


def cmd_menuconfig(kqf: 'KQF', args):
    with KQFFlavor(kqf, kqf.config, args.flavor) as flavor:
        kqf.menuconfig(flavor)
        if args.build:
            kqf.build(flavor)


def cmd_build(kqf: 'KQF', args):
    if args.build_all:
        # TODO get list of all flavors
        flavors = set(KQFFlavor.list_existing(kqf))
    else:
        flavors = {args.flavor}
    flavor_success = set()
    for flavor in flavors:
        if kqf.build(flavor):
            flavor_success.add(flavor)
    print(
            f"Successful Flavors: {','.join(flavor_success)}\n"
            f"Failed Flavors: {','.join(flavors - flavor_success)}")


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
        flavor_mcu_type = None
        flavor_mcu_chip = None
        if flavor_path.is_file():
            with flavor_path.open("r") as flavor_file:
                for line in flavor_file.readlines():
                    if flavor_mcu_type and flavor_mcu_chip:
                        break
                    mach_matches = KlipperMCU.RE_MACHINE_TYPE.match(line)
                    if mach_matches:
                        flavor_mcu_type = mach_matches[1]
                        continue
                    type_matches = KlipperMCU.RE_MCU.match(line)
                    if type_matches:
                        flavor_mcu_chip = type_matches[1]
                        continue
        # TODO: Gather all notable info about the flavor here, so we don't churn the file
        if not self.mcu_type:
            if flavor_mcu_type:
                self.mcu_type = flavor_mcu_type
            else:
                logging.warning(f"Could not determine machine type for flavor '{self.flavor}'")
        # If the chip is unset, then make a decision based on kconfig and mcu type
        if not self.mcu_chip:
            if self.mcu_type in ['linux', 'pru', 'ar110', 'simulator']:
                self.mcu_chip = self.mcu_type
            elif self.mcu_type in ['stm32', 'avr', 'atsam', 'atsamd', 'lpc1768', 'hc32f460', 'rp2040']:
                if flavor_mcu_chip:
                    self.mcu_chip = flavor_mcu_chip
                else:
                    logging.warning(f"Could not determine mcu type for flavor '{self.flavor}' (missing from config)")
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
            logging.warning(f"There is no flavor defined for [{config_section.name}].")
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
        
        #[mcu secondary_mcu]
        # place configuration for another MCU here, and uncomment the above.
        #   The section name should match the name in your printer.cfg (e.g. [mcu somemcu].
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


class KQF(object):
    """
    Program state and other such
    """

    def __init__(self, config_path: str, logger=logging.getLogger()):
        self._logger = logger
        self._config = KQFConfig.get(config_path)
        self._mcus = {s: KlipperMCU.from_kqf_config(s, self._config) for s in self._config.mcus.keys()}

    def _log(self, *args, **kwargs):
        self._logger.log(*args, **kwargs)

    @property
    def logger(self):
        return self._logger

    @property
    def config(self):
        return self._config

    def flavor_path(self, flavor: str) -> pathlib.Path:
        return (self._config.config_flavors_path / flavor).with_suffix('.conf')

    def dump_mcu_info(self):
        mcu_info_log = logging.getLogger('kqf.mcu_info')
        mcu_info_log.setLevel(logging.INFO)
        mcu_info_log.log(logging.INFO,
                         "\n" + "---\n".join([self._mcus[m].pretty_format() for m in self._mcus]) + "---")

    def inventory(self, self_extend: bool = True):
        if self._config.klipper_config:
            self._logger.debug("Loading MCU definitions from klipper configs")
            klipper_conf = KlipperConf(self._config.klipper_config)
            self._logger.debug(
                f"Loaded {len(klipper_conf.mcu_names())} MCUs definitions from Klipper: "
                f"[{', '.join(klipper_conf.mcu_names())}]")
            self._logger.debug('Augmenting mcu configs from klipper config')
            for mcu_name in self._mcus.keys() & klipper_conf.mcu_names():
                self._logger.debug(f'Augmenting {mcu_name} with klipper config')
                klipper_conf.extend_mcu(self._mcus[mcu_name])
        if self_extend:
            for mcu in self._mcus:
                self._mcus[mcu].self_extend()

    def flavor_exists(self, flavor):
        return self.flavor_path(flavor).is_file()

    def menuconfig(self, flavor: Union[str, 'KQFFlavor']):
        if isinstance(flavor, str):
            flavor = KQFFlavor(self, self.config, flavor)
            ctx = flavor
        elif isinstance(flavor, KQFFlavor):
            ctx = contextlib.nullcontext()
        else:
            raise ValueError("Invalid flavor")
        with ctx:
            subprocess.run(['make', 'clean', 'menuconfig'], cwd=self._config.klipper_repo, check=True)

    def build(self, flavor: Union[str, 'KQFFlavor']) -> bool:
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
                subprocess.run(['make', 'clean'], cwd=self._config.klipper_repo, check=True)
                subprocess.run(['make', 'all'], cwd=self._config.klipper_repo, check=True)
                return True
            except Exception:
                self.logger.exception(f'An error occurred when building {flavor.name}')
                return False



class KQFFlavor(object):
    ACTIVE_FLAVOR = None

    @staticmethod
    def list_existing(kqf: KQF):
        flavor_path = kqf.config.config_flavors_path
        if not flavor_path.is_dir():
            return []
        else:
            return [f.stem for f in flavor_path.iterdir() if f.is_file() and f.suffix == ".config"]

    def __init__(self, kqf: KQF, kqf_config: KQFConfig, name: str, must_exist: bool = False):
        self._parent = kqf
        self._flavor = name
        self._config = kqf_config
        self.__kconfig_path = self._config.klipper_repo / '.config'
        if must_exist and not self.exists():
            raise ValueError(f"kConfig for flavor '{name}' does not exist, try running 'menuconfig {name}'")

    def exists(self) -> bool:
        return self.path.exists()

    @property
    def name(self) -> str:
        return self._flavor

    @property
    def path(self) -> pathlib.Path:
        return (self._config.config_flavors_path / self._flavor).with_suffix('.config')

    def __enter__(self):
        if KQFFlavor.ACTIVE_FLAVOR:
            if KQFFlavor.ACTIVE_FLAVOR == self:
                # This flavor is already active, so this is a no-op
                return
            else:
                raise RuntimeError("Tried to activate a flavor while one was still in use")
        KQFFlavor.ACTIVE_FLAVOR = self

        if self.__kconfig_path.is_file():
            self._parent.logger.warning("kConfig file already exists")
            kconfig_modified_time = datetime.fromtimestamp(self.__kconfig_path.stat().st_mtime)
            kconfig_modified_time_slug = kconfig_modified_time.strftime("%Y%m%dT%H%M")
            kconfig_backup_time_slug = datetime.now().strftime("%Y%m%dT%H%M")
            backup_suffix = f'-mod_{kconfig_modified_time_slug}-saved_-{kconfig_backup_time_slug}-{os.getpid()}.bak'
            backup_path = self.__kconfig_path.with_name(self.__kconfig_path.name + backup_suffix)
            self._parent.logger.info(f"Renaming previous kConfig (last modified at {kconfig_modified_time_slug}:")
            self._parent.logger.info(f"{self.__kconfig_path.absolute()} -> {backup_path.absolute()}")
            shutil.move(self.__kconfig_path, backup_path)

        if self.exists():
            shutil.copy(self.path, self.__kconfig_path)
            subprocess.run(['make', 'olddefconfig'], cwd=self._config.klipper_repo)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._config.config_flavors_path.exists():
            self._parent.logger.info(f"Created flavor directory at {self._config.config_flavors_path.absolute()}")
            self._config.config_flavors_path.mkdir(exist_ok=True, parents=True)
        if self.__kconfig_path.exists():
            shutil.move(self.__kconfig_path, self.path)
            self._parent.logger.info(f"Saved kConfig for flavor '{self._flavor}'")
        KQFFlavor.ACTIVE_FLAVOR = None


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
    entrypoint()
