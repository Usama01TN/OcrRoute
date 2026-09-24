# coding=utf-8
"""
None
"""

from typer.testing import CliRunner

from ocrroute.cli.main import app

runner = CliRunner()


def test_cli_version_and_engines(ctx):
    r = runner.invoke(app, ['version', '--json'])
    assert r.exit_code == 0 and "0.4.3" in r.stdout
    r = runner.invoke(app, ['engines', 'list', '--json'])
    assert r.exit_code == 0 and 'FakeOcr' in r.stdout


def test_cli_provider_route_ocr(ctx, sample_png, tmp_path):
    (tmp_path / 'in.png').write_bytes(sample_png)
    assert runner.invoke(app, ['provider', 'add', 'FakeOcr', 'fake-main']).exit_code == 0
    assert runner.invoke(app, ['route', 'add', 'chain', 'fake-main', '--default']).exit_code == 0
    r = runner.invoke(app, ['route', 'simulate', 'chain'])
    assert r.exit_code == 0 and 'FakeOcr' in r.stdout
    r = runner.invoke(
        app, ['ocr', str(tmp_path / 'in.png'), '-o', 'text,json,hocr', '--output-dir', str(tmp_path / 'out')]
    )
    assert r.exit_code == 0, r.stdout
    assert (tmp_path / 'out' / 'in.txt').read_text().startswith('hello world')
    r = runner.invoke(app, ['runs', 'list', '--json'])
    assert r.exit_code == 0 and 'succeeded' in r.stdout
    r = runner.invoke(app, ['doctor', '--json'])
    assert r.exit_code == 0 and 'engines_total' in r.stdout


def test_cli_missing_file(ctx, tmp_path):
    r = runner.invoke(app, ['ocr', str(tmp_path / 'nope.png')])
    assert r.exit_code != 0
