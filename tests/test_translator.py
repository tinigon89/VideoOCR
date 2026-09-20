"""Kiểm tra phần dịch Gemini bằng HTTP giả - không gọi API thật, không tốn hạn mức."""

import json
import urllib.error

import pytest

from videoocr.translator import (
    AllKeysFailedError,
    GeminiTranslator,
    KeyPool,
    TranslationError,
    fetch_models,
    sort_models,
    translation_models,
)


def http_error(code: int, message: str = "loi") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    import io

    return urllib.error.HTTPError("http://x", code, message, {}, io.BytesIO(body))


def gemini_reply(lines: dict[int, str]) -> dict:
    """Dựng phản hồi đúng hình dạng Gemini trả về."""
    payload = json.dumps({"lines": [{"id": i, "vi": v} for i, v in lines.items()]})
    return {"candidates": [{"content": {"parts": [{"text": payload}]}}]}


class FakeApi:
    """Ghi lại từng lần gọi và trả về kịch bản đã dựng sẵn."""

    def __init__(self, script=None):
        self.calls: list[str] = []      # key dùng cho mỗi lần gọi
        self.bodies: list[dict] = []    # thân yêu cầu, để soi lời nhắc gửi đi
        self.script = script or []      # mỗi phần tử: Exception để ném, hoặc dict để trả

    def __call__(self, url, api_key, body):
        self.calls.append(api_key)
        self.bodies.append(body)
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        if step is not None:
            return step
        # Mặc định: dịch bằng cách thêm tiền tố, giữ nguyên số thứ tự.
        sent = json.loads(body["contents"][0]["parts"][0]["text"].split("Dữ liệu vào (JSON):\n")[1])
        return gemini_reply({item["id"]: f"VI:{item['text']}" for item in sent})


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr("videoocr.translator._post", api)
    monkeypatch.setattr("videoocr.translator.time.sleep", lambda s: None)
    return api


class TestKeyPool:
    def test_removes_blank_and_duplicate_keys(self):
        pool = KeyPool(["a", "  ", "a", "b", ""])
        assert len(pool) == 2

    def test_rotates_round_robin(self):
        pool = KeyPool(["a", "b", "c"])
        assert [pool.acquire().key for _ in range(4)] == ["a", "b", "c", "a"]

    def test_cooled_key_is_skipped(self):
        pool = KeyPool(["a", "b"])
        first = pool.acquire()
        pool.cool_down(first, 60)
        assert pool.acquire().key == "b"
        assert pool.acquire().key == "b"

    def test_dead_key_never_returns(self):
        pool = KeyPool(["a", "b"])
        pool.kill(pool.states[0], "hỏng")
        assert all(pool.acquire().key == "b" for _ in range(3))

    def test_acquire_returns_none_when_all_cooling(self):
        pool = KeyPool(["a", "b"])
        for state in pool.states:
            pool.cool_down(state, 60)
        assert pool.acquire() is None

    def test_wait_seconds_reports_soonest(self):
        pool = KeyPool(["a", "b"])
        pool.cool_down(pool.states[0], 60)
        pool.cool_down(pool.states[1], 10)
        assert 0 < pool.wait_seconds() <= 10

    def test_wait_seconds_is_none_when_all_dead(self):
        pool = KeyPool(["a"])
        pool.kill(pool.states[0], "hỏng")
        assert pool.wait_seconds() is None

    def test_key_label_hides_most_of_the_key(self):
        pool = KeyPool(["AIzaSyVERYSECRET1234"])
        label = pool.states[0].label
        assert "1234" in label
        assert "VERYSECRET" not in label


class TestTranslatorSetup:
    def test_rejects_empty_key_list(self):
        with pytest.raises(TranslationError):
            GeminiTranslator(keys=[])

    def test_rejects_blank_keys(self):
        with pytest.raises(TranslationError):
            GeminiTranslator(keys=["  ", ""])


