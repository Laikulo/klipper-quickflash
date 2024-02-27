import argparse
import logging
import sys
from typing import Callable

from .kqf import KQF, KQFFlavor


def entrypoint() -> None:
    if sys.version_info < (3, 7):
        logging.fatal("Python 3.7 or greater is required")
        sys.exit(1)

    logging.basicConfig()
    kqf_log = logging.getLogger("kqf")
    kqf_log.setLevel(logging.INFO)

    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true", help="Enable verbose output")
    ap.add_argument(
        "-c", metavar="CONFIG_FILE", help="Config file to use", default="~/.kqf/kqf.cfg"
    )
    ap.set_defaults(cmd_action=None)

    commands = ap.add_subparsers(metavar="ACTION", help="The action to perform")

    add_cmd(
        commands, "mcu_info", cmd_dump_mcu, help="Prints info about MCUs, for debugging"
    )

    menuconfig_cmd = add_cmd(
        commands, "menuconfig", cmd_menuconfig, help="Launch menuconfig for a flavor"
    )
    menuconfig_cmd.add_argument(
        "flavor", metavar="FLAVOR", help="The flavor to run menuconfig for"
    )
    menuconfig_cmd.add_argument(
        "--build",
        action="store_true",
        default=False,
        help="Build firmware after configuring",
    )

    build_cmd = add_cmd(
        commands, "build", cmd_build, help="Build firmware for a flavor"
    )
    build_flavor_spec = build_cmd.add_mutually_exclusive_group(required=True)
    build_flavor_spec.add_argument(
        "flavor", metavar="FLAVOR", help="The flavor to build firmware for", nargs="?"
    ),
    build_flavor_spec.add_argument(
        "--all", dest="build_all", action="store_true", help="Build all"
    )

    flash_cmd = add_cmd(commands, "flash", cmd_flash, help="Flash to a given MCU")
    flash_cmd.add_argument(
        "--all", dest="flash_all", action="store_true", help="Build all"
    )
    flash_cmd.add_argument("mcu", metavar="MCU", help="the mcu to flash", nargs="*"),
    flash_cmd.add_argument(
        "--build",
        dest="build_before_flash",
        action="store_true",
        help="Build firmware for mcus before flashing",
    )

    args = ap.parse_args()

    logging.basicConfig()
    if args.v:
        logging.getLogger().setLevel(logging.DEBUG)
        kqf_log.setLevel(logging.DEBUG)

    kqf = KQF(config_path=args.c, logger=kqf_log)

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
