"""Dịch phụ đề sang tiếng Việt bằng Google Gemini.

Gọi thẳng REST API qua thư viện chuẩn nên không thêm phụ thuộc nào.

Hai điểm đáng chú ý trong thiết kế:

* Mỗi lô gửi đi kèm số thứ tự và bắt Gemini trả về JSON đúng theo schema có số
  thứ tự ấy. Nhờ vậy số khối phụ đề và mốc thời gian giữ nguyên tuyệt đối, model
  không gộp hay tách câu làm lệch timing.
* Google tính hạn mức theo *project* chứ không theo từng API key, nên nhiều key
  cùng một project vẫn dùng chung quota. Việc xoay vòng ở đây vẫn có giá trị cho
  key hỏng, key bị thu hồi, và cho nhiều key thuộc các project khác nhau.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .subtitle import join_display_lines

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"

# Chỉ là gợi ý ban đầu cho dropdown. Google đổi danh sách model liên tục nên
# đừng tin vào danh sách chết này - bấm nút lấy danh sách để hỏi thẳng API.
SUGGESTED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

# Những model tuy hỗ trợ generateContent nhưng không dùng để dịch chữ được:
# nhúng vector, sinh ảnh, sinh video, đọc/nghe giọng nói.
_NOT_FOR_TEXT = (
    "embedding", "aqa", "imagen", "veo", "tts",
    "image-generation", "native-audio", "live-", "-live",
    "robotics", "computer-use",
)

# Bản preview/exp hay đổi và hạn mức chặt hơn, nên xếp xuống dưới bản ổn định.
_UNSTABLE = ("preview", "exp", "latest", "-002", "-001")

REQUEST_TIMEOUT = 120
RATE_LIMIT_COOLDOWN = 60.0     # 429: nghỉ key này một phút rồi quay lại
SERVER_ERROR_COOLDOWN = 5.0    # 5xx: trục trặc thoáng qua, nghỉ ngắn
MAX_ATTEMPTS_PER_BATCH = 6
MAX_WAIT_FOR_KEY = 90.0

PROMPT = """Bạn là người dịch phụ đề phim chuyên nghiệp.

Dịch từng dòng phụ đề dưới đây sang tiếng Việt tự nhiên, đúng văn phong hội thoại.

Quy tắc bắt buộc:
- Trả về ĐÚNG {count} dòng, giữ nguyên trường "id" của từng dòng.
- Không gộp hai dòng làm một, không tách một dòng thành hai.
- Dịch trọn nghĩa từng dòng, kể cả khi dòng đó là câu dở dang.
- Giữ nguyên tên riêng, số liệu và các thán từ.
- Không thêm lời giải thích, không thêm dấu ngoặc chú thích.
- Nếu một dòng không có gì để dịch, trả lại đúng nội dung gốc.

Dữ liệu vào (JSON):
{payload}"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "INTEGER"},
                    "vi": {"type": "STRING"},
                },
                "required": ["id", "vi"],
            },
        },
    },
    "required": ["lines"],
}


class TranslationError(RuntimeError):
    pass


class AllKeysFailedError(TranslationError):
    """Không còn API key nào dùng được."""


@dataclass
class KeyState:
    key: str
    index: int
    available_at: float = 0.0
    dead: bool = False
    note: str = ""
    uses: int = 0

    @property
    def label(self) -> str:
        """Chỉ hiện vài ký tự cuối - đừng ghi cả key ra nhật ký."""
        tail = self.key[-4:] if len(self.key) >= 4 else "????"
        return f"key #{self.index + 1} (...{tail})"


class KeyPool:
    """Xoay vòng API key, tạm nghỉ key bị giới hạn và loại hẳn key hỏng."""

    def __init__(self, keys: list[str]) -> None:
        seen: set[str] = set()
        self.states: list[KeyState] = []
        for raw in keys:
            key = raw.strip()
            if key and key not in seen:
                seen.add(key)
                self.states.append(KeyState(key=key, index=len(self.states)))
        self._cursor = 0

    def __len__(self) -> int:
        return len(self.states)

    @property
    def alive(self) -> list[KeyState]:
        return [s for s in self.states if not s.dead]

    def acquire(self, now: float | None = None) -> KeyState | None:
        """Key dùng được ngay bây giờ, theo vòng tròn. None nếu chưa có."""
        now = time.monotonic() if now is None else now
        alive = self.alive
        if not alive:
            return None

        for offset in range(len(self.states)):
            state = self.states[(self._cursor + offset) % len(self.states)]
            if state.dead or state.available_at > now:
                continue
            self._cursor = (self.states.index(state) + 1) % len(self.states)
            state.uses += 1
            return state
        return None

    def wait_seconds(self, now: float | None = None) -> float | None:
        """Còn bao lâu nữa mới có key rảnh. None nếu không key nào sống."""
        now = time.monotonic() if now is None else now
        waits = [s.available_at - now for s in self.alive]
        if not waits:
            return None
        return max(0.0, min(waits))

    def cool_down(self, state: KeyState, seconds: float, note: str = "") -> None:
        state.available_at = time.monotonic() + seconds
        state.note = note

    def kill(self, state: KeyState, note: str) -> None:
        state.dead = True
        state.note = note


