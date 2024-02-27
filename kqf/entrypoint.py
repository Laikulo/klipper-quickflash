import os
import sys

MAX_PY_3 = 15

PY_3_NAMES = ["python3", "py3", "python-3"]

PY_NEWER_MINOR_FORMATS = ["py3{}", "python3{}", "python3.{}", "python3-{}"]


def entrypoint(*args, **kwargs):
    if sys.version_info >= (3, 7):
        go()
    elif sys.version_info[0] == 3:
        sys.stderr.write(
            "KQF requires python 3.7 or greater, looking for a newer one to switch to...\n",
        )
        reinvoke(find_newer_py3())
    elif sys.version_info[0] == 2:
        # We are in python 2, so let's try to find a python 3
        sys.stderr.write("KQF requires python 3, looking for one to switch to...\n")
        reinvoke(find_py3())
    else:
        sys.stderr.write(
            "KQF was unable to determine your python version, continuing anyway.\nThings may behave poorly.\n"
            "Unless python 4 has been released, this is a bug, and should be reported at "
            "https://github.com/laikulo/klipper-quickflash/issues\n"
        )
        go()


def find_py3():
    for name in PY_3_NAMES:
        interp_name = which(name)
        if interp_name:
            return interp_name
    die("Could not find a python3 to switch to. Please install python 3.7 or greater")


def find_newer_py3():
    for version in range(MAX_PY_3, sys.version_info[1] + 1, -1):
        for name_format in PY_NEWER_MINOR_FORMATS:
            interp_name = name_format.format(version)
            interp_path = which(interp_name)
            if interp_path:
                return interp_path
    die("Could not find a newer python3. Please install python 3.7 or newer")


def reinvoke(interpreter):
    sys.stderr.write("Switching to newer python at " + interpreter + "\n")
    from sys import argv

    try:
        os.execv(interpreter, [interpreter] + argv)
    # This python is no longer executing, if we are still here, execv failed
    except Exception:
        die("Unable to switch to " + interpreter)


def die(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(2)


def which(name):
    path_members = os.environ.get("PATH").split(":")
    for i in path_members:
        full_name = i + "/" + name
        if os.path.isfile(full_name):
            return full_name
    return None


def go():
    from .cli import entrypoint as real_entrypoint

    real_entrypoint()


if __name__ == "__main__":
    entrypoint()
