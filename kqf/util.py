import logging
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
