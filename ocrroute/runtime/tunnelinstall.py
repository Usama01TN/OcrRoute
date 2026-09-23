# coding=utf-8
"""
One-click tunnel installers.

Binaries are downloaded into ``~/.ocrroute/bin`` (no administrator rights needed) for providers that ship a
portable executable (cloudflared, ngrok). Tailscale needs a system service, so it is installed with the vendor's
official installer for the platform and reports the login URL it prints.
"""
from __future__ import absolute_import, division, print_function

import io
import os
import platform
import stat
import subprocess
import tarfile
import zipfile
from os.path import exists, join

from ocrroute.config import getSettings
from ocrroute.logsetup import getLogger

log = getLogger(__name__)


def binDir():
    """
    :return: str  ``~/.ocrroute/bin``, created on demand
    """
    d = join(str(getSettings().home), 'bin')
    if not exists(d):
        os.makedirs(d)
    return d


def _arch():
    m = platform.machine().lower()
    if m in ('x86_64', 'amd64'):
        return 'amd64'
    if m in ('aarch64', 'arm64'):
        return 'arm64'
    if m.startswith('arm'):
        return 'arm'
    return m


def _system():
    return {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'windows'}.get(platform.system(), platform.system().lower())


def _download(url, timeout=180):
    """
    :param url: str
    :return: bytes
    """
    import requests

    log.info('downloading', url=url)
    r = requests.get(url, timeout=timeout, stream=True, headers={'User-Agent': 'OcrRoute/0.3'})
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(1024 * 256):
        buf.write(chunk)
    return buf.getvalue()


def _place(name, data, exe=True):
    """
    :param name: str  file name inside binDir
    :param data: bytes
    :return: str  path
    """
    path = join(binDir(), name)
    with open(path, 'wb') as fh:
        fh.write(data)
    if exe and _system() != 'windows':
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def installCloudflared():
    """
    Download the latest cloudflared release for this OS/arch.

    :return: dict  {ok, path, output}
    """
    sysname, arch = _system(), _arch()
    if sysname == 'darwin':
        url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{}.tgz'.format(arch)
        data = _download(url)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith('cloudflared'))
            path = _place('cloudflared', tf.extractfile(member).read())
    elif sysname == 'windows':
        url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-{}.exe'.format(arch)
        path = _place('cloudflared.exe', _download(url))
    else:
        url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{}'.format(arch)
        path = _place('cloudflared', _download(url))
    out = _version(path)
    return {'ok': bool(out), 'path': path, 'output': out or 'downloaded but --version failed', 'command': url}


def installNgrok():
    """
    Download the ngrok v3 agent zip for this OS/arch.

    :return: dict
    """
    sysname, arch = _system(), _arch()
    url = 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-{}-{}.zip'.format(sysname, arch)
    data = _download(url)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        member = next(n for n in zf.namelist() if n.startswith('ngrok'))
        path = _place('ngrok.exe' if sysname == 'windows' else 'ngrok', zf.read(member))
    out = _version(path)
    return {'ok': bool(out), 'path': path, 'output': out or 'downloaded but version failed', 'command': url}


def installTailscale():
    """
    Tailscale runs as a system service: use the vendor installer for the platform (may prompt for privileges).

    :return: dict
    """
    sysname = _system()
    cmd = {'linux': 'curl -fsSL https://tailscale.com/install.sh | sh', 'darwin': 'brew install tailscale',
           'windows': 'winget install --id tailscale.tailscale --accept-source-agreements --accept-package-agreements'}.get(sysname)
    if not cmd:
        return {'ok': False, 'output': 'unsupported platform', 'command': ''}
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)  # noqa: S602 - operator-triggered
    return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:], 'command': cmd, 'path': 'tailscale'}


def _version(path):
    try:
        r = subprocess.run([path, '--version' if 'cloudflared' in path else 'version'], capture_output=True, text=True, timeout=20)
        return (r.stdout or r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return ''


INSTALLERS = {'cloudflare': installCloudflared, 'ngrok': installNgrok, 'tailscale': installTailscale}


def install(name):
    """
    :param name: str  tunnel name
    :return: dict  {ok, output, path?, command?}
    """
    fn = INSTALLERS.get(name)
    if fn is None:
        return {'ok': False, 'output': 'unknown tunnel'}
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.warning('install failed', tunnel=name, error=str(exc))
        return {'ok': False, 'output': '{}: {}'.format(type(exc).__name__, exc)}


__all__ = ['INSTALLERS', 'binDir', 'install', 'installCloudflared', 'installNgrok', 'installTailscale']
