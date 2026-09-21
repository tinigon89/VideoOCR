"""Gộp nhiều video trong một thư mục thành một file.

Hai đường đi tuỳ theo các file có cùng thông số hay không:

* **Nối thẳng (stream copy)** - khi mọi file cùng codec, cùng độ phân giải, cùng
  thông số audio. Không giải mã lại nên nhanh gần bằng tốc độ chép file và chất
  lượng giữ nguyên tuyệt đối. Video tải về từ cùng một nguồn hầu như luôn rơi
  vào trường hợp này.
* **Mã hoá lại** - khi thông số khác nhau. Mỗi video được co giãn và chèn viền
  về cùng một khung hình rồi mới nối, nên chậm hơn nhiều và chất lượng giảm đôi
  chút.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .audio import NO_WINDOW, find_ffmpeg, find_ffprobe

# Mã hoá lại thì dùng preset nhanh, chất lượng vẫn thừa cho phụ đề.
ENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k",
]


class MergeError(RuntimeError):
    pass


class MergeCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamInfo:
    """Thông số cần khớp nhau thì mới nối thẳng được."""

    video_codec: str
    width: int
    height: int
    audio_codec: str
    sample_rate: str
    channels: int
    duration: float

    @property
    def shape(self) -> tuple:
        """Phần quyết định có nối thẳng được hay không (bỏ thời lượng ra)."""
        return (self.video_codec, self.width, self.height,
                self.audio_codec, self.sample_rate, self.channels)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)


def probe(video: Path) -> StreamInfo:
    """Đọc thông số luồng của một video."""
    result = subprocess.run(
        [
            find_ffprobe(), "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height,"
                             "sample_rate,channels:format=duration",
            "-of", "json", str(video),
        ],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise MergeError(f"Không đọc được {video.name}: {result.stderr.strip()[:200]}")

    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise MergeError(f"ffprobe trả về dữ liệu hỏng cho {video.name}.") from exc

    video_stream: dict = {}
    audio_stream: dict = {}
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not video_stream:
            video_stream = stream
        elif kind == "audio" and not audio_stream:
            audio_stream = stream

    if not video_stream:
        raise MergeError(f"{video.name} không có luồng hình.")

    try:
        duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    return StreamInfo(
        video_codec=str(video_stream.get("codec_name", "")),
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        audio_codec=str(audio_stream.get("codec_name", "")),
        sample_rate=str(audio_stream.get("sample_rate", "")),
        channels=int(audio_stream.get("channels", 0) or 0),
        duration=duration,
    )


def inspect(videos: list[Path]) -> tuple[list[StreamInfo], bool, str]:
    """Dò tất cả video. Trả về (thông số, nối thẳng được không, lời giải thích)."""
    if len(videos) < 2:
        raise MergeError("Cần ít nhất hai video mới có gì để gộp.")

    infos = [probe(video) for video in videos]
    shapes = {info.shape for info in infos}

    if len(shapes) == 1:
        first = infos[0]
        return infos, True, (
            f"Tất cả {len(videos)} video cùng thông số "
            f"({first.width}x{first.height}, {first.video_codec}), nối thẳng được - "
            f"rất nhanh và không giảm chất lượng."
        )

    missing_audio = [v.name for v, i in zip(videos, infos) if not i.has_audio]
    if missing_audio:
        raise MergeError(
            "Không gộp được vì các video có thông số khác nhau mà "
            f"{len(missing_audio)} file lại không có tiếng "
            f"({', '.join(missing_audio[:3])}). Hãy tách riêng nhóm không tiếng."
        )

    sizes = {(i.width, i.height) for i in infos}
    codecs = {i.video_codec for i in infos}
    reasons = []
    if len(sizes) > 1:
        reasons.append("độ phân giải khác nhau (" +
                       ", ".join(f"{w}x{h}" for w, h in sorted(sizes)) + ")")
    if len(codecs) > 1:
        reasons.append("codec khác nhau (" + ", ".join(sorted(codecs)) + ")")
    if not reasons:
        reasons.append("thông số audio khác nhau")

    return infos, False, (
        "Phải mã hoá lại vì " + "; ".join(reasons) +
        ". Việc này chậm hơn nhiều và chất lượng giảm đôi chút."
    )


def target_size(infos: list[StreamInfo]) -> tuple[int, int]:
    """Khung hình đích khi mã hoá lại: lấy bề rộng và chiều cao lớn nhất.

    Số lẻ làm libx264 từ chối nên làm tròn xuống số chẵn.
    """
    width = max(i.width for i in infos)
    height = max(i.height for i in infos)
    return width - width % 2, height - height % 2


def _concat_list(videos: list[Path], folder: Path) -> Path:
    """File danh sách cho concat demuxer của ffmpeg."""
    listing = folder / "concat.txt"
    lines = []
    for video in videos:
        # concat demuxer nhận dấu / trên Windows, và dấu nháy đơn phải thoát.
        path = str(video.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{path}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing


def _copy_command(listing: Path, output: Path) -> list[str]:
    return [
        find_ffmpeg(), "-nostdin", "-y", "-hide_banner",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-movflags", "+faststart",
        "-progress", "pipe:1", "-loglevel", "error",
        str(output),
    ]


def _encode_command(videos: list[Path], infos: list[StreamInfo], output: Path) -> list[str]:
    """Nối bằng filter concat, co giãn mọi video về cùng khung hình."""
    width, height = target_size(infos)

    command = [find_ffmpeg(), "-nostdin", "-y", "-hide_banner"]
    for video in videos:
        command += ["-i", str(video)]

    steps = []
    for index in range(len(videos)):
        steps.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{index}]"
        )
        steps.append(f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo[a{index}]")

    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(videos)))
    steps.append(f"{pairs}concat=n={len(videos)}:v=1:a=1[v][a]")

    command += [
        "-filter_complex", ";".join(steps),
        "-map", "[v]", "-map", "[a]",
        *ENCODE_ARGS, "-movflags", "+faststart",
        "-progress", "pipe:1", "-loglevel", "error",
        str(output),
    ]
    return command


def merge(
    videos: list[Path],
    output: Path,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    force_encode: bool = False,
) -> tuple[Path, str]:
    """Gộp danh sách video thành một file. Trả về (file kết quả, lời tóm tắt)."""
    infos, can_copy, note = inspect(videos)
    total = sum(i.duration for i in infos)

    if output.resolve() in {v.resolve() for v in videos}:
        raise MergeError("File kết quả trùng với một video đầu vào.")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="videoocr_merge_") as work:
        folder = Path(work)
        # Ghi ra file tạm rồi mới đổi tên, để nửa chừng bị dừng không để lại
        # một file video hỏng mang đúng tên đích.
        partial = folder / f"merged{output.suffix or '.mp4'}"

        if can_copy and not force_encode:
            command = _copy_command(_concat_list(videos, folder), partial)
        else:
            command = _encode_command(videos, infos, partial)

        _run(command, total, on_progress, should_cancel)

        if not partial.exists() or partial.stat().st_size == 0:
            raise MergeError("ffmpeg chạy xong nhưng không tạo ra file nào.")
        partial.replace(output)

    how = "nối thẳng" if (can_copy and not force_encode) else "mã hoá lại"
    return output, f"Đã gộp {len(videos)} video bằng cách {how}. {note}"


def _run(
    command: list[str],
    total: float,
    on_progress: Callable[[float], None] | None,
    should_cancel: Callable[[], bool] | None,
) -> None:
    """Chạy ffmpeg và đọc tiến trình từ dòng ra chuẩn."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=NO_WINDOW,
    )

    try:
        assert process.stdout is not None
        for line in process.stdout:
            if should_cancel is not None and should_cancel():
                process.kill()
                raise MergeCancelled()
            # ffmpeg -progress in ra từng dòng khoá=giá trị.
            if line.startswith("out_time_ms=") and total > 0 and on_progress is not None:
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(min(done / total, 1.0))
    finally:
        process.stdout.close() if process.stdout else None
        stderr = process.stderr.read() if process.stderr else ""
        process.wait()

    if process.returncode != 0:
        tail = (stderr or "").strip().splitlines()
        raise MergeError("ffmpeg lỗi: " + (tail[-1] if tail else "không rõ lý do"))


def default_output(folder: Path) -> Path:
    """Mặc định đặt file gộp ở thư mục cha, tên theo thư mục.

    Để ngoài thư mục nguồn để nó không lọt vào danh sách video cần làm phụ đề.
    """
    name = folder.resolve().name or "merged"
    parent = folder.resolve().parent
    candidate = parent / f"{name}.mp4"

    index = 2
    while candidate.exists():
        candidate = parent / f"{name} ({index}).mp4"
        index += 1
    return candidate