def _post(url: str, api_key: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, api_key: str) -> dict:
    request = urllib.request.Request(url, headers={"x-goog-api-key": api_key})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return str(payload.get("error", {}).get("message", "")) or str(exc)
    except Exception:
        return str(exc)


def list_models(api_key: str) -> list[str]:
    """Hỏi Gemini xem key này dùng được những model nào."""
    try:
        data = _get(f"{API_ROOT}/models", api_key)
    except urllib.error.HTTPError as exc:
        raise TranslationError(_error_detail(exc)) from exc
    except urllib.error.URLError as exc:
        raise TranslationError(f"Không kết nối được: {exc.reason}") from exc

    names = []
    for item in data.get("models", []):
        if "generateContent" not in item.get("supportedGenerationMethods", []):
            continue
        name = str(item.get("name", ""))
        names.append(name.removeprefix("models/"))
    return sorted(names)


def sort_models(names: list[str]) -> list[str]:
    """Xếp model dễ chọn: gemini trước, bản ổn định trước, số hiệu mới trước."""
    buckets: dict[tuple[int, int], list[str]] = {}
    for name in names:
        family = 0 if name.startswith("gemini") else 1
        unstable = 1 if any(tag in name for tag in _UNSTABLE) else 0
        buckets.setdefault((family, unstable), []).append(name)

    ordered: list[str] = []
    for key in sorted(buckets):
        # Sắp giảm dần để gemini-3 đứng trên gemini-2.5.
        ordered.extend(sorted(buckets[key], reverse=True))
    return ordered


def translation_models(api_key: str) -> list[str]:
    """Danh sách model dùng dịch được, đã lọc và xếp thứ tự."""
    usable = [
        name for name in list_models(api_key)
        if not any(hint in name.lower() for hint in _NOT_FOR_TEXT)
    ]
    return sort_models(usable)


def fetch_models(keys: list[str]) -> tuple[list[str], str]:
    """Thử lần lượt từng key cho tới khi lấy được danh sách.

    Trả về (danh sách model, lời nhắn để ghi nhật ký).
    """
    if not keys:
        raise TranslationError("Chưa nhập API key nào.")

    problems = []
    for position, key in enumerate(keys, start=1):
        try:
            models = translation_models(key)
        except TranslationError as exc:
            tail = key[-4:] if len(key) >= 4 else "????"
            problems.append(f"key #{position} (...{tail}): {exc}")
            continue
        if models:
            return models, f"Lấy được {len(models)} model từ key #{position}."

    raise TranslationError("Không key nào lấy được danh sách. " + " | ".join(problems))


def check_key(api_key: str) -> tuple[bool, str]:
    """Kiểm tra nhanh một key. Trả về (dùng được, lời nhắn)."""
    try:
        models = list_models(api_key)
    except TranslationError as exc:
        return False, str(exc)
    return True, f"dùng được, thấy {len(models)} model"


def _extract_text(response: dict) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        feedback = response.get("promptFeedback", {}).get("blockReason")
        raise TranslationError(f"Gemini không trả về nội dung (lý do: {feedback or 'không rõ'})")
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts)


@dataclass
class TranslationStats:
    batches: int = 0
    requests: int = 0
    rotations: int = 0
    missing: int = 0
    notes: list[str] = field(default_factory=list)


