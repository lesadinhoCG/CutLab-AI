from pathlib import Path

from shorts_generator.local.downloader import _resolve_local_path
from shorts_generator.local.downloader import probe_youtube_quality


def test_file_uri_with_windows_drive_letter_resolves(tmp_path: Path):
    source = tmp_path / "vÃ­deo de teste.mp4"
    source.write_bytes(b"not a real video")

    assert _resolve_local_path(source.as_uri()) == str(source.resolve())


def test_localhost_file_uri_resolves(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not a real video")
    uri = source.as_uri().replace("file:///", "file://localhost/")

    assert _resolve_local_path(uri) == str(source.resolve())


def test_quality_probe_returns_best_available_height(monkeypatch):
    calls = []

    class FakeYDL:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {
                "formats": [
                    {"height": 720, "vcodec": "avc1"},
                    {"height": 1080, "vcodec": "avc1"},
                ]
            }

    class FakeYtDlp:
        YoutubeDL = FakeYDL

    monkeypatch.setattr(
        "shorts_generator.local.downloader._import_ytdlp",
        lambda: FakeYtDlp,
    )

    assert probe_youtube_quality("https://youtube.com/watch?v=test") == {
        "max_height": 1080,
        "mode": "firefox",
    }
    assert len(calls) == 1


