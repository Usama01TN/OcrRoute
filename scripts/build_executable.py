# coding=utf-8
"""
Build stand-alone executables with PyInstaller (Windows .exe, macOS .app, Linux binary).

    python scripts/build_executable.py                 # both: ocrroute-server (CLI+API+panel) and OcrRoute Desktop
    python scripts/build_executable.py --target server
    python scripts/build_executable.py --target desktop --onefile

The same script runs locally and in .github/workflows/build.yml.
"""
from __future__ import absolute_import, division, print_function

import argparse
import os
import platform
import shutil
import subprocess
import sys
from os.path import abspath, dirname, exists, join

ROOT = abspath(join(dirname(__file__), '..'))
DIST = join(ROOT, 'dist')


def version():
    """
    :return: str
    """
    ns = {}
    with open(join(ROOT, 'ocrroute', 'version.py')) as fh:
        exec(fh.read(), ns)  # noqa: S102 - our own file
    return ns['__version__']


def osTag():
    """
    :return: str  windows | macos | linux plus architecture
    """
    name = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}.get(platform.system(), platform.system().lower())
    arch = platform.machine().lower().replace('amd64', 'x86_64').replace('aarch64', 'arm64')
    return '{}-{}'.format(name, arch)


def dataArgs():
    """
    :return: list[str]  --add-data arguments for every non-Python asset
    """
    sep = ';' if platform.system() == 'Windows' else ':'
    pairs = [
        ('ocrroute/catalog/engines.toml', 'ocrroute/catalog'),
        ('ocrroute/i18n', 'ocrroute/i18n'),
        ('ocrroute/assets', 'ocrroute/assets'),
        ('ocrroute/panel/templates', 'ocrroute/panel/templates'),
        ('ocrroute/panel/static', 'ocrroute/panel/static'),
        ('ocrroute/desktop/styles', 'ocrroute/desktop/styles'),
        ('ocrroute/tools/README.md', 'ocrroute/tools'),
        ('AioOCR', 'AioOCR'),
    ]
    out = []
    for src, dst in pairs:
        out += ['--add-data', '{}{}{}'.format(join(ROOT, src), sep, dst)]
    return out


def hiddenImports():
    """
    :return: list[str]
    """
    mods = ['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
            'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'anyio._backends._asyncio', 'engines',
            'engines.ocrplugin', 'engines.api', 'engines.local', 'AioOCR', 'AioOCR.ocrbase', 'pypdfium2', 'openpyxl',
            'argon2', 'PIL.Image', 'six', 'ocrroute.selftest', 'ocrroute.enginelib_probe', 'ocrroute.runtime.engineinstall', 'mistralai', 'faulthandler', 'pytesseract', 'structlog', 'prometheus_client', 'sqlalchemy.dialects.sqlite']
    out = []
    for m in mods:
        out += ['--hidden-import', m]
    for pkg in ('mistralai', 'ocrroute.api', 'ocrroute.panel', 'ocrroute.routing', 'ocrroute.pipeline', 'ocrroute.runtime',
                'ocrroute.cli', 'ocrroute.db', 'ocrroute.tools', 'ocrroute.compat', 'engines.api', 'engines.local'):
        out += ['--collect-submodules', pkg]
    return out


FULL_PACKAGES = ('easyocr', 'paddleocr', 'paddlex', 'paddle')  # collected completely when installed
# distribution metadata PaddleX / PaddleOCR / EasyOCR read at runtime via importlib.metadata
FULL_METADATA = ('paddlex', 'paddleocr', 'paddlepaddle', 'easyocr', 'torch', 'torchvision', 'numpy', 'pillow',
                 'opencv-contrib-python-headless', 'pyclipper', 'shapely', 'imagesize', 'pypdfium2', 'python-bidi',
                 'pydantic', 'pyyaml', 'requests', 'ujson', 'tqdm', 'modelscope', 'huggingface-hub', 'aistudio-sdk')


def installed(dist):
    """
    :param dist: str  distribution name
    :return: bool
    """
    from importlib import metadata

    try:
        metadata.distribution(dist)
        return True
    except metadata.PackageNotFoundError:
        return False


