# coding=utf-8
"""
None
"""

from typer.testing import CliRunner

from ocrroute.cli.main import app

runner = CliRunner()


def test_cli_version_and_engines(ctx):
    r = runner.invoke(app, ['version', '--json'])
    assert r.exit_code == 0 and "0.4.6" in r.stdout
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


def test_json_output_survives_a_closed_pipe(monkeypatch):
    """`ocrroute ... --json | head` must exit 0: EPIPE on POSIX, EINVAL (errno 22) on Windows."""
    import errno
    import io
    import sys

    import pytest

    from ocrroute.cli import main as cli

    class ClosedPipe(io.RawIOBase):
        def __init__(self, err):
            self.err = err

        def writable(self):
            return True

        def write(self, b):
            raise self.err

        def fileno(self):
            raise OSError('no fd')

    for err in (BrokenPipeError(errno.EPIPE, 'Broken pipe'), OSError(errno.EINVAL, 'Invalid argument')):
        fake = io.TextIOWrapper(io.BufferedWriter(ClosedPipe(err)), encoding='utf-8')
        monkeypatch.setattr(sys, 'stdout', fake)
        with pytest.raises(SystemExit) as exit_info:
            cli.emitJson([{'id': 'x' * 10000}])
        assert exit_info.value.code == 0
    other = OSError(errno.ENOSPC, 'No space left')
    monkeypatch.setattr(sys, 'stdout', io.TextIOWrapper(io.BufferedWriter(ClosedPipe(other)), encoding='utf-8'))
    with pytest.raises(OSError):
        cli.emitJson({'a': 1})  # real I/O errors are still reported


def test_json_output_is_plain_utf8(capsys):
    import json

    from ocrroute.cli import main as cli

    cli.emitJson({'name': 'مرحبا', 'n': 1})
    out = capsys.readouterr().out
    assert '\x1b[' not in out  # no ANSI colour codes in machine output
    assert json.loads(out) == {'name': 'مرحبا', 'n': 1}
