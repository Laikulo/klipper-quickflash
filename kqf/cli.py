import argparse
import logging
import pathlib
import sys
import termios
from typing import Callable, Optional, List, Sequence

from . import config, util
from .kqf import KQF, KQFFlavor
from .updater import upgrade_kqf
from .util import launch_editor, get_license_text
from .version import KQF_VERSION, KQF_GITHASH, KQF_DATE


class KQFCli(object):
    __INSTANCE: Optional["KQFCli"] = None

    @classmethod
    def get(cls) -> "KQFCli":
        if not KQFCli.__INSTANCE:
            KQFCli.__INSTANCE = KQFCli()
        return KQFCli.__INSTANCE

    @classmethod
    def main(cls):
        return cls.get().entrypoint()

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
            f"Klipper QuickFlash v{KQF_VERSION} ({KQF_GITHASH}) by Laikulo - This KQF was packaged on {KQF_DATE}\n"
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
        except Exception as e:
            if args.postmortem:
                import pdb
                pdb.pm()
            else:
                throw e

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
            "--postmortem", action="store_true",
            help="Launch the postmortem debugger upon exceptions"
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

    @classmethod
    def kqf_command(cls, name, *args, **kwargs) -> Callable:
        def cmd_wrapper(fn: Callable):
            cli = cls.get()
            cli.add_command(KQFCommand(cli, name, fn, *args, **kwargs))

        return cmd_wrapper

    def _add_default_commands(self):
        self._wizard = KQFWizard(self)
        self.add_command(self._wizard)
        return


class KQFCommand(object):
    def __init__(
        self,
        cli: KQFCli,
        name: str,
        fn: Callable,
        needs_kqf=True,
        help_text: Optional[str] = None,
        args: Sequence["KQFArgBase"] = (),
    ):
        self.name = name
        self.action = fn
        self.needs_kqf: bool = needs_kqf
        self.help_text: Optional[str] = help_text
        self.args: Sequence["KQFArgBase"] = args

    def subparser(self, subparsers):
        sp = subparsers.add_parser(name=self.name, help=self.help_text)
        sp.set_defaults(cmd_obj=self)
        for arg in self.args:
            arg.add_to_sp(sp)


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
                    config_path.parent.mkdir(exist_ok=True)
                    with config_path.open("w") as config_data:
                        config_data.write(config.KQFConfig.DEFAULT)
                        config_data.close()
                elif config_generation_mode == "e":
                    config_path.mkdir(exist_ok=True)
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


class KQFArgBase(object):
    def add_to_sp(self, sp):
        raise NotImplementedError("Broken arg type")


class KQFArg(KQFArgBase):
    def __init__(self, *args, **kwags):
        self._opts = args
        self._kwopts = kwags

    def add_to_sp(self, sp):
        sp.add_argument(*self._opts, **self._kwopts)


class KQFMEGroup(KQFArgBase):
    def __init__(self, *args: KQFArgBase, **kwargs):
        self._children: List[KQFArgBase] = list(args)
        self._opts = kwargs

    def add_to_sp(self, sp):
        grp = sp.add_mutually_exclusive_group(**self._opts)
        for child in self._children:
            child.add_to_sp(grp)


@KQFCli.kqf_command(
    "mcu_info",
    help_text="Prints info about MCUs, for debugging",
    args=(
        KQFArg(
            "mcu_names",
            nargs="*",
            help="The mcu to print info about, defaults to all",
            metavar="MCU",
        ),
    ),
)
def cmd_dump_mcu(kqf, args):
    kqf.inventory()
    kqf.dump_mcu_info(mcu_names=args.mcu_names)


@KQFCli.kqf_command("d", help_text="Does some debuggigng thing", needs_kqf=True)
def cmd_d(kqf, _):
    flavor = KQFFlavor(kqf, kqf.config, "rp2040-zero-canbridge")
    print(flavor.get_kconfig_var("CONFIG_RP2040_HAVE_STAGE2"))
    return


@KQFCli.kqf_command(
    "upgrade",
    help_text="Upgrade KQF to a new version",
    args=(
        KQFArg(
            "--complete",
            nargs="?",
            dest="upgrade_script_completed",
            help=argparse.SUPPRESS,
        ),
        KQFArg(
            "--release",
            nargs="?",
            dest="target_release",
            help="Specify the release tag to upgrade to, defaults to the latest release",
        ),
        KQFArg(
            "--allow-prerelease",
            action="store_true",
            dest="allow_prerelease",
            help="Allow upgrading to prereleases",
        ),
    ),
)
def cmd_upgrade(_, args):
    if args.upgrade_script_completed:
        from .updater import complete_upgrade

        return complete_upgrade(args.upgrade_script_completed)
    else:
        upgrade_kqf(args.target_release, args.allow_prerelease)