class TestTranslate:
    def test_translates_every_line(self, fake_api):
        t = GeminiTranslator(keys=["k1"])
        assert t.translate(["你好", "再见"]) == ["VI:你好", "VI:再见"]

    def test_keeps_line_count_and_order(self, fake_api):
        t = GeminiTranslator(keys=["k1"], batch_size=2)
        source = [f"câu {i}" for i in range(7)]
        result = t.translate(source)
        assert len(result) == len(source)
        assert result[3] == "VI:câu 3"

    def test_joins_wrapped_lines_before_sending(self, fake_api):
        t = GeminiTranslator(keys=["k1"])
        assert t.translate(["dòng một\ndòng hai"]) == ["VI:dòng một dòng hai"]

    def test_empty_cue_is_left_alone(self, fake_api):
        t = GeminiTranslator(keys=["k1"])
        assert t.translate(["", "你好"]) == ["", "VI:你好"]

    def test_missing_line_falls_back_to_original(self, fake_api):
        # Gemini chỉ trả về dòng 0, bỏ sót dòng 1.
        fake_api.script = [gemini_reply({0: "VI:một"})]
        t = GeminiTranslator(keys=["k1"])
        assert t.translate(["một", "hai"]) == ["VI:một", "hai"]
        assert t.stats.missing == 1

    def test_splits_into_batches(self, fake_api):
        t = GeminiTranslator(keys=["k1"], batch_size=2)
        t.translate([f"c{i}" for i in range(5)])
        assert len(fake_api.calls) == 3      # 2 + 2 + 1

    def test_cancel_stops_early(self, fake_api):
        t = GeminiTranslator(keys=["k1"], batch_size=1)
        result = t.translate(["a", "b", "c"], should_cancel=lambda: True)
        assert result == ["a", "b", "c"]
        assert fake_api.calls == []

    def test_reports_progress(self, fake_api):
        seen = []
        t = GeminiTranslator(keys=["k1"], batch_size=2)
        t.translate([f"c{i}" for i in range(4)], on_progress=lambda d, tot: seen.append((d, tot)))
        assert seen == [(2, 4), (4, 4)]


class TestKeyRotation:
    def test_rate_limit_moves_to_next_key(self, fake_api):
        fake_api.script = [http_error(429, "hết hạn mức"), None]
        t = GeminiTranslator(keys=["k1", "k2"])
        assert t.translate(["你好"]) == ["VI:你好"]
        assert fake_api.calls == ["k1", "k2"]
        assert t.stats.rotations == 1

    def test_invalid_key_is_retired_permanently(self, fake_api):
        fake_api.script = [http_error(400, "API key không hợp lệ"), None, None]
        t = GeminiTranslator(keys=["k1", "k2"], batch_size=1)
        t.translate(["a", "b"])
        # k1 bị loại sau lần đầu nên không bao giờ được gọi lại.
        assert fake_api.calls == ["k1", "k2", "k2"]
        assert t.pool.states[0].dead

    def test_server_error_retries_with_another_key(self, fake_api):
        fake_api.script = [http_error(503, "quá tải"), None]
        t = GeminiTranslator(keys=["k1", "k2"])
        assert t.translate(["你好"]) == ["VI:你好"]
        assert len(fake_api.calls) == 2

    def test_all_keys_dead_raises(self, fake_api):
        fake_api.script = [http_error(403, "bị thu hồi"), http_error(403, "bị thu hồi")]
        t = GeminiTranslator(keys=["k1", "k2"])
        with pytest.raises(AllKeysFailedError):
            t.translate(["你好"])

    def test_network_error_is_retried(self, fake_api):
        fake_api.script = [urllib.error.URLError("mạng hỏng"), None]
        t = GeminiTranslator(keys=["k1", "k2"])
        assert t.translate(["你好"]) == ["VI:你好"]

    def test_key_report_never_leaks_full_key(self, fake_api):
        t = GeminiTranslator(keys=["AIzaSyTUYETMATABCD"])
        t.translate(["你好"])
        assert "TUYETMAT" not in t.key_report()
        assert "ABCD" in t.key_report()


