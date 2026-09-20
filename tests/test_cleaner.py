"""Kiểm tra bộ lọc ảo giác của Whisper."""

from videoocr.cleaner import clean_cues, is_junk, is_repetitive, normalize
from videoocr.subtitle import Cue


class TestNormalize:
    def test_strips_punctuation_and_spaces(self):
        assert normalize("你好，世界。") == "你好世界"

    def test_lowercases_latin(self):
        assert normalize("Hello, World!") == "helloworld"


class TestIsJunk:
    def test_youtube_boilerplate_chinese(self):
        assert is_junk("请不吝点赞 订阅 转发 打赏支持明镜与点点栏目")

    def test_amara_credit(self):
        assert is_junk("字幕由Amara.org社群提供")

    def test_english_boilerplate(self):
        assert is_junk("Subtitles by the Amara.org community")

    def test_empty_text_is_junk(self):
        assert is_junk("   ")

    def test_real_dialogue_survives(self):
        assert not is_junk("今天我们来聊聊人工智能的发展")

    def test_real_english_dialogue_survives(self):
        assert not is_junk("I think we should start with the basics")

    def test_keyword_inside_real_sentence_survives(self):
        # Nhắc tới 'đăng ký' nhưng phần lớn câu là nội dung thật.
        assert not is_junk("很多人问我怎么订阅这个课程，其实流程非常简单，只要三步就够了")


class TestIsRepetitive:
    def test_single_character_loop(self):
        assert is_repetitive("啊啊啊啊啊啊啊啊")

    def test_repeated_phrase_chinese(self):
        assert is_repetitive("好的好的好的好的")

    def test_repeated_word_english(self):
        assert is_repetitive("ok ok ok ok ok")

    def test_normal_sentence_is_not_repetitive(self):
        assert not is_repetitive("今天天气很好我们去公园")

    def test_short_text_is_not_repetitive(self):
        assert not is_repetitive("好")


class TestCleanCues:
    def test_removes_junk_cue(self):
        cues = [
            Cue(0.0, 1.0, "今天我们来聊聊人工智能"),
            Cue(1.0, 2.0, "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"),
        ]
        assert len(clean_cues(cues)) == 1

    def test_collapses_consecutive_duplicates(self):
        cues = [
            Cue(0.0, 1.0, "你好世界"),
            Cue(1.0, 2.0, "你好世界。"),
            Cue(2.0, 3.0, "再见"),
        ]
        result = clean_cues(cues)
        assert [c.text for c in result] == ["你好世界", "再见"]

    def test_non_consecutive_repeat_is_kept(self):
        cues = [
            Cue(0.0, 1.0, "好的"),
            Cue(1.0, 2.0, "我明白了"),
            Cue(2.0, 3.0, "好的"),
        ]
        assert len(clean_cues(cues)) == 3

    def test_disabled_filter_keeps_everything(self):
        cues = [Cue(0.0, 1.0, "字幕由Amara.org社群提供")]
        assert clean_cues(cues, enabled=False) == cues
