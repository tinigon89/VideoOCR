"""Cấu hình chạy và phần lưu/đọc lại lựa chọn của người dùng."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# Danh sách ngôn ngữ cho dropdown. Hai thứ tiếng chính đặt lên đầu.
LANGUAGES: list[tuple[str, str | None]] = [
    ("中文 (Tiếng Trung phổ thông)", "zh"),
    ("English (Tiếng Anh)", "en"),
    ("Tự động phát hiện", None),
    ("日本語 (Tiếng Nhật)", "ja"),
    ("한국어 (Tiếng Hàn)", "ko"),
    ("Tiếng Việt", "vi"),
    ("Français (Tiếng Pháp)", "fr"),
    ("Deutsch (Tiếng Đức)", "de"),
    ("Español (Tiếng Tây Ban Nha)", "es"),
    ("Русский (Tiếng Nga)", "ru"),
    ("ไทย (Tiếng Thái)", "th"),
    ("Bahasa Indonesia", "id"),
]

# Xếp từ chính xác nhất xuống nhẹ nhất. "tiny"/"base" chủ yếu để chạy thử nhanh.
MODELS = ["large-v3", "large-v2", "distil-large-v3", "medium", "small", "base", "tiny"]

# distil-* chỉ được huấn luyện cho tiếng Anh.
ENGLISH_ONLY_MODELS = {"distil-large-v3", "distil-large-v2", "distil-medium.en", "distil-small.en"}

COMPUTE_TYPES = ["float16", "int8_float16", "int8", "float32"]

CHINESE_VARIANTS: list[tuple[str, str]] = [
    ("Giản thể (简体)", "s"),
    ("Phồn thể (繁體)", "t"),
    ("Giữ nguyên như model trả về", "none"),
]

# Câu mồi đẩy model về tiếng phổ thông, ưu tiên giản thể và quan trọng nhất
# là buộc nó chấm câu - bộ cắt dòng dựa vào dấu câu để ngắt cho đẹp.
DEFAULT_ZH_PROMPT = "以下是普通话的句子。"

# Đuôi của bản dịch: phim.mp4 -> phim.srt (nguyên ngữ) + phim.vi.srt (tiếng Việt).
TRANSLATED_SUFFIX = ".vi.srt"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".rmvb", ".3gp",
}


APP_ROOT = Path(__file__).resolve().parent.parent


def default_model_dir() -> Path:
    """Mặc định để model ngay cạnh app, để chép cả thư mục đi là chạy được luôn."""
    return APP_ROOT / "models"


def resolve_model_dir(value: str | None) -> str:
    """Đường dẫn rỗng nghĩa là dùng thư mục mặc định.

    Trỏ vào cache chung của HuggingFace (``~/.cache/huggingface/hub``) cũng được,
    khi đó model đã tải cho phần mềm khác sẽ được dùng lại chứ không tải trùng.
    """
    text = (value or "").strip()
    return str(Path(text).expanduser()) if text else str(default_model_dir())


def settings_path() -> Path:
    """Nơi lưu lựa chọn lần trước, trong AppData của người dùng."""
    base = os.environ.get("APPDATA")
    root = Path(base) / "VideoOCR" if base else Path.home() / ".videoocr"
    return root / "settings.json"


@dataclass
class Settings:
    input_dir: str = ""
    recursive: bool = True
    model: str = "large-v3"
    language: str | None = "zh"
    compute_type: str = "float16"
    device: str = "auto"          # auto | cuda | cpu
    batch_size: int = 8           # vừa với 11GB VRAM của 2080 Ti
    beam_size: int = 5
    vad_filter: bool = True
    overwrite: bool = False       # mặc định bỏ qua video đã có .srt
    chinese_variant: str = "s"    # s | t | none
    initial_prompt: str = DEFAULT_ZH_PROMPT
    max_chars_latin: int = 42     # độ dài dòng cho chữ Latin
    max_chars_cjk: int = 16       # độ dài dòng cho chữ Trung/Nhật/Hàn
    max_cue_duration: float = 6.0
    filter_hallucinations: bool = True

    # Nơi tải và lưu model. Rỗng = thư mục models\ cạnh app.
    model_dir: str = ""

    # Dịch sang tiếng Việt bằng Gemini.
    translate_enabled: bool = False
    gemini_keys: list[str] = field(default_factory=list)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    translate_batch_size: int = 40

    def save(self, path: Path | None = None) -> None:
        target = path or settings_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError):
            # Không ghi được cấu hình thì cũng không đáng để làm hỏng cả lần chạy.
            # ValueError vì đường dẫn dị dạng không ném OSError.
            pass

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        target = path or settings_path()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