class TestCustomInstructions:
    def sent_prompt(self, api):
        return api.bodies[0]["contents"][0]["parts"][0]["text"]

    def test_instructions_reach_the_prompt(self, fake_api):
        t = GeminiTranslator(keys=["k1"],
                             instructions="Nam chính xưng 'anh', gọi nữ chính là 'em'.")
        t.translate(["你好"])
        assert "Nam chính xưng 'anh'" in self.sent_prompt(fake_api)

    def test_marked_as_high_priority(self, fake_api):
        t = GeminiTranslator(keys=["k1"], instructions="Dùng ta - ngươi.")
        t.translate(["你好"])
        assert "ưu tiên cao" in self.sent_prompt(fake_api)

    def test_no_extra_block_when_empty(self, fake_api):
        t = GeminiTranslator(keys=["k1"])
        t.translate(["你好"])
        assert "Yêu cầu riêng" not in self.sent_prompt(fake_api)

    def test_whitespace_only_counts_as_empty(self, fake_api):
        t = GeminiTranslator(keys=["k1"], instructions="   \n  ")
        t.translate(["你好"])
        assert "Yêu cầu riêng" not in self.sent_prompt(fake_api)

    def test_base_rules_survive_alongside_instructions(self, fake_api):
        t = GeminiTranslator(keys=["k1"], instructions="Dùng ta - ngươi.")
        t.translate(["你好", "再见"])
        prompt = self.sent_prompt(fake_api)
        assert "ĐÚNG 2 dòng" in prompt
        assert "Không gộp hai dòng làm một" in prompt


class TestSortModels:
    def test_gemini_comes_before_other_families(self):
        result = sort_models(["gemma-3-27b-it", "gemini-2.5-flash"])
        assert result[0] == "gemini-2.5-flash"

    def test_newer_version_first(self):
        result = sort_models(["gemini-2.0-flash", "gemini-3-flash", "gemini-2.5-flash"])
        assert result[0] == "gemini-3-flash"

    def test_stable_before_preview(self):
        result = sort_models(["gemini-2.5-flash-preview-09-2025", "gemini-2.5-flash"])
        assert result[0] == "gemini-2.5-flash"

    def test_keeps_every_model(self):
        names = ["gemini-2.5-flash", "gemma-3-27b-it", "gemini-2.5-pro-preview"]
        assert sorted(sort_models(names)) == sorted(names)

    def test_empty_list(self):
        assert sort_models([]) == []


class TestTranslationModels:
    def test_drops_models_that_cannot_translate(self, monkeypatch):
        monkeypatch.setattr("videoocr.translator.list_models", lambda key: [
            "gemini-2.5-flash",
            "gemini-embedding-001",
            "imagen-4.0-generate-001",
            "veo-3.0-generate-preview",
            "gemini-2.5-flash-native-audio",
            "gemini-2.5-flash-preview-tts",
        ])
        assert translation_models("k") == ["gemini-2.5-flash"]

    def test_keeps_ordinary_text_models(self, monkeypatch):
        monkeypatch.setattr("videoocr.translator.list_models", lambda key: [
            "gemini-2.5-flash", "gemini-2.5-pro", "gemma-3-27b-it",
        ])
        assert len(translation_models("k")) == 3


class TestFetchModels:
    def test_uses_the_first_working_key(self, monkeypatch):
        seen = []

        def fake(key):
            seen.append(key)
            if key == "bad":
                raise TranslationError("key hỏng")
            return ["gemini-2.5-flash"]

        monkeypatch.setattr("videoocr.translator.translation_models", fake)
        models, note = fetch_models(["bad", "good"])
        assert models == ["gemini-2.5-flash"]
        assert seen == ["bad", "good"]
        assert "key #2" in note

    def test_raises_when_no_key_works(self, monkeypatch):
        def fake(key):
            raise TranslationError("key hỏng")

        monkeypatch.setattr("videoocr.translator.translation_models", fake)
        with pytest.raises(TranslationError):
            fetch_models(["a", "b"])

    def test_empty_key_list_raises(self):
        with pytest.raises(TranslationError):
            fetch_models([])

    def test_error_message_does_not_leak_keys(self, monkeypatch):
        def fake(key):
            raise TranslationError("key hỏng")

        monkeypatch.setattr("videoocr.translator.translation_models", fake)
        with pytest.raises(TranslationError) as info:
            fetch_models(["AIzaSyTUYETMAT1234"])
        assert "TUYETMAT" not in str(info.value)


class TestBadResponses:
    def test_broken_json_raises(self, fake_api):
        fake_api.script = [{"candidates": [{"content": {"parts": [{"text": "{ hỏng"}]}}]}]
        t = GeminiTranslator(keys=["k1"])
        with pytest.raises(TranslationError):
            t.translate(["你好"])

    def test_blocked_response_raises(self, fake_api):
        fake_api.script = [{"promptFeedback": {"blockReason": "SAFETY"}}]
        t = GeminiTranslator(keys=["k1"])
        with pytest.raises(TranslationError):
            t.translate(["你好"])
