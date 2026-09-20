"""Lọc phần 'ảo giác' của Whisper.

Ở đoạn im lặng hoặc chỉ có nhạc nền, model hay chèn vào những câu rác nó học
được từ phụ đề YouTube, và dễ rơi vào vòng lặp lặp lại một câu. Module này dọn
các trường hợp đó trước khi ghi ra SRT.
"""

from __future__ import annotations

import re
import unicodedata

# Những câu rác quen mặt trong dữ liệu huấn luyện của Whisper.
JUNK_PHRASES = [
    "请不吝点赞",
    "订阅",
    "转发",
    "打赏支持明镜与点点栏目",
    "明镜与点点栏目",
    "字幕由amara.org社群提供",
    "字幕志愿者",
    "由amara.org社群提供",
    "amara.org",
    "subtitles by the amara.org community",
    "subtitled by",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "www.youtube.com",
    "本字幕由",
    "中文字幕志愿者",
    "翻译по",
]

# Câu rác chỉ tính khi nó chiếm gần trọn khối phụ đề, tránh cắt nhầm nội dung
# thật có nhắc tới cùng từ khoá.
_JUNK_COVERAGE = 0.6

_PUNCT_RE = re.compile(r"[\s,.!?;:\-—…、。，！？；：「」『』（）()\[\]\"'`~]+")


def normalize(text: str) -> str:
    """Bỏ dấu câu và khoảng trắng, hạ chữ thường - dùng để so khớp."""
    text = unicodedata.normalize("NFKC", text).lower()
    return _PUNCT_RE.sub("", text)


def is_junk(text: str) -> bool:
    """Khối phụ đề này có phải câu rác quảng cáo/kêu gọi đăng ký không."""
    flat = normalize(text)
    if not flat:
        return True

    matched = 0
    for phrase in JUNK_PHRASES:
        key = normalize(phrase)
        if key and key in flat:
            matched += len(key)

    return matched >= len(flat) * _JUNK_COVERAGE


def is_repetitive(text: str, min_repeats: int = 4) -> bool:
    """Bắt kiểu lặp bệnh lý: '啊啊啊啊啊啊' hoặc 'ok ok ok ok ok'."""
    flat = normalize(text)
    if len(flat) < min_repeats:
        return False

    # Thử mọi đơn vị lặp đủ ngắn xem có phủ kín toàn bộ chuỗi không.
    for unit in range(1, len(flat) // min_repeats + 1):
        chunk = flat[:unit]
        if chunk * (len(flat) // unit) == flat[: len(flat) // unit * unit]:
            if len(flat) // unit >= min_repeats and len(flat) % unit == 0:
                return True

    # Hoặc lặp theo từ, cho tiếng Anh.
    words = text.lower().split()
    if len(words) >= min_repeats and len(set(words)) == 1:
        return True

    return False


def clean_cues(cues: list, enabled: bool = True) -> list:
    """Bỏ câu rác, khối lặp và các khối trùng nhau liên tiếp.

    Nhận và trả về danh sách object có thuộc tính ``.text``.
    """
    if not enabled:
        return cues

    kept: list = []
    for cue in cues:
        if is_junk(cue.text) or is_repetitive(cue.text):
            continue
        # Cùng một câu lặp lại ngay sau chính nó gần như luôn là lỗi model.
        if kept and normalize(kept[-1].text) == normalize(cue.text):
            continue
        kept.append(cue)

    return kept
