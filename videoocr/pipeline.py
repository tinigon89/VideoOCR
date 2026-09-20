"""Điều phối toàn bộ quy trình: quét -> tách audio -> nhận dạng -> ghi SRT."""

from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import audio as audio_mod
from . import chinese, cleaner, gpu
from .config import DEFAULT_ZH_PROMPT, Settings, resolve_model_dir
from .scanner import Job, find_videos, plan_jobs
from .subtitle import Cue, build_cues, finalize_cues, render_srt
from .transcriber import OutOfMemoryError, Transcriber, TranscriptionError

# SRT ghi kèm BOM để PotPlayer/MPC-HC trên Windows không đoán nhầm bảng mã
# và làm vỡ font chữ Trung.
SRT_ENCODING = "utf-8-sig"


class Cancelled(Exception):
    """Người dùng bấm Dừng."""


@dataclass
class Event:
    kind: str                       # log | scan | file_start | file_progress | file_done | finished
    message: str = ""
    level: str = "info"             # info | warn | error | success
    index: int = 0
    total: int = 0
    progress: float = 0.0           # 0..1 cho file đang chạy
    path: Path | None = None
    cues: int = 0                   # số khối phụ đề, chỉ có ở file_done thành công


@dataclass
class Summary:
    done: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total(self) -> int:
        return len(self.done) + len(self.skipped) + len(self.failed)


Emit = Callable[[Event], None]


def _noop(event: Event) -> None:
    pass


def _prompt_for(settings: Settings) -> str | None:
    """Câu mồi chỉ dùng cho tiếng Trung, trừ khi người dùng tự đặt câu khác."""
    prompt = (settings.initial_prompt or "").strip()
    if not prompt:
        return None
    if settings.language == "zh":
        return prompt
    return prompt if prompt != DEFAULT_ZH_PROMPT else None


def _write_srt(cues: list[Cue], target: Path) -> None:
    """Ghi vào file tạm rồi mới đổi tên.

    Nếu bị tắt giữa chừng, lần chạy sau sẽ không thấy một file SRT cụt và
    tưởng nhầm là đã xong.
    """
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_text(render_srt(cues), encoding=SRT_ENCODING)
    partial.replace(target)


def _build_subtitle(words, language: str | None, settings: Settings) -> list[Cue]:
    """Từ danh sách từ có mốc thời gian ra danh sách khối phụ đề hoàn chỉnh."""
    cues = build_cues(
        words,
        max_chars_latin=settings.max_chars_latin,
        max_chars_cjk=settings.max_chars_cjk,
        max_duration=settings.max_cue_duration,
    )
    cues = cleaner.clean_cues(cues, enabled=settings.filter_hallucinations)

    if language == "zh" and settings.chinese_variant in {"s", "t"}:
        for cue in cues:
            cue.text = chinese.convert(cue.text, settings.chinese_variant)

    return finalize_cues(
        cues,
        max_chars_latin=settings.max_chars_latin,
        max_chars_cjk=settings.max_chars_cjk,
    )