NATIVE_SUFFIXES = ('.so', '.pyd', '.dll', '.dylib')


def isNative(fileName):
    """
    :param fileName: str
    :return: bool  shared library or extension module (``x.so``, ``x.so.13``, ``x.pyd``, ``x.dll``, ``x.dylib``)
    """
    return fileName.endswith(NATIVE_SUFFIXES) or '.so.' in fileName


def nativeFiles(package):
    """
    Every native file of ``package`` with the destination that keeps its relative location in the bundle, plus the
    sibling ``<package>.libs`` folder that Linux wheels (auditwheel) use for vendored libraries.

    Needed for packages that load extensions *by path* rather than by import: torchvision >= 0.29 loads
    ``_C_stable`` through ``torch.ops.load_library``, which neither PyInstaller's analysis nor the (older) community
    hook listing ``torchvision._C`` can see, and ``collect_dynamic_libs`` skips ``.pyd`` files and ``_C_stable.so``.

    :param package: str
    :return: list[tuple[str, str]]  (source file, destination directory inside the bundle)
    """
    from importlib.util import find_spec

    spec = find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        return []
    pkgDir = list(spec.submodule_search_locations)[0]
    site = os.path.dirname(pkgDir)
    out = []
    for folder in (pkgDir, os.path.join(site, package + '.libs')):
        if not os.path.isdir(folder):
            continue
        for dirPath, _dirs, files in os.walk(folder):
            for f in files:
                if isNative(f):
                    rel = os.path.relpath(dirPath, site)
                    out.append((os.path.join(dirPath, f), rel))
    return sorted(out)


def nativeArgs(package):
    """
    :param package: str
    :return: list[str]  --add-binary arguments for ``nativeFiles(package)``
    """
    sep = ';' if platform.system() == 'Windows' else ':'
    args = []
    for src, dest in nativeFiles(package):
        args += ['--add-binary', '{}{}{}'.format(src, sep, dest)]
    return args


def declaredDependencies(dist, extras=()):
    """
    Distribution names ``dist`` declares (base requirements plus the given extras) that are installed, followed
    recursively. Libraries such as PaddleX check their dependencies by *distribution metadata* at runtime, so the
    metadata of every declared dependency has to be in the bundle, whatever the library version.

    :param dist: str
    :param extras: tuple[str]
    :return: list[str]
    """
    from importlib import metadata

    try:
        from packaging.requirements import Requirement
    except ImportError:  # pragma: no cover
        return []
    seen, todo = set(), [(dist, tuple(extras))]
    while todo:
        name, wanted = todo.pop()
        try:
            reqs = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue
        for raw in reqs:
            try:
                req = Requirement(raw)
            except Exception:  # noqa: BLE001
                continue
            envs = [{'extra': e} for e in wanted] or [{'extra': ''}]
            if req.marker is not None and not any(req.marker.evaluate(env) for env in envs):
                continue
            key = req.name.lower()
            if key in seen or not installed(req.name):
                continue
            seen.add(key)
            todo.append((req.name, tuple(req.extras)))
    return sorted(seen)


def suryaArgs():
    """
    Surya's engine modules and data, without ``surya.scripts`` / ``surya.debug`` (a Streamlit demo, fine-tuning and
    S3 tools that import streamlit, datasets, boto3, playwright...: never used by the engine).

    :return: list[str]
    """
    from importlib.util import find_spec

    spec = find_spec('surya')
    if spec is None or not spec.submodule_search_locations:
        return []
    import pkgutil

    out = ['--collect-data', 'surya']
    for info in pkgutil.walk_packages(list(spec.submodule_search_locations), prefix='surya.'):
        if not info.name.startswith(('surya.scripts', 'surya.debug')):
            out += ['--hidden-import', info.name]
    return out + ['--exclude-module', 'surya.scripts', '--exclude-module', 'surya.debug']


