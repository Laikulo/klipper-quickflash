import json
import urllib.request
from pprint import pprint as pp

def get_latest_release(pre_release: bool = False):
    if pre_release:
        raise NotImplementedError("Self-updating to a prerelease")
    try:
        gh_rel_data = urllib.request.urlopen('https://api.github.com/repos/laikulo/klipper-quickflash/releases/latest')
        gh_rel_json = json.load(gh_rel_data)
        return gh_rel_json
    except Exception:
        # TODO: If in debug mode, print exception details
        return None

def get_release_pyz_url(release_data):
    if release_data is None:
        return None
    try:
        assets = release_data['assets']
        (pyz_asset,) = filter(lambda a: a['name'] == "kqf.pyz", assets)
        return pyz_asset['browser_download_url']
    except KeyError as e:
        return None

