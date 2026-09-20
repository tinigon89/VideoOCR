"""Chuyển đổi Giản thể <-> Phồn thể.

Whisper xuất tiếng Trung không nhất quán, cùng một video có thể ra lẫn cả hai
kiểu chữ. Chuyển dứt điểm sau khi nhận dạng là cách chắc chắn nhất.
"""

from __future__ import annotations

_CONVERTERS: dict[str, object] = {}
_UNAVAILABLE = False

# 't2s' = phồn thể sang giản thể, 's2t' = ngược lại.
_CONFIGS = {"s": "t2s", "t": "s2t"}


def available() -> bool:
    global _UNAVAILABLE
    if _UNAVAILABLE:
        return False
    try:
        import opencc  # noqa: F401
        return True
    except ImportError:
        _UNAVAILABLE = True
        return False


def _converter(variant: str):
    if variant in _CONVERTERS:
        return _CONVERTERS[variant]
    try:
        import opencc

        _CONVERTERS[variant] = opencc.OpenCC(_CONFIGS[variant])
    except Exception:
        # Thiếu opencc hay hỏng bảng chuyển đổi thì giữ nguyên chữ, không làm
        # hỏng cả file phụ đề vì một bước phụ.
        _CONVERTERS[variant] = None
    return _CONVERTERS[variant]


def convert(text: str, variant: str) -> str:
    """variant: 's' giản thể, 't' phồn thể, 'none' giữ nguyên."""
    if variant not in _CONFIGS or not text:
        return text
    converter = _converter(variant)
    if converter is None:
        return text
    try:
        return converter.convert(text)
    except Exception:
        return text