def editionArgs(edition):
    """
    :param edition: str  lean | full
    :return: list[str]  PyInstaller arguments for the edition
    """
    if edition == 'lean':
        return ['--exclude-module', 'torch', '--exclude-module', 'torchvision', '--exclude-module', 'transformers',
                '--exclude-module', 'tensorflow', '--exclude-module', 'paddle', '--exclude-module', 'paddlex',
                '--exclude-module', 'paddleocr', '--exclude-module', 'easyocr', '--exclude-module', 'cv2.gapi']
    from importlib.util import find_spec

    out = ['--exclude-module', 'tensorflow']
    if find_spec('surya') is None:  # Surya is the only bundled engine that needs transformers
        out += ['--exclude-module', 'transformers']
    for pkg in FULL_PACKAGES:
        if find_spec(pkg) is not None:
            out += ['--collect-all', pkg]
    out += suryaArgs()
    dists = set(d for d in FULL_METADATA if installed(d))
    for root, extras in (('paddlex', ('ocr', 'ocr-core')), ('paddleocr', ()), ('easyocr', ()), ('transformers', ()),
                         ('surya-ocr', ())):  # transformers checks its dependencies' versions by metadata at import
        if installed(root):
            dists.update(declaredDependencies(root, extras))
    for dist in sorted(dists):
        out += ['--copy-metadata', dist]
    out += nativeArgs('torchvision')  # _C_stable / image_stable and their vendored libraries, at their own paths
    out += nativeArgs('paddle')  # paddle/libs (MKL, oneDNN...) at the exact path Paddle registers at runtime
    return out


def editionMarker(edition):
    """
    Write ``build/edition.json`` and return the --add-data argument that ships it as ``ocrroute/edition.json``.

    :param edition: str
    :return: list[str]
    """
    import json

    engines = []
    from importlib.util import find_spec

    if edition == 'full':
        engines = [n for n, mod in (('EasyOCR', 'easyocr'), ('PaddleOCR', 'paddleocr'), ('Surya', 'surya'))
                   if find_spec(mod) is not None]
    os.makedirs(join(ROOT, 'build'), exist_ok=True)
    path = join(ROOT, 'build', 'edition.json')
    with open(path, 'w') as fh:
        json.dump({'edition': edition, 'bundled_extra_engines': engines}, fh)
    sep = ';' if platform.system() == 'Windows' else ':'
    return ['--add-data', '{}{}ocrroute'.format(path, sep)]


