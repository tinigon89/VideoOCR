"""Cắt dòng và dựng file SRT.

Chữ Latin ngắt theo khoảng trắng, đếm khoảng 42 ký tự một dòng. Chữ Trung/Nhật
/Hàn không có khoảng trắng giữa từ nên phải đếm theo chữ (khoảng 16 chữ một
dòng theo thông lệ phụ đề CJK) và ngắt tại dấu câu 。，！？、.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Dấu kết câu: gặp là ngắt khối ngay.
SENTENCE_END = "。！？!?…"
# Dấu ngắt nhịp: chỉ ngắt khi khối đã tương đối dài.
CLAUSE_END = "，、；：,;:"

MIN_CUE_DURATION = 0.3
_SOFT_BREAK_RATIO = 0.6


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass
class Cue:
    start: float
    end: float
    text: str


def is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # Hán tự thông dụng
        or 0x3400 <= code <= 0x4DBF   # Hán tự mở rộng A
        or 0x3040 <= code <= 0x30FF   # Hiragana + Katakana
        or 0xAC00 <= code <= 0xD7AF   # Hangul
        or 0xF900 <= code <= 0xFAFF   # Hán tự tương thích
    )


def is_cjk_text(text: str, threshold: float = 0.2) -> bool:
    """Khối chữ này có phải chủ yếu là CJK không."""
    letters = [c for c in text if not c.isspace()]
    if not letters:
        return False
    cjk = sum(1 for c in letters if is_cjk_char(c))
    return cjk / len(letters) >= threshold


def join_words(words: list[Word]) -> str:
    """Nối lại chuỗi. faster-whisper đã kèm sẵn khoảng trắng đầu từ tiếng Anh,
    còn tiếng Trung thì không - nối thẳng là ra đúng cả hai."""
    return "".join(w.text for w in words).strip()


def join_display_lines(text: str) -> str:
    """Gộp các dòng của một khối phụ đề lại thành một câu liền.

    Chỗ xuống dòng nằm giữa hai chữ CJK thì nối thẳng, vì tiếng Trung không có
    dấu cách giữa từ - chèn khoảng trắng vào là làm sai câu. Các trường hợp còn
    lại (chữ Latin) thì nối bằng một khoảng trắng như bình thường.
    """
    parts = [part.strip() for part in text.splitlines() if part.strip()]
    if not parts:
        return ""

    joined = parts[0]
    for part in parts[1:]:
        separator = "" if is_cjk_char(joined[-1]) and is_cjk_char(part[0]) else " "
        joined += separator + part
    return joined


def format_timestamp(seconds: float) -> str:
    """Giây -> 'HH:MM:SS,mmm' theo chuẩn SRT."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _split_point_latin(text: str, target: int) -> int:
    """Vị trí khoảng trắng gần giữa nhất để chia hai dòng cho cân."""
    best = -1
    for match in re.finditer(r"\s+", text):
        if best == -1 or abs(match.start() - target) < abs(best - target):
            best = match.start()
    return best


def _split_point_cjk(text: str, target: int) -> int:
    """Ưu tiên ngắt ngay sau dấu câu gần giữa; không có thì cắt đúng giữa."""
    best = -1
    for i, ch in enumerate(text):
        if ch in SENTENCE_END or ch in CLAUSE_END:
            pos = i + 1
            if pos >= len(text):
                continue
            if best == -1 or abs(pos - target) < abs(best - target):
                best = pos
    return best if best != -1 else target


def wrap_text(text: str, max_chars: int, max_lines: int = 2) -> str:
    """Bẻ một khối thành tối đa ``max_lines`` dòng, chia càng cân càng tốt."""
    text = text.strip()
    if len(text) <= max_chars or max_lines <= 1:
        return text

    target = len(text) // 2
    if is_cjk_text(text):
        cut = _split_point_cjk(text, target)
    else:
        cut = _split_point_latin(text, target)
        if cut <= 0:
            cut = target

    first, second = text[:cut].strip(), text[cut:].strip()
    if not first or not second:
        return text

    if max_lines > 2:
        second = wrap_text(second, max_chars, max_lines - 1)
    return f"{first}\n{second}"


def _max_chars_for(text: str, max_chars_latin: int, max_chars_cjk: int) -> int:
    return max_chars_cjk if is_cjk_text(text) else max_chars_latin


