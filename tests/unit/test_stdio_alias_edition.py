# coding=utf-8
from __future__ import absolute_import, division, print_function

import sys


def test_windowed_process_gets_real_streams(monkeypatch, tmp_path):
    """--windowed executables start with sys.stdout/sys.stderr = None; faulthandler used to raise on that."""
    from ocrroute import stdio

    monkeypatch.setenv('OCRROUTE_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    path = stdio.ensureStreams('desktop')
    assert path and path.endswith('desktop.log')
    assert sys.stdout is not None and sys.stderr is not None
    print('hello from a windowed app')
    assert stdio.enableFaultHandler() is True
    sys.stdout.flush()
    assert 'hello from a windowed app' in open(path, encoding='utf-8').read()


def test_console_streams_are_left_alone(tmp_path, monkeypatch):
    from ocrroute import stdio

    monkeypatch.setenv('OCRROUTE_HOME', str(tmp_path))
    assert stdio.ensureStreams('desktop') is None  # pytest's streams are usable


def test_opencv_alias_answers_for_missing_gui_name(monkeypatch):
    from importlib import metadata

    from ocrroute import opencv_alias

    installed = {'opencv-contrib-python-headless': 'HEADLESS-CONTRIB'}

    def fakeDistribution(name):
        if name in installed:
            return installed[name]
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(opencv_alias, 'distribution', fakeDistribution)
    finder = opencv_alias.OpenCvAliasFinder()

    class Ctx(object):
        def __init__(self, name):
            self.name = name

    assert list(finder.find_distributions(Ctx('opencv-contrib-python'))) == ['HEADLESS-CONTRIB']
    assert list(finder.find_distributions(Ctx('opencv_contrib_python'))) == ['HEADLESS-CONTRIB']  # normalised
    assert list(finder.find_distributions(Ctx('some-other-package'))) == []
    installed['opencv-contrib-python'] = 'REAL-GUI-BUILD'
    assert list(finder.find_distributions(Ctx('opencv-contrib-python'))) == []  # never shadows the real one


def test_edition_reports_source_when_not_frozen():
    from ocrroute import edition

    info = edition.info()
    assert info['edition'] == 'source' and info['frozen'] is False


def test_dashboard_does_not_alert_for_engines_absent_by_design(client):
    client.post('/panel/setup', data={'username': 'admin', 'password': 'password123', 'confirm': 'password123'}, follow_redirects=False)
    client.post('/panel/login', data={'username': 'admin', 'password': 'password123'}, follow_redirects=False)
    html = client.get('/panel/').text
    assert 'EasyOCR unavailable' not in html and 'PaddleOCR unavailable' not in html


def test_selftest_reports_each_module(capsys):
    from ocrroute import selftest

    failed = selftest.run(['json', 'definitely_not_a_module_xyz'])
    out = capsys.readouterr().out
    assert failed == 1
    assert 'SELFTEST OK   json' in out and 'SELFTEST FAIL definitely_not_a_module_xyz' in out
    assert 'ModuleNotFoundError' in out  # full traceback, not just a flag


def test_native_files_collects_extensions_loaded_by_path(tmp_path, monkeypatch):
    """torchvision >= 0.29 loads _C_stable by path; its files and <pkg>.libs must be collected at their own paths."""
    import importlib
    import os
    import sys

    site = tmp_path / 'site'
    pkg = site / 'fakevision'
    (pkg / '.dylibs').mkdir(parents=True)
    (site / 'fakevision.libs').mkdir()
    for f in ('__init__.py', '_C_stable.so', 'image_stable.pyd', 'jpeg8.dll', '.dylibs/libz.1.dylib', 'readme.txt'):
        (pkg / f).write_text('x')
    (site / 'fakevision.libs' / 'libpng16.abc12345.so.16').write_text('x')
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
    import build_executable as b

    got = {(os.path.basename(src), dest) for src, dest in b.nativeFiles('fakevision')}
    assert got == {('_C_stable.so', 'fakevision'), ('image_stable.pyd', 'fakevision'), ('jpeg8.dll', 'fakevision'),
                   ('libz.1.dylib', os.path.join('fakevision', '.dylibs')),
                   ('libpng16.abc12345.so.16', 'fakevision.libs')}
    assert b.nativeFiles('package_that_does_not_exist_xyz') == []


def test_runtime_hook_points_site_and_mkl_at_the_bundle(tmp_path, monkeypatch):
    """Paddle 3.0 crashed on site.USER_SITE=None; Paddle 3.3 could not find libmklml_intel.so in the bundle."""
    import os
    import runpy
    import site
    import sys

    (tmp_path / 'paddle' / 'libs').mkdir(parents=True)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(site, 'USER_SITE', None)
    monkeypatch.setattr(site, 'getsitepackages', lambda *a: ['/build/machine/site-packages'])
    monkeypatch.delenv('FLAGS_mklml_dir', raising=False)
    hook = os.path.join(os.path.dirname(__file__), '..', '..', 'scripts', 'pyi_rth_ocrroute_site.py')
    runpy.run_path(hook)
    assert site.getsitepackages()[0] == str(tmp_path)  # the bundle is searched first
    assert site.USER_SITE == str(tmp_path)  # never None: Paddle 3.0 joins it without checking
    assert os.environ['FLAGS_mklml_dir'] == os.path.join(str(tmp_path), 'paddle', 'libs')
    assert os.path.sep.join([site.USER_SITE, 'paddle', 'libs'])  # the exact expression that crashed


def test_mkldnn_is_off_by_default():
    import os

    from ocrroute import enginelib  # noqa: F401

    assert os.environ.get('PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT') in ('False', 'false', '0', 'True', 'true', '1')


def test_paddle_environment_defaults(monkeypatch):
    from ocrroute import paddleenv

    monkeypatch.setattr(paddleenv, 'paddleVersion', lambda: (3, 0))
    env = {}
    paddleenv.apply(env)
    assert env['PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT'] == 'False'
    assert env['PADDLE_PDX_MODEL_SOURCE'] == 'huggingface'  # BOS answers 403 for the Paddle-3.0 exports
    monkeypatch.setattr(paddleenv, 'paddleVersion', lambda: (3, 3))
    env = {}
    paddleenv.apply(env)
    assert 'PADDLE_PDX_MODEL_SOURCE' not in env  # newer Paddle keeps PaddleX's default source
    env = {'PADDLE_PDX_MODEL_SOURCE': 'huggingface', 'PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT': 'True'}
    monkeypatch.setattr(paddleenv, 'paddleVersion', lambda: (3, 0))
    paddleenv.apply(env)
    assert env['PADDLE_PDX_MODEL_SOURCE'] == 'huggingface' and env['PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT'] == 'True'


def test_declared_dependencies_follow_requirements():
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
    import build_executable as b

    deps = b.declaredDependencies('fastapi')
    assert 'starlette' in deps and 'pydantic' in deps  # declared, installed, followed recursively
    assert b.declaredDependencies('distribution-that-does-not-exist-xyz') == []


def test_bundled_font_is_used_first_and_ships_its_license():
    """PaddleX 3.0 downloads a font at import from a URL that answers 403 everywhere; OcrRoute ships its own."""
    import os

    from ocrroute import paddleenv

    assert os.path.isfile(paddleenv.BUNDLED_FONT) and os.path.getsize(paddleenv.BUNDLED_FONT) > 100000
    assert paddleenv.systemFont() == paddleenv.BUNDLED_FONT  # deterministic on every OS
    assert os.path.isfile(os.path.join(os.path.dirname(paddleenv.BUNDLED_FONT), 'LICENSE-DejaVu.txt'))
    env = {}
    paddleenv.apply(env)
    assert env['PADDLE_PDX_LOCAL_FONT_FILE_PATH'] == paddleenv.BUNDLED_FONT


def test_paddlex30_model_sources_patch(monkeypatch):
    """BOS answers 403 for Paddle-3.0 exports; PaddleX 3.0.3 must use Hugging Face for every OCR-pipeline model."""
    import types
    from importlib import metadata

    from ocrroute import paddleenv

    fake = types.ModuleType('official_models')
    fake.HUGGINGFACE_MODELS = ['PP-OCRv5_server_det', 'UVDoc']
    fake.is_huggingface_accessible = lambda: False
    monkeypatch.setattr(metadata, 'version', lambda name: '3.7.2')
    assert paddleenv.patchOfficialModels(fake) is False  # newer PaddleX is left alone
    monkeypatch.setattr(metadata, 'version', lambda name: '3.0.3')
    assert paddleenv.patchOfficialModels(fake) is True
    assert 'PP-LCNet_x1_0_textline_ori' in fake.HUGGINGFACE_MODELS
    assert fake.is_huggingface_accessible is paddleenv.huggingFaceReachable
    assert paddleenv.patchOfficialModels(fake) is False  # idempotent
    monkeypatch.setattr(paddleenv, 'paddleVersion', lambda: (3, 0))
    env = {}
    paddleenv.apply(env)
    assert env['PADDLE_PDX_MODEL_SOURCE'] == 'huggingface'  # never the dead BOS server


def test_surya_install_plan_targets_the_pytorch_only_release(monkeypatch):
    """Surya 2 (0.20+) needs a vLLM / llama.cpp server; the self-contained engine is Surya 0.17.x."""
    import sys

    from ocrroute.runtime import engineinstall as ei

    plan = ei.planFor('engines.local.suryaocr')
    assert plan['extra'] == 'surya' and 'surya-ocr>=0.17,<0.20' in plan['packages']
    assert any(p.startswith('transformers') and '<5' in p for p in plan['packages'])
    from ocrroute import edition

    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(edition, 'name', lambda: 'lean')
    assert 'Full edition' in ei.planFor('engines.local.suryaocr')['hint']  # lean points to the Full edition


def test_surya_collection_skips_its_demo_scripts(monkeypatch, tmp_path):
    """Regression: the old check read every flag value and flagged the --exclude-module values themselves.
    Uses a fake `surya` package so it runs whether or not Surya is installed."""
    import importlib
    import os
    import sys

    pkg = tmp_path / 'surya'
    for rel in ('__init__.py', 'recognition/__init__.py', 'recognition/loader.py', 'detection/__init__.py',
                'scripts/__init__.py', 'scripts/streamlit_app.py', 'debug/__init__.py', 'debug/text.py', 'settings.py'):
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text('')
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [m for m in sys.modules if m == 'surya' or m.startswith('surya.')]:
        monkeypatch.delitem(sys.modules, name)
    importlib.invalidate_caches()
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
    import build_executable as b

    args = b.suryaArgs()
    pairs = list(zip(args[0::2], args[1::2]))
    hidden = {v for f, v in pairs if f == '--hidden-import'}
    assert hidden == {'surya', 'surya.settings', 'surya.recognition', 'surya.recognition.loader', 'surya.detection'}
    assert {v for f, v in pairs if f == '--exclude-module'} == {'surya.scripts', 'surya.debug'}
    assert ('--collect-data', 'surya') in pairs
    assert 'surya.scripts' not in sys.modules  # nothing was imported to list the modules
