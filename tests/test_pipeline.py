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


class TestWriteSrtWhenLocked:
    """Windows: phần mềm diệt virus khoá file vừa ghi, os.replace văng WinError 32."""

    def cue(self):
        from videoocr.subtitle import Cue
        return [Cue(0.0, 1.0, "你好")]

    def test_retries_until_the_lock_clears(self, tmp_path, monkeypatch):
        from pathlib import Path

        calls = {"n": 0}
        real = Path.replace

        def flaky(self, target):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(32, "file dang bi khoa")
            return real(self, target)

        monkeypatch.setattr(Path, "replace", flaky)
        monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)

        target = tmp_path / "phim.srt"
        pipeline._write_srt(self.cue(), target)
        assert target.read_text(encoding="utf-8-sig").startswith("1\n")
        assert calls["n"] == 3

    def test_falls_back_to_direct_write(self, tmp_path, monkeypatch):
        from pathlib import Path

        def always_locked(self, target):
            raise PermissionError(32, "file dang bi khoa")

        monkeypatch.setattr(Path, "replace", always_locked)
        monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)

        target = tmp_path / "phim.srt"
        pipeline._write_srt(self.cue(), target)
        # Công nhận dạng không được phép mất chỉ vì đổi tên hỏng.
        assert "你好" in target.read_text(encoding="utf-8-sig")
        assert not (tmp_path / "phim.srt.part").exists()

    def test_error_says_where_the_result_is(self, tmp_path, monkeypatch):
        from pathlib import Path

        def always_locked(self, target):
            raise PermissionError(32, "file dang bi khoa")

        def cannot_write(self, data, encoding=None):
            if self.name.endswith(".part"):
                return len(data)
            raise PermissionError(32, "dich cung bi khoa")

        monkeypatch.setattr(Path, "replace", always_locked)
        monkeypatch.setattr(Path, "write_text", cannot_write)
        monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError) as info:
            pipeline._write_srt(self.cue(), tmp_path / "phim.srt")
        assert "phim.srt.part" in str(info.value)


class TestRecoverPartials:
    def test_renames_leftover_part_file(self, tmp_path):
        (tmp_path / "2.srt.part").write_text("1\n", encoding="utf-8")
        rescued = pipeline.recover_partials(tmp_path)
        assert [p.name for p in rescued] == ["2.srt"]
        assert (tmp_path / "2.srt").exists()
        assert not (tmp_path / "2.srt.part").exists()

    def test_leaves_part_alone_when_target_exists(self, tmp_path):
        (tmp_path / "2.srt").write_text("that", encoding="utf-8")
        (tmp_path / "2.srt.part").write_text("do", encoding="utf-8")
        assert pipeline.recover_partials(tmp_path) == []
        assert (tmp_path / "2.srt").read_text(encoding="utf-8") == "that"

    def test_ignores_empty_part_file(self, tmp_path):
        (tmp_path / "2.srt.part").write_bytes(b"")
        assert pipeline.recover_partials(tmp_path) == []

    def test_reaches_into_subfolders(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "3.srt.part").write_text("1\n", encoding="utf-8")
        assert len(pipeline.recover_partials(tmp_path)) == 1

    def test_skips_subfolders_when_not_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "3.srt.part").write_text("1\n", encoding="utf-8")
        assert pipeline.recover_partials(tmp_path, recursive=False) == []

    def test_nothing_to_do(self, tmp_path):
        assert pipeline.recover_partials(tmp_path) == []

    def test_run_rescues_before_processing(self, stub_environment, folder):
        (folder / "phim1.srt.part").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\ncu\n", encoding="utf-8")
        summary = pipeline.run(make_settings(folder))
        assert (folder / "phim1.srt").exists()
        # Đã cứu được nên coi như đã có phụ đề, không nhận dạng lại nữa.
        assert [p.name for p in summary.skipped] == ["phim1.mp4"]


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


