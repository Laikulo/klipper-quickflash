import argparse
import logging
import pathlib
import sys
import termios
from typing import Callable, Optional, List, Dict

from . import config, util
from .kqf import KQF, KQFFlavor
from .util import launch_editor


class KQFCli(object):
    @classmethod
    def main(cls):
        return KQFCli().entrypoint()

    def __init__(self):
        self._commands = []
        self._setup_logging()
        self._setup_args()
        self._kqf: Optional[KQF] = None

    def entrypoint(self):
        args = self._argparse.parse_args()
        if args.v:
            self._logger.setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)
        print(
            "\n"
            "Klipper QuickFlash v0.0.0 (githash) by Laikulo - This KQF was packaged on DATE\n"
            "   KQF is free software distributed under the terms of the GPL3\n"
            "   Run kqf with the 'license' action for more information\n"
        )

        if "cmd_obj" in args:
            selected_cmd = args.cmd_obj
        else:
            self._logger.info("No action was specified, launching the wizard")
            selected_cmd = self._wizard

        if selected_cmd.needs_kqf:
            self._kqf = KQF(config_path=args.c, logger=self._logger)
        try:
            selected_cmd.action(self._kqf, args)
        except KeyboardInterrupt:
            logging.warning("Exiting due to Ctrl-C, Thanks for using KQF!")
        except NotImplementedError as e:
            if e.args:
                message = "Not Implemented - " + e.args[0]
            else:
                message = "Not Implemented"
            self._logger.fatal(
                message + "\n"
                "Sorry, but it appears you've reached a part of KQF that hasn't been written yet.\n"
                "If you encountered this in a released version of KQF, please let us know at\n"
                "https://github.com/laikulo/klipper-quickflash/issues"
            )

    def _setup_logging(self):
        logging.basicConfig()
        self._logger = logging.getLogger("kqf")
        self._logger.setLevel(logging.INFO)

    def _setup_args(self):
        self._argparse = argparse.ArgumentParser()
        self._argparse.add_argument(
            "-v", action="store_true", help="Enable verbose output"
        )
        self._argparse.add_argument(
            "-c",
            metavar="CONFIG_FILE",
            help="Config file to use",
            default="~/.kqf/kqf.cfg",
        )

        self._command_parser = self._argparse.add_subparsers(
            metavar="ACTION", help="The action to perform"
        )
        self._add_default_commands()

    def add_command(self, cmd: "KQFCommand"):
        cmd.subparser(self._command_parser)

    def add_commands(self, cmds: List["KQFCommand"]):
        for cmd in cmds:
            self.add_command(cmd)

    def _add_default_commands(self):
        self._wizard = KQFWizard(self)
        self.add_command(self._wizard)

        self.add_commands(
            [
                KQFCommand(
                    self,
                    "mcu_info",
                    cmd_dump_mcu,
                    help_text="Prints info about MCUs, for debugging",
                ),
                KQFCommand(
                    self,
                    "edit_config",
                    cmd_edit_config,
                    help_text="Launch an editor to edit the KQF config",
                ),
                KQFCommand(
                    self,
                    "configedit",
                    cmd_edit_config,
                    help_text="Opens an editor to modify the KQF configuration",
                ),
                KQFCommand(
                    self,
                    "menuconfig",
                    cmd_menuconfig,
                    args={
                        "flavor": {
                            "metavar": "FLAVOR",
                            "help": "The flavor to run menuconfig for",
                        },
                        "--build": {
                            'action': "store_true",
                            'default': False,
                            'help': "Build firmware after configuring"
                        }
                    },
                ),
                KQFCommand(
                    self,
                )
            ]
        )


class KQFCommand(object):
    def __init__(
        self,
        cli: KQFCli,
        name: str,
        fn: Callable,
        needs_kqf=True,
        help_text: Optional[str] = None,
        args: Dict[str, Dict[str, any]] = None,
    ):
        self.name = name
        self.action = fn
        self.needs_kqf: bool = needs_kqf
        self.help_text: Optional[str] = help_text
        self.args: Dict[str, Dict[str, any]] = {} if args is None else args

    def subparser(self, subparsers):
        sp = subparsers.add_parser(name=self.name, help=self.help_text)
        sp.set_defaults(cmd_obj=self)
        for arg in self.args:
            sp.add_argument(arg, **self.args[arg])


