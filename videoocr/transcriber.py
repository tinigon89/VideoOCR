"""Bọc faster-whisper: nạp model một lần rồi dùng lại cho cả loạt video."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from . import gpu
from .config import ENGLISH_ONLY_MODELS
from .subtitle import Word

# Phải khai báo thư mục DLL của CUDA trước khi ctranslate2 được nạp.
gpu.register_cuda_dll_directories()

_OOM_MARKERS = ("out of memory", "bad_alloc", "cublas_status_alloc_failed", "cuda failed with error")


class OutOfMemoryError(RuntimeError):
    """VRAM không đủ cho model đang chọn."""


class TranscriptionError(RuntimeError):
    pass


def _is_oom(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _OOM_MARKERS)


class Transcriber:
    def __init__(
        self,
        model_name: str = "large-v3",
        device: str = "auto",
        compute_type: str = "float16",
        batch_size: int = 8,
        beam_size: int = 5,
        vad_filter: bool = True,
        model_dir: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = gpu.resolve_device(device)
        self.compute_type = gpu.resolve_compute_type(self.device, compute_type)
        self.batch_size = batch_size
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.model_dir = model_dir

        self._model = None
        self._batched = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Nạp model. Tốn thời gian lần đầu vì phải tải về, nên gọi một lần duy nhất."""
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - phụ thuộc môi trường
            raise TranscriptionError(
                "Chưa cài faster-whisper. Chạy setup.bat hoặc "
                "pip install -r requirements.txt"
            ) from exc

        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.model_dir,
            )
        except Exception as exc:
            if _is_oom(exc):
                raise OutOfMemoryError(
                    f"Không đủ VRAM để nạp model '{self.model_name}' ở {self.compute_type}. "
                    f"Thử đổi compute type sang 'int8_float16', giảm batch size, "
                    f"hoặc dùng model 'medium'."
                ) from exc
            raise TranscriptionError(f"Không nạp được model: {exc}") from exc

        try:
            from faster_whisper import BatchedInferencePipeline

            self._batched = BatchedInferencePipeline(model=self._model)
        except Exception:
            # Bản faster-whisper cũ không có batched, chạy tuần tự vẫn tốt.
            self._batched = None

    def unload(self) -> None:
        self._batched = None
        self._model = None

    def supports_language(self, language: str | None) -> bool:
        if self.model_name in ENGLISH_ONLY_MODELS:
            return language == "en"
        return True

    def _run(self, wav: Path, language: str | None, initial_prompt: str | None):
        """Gọi faster-whisper, ưu tiên chế độ batched cho nhanh."""
        common = dict(
            language=language,
            task="transcribe",
            beam_size=self.beam_size,
            word_timestamps=True,
            initial_prompt=initial_prompt or None,
            vad_filter=self.vad_filter,
            # Tắt để model không lấy câu trước làm ngữ cảnh cho câu sau - đây
            # chính là nguồn gốc kiểu lặp vô tận ở những đoạn im lặng.
            condition_on_previous_text=False,
        )

        if self._batched is not None:
            try:
                return self._batched.transcribe(str(wav), batch_size=self.batch_size, **common)
            except TypeError:
                # Tham số không khớp phiên bản - quay về đường tuần tự.
                pass

        return self._model.transcribe(str(wav), **common)

    def transcribe(
        self,
        wav: Path,
        language: str | None = None,
        initial_prompt: str | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[list[Word], str | None]:
        """Nhận dạng một file WAV.

        Trả về (danh sách từ kèm mốc thời gian, mã ngôn ngữ đã dùng).
        """
        if self._model is None:
            self.load()

        try:
            segments, info = self._run(wav, language, initial_prompt)
            words = list(self._collect(segments, on_progress))
        except (OutOfMemoryError, TranscriptionError):
            raise
        except Exception as exc:
            if _is_oom(exc):
                raise OutOfMemoryError(
                    f"Hết VRAM khi đang xử lý {wav.name}. Thử giảm batch size "
                    f"hoặc đổi compute type sang 'int8_float16'."
                ) from exc
            raise TranscriptionError(f"Lỗi khi nhận dạng {wav.name}: {exc}") from exc

        detected = getattr(info, "language", None) or language
        return words, detected

    @staticmethod
    def _collect(segments: Iterator, on_progress: Callable[[float], None] | None) -> Iterator[Word]:
        """Duyệt generator segment và trải phẳng thành từng từ.

        faster-whisper trả segment theo kiểu lười, nên vòng lặp này cũng chính
        là nơi báo tiến trình ra ngoài.
        """
        for segment in segments:
            words = getattr(segment, "words", None)
            if words:
                for word in words:
                    yield Word(start=word.start, end=word.end, text=word.word)
            else:
                # Không có mốc thời gian từng từ thì lấy cả segment làm một đơn vị.
                text = (segment.text or "").strip()
                if text:
                    yield Word(start=segment.start, end=segment.end, text=text)

            if on_progress is not None:
                on_progress(float(segment.end))
