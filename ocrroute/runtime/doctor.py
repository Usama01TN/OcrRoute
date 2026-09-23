# coding=utf-8
"""Diagnostics shared by the CLI, the API and both UIs."""
from __future__ import absolute_import, division, print_function

import os
import platform
import shutil
import socket
import sqlite3
import sys

from ocrroute.config import FORBIDDEN_PORTS
from ocrroute.version import __version__


def portFree(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host if host not in ('0.0.0.0', '') else '127.0.0.1', port)) != 0


def report(settings, registry):
    disk = shutil.disk_usage(settings.home)
    db_path = settings.home / 'ocrroute.db'
    try:
        with sqlite3.connect(db_path) as c:
            integrity = c.execute('PRAGMA integrity_check').fetchone()[0]
            journal = c.execute('PRAGMA journal_mode').fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        integrity, journal = 'error: {}'.format(exc), '?'
    gpu = _gpu()
    engines = [
        {
            'id': e.id,
            'kind': e.kind,
            'available': e.available,
            'import_error': e.import_error,
            'install_hint': e.install_hint,
        }
        for e in registry.all()
    ]
    return {
        'ocrroute': __version__,
        'python': sys.version.split()[0],
        'platform': '{} {} ({})'.format(platform.system(), platform.release(), platform.machine()),
        'home': str(settings.home),
        'database': {
            'path': str(db_path),
            'size_bytes': db_path.stat().st_size if db_path.exists() else 0,
            'integrity': integrity,
            'journal_mode': journal,
        },
        'ports': {
            'api': settings.port,
            'api_free': portFree(settings.host, settings.port),
            'forbidden': sorted(FORBIDDEN_PORTS),
            'conflicts': settings.port in FORBIDDEN_PORTS,
        },
        'disk': {'free_bytes': disk.free, 'total_bytes': disk.total},
        'tesseract_binary': shutil.which('tesseract') or '',
        'gpu': gpu,
        'env': {
            k: ('***' if 'KEY' in k or 'SECRET' in k else v) for k, v in os.environ.items() if k.startswith('OCRROUTE_')
        },
        'engines': engines,
        'engines_available': sum(1 for e in engines if e['available']),
        'engines_total': len(engines),
        'privacy_mode': settings.privacy_mode,
    }


def _gpu():
    try:
        import torch  # type: ignore

        return 'cuda:{}'.format(torch.cuda.get_device_name(0)) if torch.cuda.is_available() else 'cpu (torch present)'
    except Exception:  # noqa: BLE001
        return 'cpu (torch absent)'


def toMarkdown(rep):
    lines = [
        '# OcrRoute system report',
        '',
        '- Version: {}'.format(rep['ocrroute']),
        '- Python: {}'.format(rep['python']),
        '- Platform: {}'.format(rep['platform']),
        '- Home: {}'.format(rep['home']),
        '- Database: {} bytes, integrity={}, journal={}'.format(
            rep['database']['size_bytes'], rep['database']['integrity'], rep['database']['journal_mode']
        ),
        '- API port {} free={} conflicts={}'.format(
            rep['ports']['api'], rep['ports']['api_free'], rep['ports']['conflicts']
        ),
        '- Disk free: {} MB'.format(rep['disk']['free_bytes'] // (1024 * 1024)),
        '- Tesseract: {}'.format(rep['tesseract_binary'] or 'not found'),
        '- GPU: {}'.format(rep['gpu']),
        '- Engines available: {}/{}'.format(rep['engines_available'], rep['engines_total']),
        '',
        '## Engines',
        '',
        '| engine | kind | available | hint |',
        '|---|---|---|---|',
    ]
    for e in rep['engines']:
        lines.append(
            '| {} | {} | {} | {} |'.format(
                e['id'], e['kind'], 'yes' if e['available'] else 'no', e['install_hint'] if not e['available'] else ''
            )
        )
    return '\n'.join(lines) + '\n'
