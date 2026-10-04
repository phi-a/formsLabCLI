"""The GUI's login and its recorded-run reader."""
import json
import os
import time

import pytest

from formslab import config
from formslab.gui import auth, runs

from gui_helpers import write_run


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(auth, "ITERATIONS", 1000)
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0.01)


# --- login ---------------------------------------------------------------------------

def test_a_login_is_stored_hashed_and_checked():
    assert not auth.configured()
    auth.set_login("darkroom", "s3cret!")
    assert auth.configured()
    text = auth.load() and config.state_path(auth.FILE).read_text(encoding="utf-8")
    assert "s3cret!" not in text and "darkroom" in text                       # only a hash is kept
    assert auth.check("darkroom", "s3cret!")
    assert not auth.check("darkroom", "wrong")
    assert not auth.check("someone", "s3cret!")
    assert not auth.check("darkroom", "")


def test_no_login_means_no_access():
    assert auth.check("anyone", "anything") is False


def test_a_login_needs_both_parts():
    with pytest.raises(ValueError):
        auth.set_login("", "x")
    with pytest.raises(ValueError):
        auth.set_login("x", "")


@pytest.mark.skipif(os.name == "nt", reason="Windows ignores POSIX modes")
def test_the_login_file_is_private():
    auth.set_login("a", "b")
    assert config.state_path(auth.FILE).stat().st_mode & 0o077 == 0


def test_a_wrong_password_waits(monkeypatch):
    auth.set_login("a", "b")
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0.3)
    start = time.monotonic()
    auth.check("a", "nope")
    assert time.monotonic() - start >= 0.3


def test_sessions_expire_and_can_be_dropped(monkeypatch):
    s = auth.Sessions()
    token = s.new("u")
    assert s.user(token) == "u" and s.user("nonsense") is None and s.user(None) is None
    s.drop(token)
    assert s.user(token) is None
    monkeypatch.setattr(auth, "SESSION_S", -1)
    assert s.user(s.new("u")) is None


# --- recorded runs ---------------------------------------------------------------------

def test_scan_lists_a_run_with_its_columns(tmp_path):
    write_run(tmp_path)
    found = runs.scan([tmp_path])
    assert found["unsupported"] == []
    [run] = found["runs"]
    assert run["id"] == "tvac_20261004T120000Z" and run["name"] == "tvac"
    assert run["columns"] == [{"name": "TC01", "unit": "K"}, {"name": "chamberP", "unit": "Torr"},
                              {"name": "PSU1_CH1_ON", "unit": ""}]
    assert run["started"] == 1791115200.0                      # 2026-10-04T12:00:00Z


def test_a_run_that_grew_a_variable_is_one_run_of_stitched_parts(tmp_path):
    write_run(tmp_path, header=("TC01 [K]",), rows=[["2026-10-04T12:00:00Z", "293"], ["2026-10-04T12:00:10Z", "294"]])
    write_run(tmp_path, part=1, header=("TC01 [K]", "chamberP [Torr]"),
              rows=[["2026-10-04T12:00:20Z", "295", "5.5"]])
    [run] = runs.scan([tmp_path])["runs"]
    assert len(run["parts"]) == 2 and [c["name"] for c in run["columns"]] == ["TC01", "chamberP"]
    out = runs.series({**run, "_dir": str(tmp_path)})
    assert out["series"]["TC01"] == [293.0, 294.0, 295.0]
    assert out["series"]["chamberP"] == [None, None, 5.5]            # absent in the first part
    assert out["units"] == {"TC01": "K", "chamberP": "Torr"} and out["rows"] == 3


def test_a_failed_reading_is_a_gap_not_a_number(tmp_path):
    write_run(tmp_path)
    run = {**runs.scan([tmp_path])["runs"][0], "_dir": str(tmp_path)}
    out = runs.series(run, ["TC01", "nope"])
    assert out["series"] == {"TC01": [293.15, 294.15, None]}           # nan -> None; unknown name ignored
    assert out["t"][1] - out["t"][0] == 10.0


def test_a_half_written_last_row_is_not_shown(tmp_path):
    path = write_run(tmp_path)
    with path.open("a", encoding="utf-8") as f:
        f.write("3,2026-10-04T12:00:30.000Z,295.1")                  # the host is mid-write: no newline
    run = {**runs.scan([tmp_path])["runs"][0], "_dir": str(tmp_path)}
    assert runs.series(run)["rows"] == 3


def test_other_files_are_listed_as_unsupported_not_guessed_at(tmp_path):
    write_run(tmp_path)
    (tmp_path / "pumpdown_20261001T143932Z.csv").write_text("utc,elapsed_s,pressure_Torr\n", encoding="utf-8")
    (tmp_path / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "empty_20261004T130000Z.csv").write_text("", encoding="utf-8")
    found = runs.scan([tmp_path])
    assert [r["id"] for r in found["runs"]] == ["tvac_20261004T120000Z"]
    assert found["unsupported"] == ["empty_20261004T130000Z.csv", "notes.csv", "pumpdown_20261001T143932Z.csv"]


def test_long_runs_are_thinned_but_keep_their_last_row(tmp_path):
    rows = [[f"2026-10-04T12:{i // 60:02d}:{i % 60:02d}Z", str(i), "0", "0"] for i in range(3000)
            if i < 3600]
    write_run(tmp_path, rows=rows)
    run = {**runs.scan([tmp_path])["runs"][0], "_dir": str(tmp_path)}
    out = runs.series(run, ["TC01"], max_points=500)
    assert 450 < len(out["t"]) <= 501 and out["rows"] == 3000
    assert out["series"]["TC01"][-1] == 2999.0 and len(out["series"]["TC01"]) == len(out["t"])


def test_a_run_is_found_by_scanning_never_by_building_a_path(tmp_path):
    write_run(tmp_path)
    assert runs.find([tmp_path], "tvac_20261004T120000Z")["_dir"] == str(tmp_path)
    for bad in ("../tvac_20261004T120000Z", "..\\x", "tvac_20261004T120000Z.csv", "nope", ""):
        assert runs.find([tmp_path], bad) is None


def test_column_cells():
    assert runs.column("TC01 [K]") == ("TC01", "K")
    assert runs.column("PSU1_CH1_ON") == ("PSU1_CH1_ON", "")
    assert runs.column("a b [x y]") == ("a b", "x y")


def test_the_same_folder_listed_twice_is_one_run(tmp_path):
    write_run(tmp_path)
    assert len(runs.scan([tmp_path, tmp_path])["runs"]) == 1