def split_cue_by_length(cue: Cue, max_chars: int, max_lines: int = 2) -> list[Cue]:
    """Chia một khối quá dài, phân bổ thời gian theo tỉ lệ độ dài chữ.

    Chỉ dùng khi không có mốc thời gian từng từ để chia cho chính xác.
    """
    capacity = max_chars * max_lines
    text = cue.text.strip()
    if len(text) <= capacity:
        return [cue]

    cjk = is_cjk_text(text)
    pieces: list[str] = []
    remaining = text
    while len(remaining) > capacity:
        window = remaining[:capacity]
        cut = _split_point_cjk(window, capacity) if cjk else _split_point_latin(window, capacity)
        if cut <= 0 or cut >= len(remaining):
            cut = capacity
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)

    total = sum(len(p) for p in pieces) or 1
    span = max(cue.end - cue.start, MIN_CUE_DURATION)
    out: list[Cue] = []
    cursor = cue.start
    for piece in pieces:
        length = span * (len(piece) / total)
        out.append(Cue(start=cursor, end=cursor + length, text=piece))
        cursor += length
    out[-1].end = cue.end
    return out


def build_cues(
    words: list[Word],
    max_chars_latin: int = 42,
    max_chars_cjk: int = 16,
    max_lines: int = 2,
    max_duration: float = 6.0,
    max_gap: float = 0.8,
) -> list[Cue]:
    """Gom các từ đã có mốc thời gian thành các khối phụ đề.

    Ngắt khối khi gặp dấu kết câu, khi vượt quá sức chứa của hai dòng, khi khối
    quá dài về thời gian, hoặc khi có khoảng lặng đáng kể giữa hai từ.
    """
    cues: list[Cue] = []
    buffer: list[Word] = []

    def flush() -> None:
        if not buffer:
            return
        text = join_words(buffer)
        if text:
            cues.append(Cue(start=buffer[0].start, end=buffer[-1].end, text=text))
        buffer.clear()

    for word in words:
        if not word.text.strip():
            continue

        if buffer:
            pending = join_words(buffer + [word])
            limit = _max_chars_for(pending, max_chars_latin, max_chars_cjk) * max_lines
            too_long = len(pending) > limit
            too_slow = (word.end - buffer[0].start) > max_duration
            big_gap = (word.start - buffer[-1].end) > max_gap
            if too_long or too_slow or big_gap:
                flush()

        buffer.append(word)

        stripped = word.text.strip()
        if not stripped:
            continue
        tail = stripped[-1]
        if tail in SENTENCE_END:
            flush()
        elif tail in CLAUSE_END:
            current = join_words(buffer)
            limit = _max_chars_for(current, max_chars_latin, max_chars_cjk) * max_lines
            if len(current) >= limit * _SOFT_BREAK_RATIO:
                flush()

    flush()
    return cues


def finalize_cues(
    cues: list[Cue],
    max_chars_latin: int = 42,
    max_chars_cjk: int = 16,
    max_lines: int = 2,
) -> list[Cue]:
    """Bẻ dòng, đảm bảo thời lượng tối thiểu và không có khối nào chồng lên nhau."""
    out: list[Cue] = []
    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue
        max_chars = _max_chars_for(text, max_chars_latin, max_chars_cjk)
        for piece in split_cue_by_length(Cue(cue.start, cue.end, text), max_chars, max_lines):
            piece.text = wrap_text(piece.text, max_chars, max_lines)
            out.append(piece)

    for i, cue in enumerate(out):
        if cue.end - cue.start < MIN_CUE_DURATION:
            cue.end = cue.start + MIN_CUE_DURATION
        if i + 1 < len(out) and cue.end > out[i + 1].start:
            cue.end = max(out[i + 1].start, cue.start + 0.05)

    return out


_TIMECODE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def parse_timestamp(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000
    )


def parse_srt(text: str) -> list[Cue]:
    """Đọc nội dung file SRT thành danh sách khối.

    Bỏ qua số thứ tự trong file và đánh lại khi ghi ra, nên file đánh số lộn xộn
    vẫn đọc được.
    """
    cues: list[Cue] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.lstrip("﻿").strip())

    for block in blocks:
        lines = [line.rstrip("\r") for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        timing = None
        body_start = 0
        for position, line in enumerate(lines[:2]):
            match = _TIMECODE.search(line)
            if match:
                timing = match
                body_start = position + 1
                break
        if timing is None:
            continue

        body = "\n".join(lines[body_start:]).strip()
        if not body:
            continue

        groups = timing.groups()
        cues.append(Cue(
            start=parse_timestamp(*groups[:4]),
            end=parse_timestamp(*groups[4:]),
            text=body,
        ))

    return cues


def render_srt(cues: list[Cue]) -> str:
    """Dựng nội dung file SRT hoàn chỉnh."""
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)
