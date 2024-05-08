import configparser
import logging
import os
import pathlib
import re
from typing import Optional, Dict, TYPE_CHECKING

from .util import get_can_interface_bitrate

if TYPE_CHECKING:
    from .config import KQFConfig


class KlipperConf(object):
    def __init__(self, filename):
        self.data = None
        self.data = configparser.ConfigParser()
        self.data.read_file(IncludingConfigSource(filename))
        print(self.data.sections())

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
    def from_kqf_config(cls, name, kqf_config: "KQFConfig"):
        mcu = cls(name, kqf_config)
        mcu.set_from_kqf_mcu_config(kqf_config.mcus[name])
        return mcu

    def __init__(self, name, kqf_config: "KQFConfig"):
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

    RE_MACHINE_TYPE = re.compile('^CONFIG_BOARD_DIRECTORY="([a-zA-Z0-9]+)"$')
    RE_MCU = re.compile('^CONFIG_MCU="([a-zA-Z0-9]+)"$')

    def self_extend(self):
        """
        Gather information either from the local system, or make educated guesses based on other values
        This should preferably be called exactly once, after all known values from configs have been chosen
        """
        # Read the machine type from the flavor
        flavor_path = (
            (self.parent.config_flavors_path / self.flavor)
            .with_suffix(".config")
            .expanduser()
        )
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
            self.communication_speed = get_can_interface_bitrate(
                self.communication_device
            )
            if not self.communication_speed:
                logging.warning(
                    f"Unable to automatically determine can bitrate for interface {self.communication_speed} "
                    f'please add a "connection_speed" to the [{"mcu" if self.name == "mcu" else "mcu " + self.name}] '
                    "config section - KQF may still function - run with DEBUG for more info"
                )
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
        self.flash_opts = {**self.flash_opts, **kqf_config.flash_opts}
        if len(kqf_config.flash_opts) > 0 and not kqf_config.flash_method:
            logging.warning(
                f"config: mcu '{self.name}': Flash options specified without specifying the method. This "
                f"is unsafe. Please specify method"
            )

    def pretty_format(self):
        if len(self.flash_opts) > 0:
            opt_listing = (os.linesep + " " * 4).join(
                [f"{opt}: {self.flash_opts[opt]}" for opt in self.flash_opts]
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

            self.__include_queue += [IncludingConfigSource(path_to_include) for path_to_include in paths_to_include]
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
            return ''

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

