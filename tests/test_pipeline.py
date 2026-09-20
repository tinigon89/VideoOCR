"""Kiểm tra luồng điều phối bằng transcriber giả - không cần GPU, không cần ffmpeg."""

import threading

import pytest

from videoocr import pipeline
from videoocr.config import DEFAULT_ZH_PROMPT, Settings
from videoocr.subtitle import Word


class FakeTranscriber:
    """Đóng thế Transcriber, trả về sẵn một câu tiếng Trung."""

    words = [
        Word(0.0, 0.5, "今天"),
        Word(0.5, 1.0, "天氣"),   # phồn thể, để kiểm tra phần chuyển đổi
        Word(1.0, 1.5, "很好"),
        Word(1.5, 1.8, "。"),
    ]

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.device = "cpu"
        self.compute_type = "int8"
        self.loaded = False
        self.seen_prompt = None

    def supports_language(self, language):
        return True

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe(self, wav, language=None, initial_prompt=None, on_progress=None):
        self.seen_prompt = initial_prompt
        if on_progress is not None:
            on_progress(1.8)
        return list(self.words), language or "zh"


@pytest.fixture
def stub_environment(monkeypatch, tmp_path):
    """Thay ffmpeg và faster-whisper bằng bản giả."""
    created = {}

    def fake_extract(video, out_wav):
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        out_wav.write_bytes(b"RIFF")
        return out_wav

    def make_transcriber(**kwargs):
        created["instance"] = FakeTranscriber(**kwargs)
        return created["instance"]

    monkeypatch.setattr(pipeline.audio_mod, "extract_audio", fake_extract)
    monkeypatch.setattr(pipeline.audio_mod, "probe_duration", lambda video: 10.0)
    monkeypatch.setattr(pipeline.gpu, "check_driver", lambda: None)
    monkeypatch.setattr(pipeline.gpu, "gpu_name", lambda: None)
    monkeypatch.setattr(pipeline, "Transcriber", make_transcriber)
    return created


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "phim1.mp4").write_bytes(b"x")
    (tmp_path / "phim2.mp4").write_bytes(b"x")
    return tmp_path


def make_settings(folder, **overrides):
    settings = Settings(input_dir=str(folder), language="zh")
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class TestPromptSelection:
    def test_chinese_gets_the_mandarin_prompt(self):
        assert pipeline._prompt_for(Settings(language="zh")) == DEFAULT_ZH_PROMPT

    def test_english_does_not_get_the_chinese_prompt(self):
        assert pipeline._prompt_for(Settings(language="en")) is None

    def test_custom_prompt_applies_to_any_language(self):
        settings = Settings(language="en", initial_prompt="Hello there.")
        assert pipeline._prompt_for(settings) == "Hello there."

    def test_blank_prompt_becomes_none(self):
        assert pipeline._prompt_for(Settings(language="zh", initial_prompt="  ")) is None


class TestBuildSubtitle:
    def test_converts_to_simplified(self):
        cues = pipeline._build_subtitle(
            FakeTranscriber.words, "zh", Settings(chinese_variant="s"))
        assert "天气" in cues[0].text          # 氣 -> 气
        assert "天氣" not in cues[0].text

    def test_keeps_traditional_when_asked(self):
        cues = pipeline._build_subtitle(
            FakeTranscriber.words, "zh", Settings(chinese_variant="t"))
        assert "天氣" in cues[0].text

    def test_no_conversion_for_english(self):
        words = [Word(0.0, 0.5, " hello"), Word(0.5, 1.0, " world")]
        cues = pipeline._build_subtitle(words, "en", Settings(chinese_variant="s"))
        assert cues[0].text == "hello world"

    def test_junk_is_filtered_out(self):
        words = [Word(0.0, 2.0, "请不吝点赞订阅转发打赏支持明镜与点点栏目")]
        assert pipeline._build_subtitle(words, "zh", Settings()) == []


class TestWriteSrt:
    def test_writes_utf8_with_bom(self, tmp_path):
        from videoocr.subtitle import Cue

        target = tmp_path / "out.srt"
        pipeline._write_srt([Cue(0.0, 1.0, "你好")], target)
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")
        assert target.read_text(encoding="utf-8-sig").startswith("1\n")

    def test_leaves_no_partial_file_behind(self, tmp_path):
        from videoocr.subtitle import Cue

        target = tmp_path / "out.srt"
        pipeline._write_srt([Cue(0.0, 1.0, "你好")], target)
        assert list(tmp_path.glob("*.part")) == []


class TestRun:
    def test_writes_one_srt_per_video(self, stub_environment, folder):
        summary = pipeline.run(make_settings(folder))
        assert len(summary.done) == 2
        assert (folder / "phim1.srt").exists()
        assert (folder / "phim2.srt").exists()

    def test_skips_videos_that_already_have_srt(self, stub_environment, folder):
        (folder / "phim1.srt").write_text("1\n", encoding="utf-8")
        summary = pipeline.run(make_settings(folder))
        assert [p.name for p in summary.skipped] == ["phim1.mp4"]
        assert len(summary.done) == 1

    def test_overwrite_redoes_everything(self, stub_environment, folder):
        (folder / "phim1.srt").write_text("cũ", encoding="utf-8")
        summary = pipeline.run(make_settings(folder, overwrite=True))
        assert len(summary.done) == 2
        assert "cũ" not in (folder / "phim1.srt").read_text(encoding="utf-8-sig")

    def test_one_bad_file_does_not_stop_the_batch(self, stub_environment, folder, monkeypatch):
        def explode(video, out_wav):
            if video.name == "phim1.mp4":
                raise RuntimeError("file hỏng")
            out_wav.write_bytes(b"RIFF")
            return out_wav

        monkeypatch.setattr(pipeline.audio_mod, "extract_audio", explode)
        summary = pipeline.run(make_settings(folder))
        assert len(summary.failed) == 1
        assert len(summary.done) == 1
        assert (folder / "phim2.srt").exists()

    def test_cancel_stops_before_finishing(self, stub_environment, folder):
        cancel = threading.Event()
        cancel.set()
        summary = pipeline.run(make_settings(folder), cancel=cancel)
        assert summary.cancelled
        assert summary.done == []

    def test_emits_scan_and_finished_events(self, stub_environment, folder):
        events = []
        pipeline.run(make_settings(folder), emit=events.append)
        kinds = [e.kind for e in events]
        assert kinds[0] == "scan"
        assert kinds[-1] == "finished"
        assert kinds.count("file_done") == 2

    def test_empty_folder_finishes_cleanly(self, stub_environment, tmp_path):
        summary = pipeline.run(make_settings(tmp_path))
        assert summary.done == []
        assert summary.failed == []

    def test_missing_folder_raises(self, stub_environment, tmp_path):
        with pytest.raises(NotADirectoryError):
            pipeline.run(make_settings(tmp_path / "không-có"))

    def test_mandarin_prompt_reaches_the_model(self, stub_environment, folder):
        pipeline.run(make_settings(folder))
        assert stub_environment["instance"].seen_prompt == DEFAULT_ZH_PROMPT
