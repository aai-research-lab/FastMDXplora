"""A shared study opens from its DOI, its Zenodo address or the file.

`fastmdx gui --open` and **Open a shared study** open nothing until every
file is the one the packing list names: downloaded up to a limit and
checked against the MD5 Zenodo recorded, unpacked only inside a folder of
its own, each file of the size and SHA-256 listed and nothing unlisted,
nothing packing never writes. Then the study's own folder is put back in
its records, each in its own syntax, and where it came from recorded.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from fastmdxplora.sharing import PROFILE, STUDY, ShareRefused
from fastmdxplora.sharing.crate import METADATA, check
from fastmdxplora.sharing.opening import open_shared
from tests.test_a_shared_study_goes_to_zenodo import _share_to_zenodo
from tests.test_a_shared_study_goes_to_zenodo import zenodo as _zenodo_server
from tests.test_a_study_is_shared import _packed, _study

#: The stand-in Zenodo of the draft's tests, for a study opened by its DOI.
zenodo = _zenodo_server

def _rewritten(source: Path, to: Path, change) -> Path:
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(to, "w") as new:
        for info in old.infolist():
            body = change(info.filename, old.read(info.filename))
            if body is not None:
                new.writestr(info, body)
    return to


def test_a_shared_study_opens_as_its_own(tmp_path):
    out, _ = _packed(tmp_path)
    opened = open_shared(str(out), workspace=tmp_path / "workspace")
    assert opened.name == "the-1abc-study"
    config = (opened / "resolved_config.yml").read_text()
    assert f"output: {opened.resolve()}" in config and STUDY not in config
    record = json.loads((opened / "shared_from.json").read_text())
    assert record["files_checked"] == len(check(json.loads((opened / METADATA).read_text())))
    assert "resolved_config.yml" in record["rewritten"]
    # Opened twice, it is two folders, never one over the other.
    again = open_shared(str(out), workspace=tmp_path / "workspace")
    assert again.name == "the-1abc-study-2"


@pytest.mark.parametrize("tamper, code", [
    (lambda name, body: body + b"\n" if name == "report/report.md" else body,
     "environment.share.unverified"),
    (lambda name, body: None if name == "analysis/rmsd/rmsd.dat" else body,
     "environment.share.unverified"),
    (lambda name, body: None if name == METADATA else body, "environment.share.not_a_study"),
])
def test_an_archive_not_as_it_was_packed_is_not_opened(tmp_path, tamper, code):
    out, _ = _packed(tmp_path)
    changed = _rewritten(out, tmp_path / "changed.zip", tamper)
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(changed), workspace=tmp_path / "workspace")
    assert refused.value.refusal.code == code
    assert not any((tmp_path / "workspace").glob("the-1abc*"))


@pytest.mark.parametrize("member", ["../escaped.txt", "/abs.txt", "extra.txt"])
def test_a_file_outside_or_unlisted_is_refused(tmp_path, member):
    out, _ = _packed(tmp_path)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr(member, "x")
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(out), workspace=tmp_path / "workspace")
    assert refused.value.refusal.code == ("environment.share.unverified" if member == "extra.txt"
                                          else "environment.share.unsafe")
    assert not (tmp_path / "escaped.txt").exists()


def test_a_link_in_the_archive_is_refused(tmp_path):
    out, _ = _packed(tmp_path)
    with zipfile.ZipFile(out, "a") as archive:
        link = zipfile.ZipInfo("simulation/link.pdb")
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(out), workspace=tmp_path / "workspace")
    assert refused.value.refusal.code == "environment.share.unsafe"


def test_a_newer_profile_is_named_and_refused(tmp_path):
    out, _ = _packed(tmp_path)

    def newer(name, body):
        if name != METADATA:
            return body
        return body.replace(PROFILE.encode(), b"https://w3id.org/fastmdxplora/study/2.0")

    changed = _rewritten(out, tmp_path / "newer.zip", newer)
    with pytest.raises(ShareRefused, match="version 2.0"):
        open_shared(str(changed), workspace=tmp_path / "workspace")


def test_an_archive_over_the_limit_is_not_opened(tmp_path):
    out, _ = _packed(tmp_path)
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(out), workspace=tmp_path / "workspace", most_bytes=100)
    assert refused.value.refusal.code == "environment.share.too_large"


@pytest.mark.parametrize("member", ["agent/conversations/c.json", ".fastmdxplora_run.json",
                                    "simulation/.hidden", "previous/analysis/x.dat"])
def test_what_packing_never_writes_is_not_opened(tmp_path, member):
    out, _ = _packed(tmp_path)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr(member, "{}")
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(out), workspace=tmp_path / "workspace")
    assert refused.value.refusal.code == "environment.share.unsafe"


def test_a_preview_page_is_never_unpacked(tmp_path):
    out, _ = _packed(tmp_path)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr("ro-crate-preview.html", "<script>alert(1)</script>")
    opened = open_shared(str(out), workspace=tmp_path / "workspace")
    assert not (opened / "ro-crate-preview.html").exists()


def test_a_damaged_archive_is_refused_not_raised(tmp_path):
    out, _ = _packed(tmp_path)
    data = bytearray(out.read_bytes())
    with zipfile.ZipFile(out) as archive:
        info = archive.getinfo("report/report.md")
    # Corrupt the member's bytes after its local header.
    at = info.header_offset + 30 + len(info.filename) + 5
    data[at] ^= 0xFF
    damaged = tmp_path / "damaged.zip"
    damaged.write_bytes(bytes(data))
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(damaged), workspace=tmp_path / "workspace")
    assert refused.value.refusal.code in ("environment.share.unsafe",
                                          "environment.share.unverified")


def test_a_packing_list_of_the_wrong_shape_is_refused(tmp_path):
    out, _ = _packed(tmp_path)

    def bent(name, body):
        if name != METADATA:
            return body
        data = json.loads(body)
        data["@graph"][0]["about"] = "./"
        data["@graph"][1]["fmx:placeholders"] = "nonsense"
        return json.dumps(data).encode()

    opened = open_shared(str(_rewritten(out, tmp_path / "bent.zip", bent)),
                         workspace=tmp_path / "workspace")
    assert (opened / "resolved_config.yml").is_file()


def test_a_folder_name_yaml_reads_as_syntax_is_put_back_as_a_value(tmp_path):
    import yaml

    out, _ = _packed(tmp_path)
    opened = open_shared(str(out), into=tmp_path / "odd: name #1")
    config = yaml.safe_load((opened / "resolved_config.yml").read_text())
    assert config["output"] == str(opened.resolve())


def test_a_study_opens_from_its_doi(tmp_path, zenodo):
    study = _study(tmp_path / "study")
    assert _share_to_zenodo(study, tmp_path / "shared.zip") == 0
    opened = open_shared("https://doi.org/10.5072/zenodo.7", workspace=tmp_path / "workspace")
    record = json.loads((opened / "shared_from.json").read_text())
    assert (record["doi"], record["record"]) == ("10.5072/zenodo.7", "7")
    zenodo.wrong_md5 = True
    with pytest.raises(ShareRefused) as refused:
        open_shared("10.5072/zenodo.7", workspace=tmp_path / "again")
    assert refused.value.refusal.code == "environment.share.unverified"


@pytest.mark.parametrize("source", ["10.1000/xyz123", "https://example.org/records/7",
                                    "not-a-file.zip"])
def test_what_is_not_a_zenodo_record_or_a_file_is_said(source, tmp_path):
    with pytest.raises(ShareRefused) as refused:
        open_shared(source, workspace=tmp_path)
    assert refused.value.refusal.code == "environment.share.not_a_study"


def test_the_gui_opens_a_shared_study(tmp_path):
    import urllib.request

    from fastmdxplora.gui.server import start_dashboard_session

    out, _ = _packed(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = start_dashboard_session(output=str(workspace), host="127.0.0.1", port=0)
    try:
        def post(body):
            request = urllib.request.Request(
                session.url + "/api/open-shared", data=json.dumps(body).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            try:
                return json.loads(urllib.request.urlopen(request, timeout=60).read())
            except urllib.error.HTTPError as exc:
                return json.loads(exc.read())

        empty = post({})
        opened = post({"source": str(out)})
    finally:
        session.server.shutdown()
    assert empty["ok"] is False
    assert opened["ok"], opened
    assert Path(opened["folder"]).name == "the-1abc-study"
    assert opened["state"]["active_run"] == opened["folder"]


def test_a_service_s_gui_does_not_open_one():
    from types import SimpleNamespace

    from fastmdxplora.gui.exploration import DashboardRuntime

    said = DashboardRuntime.open_shared_study(
        SimpleNamespace(hosting=object(), snapshot=dict), "10.5281/zenodo.1")
    assert said["ok"] is False and "your own computer" in said["error"]
