"""Điều phối toàn bộ quy trình: quét -> tách audio -> nhận dạng -> ghi SRT."""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import audio as audio_mod
from . import chinese, cleaner, gpu
from .config import DEFAULT_ZH_PROMPT, Settings, resolve_model_dir
from .scanner import Job, find_videos, has_subtitle, plan_jobs, stt_path_for
from .subtitle import Cue, build_cues, finalize_cues, parse_srt, render_srt, wrap_text
from .transcriber import OutOfMemoryError, Transcriber, TranscriptionError
from .translator import GeminiTranslator, TranslationError

# SRT ghi kèm BOM để PotPlayer/MPC-HC trên Windows không đoán nhầm bảng mã
# và làm vỡ font chữ Trung.
SRT_ENCODING = "utf-8-sig"

# Số lần thử đổi tên file và nhịp nghỉ giữa các lần, để vượt qua khoảng thời
# gian phần mềm diệt virus đang giữ khoá file vừa ghi. Tổng cộng chờ tối đa
# khoảng 6 giây trước khi chuyển sang đường lùi.
REPLACE_ATTEMPTS = 6
REPLACE_DELAY = 0.3


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


def format_duration(seconds: float) -> str:
    """12.3 -> '12 giây'; 754 -> '12 phút 34 giây'; 7384 -> '2 giờ 3 phút'."""
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)

    if hours:
        return f"{hours} giờ {minutes} phút"
    if minutes:
        return f"{minutes} phút {secs} giây"
    return f"{secs} giây"


def _elapsed(started: float) -> str:
    return format_duration(time.monotonic() - started)


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


def _replace_with_retry(source: Path, target: Path) -> None:
    """Đổi tên file, thử lại khi bị khoá tạm.

    Trên Windows, phần mềm diệt virus quét file ngay khi nó vừa được ghi xong và
    giữ khoá trong tích tắc. Nếu ``os.replace`` rơi đúng khoảnh khắc đó thì văng
    WinError 32, trong khi chỉ cần chờ một nhịp là qua.
    """
    last: OSError | None = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            source.replace(target)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(REPLACE_DELAY * (attempt + 1))
    assert last is not None
    raise last


def _write_srt(cues: list[Cue], target: Path) -> None:
    """Ghi vào file tạm rồi mới đổi tên.

    Nếu bị tắt giữa chừng, lần chạy sau sẽ không thấy một file SRT cụt và
    tưởng nhầm là đã xong.
    """
    text = render_srt(cues)
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_text(text, encoding=SRT_ENCODING)

    try:
        _replace_with_retry(partial, target)
        return
    except PermissionError as exc:
        locked = exc

    # Chờ mãi vẫn bị khoá thì ghi thẳng vào đích. Mất tính nguyên tử của bước
    # đổi tên, nhưng đổi lại không phí cả tiếng đồng hồ nhận dạng chỉ vì một
    # file bị giữ trong vài giây.
    try:
        target.write_text(text, encoding=SRT_ENCODING)
    except OSError:
        raise RuntimeError(
            f"Không ghi được {target.name}: file đang bị khoá, thường là do phần "
            f"mềm diệt virus hoặc ứng dụng đồng bộ. Kết quả vẫn nằm nguyên ở "
            f"{partial.name}, đổi tên thành {target.name} là dùng được."
        ) from locked

    partial.unlink(missing_ok=True)


def recover_partials(root: Path, recursive: bool = True) -> list[Path]:
    """Cứu các file .part còn sót lại từ lần chạy trước bị khoá.

    Trả về danh sách file đã cứu được.
    """
    rescued: list[Path] = []
    pattern = "**/*.part" if recursive else "*.part"

    for partial in sorted(root.glob(pattern)):
        target = partial.with_suffix("")
        if not partial.is_file() or partial.stat().st_size == 0:
            continue
        if target.exists():
            continue  # đích đã có rồi, .part chỉ là rác
        try:
            _replace_with_retry(partial, target)
        except OSError:
            continue
        rescued.append(target)

    return rescued


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
    started = time.monotonic()

    root = Path(settings.input_dir).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Không tìm thấy thư mục: {root}")

    rescued = recover_partials(root, recursive=settings.recursive)
    if rescued:
        emit(Event(kind="log", level="success", message=(
            f"Cứu được {len(rescued)} file dở từ lần chạy trước: "
            + ", ".join(p.name for p in rescued[:5])
            + (" ..." if len(rescued) > 5 else "")
        )))

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
        f"lỗi {len(summary.failed)}. Tổng thời gian {_elapsed(started)}."
    )))
    return summary


