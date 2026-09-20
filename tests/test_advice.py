"""Kiểm tra lời khuyên chọn model theo ngôn ngữ."""

from videoocr.config import MODEL_ADVICE, model_advice, recommended_model


class TestRecommendedModel:
    def test_chinese(self):
        assert recommended_model("zh") == "large-v3"

    def test_english(self):
        assert recommended_model("en") == "large-v3"

    def test_auto_detect(self):
        assert recommended_model(None) == "large-v3"

    def test_unknown_language_has_a_fallback(self):
        assert recommended_model("xx") == "large-v3"


class TestModelAdvice:
    def test_matching_model_is_a_plain_hint(self):
        text, level = model_advice("zh", "large-v3")
        assert level == "hint"
        assert "khuyến nghị" in text

    def test_smaller_model_warns(self):
        text, level = model_advice("zh", "small")
        assert level == "warn"
        assert "large-v3" in text

    def test_english_only_model_with_chinese_warns_clearly(self):
        text, level = model_advice("zh", "distil-large-v3")
        assert level == "warn"
        assert "chỉ hiểu tiếng Anh" in text

    def test_english_only_model_with_english_is_fine(self):
        # distil không phải model khuyến nghị nên vẫn nhắc, nhưng không phải
        # vì lý do sai ngôn ngữ.
        text, _ = model_advice("en", "distil-large-v3")
        assert "chỉ hiểu tiếng Anh" not in text

    def test_english_advice_mentions_the_fast_option(self):
        text, _ = model_advice("en", "large-v3")
        assert "distil-large-v3" in text

    def test_chinese_advice_explains_why(self):
        text, _ = model_advice("zh", "large-v3")
        assert "đồng âm" in text

    def test_auto_detect_warns_about_english_only_models(self):
        text, _ = model_advice(None, "large-v3")
        assert "distil" in text

    def test_every_language_in_the_table_has_a_reason(self):
        for language, (model, reason) in MODEL_ADVICE.items():
            assert model, language
            assert len(reason) > 20, language

    def test_unknown_language_still_gives_advice(self):
        text, level = model_advice("xx", "small")
        assert level == "warn"
        assert text
