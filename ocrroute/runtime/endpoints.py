# coding=utf-8
"""
Endpoints: where the gateway is reachable.

- Local addresses of this machine (every IPv4 interface) rendered as ``http://<ip>:<port>/v1``.
- Tunnels: Cloudflare Quick Tunnel, Tailscale Funnel and ngrok, detected on PATH, started/stopped as child
  processes, with the public URL captured from their output.
- A manually configured public URL (reverse proxy) and a global OCR prompt injected into every VLM request.
"""
from __future__ import absolute_import, division, print_function

import platform
import re
import socket
import subprocess
import threading
import time
from os import environ
from os.path import exists, join
from shutil import which

from ocrroute.logsetup import getLogger

log = getLogger(__name__)

_URL_RE = re.compile(r'https://[A-Za-z0-9.\-]+\.(?:trycloudflare\.com|ngrok(?:-free)?\.(?:app|dev|io)|ts\.net)[^\s"\']*')


def localAddresses():
    """
    :return: list[str]  IPv4 addresses of this machine, primary first, without duplicates or loopback
    """
    found = []
    try:  # primary (default-route) address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        found.append(s.getsockname()[0])
        s.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except socket.gaierror:
        pass
    system = platform.system()
    cmd = {'Linux': ['ip', '-4', '-o', 'addr'], 'Darwin': ['ifconfig'], 'Windows': ['ipconfig']}.get(system)
    if cmd and which(cmd[0]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            found += re.findall(r'(?:inet |IPv4[^:]*:\s*)(\d+\.\d+\.\d+\.\d+)', out)
        except Exception:  # noqa: BLE001
            pass
    seen, result = set(), []
    for ip in found:
        if ip.startswith('127.') or ip in seen:
            continue
        seen.add(ip)
        result.append(ip)
    return result


class Tunnel(object):
    """
    Tunnel class: one tunnel provider (a child process publishing the local port).
    """
    name = ''
    title = ''
    binary = ''
    homepage = ''
    needsAuth = False

    def __init__(self):
        self.__m_process = None
        self.__m_url = ''
        self.__m_error = ''
        self.__m_startedAt = 0.0
        self.__m_log = []
        self.__m_lock = threading.Lock()

    # ---- detection ---------------------------------------------------------------------------
    def executable(self):
        """
        :return: str  path of the binary: ``~/.ocrroute/bin`` first (one-click installs), then PATH, else ''
        """
        from ocrroute.runtime.tunnelinstall import binDir

        for candidate in (join(binDir(), self.binary), join(binDir(), self.binary + '.exe')):
            if exists(candidate):
                return candidate
        return which(self.binary) or ''

    def installed(self):
        """
        :return: bool
        """
        return bool(self.executable())

    def authenticated(self):
        """
        :return: bool  True when the provider is ready to publish (login / token present)
        """
        return True

    def installCommand(self):
        """
        :return: str  a copy-paste command for the current OS ('' when unknown)
        """
        return {'Linux': self.installLinux, 'Darwin': self.installMac, 'Windows': self.installWindows}.get(platform.system(), '')

    installLinux = installMac = installWindows = ''

    def command(self, port):  # pragma: no cover - overridden
        """
        :param port: int
        :return: list[str]
        """
        raise NotImplementedError

    # ---- lifecycle ---------------------------------------------------------------------------
    def running(self):
        """
        :return: bool
        """
        return self.__m_process is not None and self.__m_process.poll() is None

    def getUrl(self):
        """
        :return: str
        """
        return self.__m_url

    def getError(self):
        """
        :return: str
        """
        return self.__m_error

    def start(self, port, wait=20.0):
        """
        :param port: int  local gateway port to publish
        :param wait: float  seconds to wait for a public URL
        :return: str  public URL ('' when not captured yet)
        """
        with self.__m_lock:
            if self.running():
                return self.__m_url
            if not self.installed():
                self.__m_error = 'not installed'
                raise RuntimeError('{} is not installed'.format(self.title))
            if not self.authenticated():
                self.__m_error = 'needs authentication'
                raise RuntimeError('{} needs authentication first'.format(self.title))
            self.__m_url, self.__m_error, self.__m_log = '', '', []
            self.__m_process = subprocess.Popen(self.command(port), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                text=True, bufsize=1)
            self.__m_startedAt = time.time()
            threading.Thread(target=self._pump, daemon=True).start()
        deadline = time.time() + wait
        while time.time() < deadline and not self.__m_url and self.running():
            time.sleep(0.25)
            if not self.__m_url:
                self.__m_url = self.discoverUrl() or ''
        if not self.running() and not self.__m_url:
            self.__m_error = (self.__m_log[-1] if self.__m_log else 'process exited').strip()
            raise RuntimeError('{} exited: {}'.format(self.title, self.__m_error))
        return self.__m_url

    def _pump(self):
        proc = self.__m_process
        try:
            for line in proc.stdout:
                line = line.rstrip()
                self.__m_log.append(line)
                if len(self.__m_log) > 200:
                    del self.__m_log[0]
                m = _URL_RE.search(line)
                if m and not self.__m_url:
                    self.__m_url = m.group(0)
                    log.info('tunnel up', tunnel=self.name, url=self.__m_url)
        except Exception:  # noqa: BLE001
            pass

    def discoverUrl(self):
        """
        Provider-specific URL lookup when the process output does not print it.

        :return: str
        """
        return ''

    def authenticate(self, token):
        """
        Provider-specific authentication (ngrok token, tailscale login).

        :param token: str
        :return: dict  {ok, output, login_url}
        """
        return {'ok': False, 'output': '{} does not take a token'.format(self.title), 'login_url': ''}

    def stop(self):
        """
        Terminate the tunnel process.
        """
        with self.__m_lock:
            proc = self.__m_process
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self.__m_process = None
            self.__m_url = ''

    def status(self):
        """
        :return: dict
        """
        installed = self.installed()
        return {'name': self.name, 'title': self.title, 'binary': self.binary, 'homepage': self.homepage,
                'installed': installed, 'authenticated': installed and self.authenticated(),
                'needs_auth': self.needsAuth, 'running': self.running(), 'url': self.__m_url, 'error': self.__m_error,
                'install_command': self.installCommand(), 'log': self.__m_log[-8:],
                'uptime_s': int(time.time() - self.__m_startedAt) if self.running() else 0}


class Cloudflare(Tunnel):
    name, title, binary, homepage = 'cloudflare', 'Cloudflare Quick Tunnel', 'cloudflared', 'https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/'
    installLinux = 'curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared'
    installMac = 'brew install cloudflared'
    installWindows = 'winget install --id Cloudflare.cloudflared'

    def command(self, port):
        return [self.executable(), 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:{}'.format(port)]


class Tailscale(Tunnel):
    name, title, binary, homepage, needsAuth = 'tailscale', 'Tailscale Funnel', 'tailscale', 'https://tailscale.com/kb/1223/funnel', True
    installLinux = 'curl -fsSL https://tailscale.com/install.sh | sh'
    installMac = 'brew install tailscale'
    installWindows = 'winget install --id tailscale.tailscale'

    def authenticated(self):
        try:
            out = subprocess.run([self.executable(), 'status', '--json'], capture_output=True, text=True, timeout=5).stdout
            return '"BackendState": "Running"' in out or '"BackendState":"Running"' in out
        except Exception:  # noqa: BLE001
            return False

    def command(self, port):
        return [self.executable(), 'funnel', str(port)]

    def authenticate(self, token):
        """Run ``tailscale up`` (optionally with an auth key) and return the login URL it prints."""
        cmd = [self.executable(), 'up', '--timeout', '20s'] + (['--authkey', token] if token else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            out = r.stdout + r.stderr
            m = re.search(r'https://login\.tailscale\.com/\S+', out)
            return {'ok': r.returncode == 0 or self.authenticated(), 'output': out[-800:], 'login_url': m.group(0) if m else ''}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'output': str(exc), 'login_url': ''}

    def discoverUrl(self):
        try:
            out = subprocess.run([self.executable(), 'funnel', 'status'], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'https://[A-Za-z0-9.\-]+\.ts\.net[^\s]*', out)
            return m.group(0) if m else ''
        except Exception:  # noqa: BLE001
            return ''


class Ngrok(Tunnel):
    name, title, binary, homepage, needsAuth = 'ngrok', 'ngrok Tunnel', 'ngrok', 'https://ngrok.com/download', True
    installLinux = 'curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc && echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list && sudo apt update && sudo apt install ngrok'
    installMac = 'brew install ngrok/ngrok/ngrok'
    installWindows = 'winget install --id ngrok.ngrok'

    def authenticated(self):
        if environ.get('NGROK_AUTHTOKEN'):
            return True
        try:
            r = subprocess.run([self.executable(), 'config', 'check'], capture_output=True, text=True, timeout=5)
            return r.returncode == 0 and 'authtoken' not in (r.stdout + r.stderr).lower().replace('valid', '')
        except Exception:  # noqa: BLE001
            return False

    def command(self, port):
        return [self.executable(), 'http', str(port), '--log', 'stdout', '--log-format', 'logfmt']

    def authenticate(self, token):
        if not token:
            return {'ok': False, 'output': 'an ngrok authtoken is required (dashboard.ngrok.com)', 'login_url': 'https://dashboard.ngrok.com/get-started/your-authtoken'}
        try:
            r = subprocess.run([self.executable(), 'config', 'add-authtoken', token], capture_output=True, text=True, timeout=30)
            return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-800:], 'login_url': ''}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'output': str(exc), 'login_url': ''}

    def discoverUrl(self):
        try:
            import requests

            data = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2).json()
            for t in data.get('tunnels', []):
                if str(t.get('public_url', '')).startswith('https://'):
                    return t['public_url']
        except Exception:  # noqa: BLE001
            pass
        return ''



