"""Kiểm tra phần cắt dòng và dựng SRT - chạy được không cần GPU."""

import pytest

from videoocr.subtitle import (
    Cue,
    Word,
    build_cues,
    finalize_cues,
    format_timestamp,
    is_cjk_text,
    join_display_lines,
    parse_srt,
    render_srt,
    split_cue_by_length,
    wrap_text,
)

SAMPLE_SRT = """1
00:00:01,200 --> 00:00:03,400
今天天气很好

2
00:00:04,000 --> 00:00:06,500
我们一起去
公园散步吧
"""


def words(pairs, step=0.4, start=0.0):
    """Dựng danh sách Word nối tiếp nhau đều đặn."""
    out = []
    cursor = start
    for text in pairs:
        out.append(Word(start=cursor, end=cursor + step, text=text))
        cursor += step
    return out


class TestFormatTimestamp:
    def test_zero(self):
        assert format_timestamp(0) == "00:00:00,000"

    def test_milliseconds(self):
        assert format_timestamp(1.234) == "00:00:01,234"

    def test_hours_and_minutes(self):
        assert format_timestamp(3723.5) == "01:02:03,500"

    def test_negative_clamped_to_zero(self):
        assert format_timestamp(-5) == "00:00:00,000"

    def test_rounds_rather_than_truncates(self):
        assert format_timestamp(0.9999) == "00:00:01,000"


class TestCjkDetection:
    def test_chinese_is_cjk(self):
        assert is_cjk_text("今天天气很好")

    def test_english_is_not_cjk(self):
        assert not is_cjk_text("the weather is nice today")

    def test_empty_is_not_cjk(self):
        assert not is_cjk_text("   ")

    def test_mixed_with_chinese_counts_as_cjk(self):
        assert is_cjk_text("今天用 Python 写代码")


class TestJoinDisplayLines:
    def test_chinese_lines_join_without_space(self):
        assert join_display_lines("我们一起去\n公园散步吧") == "我们一起去公园散步吧"

    def test_english_lines_join_with_space(self):
        assert join_display_lines("the quick brown\nfox jumps") == "the quick brown fox jumps"

    def test_space_across_scripts_is_kept(self):
        assert join_display_lines("Hello\n世界") == "Hello 世界"

    def test_inner_spaces_are_preserved(self):
        assert join_display_lines("今天用 Python\n写代码") == "今天用 Python 写代码"

    def test_single_line_unchanged(self):
        assert join_display_lines("một dòng") == "một dòng"

    def test_empty_text(self):
        assert join_display_lines("  \n  ") == ""


class TestWrapText:
    def test_short_text_stays_one_line(self):
        assert wrap_text("你好世界", 16) == "你好世界"

    def test_chinese_wraps_at_punctuation(self):
        text = "今天天气很好，我们一起去公园散步吧"
        wrapped = wrap_text(text, 10)
        assert "\n" in wrapped
        # Ngắt ngay sau dấu phẩy chứ không cắt giữa cụm từ.
        assert wrapped.split("\n")[0].endswith("，")

    def test_english_wraps_at_space_not_mid_word(self):
        text = "the quick brown fox jumps over the lazy dog again"
        wrapped = wrap_text(text, 20)
        first, second = wrapped.split("\n")
        assert " " not in (first[-1] + second[0])
        assert f"{first} {second}" == text

    def test_wrap_produces_balanced_lines(self):
        text = "one two three four five six seven eight"
        first, second = wrap_text(text, 20).split("\n")
        assert abs(len(first) - len(second)) <= 6


class TestBuildCues:
    def test_empty_input(self):
        assert build_cues([]) == []

    def test_sentence_end_forces_a_break(self):
        cues = build_cues(words(["你好", "。", "再见"]), max_chars_cjk=16)
        assert len(cues) == 2
        assert cues[0].text == "你好。"
        assert cues[1].text == "再见"

    def test_english_words_join_with_spaces(self):
        cues = build_cues(words([" hello", " world"]))
        assert len(cues) == 1
        assert cues[0].text == "hello world"

    def test_long_chinese_run_is_split_by_capacity(self):
        # 60 chữ, sức chứa 2 dòng x 16 chữ = 32 -> phải tách.
        cues = build_cues(words(list("一二三四五六七八九十" * 6)), max_chars_cjk=16)
        assert len(cues) > 1
        assert all(len(c.text) <= 32 for c in cues)

    def test_long_silence_breaks_the_cue(self):
        items = [
            Word(0.0, 0.5, "你好"),
            Word(5.0, 5.5, "再见"),  # cách 4.5 giây
        ]
        cues = build_cues(items, max_gap=0.8)
        assert len(cues) == 2

    def test_duration_cap_breaks_the_cue(self):
        items = [Word(i * 1.0, i * 1.0 + 1.0, "啊") for i in range(10)]
        cues = build_cues(items, max_duration=3.0)
        assert len(cues) > 1
        assert all(c.end - c.start <= 4.0 for c in cues)

    def test_timestamps_come_from_first_and_last_word(self):
        cues = build_cues(words(["你好", "世界"], step=0.5))
        assert cues[0].start == 0.0
        assert cues[0].end == 1.0


