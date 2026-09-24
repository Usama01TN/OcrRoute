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