def run(
    settings: Settings,
    emit: Emit = _noop,
    cancel: threading.Event | None = None,
) -> Summary:
    """Chạy cả loạt. Hàm này chặn luồng gọi nó, nên GUI phải gọi trong thread riêng."""
    cancel = cancel or threading.Event()
    summary = Summary()

    root = Path(settings.input_dir).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Không tìm thấy thư mục: {root}")

    videos = find_videos(root, recursive=settings.recursive)
    todo, skipped = plan_jobs(videos, overwrite=settings.overwrite)
    summary.skipped.extend(skipped)

    emit(Event(
        kind="scan",
        total=len(todo),
        message=(
            f"Tìm thấy {len(videos)} video: {len(todo)} cần xử lý, "
            f"{len(skipped)} bỏ qua vì đã có phụ đề."
        ),
    ))

    if not todo:
        emit(Event(kind="finished", message="Không có video nào cần xử lý.", level="info"))
        return summary

    warning = gpu.check_driver()
    if warning:
        emit(Event(kind="log", message=warning, level="warn"))

    temp_dir = Path(tempfile.mkdtemp(prefix="videoocr_"))
    model_dir = resolve_model_dir(settings.model_dir)
    transcriber = Transcriber(
        model_name=settings.model,
        device=settings.device,
        compute_type=settings.compute_type,
        batch_size=settings.batch_size,
        beam_size=settings.beam_size,
        vad_filter=settings.vad_filter,
        model_dir=model_dir,
    )

    if not transcriber.supports_language(settings.language):
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise TranscriptionError(
            f"Model '{settings.model}' chỉ hiểu tiếng Anh. "
            f"Hãy chọn 'large-v3' cho tiếng Trung."
        )

    device_label = f"{transcriber.device} / {transcriber.compute_type}"
    # Chỉ nhắc tên card khi thật sự chạy trên nó.
    card = gpu.gpu_name() if transcriber.device == "cuda" else None
    emit(Event(
        kind="log",
        message=(
            f"Đang nạp model {settings.model} ({device_label})"
            + (f" trên {card}" if card else "")
            + ". Lần đầu sẽ mất vài phút để tải model về."
        ),
    ))
    emit(Event(kind="log", message=f"Model lưu tại: {model_dir}"))
    transcriber.load()
    emit(Event(kind="log", message="Đã nạp xong model.", level="success"))

    prompt = _prompt_for(settings)
    # Tách audio của video kế tiếp ngay trong lúc GPU còn đang bận với video
    # hiện tại, để CPU và GPU không phải chờ nhau.
    extractor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="extract")
    pending: dict[int, Future] = {}

    def schedule(index: int) -> None:
        if 0 <= index < len(todo) and index not in pending:
            job = todo[index]
            wav = temp_dir / f"{index:05d}.wav"
            pending[index] = extractor.submit(audio_mod.extract_audio, job.video, wav)

    try:
        schedule(0)
        for index, job in enumerate(todo):
            if cancel.is_set():
                summary.cancelled = True
                break

            schedule(index + 1)
            emit(Event(
                kind="file_start", index=index + 1, total=len(todo),
                path=job.video, message=job.video.name,
            ))

            try:
                line, cue_count = _process_one(
                    job, index, pending, transcriber, settings, prompt, emit, cancel,
                )
            except Cancelled:
                summary.cancelled = True
                break
            except OutOfMemoryError:
                raise
            except Exception as exc:
                summary.failed.append((job.video, str(exc)))
                emit(Event(
                    kind="file_done", index=index + 1, total=len(todo), path=job.video,
                    level="error", message=f"Lỗi: {exc}",
                ))
                continue

            summary.done.append(job.video)
            emit(Event(
                kind="file_done", index=index + 1, total=len(todo), path=job.video,
                level="success", message=line, cues=cue_count,
            ))
    finally:
        for future in pending.values():
            future.cancel()
        extractor.shutdown(wait=False, cancel_futures=True)
        transcriber.unload()
        shutil.rmtree(temp_dir, ignore_errors=True)

    if summary.cancelled:
        emit(Event(kind="finished", level="warn", message=(
            f"Đã dừng. Xong {len(summary.done)}, bỏ qua {len(summary.skipped)}, "
            f"lỗi {len(summary.failed)}."
        )))
    else:
        emit(Event(kind="finished", level="success", message=(
            f"Hoàn tất. Xong {len(summary.done)}, bỏ qua {len(summary.skipped)}, "
            f"lỗi {len(summary.failed)}."
        )))
    return summary


def _process_one(
    job: Job,
    index: int,
    pending: dict[int, Future],
    transcriber: Transcriber,
    settings: Settings,
    prompt: str | None,
    emit: Emit,
    cancel: threading.Event,
) -> tuple[str, int]:
    """Xử lý trọn một video, trả về (dòng tóm tắt để ghi log, số khối phụ đề)."""
    future = pending.pop(index, None)
    if future is None:
        raise RuntimeError("Không tách được audio.")
    wav = Path(future.result())

    duration = audio_mod.probe_duration(job.video)

    def report(position: float) -> None:
        if cancel.is_set():
            raise Cancelled()
        if duration > 0:
            emit(Event(
                kind="file_progress", index=index + 1, path=job.video,
                progress=min(position / duration, 1.0),
            ))

    try:
        words, detected = transcriber.transcribe(
            wav, language=settings.language, initial_prompt=prompt, on_progress=report,
        )
    finally:
        wav.unlink(missing_ok=True)

    cues = _build_subtitle(words, detected, settings)
    if not cues:
        raise RuntimeError("Không nhận ra câu thoại nào (video có thể không có tiếng nói).")

    _write_srt(cues, job.srt)
    emit(Event(kind="file_progress", index=index + 1, path=job.video, progress=1.0))
    return f"{job.srt.name} - {len(cues)} khối phụ đề ({detected or 'không rõ'})", len(cues)
