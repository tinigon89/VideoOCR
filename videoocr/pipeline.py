"""Điều phối toàn bộ quy trình: quét -> tách audio -> nhận dạng -> ghi SRT."""

from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import audio as audio_mod
from . import chinese, cleaner, gpu
from .config import DEFAULT_ZH_PROMPT, Settings, resolve_model_dir
from .scanner import Job, find_videos, has_subtitle, plan_jobs, vi_path_for
from .subtitle import Cue, build_cues, finalize_cues, parse_srt, render_srt, wrap_text
from .transcriber import OutOfMemoryError, Transcriber, TranscriptionError
from .translator import GeminiTranslator, TranslationError

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
    # Kết quả phần dịch: "" chưa/không dịch, "done", "skipped", "failed".
    translated: str = ""
    # Công việc đang chạy: "transcribe" (nhận dạng, có thể kèm dịch) hoặc
    # "translate" (chỉ dịch lại các .srt sẵn có).
    stage: str = "transcribe"


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


def _outcome_level(summary: "Summary") -> str:
    """Chỉ báo màu xanh khi thật sự không có gì hỏng."""
    if summary.cancelled or summary.failed:
        return "warn"
    return "success"


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


def _make_translator(settings: Settings, emit: Emit) -> GeminiTranslator | None:
    """Dựng bộ dịch nếu người dùng bật. Thiếu key thì báo và bỏ qua phần dịch."""
    if not settings.translate_enabled:
        return None
    try:
        translator = GeminiTranslator(
            keys=settings.gemini_keys,
            model=settings.gemini_model,
            batch_size=settings.translate_batch_size,
            instructions=settings.translate_prompt,
        )
    except TranslationError as exc:
        emit(Event(kind="log", level="warn",
                   message=f"Bỏ qua phần dịch: {exc}"))
        return None

    emit(Event(kind="log", message=(
        f"Dịch tiếng Việt bằng {translator.model}, "
        f"{len(translator.pool)} API key."
    )))
    return translator


def _translate_srt(
    source: Path,
    target: Path,
    translator: GeminiTranslator,
    settings: Settings,
    emit: Emit,
    cancel: threading.Event,
) -> int:
    """Dịch một file SRT sang tiếng Việt. Trả về số khối đã dịch."""
    cues = parse_srt(source.read_text(encoding="utf-8-sig"))
    if not cues:
        raise TranslationError(f"{source.name} không có khối phụ đề nào.")

    def log(message: str, level: str) -> None:
        emit(Event(kind="log", message=message, level=level))

    translated = translator.translate(
        [cue.text for cue in cues],
        log=log,
        should_cancel=cancel.is_set,
    )
    if cancel.is_set():
        raise Cancelled()

    # Tiếng Việt là chữ Latin nên bẻ dòng theo bề rộng của chữ Latin.
    for cue, text in zip(cues, translated):
        cue.text = wrap_text(" ".join(text.split()), settings.max_chars_latin)

    _write_srt(cues, target)
    return len(cues)


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
    translator = _make_translator(settings, emit)
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
                line, cue_count, translated = _process_one(
                    job, index, pending, transcriber, settings, prompt, emit, cancel,
                    translator,
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
                level="success", message=line, cues=cue_count, translated=translated,
            ))
    finally:
        for future in pending.values():
            future.cancel()
        extractor.shutdown(wait=False, cancel_futures=True)
        transcriber.unload()
        shutil.rmtree(temp_dir, ignore_errors=True)

    emit(Event(kind="finished", level=_outcome_level(summary), message=(
        f"{'Đã dừng' if summary.cancelled else 'Hoàn tất'}. "
        f"Xong {len(summary.done)}, bỏ qua {len(summary.skipped)}, "
        f"lỗi {len(summary.failed)}."
    )))
    return summary


def translate_existing(
    settings: Settings,
    emit: Emit = _noop,
    cancel: threading.Event | None = None,
) -> Summary:
    """Dịch các file .srt đã có trong thư mục mà không nhận dạng lại.

    Dùng cho những video bạn đã làm phụ đề từ trước.
    """
    cancel = cancel or threading.Event()
    summary = Summary()

    root = Path(settings.input_dir).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Không tìm thấy thư mục: {root}")

    videos = find_videos(root, recursive=settings.recursive)
    todo: list[tuple[Path, Path, Path]] = []
    for video in videos:
        source = video.with_suffix(".srt")
        target = vi_path_for(video)
        if not has_subtitle(source):
            continue
        if has_subtitle(target) and not settings.overwrite:
            summary.skipped.append(video)
        else:
            todo.append((video, source, target))

    emit(Event(kind="scan", total=len(todo), message=(
        f"{len(todo)} phụ đề cần dịch, {len(summary.skipped)} đã có bản tiếng Việt."
    )))
    if not todo:
        emit(Event(kind="finished", message="Không có phụ đề nào cần dịch.", level="info"))
        return summary

    forced = replace(settings, translate_enabled=True)
    translator = _make_translator(forced, emit)
    if translator is None:
        emit(Event(kind="finished", level="error",
                   message="Không dịch được vì thiếu API key hợp lệ."))
        return summary

    for index, (video, source, target) in enumerate(todo):
        if cancel.is_set():
            summary.cancelled = True
            break

        emit(Event(kind="file_start", index=index + 1, total=len(todo),
                   path=video, message=source.name, stage="translate"))
        try:
            count = _translate_srt(source, target, translator, settings, emit, cancel)
        except Cancelled:
            summary.cancelled = True
            break
        except Exception as exc:
            summary.failed.append((video, str(exc)))
            emit(Event(kind="file_done", index=index + 1, total=len(todo), path=video,
                       level="error", message=f"Lỗi: {exc}",
                       stage="translate", translated="failed"))
            continue

        summary.done.append(video)
        emit(Event(kind="file_done", index=index + 1, total=len(todo), path=video,
                   level="success", cues=count, translated="done", stage="translate",
                   message=f"{target.name} - {count} khối đã dịch"))

    emit(Event(kind="log", message=f"Tình trạng key: {translator.key_report()}"))
    level = _outcome_level(summary)
    emit(Event(kind="finished", level=level, message=(
        f"{'Đã dừng' if summary.cancelled else 'Dịch xong'}. "
        f"Xong {len(summary.done)}, bỏ qua {len(summary.skipped)}, "
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
    translator: GeminiTranslator | None = None,
) -> tuple[str, int, str]:
    """Xử lý trọn một video.

    Trả về (dòng tóm tắt để ghi log, số khối phụ đề, kết quả phần dịch).
    """
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
    line = f"{job.srt.name} - {len(cues)} khối phụ đề ({detected or 'không rõ'})"

    translated = ""
    if translator is not None:
        target = vi_path_for(job.video)
        if has_subtitle(target) and not settings.overwrite:
            translated = "skipped"
            line += " | đã có bản dịch, bỏ qua"
        else:
            emit(Event(kind="translating", index=index + 1, path=job.video,
                       message=f"Đang dịch {job.srt.name} sang tiếng Việt..."))
            try:
                _translate_srt(job.srt, target, translator, settings, emit, cancel)
                translated = "done"
                line += f" | đã dịch -> {target.name}"
            except Cancelled:
                raise
            except Exception as exc:
                # Dịch hỏng thì bản nguyên ngữ vẫn còn nguyên, không coi là
                # video lỗi - chỉ ghi nhận lại để chạy lại phần dịch sau.
                translated = "failed"
                emit(Event(kind="log", level="warn",
                           message=f"Dịch {job.srt.name} không xong: {exc}"))
                line += " | dịch lỗi"

    return line, len(cues), translated