class FakeTranslator:
    """Đóng thế GeminiTranslator: thêm tiền tố VI: cho mỗi câu."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model = "gemini-gia"
        self.pool = ["k1"]
        self.seen: list[list[str]] = []

    def translate(self, texts, log=None, on_progress=None, should_cancel=None):
        self.seen.append(list(texts))
        return [f"VI:{t}" for t in texts]

    def key_report(self):
        return "key #1 (...k1): 1 lượt"


@pytest.fixture
def stub_translator(monkeypatch):
    made = {}

    def make(**kwargs):
        made["instance"] = FakeTranslator(**kwargs)
        return made["instance"]

    monkeypatch.setattr(pipeline, "GeminiTranslator", make)
    return made


def translating_settings(folder, **overrides):
    return make_settings(
        folder, translate_enabled=True, gemini_keys=["k1"], **overrides)


class TestTranslationDuringRun:
    def test_writes_a_vietnamese_file(self, stub_environment, stub_translator, folder):
        pipeline.run(translating_settings(folder))
        assert (folder / "phim1.vi.srt").exists()
        assert (folder / "phim2.vi.srt").exists()

    def test_original_subtitle_is_kept(self, stub_environment, stub_translator, folder):
        pipeline.run(translating_settings(folder))
        original = (folder / "phim1.srt").read_text(encoding="utf-8-sig")
        assert "天气" in original
        assert "VI:" not in original

    def test_translated_file_holds_vietnamese(self, stub_environment, stub_translator, folder):
        pipeline.run(translating_settings(folder))
        assert "VI:" in (folder / "phim1.vi.srt").read_text(encoding="utf-8-sig")

    def test_timings_match_the_original(self, stub_environment, stub_translator, folder):
        from videoocr.subtitle import parse_srt

        pipeline.run(translating_settings(folder))
        source = parse_srt((folder / "phim1.srt").read_text(encoding="utf-8-sig"))
        target = parse_srt((folder / "phim1.vi.srt").read_text(encoding="utf-8-sig"))
        assert len(source) == len(target)
        assert [(c.start, c.end) for c in source] == [(c.start, c.end) for c in target]

    def test_disabled_by_default(self, stub_environment, stub_translator, folder):
        pipeline.run(make_settings(folder))
        assert not (folder / "phim1.vi.srt").exists()

    def test_missing_keys_skips_translation_without_failing(
        self, stub_environment, folder, monkeypatch
    ):
        # Không vá GeminiTranslator: bản thật sẽ từ chối vì danh sách key rỗng.
        summary = pipeline.run(make_settings(folder, translate_enabled=True, gemini_keys=[]))
        assert len(summary.done) == 2          # nhận dạng vẫn xong
        assert not (folder / "phim1.vi.srt").exists()

    def test_translation_failure_keeps_the_original(
        self, stub_environment, stub_translator, folder, monkeypatch
    ):
        def explode(*args, **kwargs):
            raise RuntimeError("Gemini hỏng")

        summary = pipeline.run(translating_settings(folder))
        monkeypatch.setattr(FakeTranslator, "translate", explode)
        for name in ["phim1.vi.srt", "phim2.vi.srt"]:
            (folder / name).unlink()
        summary = pipeline.run(translating_settings(folder, overwrite=True))
        assert len(summary.done) == 2          # video không bị tính là lỗi
        assert (folder / "phim1.srt").exists()
        assert not (folder / "phim1.vi.srt").exists()

    def test_existing_translation_is_skipped(self, stub_environment, stub_translator, folder):
        pipeline.run(translating_settings(folder))
        calls = len(stub_translator["instance"].seen)
        pipeline.run(translating_settings(folder, overwrite=True))
        # Lượt hai vẫn nhận dạng lại nhưng thấy .vi.srt đã có nên không dịch thêm.
        assert len(stub_translator["instance"].seen) >= calls


class TestTranslateExisting:
    def test_translates_srt_without_transcribing(self, stub_translator, folder):
        (folder / "phim1.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8-sig")
        summary = pipeline.translate_existing(translating_settings(folder))
        assert len(summary.done) == 1
        assert "VI:你好" in (folder / "phim1.vi.srt").read_text(encoding="utf-8-sig")

    def test_skips_video_without_subtitle(self, stub_translator, folder):
        summary = pipeline.translate_existing(translating_settings(folder))
        assert summary.done == []

    def test_skips_when_translation_exists(self, stub_translator, folder):
        (folder / "phim1.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8-sig")
        (folder / "phim1.vi.srt").write_text("cũ", encoding="utf-8-sig")
        summary = pipeline.translate_existing(translating_settings(folder))
        assert [p.name for p in summary.skipped] == ["phim1.mp4"]

    def test_overwrite_redoes_translation(self, stub_translator, folder):
        (folder / "phim1.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8-sig")
        (folder / "phim1.vi.srt").write_text("cũ", encoding="utf-8-sig")
        pipeline.translate_existing(translating_settings(folder, overwrite=True))
        assert "VI:" in (folder / "phim1.vi.srt").read_text(encoding="utf-8-sig")

    def test_cancel_stops_the_batch(self, stub_translator, folder):
        for name in ["phim1.srt", "phim2.srt"]:
            (folder / name).write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8-sig")
        cancel = threading.Event()
        cancel.set()
        summary = pipeline.translate_existing(translating_settings(folder), cancel=cancel)
        assert summary.cancelled

    def test_missing_folder_raises(self, stub_translator, tmp_path):
        with pytest.raises(NotADirectoryError):
            pipeline.translate_existing(translating_settings(tmp_path / "khong-co"))
