"""Kiểm tra cấu hình và nơi lưu model."""

from pathlib import Path

from videoocr.config import (
    APP_ROOT,
    DEFAULT_ZH_PROMPT,
    Settings,
    default_model_dir,
    resolve_model_dir,
)


class TestDefaultModelDir:
    def test_sits_next_to_the_app(self):
        assert default_model_dir() == APP_ROOT / "models"

    def test_named_models(self):
        assert default_model_dir().name == "models"


class TestResolveModelDir:
    def test_empty_falls_back_to_default(self):
        assert resolve_model_dir("") == str(default_model_dir())

    def test_none_falls_back_to_default(self):
        assert resolve_model_dir(None) == str(default_model_dir())

    def test_whitespace_falls_back_to_default(self):
        assert resolve_model_dir("   ") == str(default_model_dir())

    def test_explicit_path_is_kept(self, tmp_path):
        assert resolve_model_dir(str(tmp_path)) == str(tmp_path)

    def test_expands_home_shortcut(self):
        resolved = resolve_model_dir("~/models")
        assert "~" not in resolved
        assert resolved.endswith("models")

    def test_strips_surrounding_spaces(self, tmp_path):
        assert resolve_model_dir(f"  {tmp_path}  ") == str(tmp_path)


class TestSettingsRoundTrip:
    def test_saves_and_loads_model_dir(self, tmp_path):
        target = tmp_path / "settings.json"
        Settings(model_dir=r"D:\whisper-models").save(target)
        assert Settings.load(target).model_dir == r"D:\whisper-models"

    def test_missing_file_gives_defaults(self, tmp_path):
        loaded = Settings.load(tmp_path / "chua-co.json")
        assert loaded.model == "large-v3"
        assert loaded.language == "zh"
        assert loaded.model_dir == ""

    def test_corrupt_file_gives_defaults(self, tmp_path):
        target = tmp_path / "settings.json"
        target.write_text("{ hỏng", encoding="utf-8")
        assert Settings.load(target).model == "large-v3"

    def test_unknown_keys_are_ignored(self, tmp_path):
        target = tmp_path / "settings.json"
        target.write_text('{"model": "medium", "khong_biet": 1}', encoding="utf-8")
        assert Settings.load(target).model == "medium"

    def test_round_trip_keeps_every_field(self, tmp_path):
        target = tmp_path / "settings.json"
        original = Settings(
            input_dir=r"D:\Phim", language="en", model="medium",
            chinese_variant="t", batch_size=4, model_dir=r"E:\models",
        )
        original.save(target)
        assert Settings.load(target) == original

    def test_default_prompt_is_mandarin(self):
        assert Settings().initial_prompt == DEFAULT_ZH_PROMPT

    def test_save_survives_unwritable_path(self, tmp_path):
        # Không ghi được cấu hình thì cũng không được làm hỏng cả lần chạy.
        Settings().save(Path(tmp_path / "khong-ton-tai") / "a" / "b" / "\0bad.json")
