"""Quét thư mục tìm video và quyết định file nào cần xử lý.

Sơ đồ file của một video:

* ``phim.stt`` - bản nguyên ngữ do nhận dạng giọng nói tạo ra. Đây là bản trung
  gian; đuôi .stt cố tình khác .srt để trình phát không tự nạp nhầm, và để nhìn
  vào thư mục là biết ngay file nào đã xong file nào còn dở.
* ``phim.srt`` - bản cuối cùng, cái mà trình phát sẽ dùng. Có bật dịch thì đây
  là tiếng Việt, không bật thì chính là bản nguyên ngữ.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import STT_SUFFIX, VIDEO_EXTENSIONS


@dataclass(frozen=True)
class Job:
    video: Path
    stt: Path           # bản nguyên ngữ, trung gian
    srt: Path           # bản cuối cùng
    needs_transcribe: bool = True


@dataclass(frozen=True)
class Entry:
    """Một video cùng thông tin để hiển thị trong bảng."""

    video: Path
    stt: Path
    srt: Path
    size: int
    has_stt: bool
    has_srt: bool
    relative: str      # đường dẫn tương đối so với thư mục gốc, để hiện cho gọn


def srt_path_for(video: Path) -> Path:
    """video.mp4 -> video.srt, bản cuối cùng nằm cùng thư mục."""
    return video.with_suffix(".srt")


def stt_path_for(video: Path) -> Path:
    """video.mp4 -> video.stt, bản nguyên ngữ trung gian."""
    return video.with_suffix(STT_SUFFIX)


def has_subtitle(path: Path) -> bool:
    """File rỗng coi như chưa có - nhiều khả năng là tàn dư của lần chạy hỏng."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def format_size(num_bytes: int) -> str:
    """1536000000 -> '1,43 GB'. Dùng dấu phẩy thập phân theo kiểu Việt Nam."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.2f}".replace(".", ",") + f" {unit}"
        size /= 1024
    return f"{num_bytes} B"  # pragma: no cover - vòng lặp trên luôn trả về trước


def find_videos(root: Path, recursive: bool = True) -> list[Path]:
    """Tất cả file video dưới ``root``, sắp xếp theo đường dẫn cho ổn định."""
    if not root.is_dir():
        raise NotADirectoryError(f"Không phải thư mục: {root}")

    pattern = "**/*" if recursive else "*"
    videos = [
        p for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(videos, key=lambda p: str(p).lower())


def plan_jobs(videos: list[Path], overwrite: bool = False) -> tuple[list[Job], list[Path]]:
    """Chia danh sách video thành (cần làm, bỏ qua vì đã có bản cuối).

    Video nào đã có sẵn ``.stt`` thì không nhận dạng lại - lần chạy trước đã làm
    phần nặng nhất rồi, chỉ còn thiếu bước sau.
    """
    todo: list[Job] = []
    skipped: list[Path] = []

    for video in videos:
        srt = srt_path_for(video)
        stt = stt_path_for(video)

        if has_subtitle(srt) and not overwrite:
            skipped.append(video)
            continue

        todo.append(Job(
            video=video,
            stt=stt,
            srt=srt,
            needs_transcribe=overwrite or not has_subtitle(stt),
        ))

    return todo, skipped


def scan_entries(root: Path, recursive: bool = True) -> list[Entry]:
    """Danh sách video kèm dung lượng và tình trạng phụ đề, để hiện lên bảng."""
    entries: list[Entry] = []

    for video in find_videos(root, recursive=recursive):
        try:
            size = video.stat().st_size
        except OSError:
            size = 0
        try:
            relative = str(video.relative_to(root))
        except ValueError:  # pragma: no cover - find_videos luôn trả về file trong root
            relative = video.name

        stt, srt = stt_path_for(video), srt_path_for(video)
        entries.append(Entry(
            video=video,
            stt=stt,
            srt=srt,
            size=size,
            has_stt=has_subtitle(stt),
            has_srt=has_subtitle(srt),
            relative=relative,
        ))

    return entries