class EndpointManager(object):
    """
    EndpointManager class: process-wide registry of tunnels and endpoint facts.
    """

    def __init__(self, settings):
        """
        :param settings: Settings
        """
        self.__m_settings = settings
        self.__m_tunnels = {t.name: t for t in (Cloudflare(), Tailscale(), Ngrok())}

    def getTunnel(self, name):
        """
        :param name: str
        :return: Tunnel
        """
        if name not in self.__m_tunnels:
            raise KeyError(name)
        return self.__m_tunnels[name]

    def snapshot(self, publicUrl='', customPrompt='', customPromptEnabled=False):
        """
        :param publicUrl: str  manually configured public base URL
        :param customPrompt: str
        :param customPromptEnabled: bool
        :return: dict  everything the Endpoints page shows
        """
        s = self.__m_settings
        port = s.port
        active = [{'label': 'Local', 'url': 'http://localhost:{}/v1'.format(port), 'kind': 'local'}]
        if publicUrl:
            active.append({'label': 'Public', 'url': publicUrl.rstrip('/') + '/v1', 'kind': 'public'})
        for t in self.__m_tunnels.values():
            if t.running() and t.getUrl():
                active.append({'label': t.title, 'url': t.getUrl() + '/v1', 'kind': 'tunnel'})
        return {
            'port': port, 'host': s.host, 'panel_port': s.panel_port, 'running': True,
            'server_id': _serverId(),
            'active': active,
            'local': ['http://{}:{}/v1'.format(ip, port) for ip in localAddresses()],
            'panel_url': 'http://localhost:{}/panel/'.format(s.panel_port or port),
            'docs_url': 'http://localhost:{}/v1/docs'.format(port),
            'tunnels': [t.status() for t in self.__m_tunnels.values()],
            'tunnels_active': sum(1 for t in self.__m_tunnels.values() if t.running()),
            'public_url': publicUrl,
            'custom_prompt': customPrompt, 'custom_prompt_enabled': bool(customPromptEnabled),
            'platform': platform.system(),
        }

    def install(self, name, enable=True, port=None):
        """
        One click: download/install the tunnel binary, then start it.

        :param name: str
        :param enable: bool  start the tunnel right after a successful install
        :param port: int | None
        :return: dict  {ok, installed, output, command, url, status, needs_auth}
        """
        from ocrroute.runtime.tunnelinstall import install as doInstall

        t = self.getTunnel(name)
        result = {'ok': True, 'output': 'already installed', 'command': ''} if t.installed() else doInstall(name)
        result['installed'] = t.installed()
        result['url'] = ''
        result['needs_auth'] = False
        if result['installed'] and enable:
            if t.needsAuth and not t.authenticated():
                result['needs_auth'] = True
                result['output'] = (result.get('output') or '') + '\nInstalled. {} needs authentication before it can publish.'.format(t.title)
            else:
                try:
                    result['url'] = t.start(port or self.__m_settings.port)
                except RuntimeError as exc:
                    result['ok'] = False
                    result['output'] = (result.get('output') or '') + '\n' + str(exc)
        result['status'] = t.status()
        return result

    def stopAll(self):
        for t in self.__m_tunnels.values():
            t.stop()


_serverIdValue = ''


def _serverId():
    """
    :return: str  a short stable id for this installation (derived from the secret key file when present)
    """
    global _serverIdValue
    if not _serverIdValue:
        import hashlib

        from ocrroute.config import getSettings

        try:
            raw = getSettings().secret_file.read_bytes()
        except Exception:  # noqa: BLE001
            raw = socket.gethostname().encode()
        _serverIdValue = hashlib.sha1(raw).hexdigest()[:7]
    return _serverIdValue


_manager = None


def getEndpointManager(settings=None):
    """
    :param settings: Settings | None
    :return: EndpointManager
    """
    global _manager
    if _manager is None:
        from ocrroute.config import getSettings

        _manager = EndpointManager(settings or getSettings())
    return _manager


__all__ = ['Cloudflare', 'EndpointManager', 'Ngrok', 'Tailscale', 'Tunnel', 'getEndpointManager', 'localAddresses']