class GeminiTranslator:
    def __init__(
        self,
        keys: list[str],
        model: str = DEFAULT_MODEL,
        batch_size: int = 40,
        target_language: str = "tiếng Việt",
    ) -> None:
        self.pool = KeyPool(keys)
        self.model = (model or DEFAULT_MODEL).strip()
        self.batch_size = max(1, batch_size)
        self.target_language = target_language
        self.stats = TranslationStats()

        if not len(self.pool):
            raise TranslationError("Chưa nhập API key nào cho Gemini.")

    # ----- gọi API --------------------------------------------------------

    def _call(self, prompt: str, log: Callable[[str, str], None]) -> str:
        """Gửi một yêu cầu, tự đổi key khi gặp giới hạn hoặc key hỏng."""
        url = f"{API_ROOT}/models/{self.model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
                "temperature": 0.2,
            },
        }

        last_error = "không rõ"
        for _ in range(MAX_ATTEMPTS_PER_BATCH):
            state = self.pool.acquire()
            if state is None:
                wait = self.pool.wait_seconds()
                if wait is None:
                    raise AllKeysFailedError(
                        f"Tất cả API key đều không dùng được. Lỗi cuối: {last_error}"
                    )
                if wait > MAX_WAIT_FOR_KEY:
                    raise AllKeysFailedError(
                        f"Mọi key đều đang bị giới hạn, phải chờ {wait:.0f} giây. "
                        f"Dừng lại để bạn khỏi mất thời gian."
                    )
                log(f"Mọi key đang bị giới hạn, chờ {wait:.0f} giây...", "warn")
                time.sleep(wait + 0.5)
                continue

            self.stats.requests += 1
            try:
                return _extract_text(_post(url, state.key, body))

            except urllib.error.HTTPError as exc:
                last_error = _error_detail(exc)
                if exc.code == 429:
                    self.stats.rotations += 1
                    self.pool.cool_down(state, RATE_LIMIT_COOLDOWN, "hết hạn mức")
                    log(f"{state.label} hết hạn mức, chuyển key khác.", "warn")
                elif exc.code in (400, 401, 403):
                    self.pool.kill(state, last_error)
                    log(f"{state.label} không dùng được: {last_error}", "error")
                elif exc.code >= 500:
                    self.stats.rotations += 1
                    self.pool.cool_down(state, SERVER_ERROR_COOLDOWN, "máy chủ lỗi")
                    log(f"Gemini lỗi {exc.code}, thử lại với key khác.", "warn")
                else:
                    raise TranslationError(f"Gemini lỗi {exc.code}: {last_error}") from exc

            except urllib.error.URLError as exc:
                last_error = str(exc.reason)
                self.pool.cool_down(state, SERVER_ERROR_COOLDOWN, "lỗi mạng")
                log(f"Lỗi mạng: {last_error}. Thử lại.", "warn")

            except TimeoutError:
                last_error = "quá thời gian chờ"
                self.pool.cool_down(state, SERVER_ERROR_COOLDOWN, "quá thời gian")
                log("Yêu cầu quá thời gian chờ, thử lại.", "warn")

        raise TranslationError(f"Thử nhiều lần vẫn hỏng. Lỗi cuối: {last_error}")

    # ----- dịch -----------------------------------------------------------

    def _translate_batch(self, batch: list[tuple[int, str]],
                         log: Callable[[str, str], None]) -> dict[int, str]:
        payload = json.dumps(
            [{"id": index, "text": text} for index, text in batch],
            ensure_ascii=False,
        )
        prompt = PROMPT.format(count=len(batch), payload=payload)
        raw = self._call(prompt, log)

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise TranslationError("Gemini trả về JSON hỏng.") from exc

        result: dict[int, str] = {}
        for item in data.get("lines", []):
            try:
                result[int(item["id"])] = str(item["vi"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def translate(
        self,
        texts: list[str],
        log: Callable[[str, str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[str]:
        """Dịch danh sách câu, trả về danh sách cùng độ dài và cùng thứ tự.

        Câu nào Gemini bỏ sót thì giữ nguyên bản gốc thay vì làm hỏng cả file.
        """
        log = log or (lambda message, level: None)
        # Phụ đề đã bẻ hai dòng; nối lại thành một câu cho model dễ hiểu.
        flat = [join_display_lines(text) for text in texts]
        output = list(texts)

        total = len(flat)
        done = 0
        for start in range(0, total, self.batch_size):
            if should_cancel is not None and should_cancel():
                break

            batch = [(i, flat[i]) for i in range(start, min(start + self.batch_size, total))]
            batch = [(i, text) for i, text in batch if text]
            if not batch:
                done = min(start + self.batch_size, total)
                continue

            self.stats.batches += 1
            translated = self._translate_batch(batch, log)

            missing = 0
            for index, _ in batch:
                value = translated.get(index, "").strip()
                if value:
                    output[index] = value
                else:
                    missing += 1
            if missing:
                self.stats.missing += missing
                log(f"Gemini bỏ sót {missing} dòng, giữ nguyên bản gốc cho phần đó.", "warn")

            done = min(start + self.batch_size, total)
            if on_progress is not None:
                on_progress(done, total)

        return output

    def key_report(self) -> str:
        """Tóm tắt tình trạng từng key để ghi vào nhật ký."""
        parts = []
        for state in self.pool.states:
            if state.dead:
                parts.append(f"{state.label}: hỏng")
            elif state.note:
                parts.append(f"{state.label}: {state.uses} lượt, {state.note}")
            else:
                parts.append(f"{state.label}: {state.uses} lượt")
        return " | ".join(parts)
