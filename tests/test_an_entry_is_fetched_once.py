"""An entry typed into the builder is fetched once, and read only whole.

The builder's preview and its estimate ask for the structure together as an
identifier is typed. Each found it missing from the cache and fetched it
(5O3L from RCSB twice in a second), and the second could read the first's
file half written, since it was written where it is read.
"""

from __future__ import annotations

import threading
import time

from fastmdxplora.gui import preview

WHOLE = "HEADER    TEST\n" + "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n" * 200 + "END\n"


def test_asked_twice_at_once_it_is_fetched_once_and_read_whole(tmp_path, monkeypatch) -> None:
    fetched: list[str] = []

    def slow_fetch(pdb_id, dest):
        fetched.append(pdb_id)
        with open(dest, "w", encoding="utf-8") as out:
            for line in WHOLE.splitlines(keepends=True):
                out.write(line)
                out.flush()
                time.sleep(0.0005)
        return dest

    monkeypatch.setattr(preview, "_cache", lambda: tmp_path)
    monkeypatch.setattr("fastmdxplora.setup.pipeline._fetch_pdb_from_rcsb", slow_fetch)
    read: list[str] = []
    start = threading.Barrier(3)

    def ask() -> None:
        start.wait()
        read.append(preview._fetched("5O3L").read_text(encoding="utf-8"))

    def watch() -> None:
        # What a reader finds at the entry's place while it is fetched.
        start.wait()
        target = tmp_path / "5O3L.pdb"
        for _ in range(400):
            if target.is_file():
                read.append(target.read_text(encoding="utf-8"))
                return
            time.sleep(0.001)

    threads = [threading.Thread(target=ask), threading.Thread(target=ask),
               threading.Thread(target=watch)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert fetched == ["5O3L"]
    assert read and all(text == WHOLE for text in read)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["5O3L.pdb"]