def translate_existing(
    settings: Settings,
    emit: Emit = _noop,
    cancel: threading.Event | None = None,
) -> Summary:
    """Dịch các bản nguyên ngữ .stt đã có mà không nhận dạng lại.

    Dùng cho những video mà lần chạy trước đã nhận dạng xong nhưng phần dịch
    còn dở, hoặc khi bạn muốn dịch lại với hướng dẫn xưng hô khác.
    """
    cancel = cancel or threading.Event()
    summary = Summary()
    started = time.monotonic()

    root = Path(settings.input_dir).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Không tìm thấy thư mục: {root}")

    videos = find_videos(root, recursive=settings.recursive)
    todo: list[tuple[Path, Path, Path]] = []
    for video in videos:
        source = stt_path_for(video)
        target = video.with_suffix(".srt")
        if not has_subtitle(source):
            continue
        if has_subtitle(target) and not settings.overwrite:
            summary.skipped.append(video)
        else:
            todo.append((video, source, target))

    emit(Event(kind="scan", total=len(todo), message=(
        f"{len(todo)} bản nguyên ngữ cần dịch, "
        f"{len(summary.skipped)} đã có phụ đề tiếng Việt."
    )))
    if not todo:
        emit(Event(kind="finished", level="info", message=(
            "Không có bản nguyên ngữ nào cần dịch. "
            "Nhận dạng trước để có file .stt, rồi mới dịch được."
        )))
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
        file_started = time.monotonic()
        try:
            count = _translate_srt(source, target, translator, settings, emit, cancel)
            if not settings.keep_stt:
                source.unlink(missing_ok=True)
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
                   message=f"{target.name} - {count} khối đã dịch · {_elapsed(file_started)}"))

    emit(Event(kind="log", message=f"Tình trạng key: {translator.key_report()}"))
    level = _outcome_level(summary)
    emit(Event(kind="finished", level=level, message=(
        f"{'Đã dừng' if summary.cancelled else 'Dịch xong'}. "
        f"Xong {len(summary.done)}, bỏ qua {len(summary.skipped)}, "
        f"lỗi {len(summary.failed)}. Tổng thời gian {_elapsed(started)}."
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
    """Xử lý trọn một video: nhận dạng ra .stt rồi dựng .srt cuối cùng.

    Trả về (dòng tóm tắt để ghi log, số khối phụ đề, kết quả phần dịch).
    """
    started = time.monotonic()

    if job.needs_transcribe:
        cues, detected = _transcribe_one(
            job, index, pending, transcriber, settings, prompt, emit, cancel)
        _write_srt(cues, job.stt)
    else:
        # Lần chạy trước đã nhận dạng xong, chỉ dở ở bước sau. Đừng bắt GPU
        # làm lại phần nặng nhất.
        cues = parse_srt(job.stt.read_text(encoding=SRT_ENCODING))
        detected = settings.language
        emit(Event(kind="log", message=(
            f"Dùng lại {job.stt.name} từ lần chạy trước, không nhận dạng lại."
        )))

    emit(Event(kind="file_progress", index=index + 1, path=job.video, progress=1.0))
    count = len(cues)
    translated = ""

    if translator is None:
        # Không dịch thì bản nguyên ngữ chính là bản cuối cùng.
        _promote(job.stt, job.srt, cues)
        line = f"{job.srt.name} - {count} khối ({detected or 'không rõ'})"
    else:
        emit(Event(kind="translating", index=index + 1, path=job.video,
                   message=f"Đang dịch {job.stt.name} sang tiếng Việt..."))
        try:
            _translate_srt(job.stt, job.srt, translator, settings, emit, cancel)
            translated = "done"
            line = f"{job.srt.name} - {count} khối, đã dịch từ {detected or 'không rõ'}"
            if not settings.keep_stt:
                job.stt.unlink(missing_ok=True)
        except Cancelled:
            raise
        except Exception as exc:
            # Dịch hỏng thì bản nguyên ngữ vẫn còn nguyên trong .stt, chạy lại
            # là dịch tiếp được mà không phải nhận dạng lại từ đầu.
            translated = "failed"
            emit(Event(kind="log", level="warn",
                       message=f"Dịch {job.stt.name} không xong: {exc}"))
            line = f"{job.stt.name} - {count} khối, CHƯA dịch được"

    return f"{line} · {_elapsed(started)}", count, translated


def _transcribe_one(
    job: Job,
    index: int,
    pending: dict[int, Future],
    transcriber: Transcriber,
    settings: Settings,
    prompt: str | None,
    emit: Emit,
    cancel: threading.Event,
) -> tuple[list[Cue], str | None]:
    """Tách audio, nhận dạng, dựng các khối phụ đề nguyên ngữ."""
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
    return cues, detected


def _promote(stt: Path, srt: Path, cues: list[Cue]) -> None:
    """Đưa bản nguyên ngữ lên thành bản cuối cùng khi không dịch."""
    try:
        _replace_with_retry(stt, srt)
    except OSError:
        # Đổi tên hỏng thì ghi lại nội dung, miễn sao có file cuối cùng.
        _write_srt(cues, srt)
        stt.unlink(missing_ok=True)
