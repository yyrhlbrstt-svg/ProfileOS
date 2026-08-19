"""DWG import tests.

DWG cannot be parsed here and the code does not pretend otherwise, so what is
tested is the honest part: that a missing converter produces a message naming
what to install, that an installed converter is actually used, and that a
converted drawing goes through the normal pipeline and comes out as a section.

The converter is stood in for by a small script, because the point under test
is the plumbing around the external program, not the program itself.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from profileos.core.errors import DxfReadError
from profileos.geometry import load_section
from profileos.geometry.dwg import (
    CONVERTER_ENV,
    CONVERTERS,
    available_converters,
    convert_dwg,
    converter_status,
    is_dwg,
)


@pytest.fixture
def no_converters(monkeypatch, tmp_path):
    """A machine with nothing installed."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv(CONVERTER_ENV, raising=False)


@pytest.fixture
def fake_dwg2dxf(monkeypatch, tmp_path, mullion_dxf):
    """A stand-in for LibreDWG that copies a known DXF to the output path."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    script = binaries / "dwg2dxf"
    script.write_text(
        "#!/bin/sh\n"
        '# usage: dwg2dxf -o <out.dxf> <in.dwg>\n'
        f'cp "{mullion_dxf}" "$2"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(CONVERTER_ENV, raising=False)
    return script


@pytest.fixture
def a_dwg(tmp_path):
    path = tmp_path / "mullion.dwg"
    path.write_bytes(b"AC1032" + b"\x00" * 64)  # a DWG header and nothing useful
    return path


class TestRecognition:
    @pytest.mark.parametrize("name", ["a.dwg", "A.DWG", "dir/b.Dwg"])
    def test_a_dwg_is_recognised_whatever_the_case(self, name):
        assert is_dwg(name)

    def test_a_dxf_is_not_a_dwg(self, mullion_dxf):
        assert not is_dwg(mullion_dxf)


class TestWithoutAConverter:
    def test_the_message_says_what_to_install(self, no_converters, a_dwg):
        with pytest.raises(DxfReadError) as excinfo:
            convert_dwg(a_dwg)
        message = str(excinfo.value)
        for converter in CONVERTERS:
            assert converter.name in message
            assert converter.source in message
        assert CONVERTER_ENV in message

    def test_the_status_page_says_where_to_get_one(self, no_converters):
        status = converter_status()
        assert set(status) == {converter.name for converter in CONVERTERS}
        assert all("not installed" in value for value in status.values())

    def test_loading_a_dwg_fails_with_that_message_not_a_parse_error(
        self, no_converters, a_dwg
    ):
        with pytest.raises(DxfReadError, match="converter"):
            load_section(str(a_dwg))


class TestWithAConverter:
    def test_an_installed_converter_is_found(self, fake_dwg2dxf):
        found = available_converters()
        assert [converter.name for converter, _ in found] == ["LibreDWG dwg2dxf"]
        assert found[0][1] == str(fake_dwg2dxf)

    def test_a_dwg_converts_to_a_dxf(self, fake_dwg2dxf, a_dwg, tmp_path):
        out = tmp_path / "out"
        produced = convert_dwg(a_dwg, out_dir=out)
        assert produced.is_file()
        assert produced.suffix == ".dxf"
        assert produced.parent == out

    def test_a_dwg_loads_as_a_section(self, fake_dwg2dxf, a_dwg):
        """The whole point: after conversion it is an ordinary section."""
        section = load_section(str(a_dwg))
        assert section.area > 0
        assert section.topology.chamber_count > 0
        # The section remembers the drawing the operator actually opened,
        # not the temporary DXF it was converted into.
        assert section.source == str(a_dwg)

    def test_the_temporary_dxf_does_not_survive_the_import(self, fake_dwg2dxf, a_dwg):
        before = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("profileos-dwg-*"))
        load_section(str(a_dwg))
        after = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("profileos-dwg-*"))
        assert after == before

    def test_a_converter_that_produces_nothing_is_reported(
        self, monkeypatch, tmp_path, a_dwg
    ):
        binaries = tmp_path / "bin"
        binaries.mkdir()
        script = binaries / "dwg2dxf"
        script.write_text("#!/bin/sh\necho 'unsupported version' >&2\nexit 1\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
        with pytest.raises(DxfReadError) as excinfo:
            convert_dwg(a_dwg, out_dir=tmp_path / "out")
        assert "unsupported version" in str(excinfo.value)