class KQFWizard(KQFCommand):
    def __init__(self, cli: KQFCli):
        super().__init__(
            cli,
            "wizard",
            self.begin,
            needs_kqf=False,
            help_text="Launch the KQF interactive wizard",
        )

    def begin(self, _, args):
        config_path = pathlib.Path(args.c).expanduser()
        if not config_path.exists():
            if (
                self.ask(
                    f"KQF's configuration file does not exist at {config_path}\n"
                    "Would you like to create it"
                )
                == "y"
            ):
                config_generation_mode = self.ask(
                    "Would you like to:\n"
                    " d) Start with the default configuration for KQF\n"
                    " e) Start with an empty configuration file\n"
                    " i) Answer questions to generate a configuration\n"
                    "Select",
                    ["d", "e", "i"],
                )
                if config_generation_mode == "d":
                    with config_path.open("w") as config_data:
                        config_data.write(config.KQFConfig.DEFAULT)
                        config_data.close()
                elif config_generation_mode == "e":
                    with config_path.open("w") as config_data:
                        config_data.close()
                elif config_generation_mode == "i":
                    raise NotImplementedError("Configuration Interview")
                else:
                    raise RuntimeError("This should never happen")

                if self.ask("Would you like to open the config in an editor") == "y":
                    util.launch_editor(config_path)

            else:
                self.write(
                    "The KQF wizard requires a config file, KQF will now exit...\n"
                    "Hint: run KQF with the -h option to see other actions that may not require a config"
                )
                return
        raise NotImplementedError("Wizard Main menu")

    def ask(self, prompt: str, answers: List[str] = ("y", "n")):
        self.write(f'\n{prompt} ({"/".join(answers)}): ', nl=False)
        while True:
            ret = self.quietread(1).lower()
            if ret in answers:
                self.write(ret)
                return ret

    def quietread(self, length):
        stderr = sys.stderr.fileno()
        initial_termios = termios.tcgetattr(stderr)
        noecho_termios = initial_termios.copy()
        noecho_termios[3] &= ~termios.ECHO
        noecho_termios[3] &= ~termios.ICANON
        termios.tcflush(stderr, termios.TCIOFLUSH)
        termios.tcsetattr(stderr, termios.TCSAFLUSH, noecho_termios)
        termios.tcflush(stderr, termios.TCIOFLUSH)
        in_str = sys.stdin.read(length)
        termios.tcsetattr(stderr, termios.TCSAFLUSH, initial_termios)
        termios.tcflush(stderr, termios.TCIOFLUSH)
        return in_str

    def write(self, text: str, nl: bool = True):
        print(text, end="\n" if nl else "", file=sys.stderr, flush=True)


# def entrypoint() -> None:
#     add_cmd(
#         commands,
#         "configedit",
#         cmd_edit_config,
#         help="Opens an editor to modify the KQF configuration",
#     )
#
#     menuconfig_cmd = add_cmd(
#         commands, "menuconfig", cmd_menuconfig, help="Launch menuconfig for a flavor"
#     )
#     menuconfig_cmd.add_argument(
#         "flavor", metavar="FLAVOR", help="The flavor to run menuconfig for"
#     )
#     menuconfig_cmd.add_argument(
#         "--build",
#         action="store_true",
#         default=False,
#         help="Build firmware after configuring",
#     )
#
#     build_cmd = add_cmd(
#         commands, "build", cmd_build, help="Build firmware for a flavor"
#     )
#     build_flavor_spec = build_cmd.add_mutually_exclusive_group(required=True)
#     build_flavor_spec.add_argument(
#         "flavor", metavar="FLAVOR", help="The flavor to build firmware for", nargs="?"
#     ),
#     build_flavor_spec.add_argument(
#         "--all", dest="build_all", action="store_true", help="Build all"
#     )
#
#     flash_cmd = add_cmd(commands, "flash", cmd_flash, help="Flash to a given MCU")
#     flash_cmd.add_argument(
#         "--all", dest="flash_all", action="store_true", help="Build all"
#     )
#     flash_cmd.add_argument("mcu", metavar="MCU", help="the mcu to flash", nargs="*"),
#     flash_cmd.add_argument(
#         "--build",
#         dest="build_before_flash",
#         action="store_true",
#         help="Build firmware for mcus before flashing",
#     )
#
#     args = ap.parse_args()
#
#
#     if args.cmd_action:
#         args.cmd_action(kqf, args)
#     else:
#         logging.fatal("No action was specified, kqf will now exit")
#         ap.print_help()
#


def cmd_dump_mcu(kqf, _):
    kqf.inventory()
    kqf.dump_mcu_info()


def cmd_menuconfig(kqf: "KQF", args):
    with KQFFlavor(kqf, kqf.config, args.flavor) as flavor:
        kqf.menuconfig(flavor)
        if args.build:
            kqf.build(flavor)


def cmd_build(kqf: "KQF", args):
    if args.build_all:
        flavors = set(KQFFlavor.list_existing(kqf))
    else:
        flavors = {args.flavor}
    flavor_success = set()
    for flavor in flavors:
        if kqf.build(flavor):
            flavor_success.add(flavor)
    print(
        f"Successful Flavors: {','.join(flavor_success)}\n"
        f"Failed Flavors: {','.join(flavors - flavor_success)}"
    )


def cmd_flash(kqf: "KQF", args):
    if args.flash_all and len(args.mcu) > 0:
        raise ValueError("Both '--all' and a list of flavors may not be specified")
    kqf.inventory()
    if args.flash_all:
        mcu_names_to_flash = kqf.list_mcus()
    else:
        mcu_names_to_flash = args.mcu
    if not len(mcu_names_to_flash) > 0:
        raise ValueError("No mcus are specified for flashing")
    mcus_to_flash = [kqf.get_mcu(mcu_name) for mcu_name in mcu_names_to_flash]
    if args.build_before_flash:
        flavors_to_build = set(mcu.flavor for mcu in mcus_to_flash)
        kqf.logger.info(f"Building flavors: {flavors_to_build}")
        for flavor in flavors_to_build:
            flavor_success = kqf.build(flavor)
            if not flavor_success:
                raise RuntimeError(
                    "Unable to build flavor {flavor}, aborting auto-build-and-flash"
                )
    for mcu_name in mcu_names_to_flash:
        mcu = kqf.get_mcu(mcu_name)
        if not mcu:
            raise ValueError(
                f"The MCU configuration '{mcu_name}' could not be found, check the KQF configuration"
            )
        kqf.flash(mcu)


def cmd_edit_config(kqf: KQF, args):
    launch_editor(kqf.config_path)
