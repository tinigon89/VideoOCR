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

# Model nên dùng cho từng ngôn ngữ, kèm lý do. Khoá None là "tự phát hiện".
MODEL_ADVICE: dict[str | None, tuple[str, str]] = {
    "zh": ("large-v3",
           "Tiếng Trung nên để large-v3. Model nhỏ hơn nhầm nhiều chữ đồng âm "
           "và hay bỏ dấu câu, còn distil-* thì không biết tiếng Trung."),
    "en": ("large-v3",
           "large-v3 chính xác nhất. Muốn nhanh gấp đôi mà chất lượng gần ngang "
           "thì chọn distil-large-v3 - nó chỉ dùng được cho tiếng Anh."),
    "ja": ("large-v3",
           "Tiếng Nhật nên để large-v3. Model nhỏ hơn lẫn lộn kanji và cách đọc."),
    "ko": ("large-v3",
           "Tiếng Hàn nên để large-v3. Model nhỏ hơn sai nhiều với chữ Hangul."),
    "vi": ("large-v3",
           "Whisper nhận tiếng Việt kém hơn tiếng Trung và tiếng Anh, nên đừng "
           "hạ xuống model nhỏ."),
    None: ("large-v3",
           "Tự phát hiện ngôn ngữ cần model lớn mới đoán đúng. distil-* chỉ biết "
           "tiếng Anh nên không dùng cho chế độ này được."),
}

_DEFAULT_ADVICE = ("large-v3", "large-v3 là lựa chọn an toàn cho hầu hết ngôn ngữ.")


def recommended_model(language: str | None) -> str:
    return MODEL_ADVICE.get(language, _DEFAULT_ADVICE)[0]


def model_advice(language: str | None, model: str) -> tuple[str, str]:
    """Lời khuyên về model cho ngôn ngữ đang chọn.

    Trả về (câu chữ, mức độ) với mức độ là "hint" hoặc "warn".
    """
    best, reason = MODEL_ADVICE.get(language, _DEFAULT_ADVICE)

    if model in ENGLISH_ONLY_MODELS and language != "en":
        return (f"'{model}' chỉ hiểu tiếng Anh, không dùng được cho ngôn ngữ này. "
                f"Hãy chọn {best}."), "warn"

    if model != best:
        return f"Nên dùng {best}: {reason}", "warn"

    return f"Đang dùng đúng model khuyến nghị. {reason}", "hint"


CHINESE_VARIANTS: list[tuple[str, str]] = [
    ("Giản thể (简体)", "s"),
    ("Phồn thể (繁體)", "t"),
    ("Giữ nguyên như model trả về", "none"),
]

# Câu mồi đẩy model về tiếng phổ thông, ưu tiên giản thể và quan trọng nhất
# là buộc nó chấm câu - bộ cắt dòng dựa vào dấu câu để ngắt cho đẹp.
DEFAULT_ZH_PROMPT = "以下是普通话的句子。"

# phim.mp4 -> phim.stt (bản nguyên ngữ, trung gian) -> phim.srt (bản cuối cùng).
# Đuôi .stt cố tình khác .srt để trình phát không tự nạp nhầm bản chưa dịch.
STT_SUFFIX = ".stt"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Gợi ý sẵn cho ô hướng dẫn dịch. Xưng hô là chỗ máy dịch hay sai nhất khi
# chuyển sang tiếng Việt, vì tiếng Trung và tiếng Anh không phân biệt vai vế.
PROMPT_PRESETS: list[tuple[str, str]] = [
    ("(Không dùng hướng dẫn riêng)", ""),
    ("Phim hiện đại - anh/em",
     "Bối cảnh hiện đại. Nam chính xưng 'anh', gọi nữ chính là 'em'; nữ chính "
     "xưng 'em', gọi nam chính là 'anh'. Bạn bè đồng trang lứa xưng 'tôi - cậu'."),
    ("Phim cổ trang - ta/ngươi",
     "Bối cảnh cổ trang. Dùng lối xưng hô cổ: 'ta - ngươi', 'tại hạ', 'các hạ', "
     "'muội', 'huynh'. Giữ nguyên các chức danh như hoàng thượng, công tử, tiểu thư."),
    ("Phim gia đình",
     "Xưng hô theo vai vế gia đình: con - bố/mẹ, em - anh/chị, cháu - ông/bà. "
     "Giữ giọng thân mật, tự nhiên như hội thoại trong nhà."),
    ("Tài liệu / thuyết minh",
     "Văn phong tài liệu, trung tính, không xưng hô thân mật. Dùng 'chúng ta' "
     "khi người dẫn nói với khán giả. Giữ nguyên thuật ngữ chuyên ngành."),
]

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
    # Hướng dẫn riêng cho người dịch: xưng hô, văn phong, tên riêng giữ nguyên...
    translate_prompt: str = ""
    # Giữ lại bản nguyên ngữ .stt sau khi đã dịch xong.
    keep_stt: bool = False

    # Trạng thái gập/mở của các khung, để màn hình thấp còn chỗ cho bảng danh sách.
    panel_options_open: bool = True
    panel_translate_open: bool = True
    hide_keys: bool = True

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
