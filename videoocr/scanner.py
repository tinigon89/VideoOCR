"""Quét thư mục tìm video và quyết định file nào cần xử lý."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import TRANSLATED_SUFFIX, VIDEO_EXTENSIONS


@dataclass(frozen=True)
class Job:
    video: Path
    srt: Path


@dataclass(frozen=True)
class Entry:
    """Một video cùng thông tin để hiển thị trong bảng."""

    video: Path
    srt: Path
    size: int
    has_srt: bool
    relative: str      # đường dẫn tương đối so với thư mục gốc, để hiện cho gọn
    vi: Path | None = None
    has_vi: bool = False


def srt_path_for(video: Path) -> Path:
    """video.mp4 -> video.srt, nằm cùng thư mục."""
    return video.with_suffix(".srt")


def vi_path_for(video: Path) -> Path:
    """phim.mp4 -> phim.vi.srt, nằm cạnh bản nguyên ngữ."""
    return video.with_name(video.stem + TRANSLATED_SUFFIX)


def has_subtitle(srt: Path) -> bool:
    """File rỗng coi như chưa có - nhiều khả năng là tàn dư của lần chạy hỏng."""
    try:
        return srt.is_file() and srt.stat().st_size > 0
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
    """Chia danh sách video thành (cần làm, bỏ qua vì đã có .srt)."""
    todo: list[Job] = []
    skipped: list[Path] = []

    for video in videos:
        srt = srt_path_for(video)
        if has_subtitle(srt) and not overwrite:
            skipped.append(video)
        else:
            todo.append(Job(video=video, srt=srt))

    return todo, skipped


def scan_entries(root: Path, recursive: bool = True) -> list[Entry]:
    """Danh sách video kèm dung lượng và tình trạng phụ đề, để hiện lên bảng."""
    entries: list[Entry] = []

    for video in find_videos(root, recursive=recursive):
        srt = srt_path_for(video)
        try:
            size = video.stat().st_size
        except OSError:
            size = 0
        try:
            relative = str(video.relative_to(root))
        except ValueError:  # pragma: no cover - find_videos luôn trả về file trong root
            relative = video.name

        vi = vi_path_for(video)
        entries.append(Entry(
            video=video,
            srt=srt,
            size=size,
            has_srt=has_subtitle(srt),
            relative=relative,
            vi=vi,
            has_vi=has_subtitle(vi),
        ))

    return entries
