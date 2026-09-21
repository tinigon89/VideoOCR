"""Tách audio khỏi video bằng ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Whisper làm việc ở 16kHz mono, tách sẵn đúng định dạng đó cho nhẹ.
SAMPLE_RATE = 16000

_APP_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_BIN = _APP_ROOT / "bin"

# Trên Windows, đừng để cửa sổ console đen nháy lên mỗi lần gọi ffmpeg.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FFmpegNotFound(RuntimeError):
    pass


class AudioExtractionError(RuntimeError):
    pass


def _find_tool(name: str) -> str:
    """Ưu tiên bản ffmpeg setup.bat tải về cạnh app, sau đó mới tới PATH."""
    exe = f"{name}.exe" if sys.platform == "win32" else name
    bundled = _BUNDLED_BIN / exe
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegNotFound(
        f"Không tìm thấy {name}. Chạy setup.bat để tải về, "
        f"hoặc cài ffmpeg và thêm vào PATH."
    )


def find_ffmpeg() -> str:
    return _find_tool("ffmpeg")


def find_ffprobe() -> str:
    return _find_tool("ffprobe")


def probe_duration(video: Path) -> float:
    """Thời lượng video tính bằng giây; 0.0 nếu không đọc được."""
    try:
        result = subprocess.run(
            [
                find_ffprobe(), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", str(video),
            ],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            return 0.0
        return float(json.loads(result.stdout)["format"]["duration"])
    except (FFmpegNotFound, ValueError, KeyError, TypeError):
        return 0.0


def has_audio_stream(video: Path) -> bool:
    try:
        result = subprocess.run(
            [
                find_ffprobe(), "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "json", str(video),
            ],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            return True  # Không chắc thì cứ thử tách, để ffmpeg nói tiếng nói cuối.
        return bool(json.loads(result.stdout).get("streams"))
    except (FFmpegNotFound, ValueError):
        return True


def extract_audio(video: Path, out_wav: Path) -> Path:
    """Tách track audio đầu tiên thành WAV 16kHz mono."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        find_ffmpeg(), "-nostdin", "-y",
        "-i", str(video),
        "-vn",                      # bỏ hình
        "-map", "0:a:0",            # chỉ lấy track audio đầu tiên
        "-ac", "1",                 # mono
        "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        "-loglevel", "error",
        str(out_wav),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)

    if result.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size == 0:
        detail = (result.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else "ffmpeg không cho biết lý do"
        raise AudioExtractionError(f"Không tách được audio từ {video.name}: {tail}")

    return out_wav