@KQFCli.kqf_command(
    "menuconfig",
    help_text="Launch menuconfig for a flavor",
    args=(
        KQFArg(
            "flavor",
            metavar="FLAVOR",
            help="The flavor to run menuconfig for",
        ),
        KQFArg(
            "--build",
            action="store_true",
            default=False,
            help="Build firmware after configuring",
        ),
    ),
)
def cmd_menuconfig(kqf: "KQF", args):
    with KQFFlavor(kqf, kqf.config, args.flavor) as flavor:
        kqf.menuconfig(flavor)
        if args.build:
            kqf.build(flavor)


@KQFCli.kqf_command(
    "build",
    help_text="Build firmware for a flavor",
    args=(
        KQFMEGroup(
            KQFArg(
                "flavor",
                metavar="FLAVOR",
                nargs="?",
                help="The flavor to build for",
            ),
            KQFArg(
                "--all",
                dest="build_all",
                action="store_true",
                help="Build all flavors",
            ),
            required=True,
        ),
    ),
)
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


@KQFCli.kqf_command(
    "flash",
    help_text="Flash to a given MCU",
    args=(
        KQFArg("mcu", metavar="MCU", help="The MCU to flash", nargs="*"),
        KQFArg(
            "--all",
            dest="flash_all",
            action="store_true",
            help="Flash all",
        ),
        KQFArg(
            "--build",
            dest="build_before_flash",
            action="store_true",
            help="Build before flashing",
        ),
        KQFArg(
            "--skip-enter",
            action="store_true",
            dest="skip_bootloader_entry",
            help="Skip entering the bootloader (if configured)",
        ),
        KQFArg(
            "--service-control",
            action="store_true",
            dest="do_service_control",
            help="Stop and start klipper (if it is running) around flashing)",
        ),
    ),
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
                    f"Unable to build flavor {flavor}, aborting auto-build-and-flash"
                )
    if args.do_service_control:
        # First, we need to determine if klipper is running.
        # Ways we want to be able to detect/manage klipper
        #  - systemd service
        #  - redhat-style rc (var/subsys/lock
        #  - debian-style rc (pidfiles only)
        #  - busybox-style rc (Only K/S scripts, no status verb)
        #  - openrc service
        # Not going to bother with:
        #  - runsv
        #  - upstart
        #  - supervisord
        #  - klipper's provided start-klipper script (kill, reinvoke)
        raise NotImplementedError("Service Control Not Yet Implemented")
    for mcu_name in mcu_names_to_flash:
        mcu = kqf.get_mcu(mcu_name)
        if not mcu:
            raise ValueError(
                f"The MCU configuration '{mcu_name}' could not be found, check the KQF configuration"
            )
        kqf.flash(
            mcu, permit_bootloader_entry=False if args.skip_bootloader_entry else True
        )


@KQFCli.kqf_command(
    "license",
    needs_kqf=False,
    help_text="Print info about the license of KQF",
    args=(
        [
            KQFArg(
                "--text",
                action="store_true",
                help="Print the full text of the license, if available",
            )
        ]
    ),
)
def cmd_license(kqf: "KQF", args):
    if args.text:
        license_text = get_license_text()
        if license_text:
            print(license_text)
        else:
            print(
                "KQF could not load the license text, please see the FSF's website for a copy."
            )
    else:
        print(
            "KQF is free software: you can redistribute it and/or modify it under the terms of the "
            "GNU General Public License as published by the Free Software Foundation, version 3 of the License.\n"
            "This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;"
            " without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE."
            " See the GNU General Public License for more details.\n"
            "You should have received a copy of the GNU General Public License along with this program."
            " If not, see <https://www.gnu.org/licenses/>.\n\n"
            "KQF Contains a copy of the GPL3 text. To show the text, run this action with the --text argument"
            " you may want to pipe this to a pager, like `less'"
        )


@KQFCli.kqf_command(
    "configedit",
    help_text="Opens an editor to modify the KQF configuration",
)
def cmd_edit_config(kqf: KQF, args):
    launch_editor(kqf.config_path)