def build(target, onefile, clean, edition='lean'):
    """
    :param target: str  server | desktop
    :param onefile: bool
    :param clean: bool
    :param edition: str  lean | full (Full adds EasyOCR and PaddleOCR with their frameworks)
    :return: str  path of the produced bundle
    """
    name = ('ocrroute-server' if target == 'server' else 'OcrRoute-Desktop') + ('-full' if edition == 'full' else '')
    entry = join(ROOT, 'scripts', 'entry_server.py' if target == 'server' else 'entry_desktop.py')
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--name', name, '--distpath', DIST,
           '--workpath', join(ROOT, 'build', target + '-' + edition), '--specpath', join(ROOT, 'build'),
           '--paths', ROOT, '--paths', join(ROOT, 'AioOCR')]
    cmd += ['--onefile'] if onefile else ['--onedir']
    if clean:
        cmd.append('--clean')
    if target == 'desktop':
        cmd += ['--windowed', '--collect-submodules', 'ocrroute.desktop', '--hidden-import', 'PyQt5.QtSvg']
        if platform.system() == 'Darwin':
            cmd += ['--osx-bundle-identifier', 'io.ocrroute.desktop']
    else:
        cmd += ['--console', '--exclude-module', 'PyQt5']
    cmd += ['--runtime-hook', join(ROOT, 'scripts', 'pyi_rth_ocrroute_site.py')]  # site paths point at the bundle
    cmd += editionArgs(edition) + editionMarker(edition)
    cmd += dataArgs() + hiddenImports() + [entry]
    print(' '.join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    produced = join(DIST, name + ('.app' if target == 'desktop' and platform.system() == 'Darwin' else ''))
    if not exists(produced):
        produced = join(DIST, name + ('.exe' if platform.system() == 'Windows' else ''))
    if edition == 'full' and target == 'server':
        selftest(join(DIST, name, name + ('.exe' if platform.system() == 'Windows' else '')))
    return produced


def selftest(binary):
    """
    Import every bundled deep-learning engine *inside* the frozen executable; raise with the full traceback when
    one fails, so a broken bundle can never be archived or published.

    :param binary: str  path of the frozen server executable
    """
    import json

    with open(join(ROOT, 'build', 'edition.json')) as fh:
        engines = json.load(fh).get('bundled_extra_engines', [])
    modules = {'EasyOCR': ['torch', 'torchvision', 'easyocr', 'engines.local.easy', 'torch:compute', 'torchvision:ops'],
               'PaddleOCR': ['paddle', 'paddlex', 'paddleocr', 'engines.local.paddleocrlib', 'paddleocr:requirements',
                             'paddleocr:model-sources',
                             'paddle:compute'],
               'Surya': ['torch', 'transformers', 'surya', 'surya.recognition', 'surya.detection',
                         'engines.local.suryaocr', 'torch:compute']}
    wanted = [m for e in engines for m in modules.get(e, [])]
    if not wanted:
        return
    print('frozen self-test:', ', '.join(wanted), flush=True)
    env = dict(os.environ, TORCHVISION_WARN_WHEN_EXTENSION_LOADING_FAILS='1')  # print why an extension did not load
    r = subprocess.run([binary, '--ocrroute-selftest', ','.join(wanted)], capture_output=True, text=True, timeout=900,
                       env=env)
    print(r.stdout[-20000:], flush=True)
    if r.returncode != 0:
        tail = '\n'.join(ln for ln in r.stderr.splitlines() if 'extension' in ln.lower() or 'error' in ln.lower())[-6000:]
        raise SystemExit('frozen self-test failed ({} module(s)); the bundle is incomplete.\n{}'.format(
            r.stdout.count('SELFTEST FAIL'), tail))


def archive(path, target):
    """
    :param path: str  bundle path
    :param target: str
    :return: str  archive path  dist/ocrroute-<target>-<version>-<os-arch>.zip|.tar.gz
    """
    base = join(DIST, 'ocrroute-{}-{}-{}'.format(target, version(), osTag()))
    fmt = 'zip' if platform.system() in ('Windows', 'Darwin') else 'gztar'
    if os.path.isdir(path):
        out = shutil.make_archive(base, fmt, root_dir=dirname(path), base_dir=os.path.basename(path))
    else:
        staging = base + '-staging'
        os.makedirs(staging, exist_ok=True)
        shutil.copy2(path, staging)
        out = shutil.make_archive(base, fmt, root_dir=staging)
        shutil.rmtree(staging, ignore_errors=True)
    print('archive:', out)
    return out


def main():
    """
    :return: int
    """
    sys.path.insert(0, ROOT)
    from ocrroute import paddleenv

    # PyInstaller imports packages in helper processes to enumerate their modules; they inherit this environment.
    # Without it PaddleX 3.0 downloads fonts at import, and a failed download silently empties its module list.
    print('build environment:', paddleenv.apply(), flush=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', choices=('server', 'desktop', 'all'), default='all')
    ap.add_argument('--onefile', action='store_true', help='single-file executables (slower start)')
    ap.add_argument('--no-archive', action='store_true')
    ap.add_argument('--clean', action='store_true')
    ap.add_argument('--edition', choices=('lean', 'full'), default='lean',
                    help='full = also EasyOCR and PaddleOCR (run scripts/install_full_edition.py first)')
    args = ap.parse_args()
    targets = ['server', 'desktop'] if args.target == 'all' else [args.target]
    outputs = []
    for t in targets:
        produced = build(t, args.onefile, args.clean, args.edition)
        label = t + ('-full' if args.edition == 'full' else '')
        outputs.append(produced if args.no_archive else archive(produced, label))
    print('\n'.join(outputs))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
