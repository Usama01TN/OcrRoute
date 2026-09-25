# coding=utf-8
"""
On-demand installation of engine dependencies.

Deep-learning engines need PyTorch, TensorFlow or PaddlePaddle. They are deliberately not bundled in the stand-alone
executables (several GB, conflicting version pins), so this module installs one engine family on demand with pip
into the running Python environment and then re-runs AioOCR's discovery so the engine becomes available without a
restart. In a frozen executable pip cannot modify the bundle; the plan explains the pip route instead.
"""
from __future__ import absolute_import, division, print_function

import subprocess
import sys

from ocrroute.logsetup import getLogger

log = getLogger(__name__)

# engine module (as named in AioOCR) -> (pyproject extra, pip packages, framework, approximate download size)
ENGINE_DEPS = {
    'engines.api.mistralocr': ('api', ['mistralai>=1.0'], '', '1 MB'),
    'engines.local.easy': ('easyocr', ['easyocr>=1.7'], 'PyTorch', '~800 MB'),
    # Surya 0.17.x runs OCR in PyTorch alone; Surya 2 (0.20+) needs a vLLM / llama.cpp server (extra "surya2").
    'engines.local.suryaocr': ('surya', ['surya-ocr>=0.17,<0.20', 'transformers>=4.56.1,<5'], 'PyTorch', '~1.5 GB'),
    'engines.local.glmocrhf': ('transformers', ['transformers>=4.45', 'torch>=2.4', 'accelerate>=0.33'], 'PyTorch', '~900 MB'),
    'engines.local.olmocrlib': ('olmocr', ['olmocr>=0.4', 'transformers>=4.45', 'torch>=2.4', 'pypdf>=4'], 'PyTorch', '~1 GB'),
    'engines.local.paddleocrlib': ('paddle', ['paddleocr>=3.0', 'paddlepaddle>=3.0'], 'PaddlePaddle', '~600 MB'),
    'engines.local.calamariocr': ('calamari', ['calamari-ocr>=2.3', 'tensorflow>=2.15'], 'TensorFlow', '~700 MB'),
    'engines.local.kerasocr': ('keras', ['keras-ocr>=0.9'], 'TensorFlow',
                               'legacy: needs numpy<2, use a separate environment'),
}


def isFrozen():
    """
    :return: bool  running from a PyInstaller executable
    """
    return bool(getattr(sys, 'frozen', False))


def planFor(module):
    """
    :param module: str  engine module name, e.g. ``engines.local.easy`` (an ``AioOCR.`` prefix is accepted)
    :return: dict | None  {extra, packages, framework, size, command, frozen, hint}
    """
    key = module[len('AioOCR.'):] if module.startswith('AioOCR.') else module
    entry = ENGINE_DEPS.get(key)
    if entry is None:
        return None
    extra, packages, framework, size = entry
    command = 'pip install "ocrroute[{}]"'.format(extra)
    if isFrozen() and framework:
        from ocrroute import edition

        full = extra in ('easyocr', 'paddle', 'surya')  # engines the Full edition bundles
        if full and edition.name() == 'lean':
            hint = ('Not included in this lean executable (needs {}, {}). Download the Full edition '
                    '(ocrroute-server-full / OcrRoute-Desktop-Full), or install with pip: {}'.format(framework, size, command))
        elif full and edition.name() == 'full':
            import platform

            intelMac = sys.platform == 'darwin' and platform.machine().lower() in ('x86_64', 'amd64')
            why = ('{} has no build for Intel Macs newer than 2.2, which predates NumPy 2'.format(framework)
                   if intelMac and framework == 'PyTorch' else 'this Full build was made without it')
            hint = 'Not included in this Full executable ({}). Install with pip: {}'.format(why, command)
        else:
            hint = ('Not included in the stand-alone executables (needs {}, {}). Install OcrRoute with pip and run: {}'
                    .format(framework, size, command))
    else:
        hint = 'One click: Install ({}{}), or run: {}'.format(', '.join(packages), ', ' + size if size else '', command)
    return {'extra': extra, 'packages': packages, 'framework': framework, 'size': size, 'command': command,
            'frozen': isFrozen(), 'installable': not isFrozen(), 'hint': hint}


def install(module, timeout=3600):
    """
    pip-install the dependencies of one engine family into the running interpreter.

    :param module: str
    :param timeout: int  seconds (deep-learning wheels are large)
    :return: dict  {ok, output, command, packages}
    """
    plan = planFor(module)
    if plan is None:
        return {'ok': False, 'output': 'no known dependencies for {}'.format(module), 'command': '', 'packages': []}
    if plan['frozen']:
        return {'ok': False, 'output': plan['hint'], 'command': plan['command'], 'packages': plan['packages']}
    import os
    import shlex

    extra = shlex.split(os.environ.get('OCRROUTE_PIP_ARGS', ''))  # e.g. "--user" or an index URL; opt-in only
    cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', '--prefer-binary'] + extra + plan['packages']
    log.info('installing engine dependencies', module=module, packages=plan['packages'])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'output': 'pip timed out after {} s'.format(timeout), 'command': ' '.join(cmd),
                'packages': plan['packages']}
    output = (r.stdout + r.stderr)[-6000:]
    if r.returncode != 0 and 'externally-managed-environment' in output:
        # PEP 668: the system Python (Debian/Ubuntu/Homebrew) refuses pip. Never override that silently.
        output = ('This Python is managed by your operating system (PEP 668), so pip may not install into it.\n'
                  'Recommended: run OcrRoute from a virtual environment:\n'
                  '  python3 -m venv ~/.ocrroute/venv && ~/.ocrroute/venv/bin/pip install "ocrroute[{extra}]"\n'
                  'Or allow it explicitly by setting OCRROUTE_PIP_ARGS="--user --break-system-packages" '
                  '(at your own risk).\n\n'.format(extra=plan['extra']) + output[-1500:])
    return {'ok': r.returncode == 0, 'output': output, 'command': ' '.join(cmd), 'packages': plan['packages'],
            'externally_managed': 'externally-managed-environment' in output}


__all__ = ['ENGINE_DEPS', 'install', 'isFrozen', 'planFor']
