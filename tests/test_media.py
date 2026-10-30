"""Zusicherungen über `python -m german_seo_bench.media`.

Ohne Netz: `_get` wird ersetzt. Geprüft wird, was beim Holen schiefgehen kann,
ohne dass es auffiele — eine Datei mit anderen Bytes, ein halber Download, ein
Schlüssel in einer Fehlermeldung.
"""

import hashlib
import io
import json
import urllib.error

import pytest

from german_seo_bench import media
from german_seo_bench.media import ENV_DIR, manifest, sync

KEY = "test-schluessel-123"


def _image(entry) -> bytes:
    return f"bild {entry.id}".encode()


@pytest.fixture
def entries(monkeypatch):
    """Zwei Einträge mit Prüfsummen, die zu `_image` passen."""
    chosen = [
        media.MediaFile(**{**e.__dict__, "sha256": hashlib.sha256(_image(e)).hexdigest()})
        for e in manifest()[:2]
    ]
    monkeypatch.setattr(media, "manifest", lambda: chosen)
    return chosen


@pytest.fixture
def target(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_DIR, str(tmp_path))
    return tmp_path


@pytest.fixture
def pixabay(monkeypatch, entries):
    """Ein Pixabay, das jede Anfrage protokolliert."""
    calls: list[str] = []
    by_id = {e.id: e for e in entries}

    def fake_get(url: str) -> bytes:
        calls.append(url)
        if url.startswith(media.PIXABAY_API):
            entry_id = int(url.split("id=")[1])
            return json.dumps(
                {"hits": [{"webformatURL": f"https://cdn.example/{entry_id}_640.jpg"}]}
            ).encode()
        return _image(by_id[int(url.rsplit("/", 1)[1].split("_")[0])])

    monkeypatch.setattr(media, "_get", fake_get)
    return calls


def _run(key=KEY) -> tuple[int, str]:
    out = io.StringIO()
    return sync(key, out=out), out.getvalue()


def test_holt_was_fehlt(target, entries, pixabay) -> None:
    code, text = _run()
    assert code == 0, text
    for entry in entries:
        assert (target / entry.path).read_bytes() == _image(entry)
    assert "2 von 2 Dateien vorhanden und unverändert" in text


def test_holt_vorhandenes_nicht_noch_einmal(target, entries, pixabay) -> None:
    _run()
    pixabay.clear()
    code, _ = _run()
    assert code == 0
    assert pixabay == []


def test_ohne_schluessel_keine_anfrage(target, entries, pixabay) -> None:
    code, text = _run(key=None)
    assert code == 1
    assert pixabay == []
    assert "PIXABAY_API_KEY" in text


def test_ohne_schluessel_reicht_es_wenn_alles_da_ist(target, entries, pixabay) -> None:
    # Wer die Bilder hat, braucht keinen Schlüssel, um sie zu prüfen.
    _run()
    code, text = _run(key=None)
    assert code == 0, text


def test_abweichende_bytes_werden_gemeldet(target, entries, pixabay) -> None:
    """Die Prüfsumme ist der Grund für das Manifest — hier die Gegenprobe.

    Würde nur auf Vorhandensein geprüft, liefe ein Lauf auf einer neu
    kodierten Fassung und sähe aus wie einer auf der Referenz.
    """
    _run()
    (target / entries[0].path).write_bytes(b"neu kodiert")
    code, text = _run()
    assert code == 1
    assert f"ABWEICHEND  {entries[0].path}" in text
    assert "1 von 2 Dateien vorhanden und unverändert" in text


def test_fehler_nennt_den_schluessel_nicht(target, entries, monkeypatch) -> None:
    def broken(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(media, "_get", broken)
    code, text = _run()
    assert code == 1
    assert KEY not in text
    assert "HTTP 400" in text
    assert entries[0].page_url in text
    assert list(target.rglob("*")) == []


def test_kein_halber_download(target, entries, pixabay, monkeypatch) -> None:
    # Bricht das Schreiben ab, darf keine Datei liegen bleiben, die beim
    # nächsten Aufruf als vorhanden gilt.
    from pathlib import Path

    def fail(self, target_path):
        raise OSError("Platte voll")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        _run()
    assert not (target / entries[0].path).exists()


class TestMedienverzeichnis:
    """Wo die Bilder liegen, wenn niemand etwas einstellt — und wo nicht."""

    def test_ohne_variable_im_arbeitsverzeichnis(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv(ENV_DIR, raising=False)
        monkeypatch.chdir(tmp_path)
        assert media.media_dir() == tmp_path.resolve() / "bench-media"

    def test_nicht_im_versteckten_verzeichnis_je_benutzer(self, monkeypatch, tmp_path) -> None:
        # Gegenprobe zum Ort selbst: Weder `~/.local/share` noch ein anderer
        # Ort unter HOME außerhalb des Arbeitsverzeichnisses.
        monkeypatch.delenv(ENV_DIR, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.chdir(tmp_path)
        assert not media.media_dir().is_relative_to(tmp_path / "home")
        assert ".local" not in media.media_dir().parts

    def test_folgt_dem_arbeitsverzeichnis(self, monkeypatch, tmp_path) -> None:
        # Bei jedem Aufruf neu bestimmt, nicht beim Import eingefroren.
        monkeypatch.delenv(ENV_DIR, raising=False)
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        monkeypatch.chdir(tmp_path / "a")
        first = media.media_dir()
        monkeypatch.chdir(tmp_path / "b")
        assert media.media_dir() != first

    def test_variable_geht_vor(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv(ENV_DIR, str(tmp_path / "fest"))
        monkeypatch.chdir(tmp_path)
        assert media.media_dir() == (tmp_path / "fest").resolve()

    def test_relative_variable_wird_absolut(self, monkeypatch, tmp_path) -> None:
        # Der Pfad steht in der Abbruchmeldung von M10 und muss dort eindeutig sein.
        monkeypatch.setenv(ENV_DIR, "relativ")
        monkeypatch.chdir(tmp_path)
        assert media.media_dir() == tmp_path.resolve() / "relativ"
        assert media.media_dir().is_absolute()

    def test_sync_nennt_und_fuellt_den_ort(self, monkeypatch, tmp_path, entries, pixabay) -> None:
        monkeypatch.delenv(ENV_DIR, raising=False)
        monkeypatch.chdir(tmp_path)
        out = io.StringIO()
        assert sync(KEY, out=out) == 0
        assert f"Medienverzeichnis: {tmp_path.resolve() / 'bench-media'}" in out.getvalue()
        assert (tmp_path / "bench-media" / entries[0].path).is_file()
