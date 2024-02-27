import logging
import os
import pathlib
import shutil
import subprocess
import json
from typing import Optional


def get_can_interface_bitrate(ifname: str) -> Optional[str]:
    # noinspection PyBroadException
    try:
        ipl = subprocess.run(
            ["ip", "-details", "-json", "link", "show", ifname],
            capture_output=True,
            check=True,
        )
        net_json = json.loads(ipl.stdout.decode("UTF-8"))
        bitrate = net_json[0]["linkinfo"]["info_data"]["bittiming"]["bitrate"]
    except Exception:
        logging.debug(
            f"Unable to determine bitrate for can interface {ifname}", exc_info=True
        )
        return None
    return bitrate


def launch_editor(filename: os.PathLike, editor: Optional[str] = None) -> None:
    editor = editor or find_editor()
    subprocess.run([
        editor,
        filename
    ])


EDITOR_ENV_TO_TRY = ['KQF_EDITOR', 'VISUAL', 'EDITOR']
EDITORS_TO_TRY = ['sensible-editor', 'editor', 'vim', 'vi', 'emacs', 'nano', 'pico']


def find_editor() -> str:
    for i in EDITOR_ENV_TO_TRY:
        if i in os.environ and shutil.which(os.environ[i]):
            return os.environ[i]
    for i in EDITORS_TO_TRY:
        if shutil.which(i):
            return i
    raise RuntimeError("Unable to find an editor, please set the KQF_EDITOR, EDITOR, or VISUAL envvar")