class TestSplitCueByLength:
    def test_short_cue_untouched(self):
        cue = Cue(0.0, 2.0, "你好世界")
        assert split_cue_by_length(cue, 16) == [cue]

    def test_long_cue_keeps_total_span(self):
        cue = Cue(0.0, 10.0, "一二三四五六七八九十" * 6)
        pieces = split_cue_by_length(cue, 16)
        assert len(pieces) > 1
        assert pieces[0].start == 0.0
        assert pieces[-1].end == 10.0

    def test_long_cue_preserves_all_characters(self):
        text = "一二三四五六七八九十" * 6
        pieces = split_cue_by_length(Cue(0.0, 10.0, text), 16)
        assert "".join(p.text for p in pieces) == text


class TestFinalizeCues:
    def test_drops_empty_cues(self):
        assert finalize_cues([Cue(0.0, 1.0, "   ")]) == []

    def test_enforces_minimum_duration(self):
        cues = finalize_cues([Cue(1.0, 1.01, "你好")])
        assert cues[0].end - cues[0].start >= 0.3

    def test_trims_overlap_between_neighbours(self):
        cues = finalize_cues([Cue(0.0, 5.0, "你好"), Cue(2.0, 6.0, "再见")])
        assert cues[0].end <= cues[1].start


class TestRenderSrt:
    def test_block_format(self):
        output = render_srt([Cue(0.0, 1.5, "你好")])
        assert output == "1\n00:00:00,000 --> 00:00:01,500\n你好\n"

    def test_numbering_is_sequential(self):
        output = render_srt([Cue(0.0, 1.0, "a"), Cue(1.0, 2.0, "b"), Cue(2.0, 3.0, "c")])
        assert output.splitlines()[0] == "1"
        assert "\n2\n" in output
        assert "\n3\n" in output

    def test_blocks_separated_by_blank_line(self):
        output = render_srt([Cue(0.0, 1.0, "a"), Cue(1.0, 2.0, "b")])
        assert "a\n\n2\n" in output

    def test_empty_list(self):
        assert render_srt([]) == ""


class TestParseSrt:
    def test_reads_every_block(self):
        assert len(parse_srt(SAMPLE_SRT)) == 2

    def test_reads_timestamps(self):
        cues = parse_srt(SAMPLE_SRT)
        assert cues[0].start == pytest.approx(1.2)
        assert cues[0].end == pytest.approx(3.4)

    def test_keeps_multi_line_text(self):
        assert parse_srt(SAMPLE_SRT)[1].text == "我们一起去\n公园散步吧"

    def test_handles_bom(self):
        assert len(parse_srt("﻿" + SAMPLE_SRT)) == 2

    def test_handles_crlf(self):
        assert len(parse_srt(SAMPLE_SRT.replace("\n", "\r\n"))) == 2

    def test_handles_dot_as_decimal_separator(self):
        text = "1\n00:00:01.200 --> 00:00:03.400\nxin chào\n"
        assert parse_srt(text)[0].start == pytest.approx(1.2)

    def test_tolerates_wrong_numbering(self):
        text = SAMPLE_SRT.replace("1\n00:00:01", "7\n00:00:01")
        assert len(parse_srt(text)) == 2

    def test_block_without_index_line(self):
        text = "00:00:01,000 --> 00:00:02,000\nxin chào\n"
        assert parse_srt(text)[0].text == "xin chào"

    def test_skips_block_without_timecode(self):
        text = "rác\n\n1\n00:00:01,000 --> 00:00:02,000\nxin chào\n"
        assert len(parse_srt(text)) == 1

    def test_skips_block_without_text(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nok\n"
        assert len(parse_srt(text)) == 1

    def test_empty_input(self):
        assert parse_srt("") == []

    def test_round_trip_through_render(self):
        original = parse_srt(SAMPLE_SRT)
        assert parse_srt(render_srt(original)) == original
