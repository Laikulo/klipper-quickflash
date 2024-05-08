import json
import logging
import os
import pathlib
import shlex
import urllib.request
from enum import Enum
from typing import Optional
from zipimport import zipimporter


def get_latest_release(pre_release: bool = False):
    if pre_release:
        raise NotImplementedError("Self-updating to a prerelease")
    try:
        gh_rel_data = urllib.request.urlopen(
            "https://api.github.com/repos/laikulo/klipper-quickflash/releases/latest"
        )
        gh_rel_json = json.load(gh_rel_data)
        return gh_rel_json
    except Exception:
        # TODO: If in debug mode, print exception details
        return None


def get_release_tag(release_tag: str):
    try:
        gh_rel_data = urllib.request.urlopen(
            f"https://api.github.com/repos/laikulo/klipper-quickflash/releases/tags/{release_tag}"
        )
        gh_rel_json = json.load(gh_rel_data)
        return gh_rel_json
    except Exception:
        # TODO: If in debug mode, print exception details
        return None


def get_release_pyz_url(release_data):
    if release_data is None:
        return None
    try:
        assets = release_data["assets"]
        (pyz_asset,) = filter(lambda a: a["name"] == "kqf.pyz", assets)
        return pyz_asset["browser_download_url"]
    except KeyError:
        return None


def upgrade_kqf(revision: Optional[str], allow_prereleases: bool = False):
    im = get_installation_method(True)
    print("Preparing to upgrade KQF...")
    if im == InstallationMethod.PYZ:
        if revision:
            rev_data = get_release_tag(revision)
            if rev_data is None:
                raise ValueError(f"Could not find version {revision}")
            if rev_data['prerelease'] and not allow_prereleases:
                raise ValueError(f"{revision} is a prerelease, give --allow-prerelease to use anyway")
        else:
            if not allow_prereleases:
                rev_data = get_latest_release()
            else:
                raise NotImplementedError("upgrade to latest prerelease")
        upgrade_pyz(get_release_pyz_url(rev_data))
    else:
        raise ValueError("Self-upgrades are not supported for this installation type")


def upgrade_pyz(new_pyz_url: str):
    if get_installation_method() != InstallationMethod.PYZ:
        raise ValueError("pyz upgrade called for non pyz installation")
    # Detect the full path of the current pyz
    current_pyz_path = pathlib.Path(__loader__.archive).resolve()
    new_pyz_path = current_pyz_path.with_suffix(current_pyz_path.suffix + ".new")
    backup_pyz_path = current_pyz_path.with_suffix(current_pyz_path.suffix + ".bak")
    logging.info(f"KQF Installation path: {current_pyz_path}")
    logging.info(f"Downloading new pyz to {new_pyz_path}")
    print(f"Downloading {new_pyz_url}...")
    urllib.request.urlretrieve(new_pyz_url, new_pyz_path)
    new_pyz_path.chmod(current_pyz_path.stat().st_mode)
    if backup_pyz_path.exists():
        os.remove(backup_pyz_path)
    script_lines = [
        "#!/usr/bin/env sh",
        f"echo {shlex.quote('Backing up current KQF to ' + str(backup_pyz_path)) + '...'}",
        f"mv {shlex.quote(str(current_pyz_path))} {shlex.quote(str(backup_pyz_path))}",
        f"echo {shlex.quote('Copying new KQF to ' + str(current_pyz_path) + '...')}",
        f"mv {shlex.quote(str(new_pyz_path))} {shlex.quote(str(current_pyz_path))}",
        "echo Launching new KQF...",
        f"exec {shlex.quote(str(current_pyz_path))} upgrade --complete \"$1\""
    ]
    updater_path = current_pyz_path.with_suffix(".updater.sh")
    updater_path.touch(mode=0o700)
    updater_path.write_text("\n".join(script_lines))
    updater_data = {
        'kind': 'PYZ',
        'script_path': str(updater_path)
    }
    print("Launching updater script...")
    os.execv(updater_path.resolve(), [updater_path.resolve(), json.dumps(updater_data)])


def complete_upgrade(upgrade_blob):
    upgrade_data = json.loads(upgrade_blob)
    upgrade_kind = upgrade_data['kind']
    if upgrade_kind == 'PYZ':
        print("Cleaning up updater script")
        os.remove(pathlib.Path(upgrade_data['script_path']))
    elif upgrade_kind == "SIMPLE":
        pass  # No action needed
    else:
        raise ValueError(f"Unexpected upgrade type {upgrade_kind}")
    print("Upgrade Complete!")


class InstallationMethod(Enum):
    UNKNOWN = 0
    PYZ = 1
    PYTHON_PACKAGE = 2
    PYTHON_FILES = 3


def get_installation_method(required: bool = False) -> InstallationMethod:
    if isinstance(__loader__, zipimporter):
        return InstallationMethod.PYZ
    if required:
        raise ValueError("Could not determine the installation type")
    return InstallationMethod.UNKNOWN
