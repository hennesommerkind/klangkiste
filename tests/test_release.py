"""Erscheinungstermine (Adventskalender) – Test ohne Box.

    python3 tests/test_release.py
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klangkiste import display  # noqa: E402
from klangkiste.library import Library  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        lib = Library(tmp)
        series = "Hörspiele/Advent"
        days = [*range(1, 24, 2), 24]                     # ungerade Tage und der 24.
        for i, _ in enumerate(days, 1):
            d = Path(tmp) / series / f"Folge {i}"
            d.mkdir(parents=True)
            (d / "a.mp3").write_bytes(b"x")
        at = lambda *a: setattr(lib, "now", lambda: datetime(2026, *a))  # noqa: E731

        at(11, 30, 12, 0)
        assert len(lib.all_files(series)) == 13 and lib.newest(series) is None      # ohne Plan: alles da
        lib.set_release(series, {f"Folge {i}": f"2026-12-{t:02d}" for i, t in enumerate(days, 1)}, "06:00")
        assert lib.all_files(series) == [] and lib.newest(series) is None           # vor dem 1.: nichts
        assert lib.all_files(series + "/Folge 5") == [series + "/Folge 5/a.mp3"]    # einzeln geht immer

        at(12, 1, 5, 59)
        assert lib.all_files(series) == []
        at(12, 1, 6, 0)
        assert lib.all_files(series) == [series + "/Folge 1/a.mp3"]
        at(12, 4, 20, 0)                                                            # gerader Tag: weiter Folge 2
        assert len(lib.all_files(series)) == 2 and lib.newest(series) == series + "/Folge 2"
        assert lib.is_locked(series + "/Folge 3") and not lib.is_locked(series + "/Folge 2")
        assert display._level(lib, series + "/Folge 1")[1] == ["Folge 1", "Folge 2"]
        at(12, 23, 7, 0)
        assert lib.newest(series) == series + "/Folge 12"
        at(12, 24, 7, 0)
        assert len(lib.all_files(series)) == 13 and lib.newest(series) == series + "/Folge 13"

        at(12, 4, 20, 0)
        lib.rename(series + "/Folge 3", "Folge 3 - Nikolaus")                       # Termin wandert mit
        assert lib.is_locked(series + "/Folge 3 - Nikolaus")
        f = {x["name"]: x for x in lib.folders()}
        assert f[series]["plan"] and f[series + "/Folge 3 - Nikolaus"]["locked"]
        assert f[series + "/Folge 2"]["release"] == "2026-12-03" and not f[series + "/Folge 2"]["locked"]

        # Uhr: nie früher als schon gesehen (Start ohne Netz)
        del lib.now
        (Path(tmp) / ".uhr").write_text("2099-01-01T00:00", "utf-8")
        lib._clock = None
        assert lib.now().year == 2099 and len(lib.all_files(series)) == 13

        lib.set_release(series, {})
        assert not (Path(tmp) / series / ".erscheinen.json").exists()
    print("OK – alle Fälle bestanden")


if __name__ == "__main__":
    main()
