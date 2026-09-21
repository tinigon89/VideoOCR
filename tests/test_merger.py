"""Kiểm tra phần gộp video. Chạy ffmpeg thật nhưng trên clip tí hon do lavfi tạo."""

import shutil
import subprocess
from pathlib import Path

import pytest

from videoocr import merger
from videoocr.merger import MergeError, StreamInfo, default_output, inspect, target_size

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="máy chưa cài ffmpeg")


def make_clip(path: Path, seconds: float = 1.0, size: str = "160x120",
              with_audio: bool = True) -> Path:
    """Dựng một clip nhỏ bằng nguồn giả của ffmpeg."""
    command = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds)]
    if with_audio:
        command += ["-c:a", "aac", "-ac", "2", "-ar", "48000", "-shortest"]
    command.append(str(path))

    subprocess.run(command, check=True, capture_output=True)
    return path


def info(**overrides) -> StreamInfo:
    base = dict(video_codec="h264", width=1920, height=1080, audio_codec="aac",
                sample_rate="48000", channels=2, duration=10.0)
    base.update(overrides)
    return StreamInfo(**base)


class TestTargetSize:
    def test_takes_the_largest(self):
        assert target_size([info(width=640, height=360), info()]) == (1920, 1080)

    def test_rounds_odd_numbers_down(self):
        # libx264 từ chối kích thước lẻ.
        assert target_size([info(width=641, height=361)]) == (640, 360)

    def test_mixes_width_and_height_independently(self):
        assert target_size([info(width=1920, height=360),
                            info(width=640, height=1080)]) == (1920, 1080)


class TestDefaultOutput:
    def test_lands_beside_the_folder_not_inside(self, tmp_path):
        folder = tmp_path / "Phim hay"
        folder.mkdir()
        # Để trong thư mục nguồn thì nó lại lọt vào danh sách cần làm phụ đề.
        assert default_output(folder).parent == tmp_path

    def test_named_after_the_folder(self, tmp_path):
        folder = tmp_path / "Tập 20"
        folder.mkdir()
        assert default_output(folder).name == "Tập 20.mp4"

    def test_avoids_clobbering_an_existing_file(self, tmp_path):
        folder = tmp_path / "Phim"
        folder.mkdir()
        (tmp_path / "Phim.mp4").write_bytes(b"x")
        assert default_output(folder).name == "Phim (2).mp4"


class TestInspectGuards:
    def test_needs_at_least_two(self, tmp_path):
        with pytest.raises(MergeError):
            inspect([tmp_path / "a.mp4"])

    def test_empty_list(self, tmp_path):
        with pytest.raises(MergeError):
            inspect([])


@needs_ffmpeg
class TestInspectReal:
    def test_identical_clips_can_be_copied(self, tmp_path):
        clips = [make_clip(tmp_path / f"{i}.mp4") for i in range(2)]
        _, can_copy, note = inspect(clips)
        assert can_copy
        assert "nối thẳng" in note

    def test_different_sizes_need_encoding(self, tmp_path):
        clips = [
            make_clip(tmp_path / "a.mp4", size="160x120"),
            make_clip(tmp_path / "b.mp4", size="320x240"),
        ]
        _, can_copy, note = inspect(clips)
        assert not can_copy
        assert "độ phân giải" in note

    def test_missing_audio_is_reported_clearly(self, tmp_path):
        clips = [
            make_clip(tmp_path / "a.mp4", size="160x120"),
            make_clip(tmp_path / "b.mp4", size="320x240", with_audio=False),
        ]
        with pytest.raises(MergeError) as err:
            inspect(clips)
        assert "không có tiếng" in str(err.value)

    def test_probe_reads_size_and_duration(self, tmp_path):
        clip = make_clip(tmp_path / "a.mp4", seconds=2.0, size="320x240")
        found = merger.probe(clip)
        assert (found.width, found.height) == (320, 240)
        assert 1.5 < found.duration < 2.6


@needs_ffmpeg
class TestMergeReal:
    def test_stream_copy_keeps_total_duration(self, tmp_path):
        clips = [make_clip(tmp_path / f"{i}.mp4", seconds=1.0) for i in range(3)]
        out = tmp_path / "out" / "gop.mp4"
        result, summary = merge_quiet(clips, out)

        assert result.exists()
        assert "nối thẳng" in summary
        assert 2.5 < merger.probe(result).duration < 3.6

    def test_reencode_path_handles_mixed_sizes(self, tmp_path):
        clips = [
            make_clip(tmp_path / "a.mp4", seconds=1.0, size="160x120"),
            make_clip(tmp_path / "b.mp4", seconds=1.0, size="320x240"),
        ]
        result, summary = merge_quiet(clips, tmp_path / "gop.mp4")

        assert "mã hoá lại" in summary
        found = merger.probe(result)
        # Mọi clip được đưa về khung hình lớn nhất.
        assert (found.width, found.height) == (320, 240)

    def test_reports_progress(self, tmp_path):
        clips = [make_clip(tmp_path / f"{i}.mp4", seconds=1.0) for i in range(2)]
        seen: list[float] = []
        merger.merge(clips, tmp_path / "gop.mp4", on_progress=seen.append)
        assert seen, "không nhận được tiến trình nào"
        assert max(seen) <= 1.0

    def test_refuses_to_overwrite_an_input(self, tmp_path):
        clips = [make_clip(tmp_path / f"{i}.mp4") for i in range(2)]
        with pytest.raises(MergeError):
            merger.merge(clips, clips[0])

    def test_leaves_no_broken_file_when_cancelled(self, tmp_path):
        clips = [make_clip(tmp_path / f"{i}.mp4", seconds=2.0) for i in range(3)]
        out = tmp_path / "gop.mp4"
        with pytest.raises(merger.MergeCancelled):
            merger.merge(clips, out, should_cancel=lambda: True)
        assert not out.exists()


def merge_quiet(clips, out):
    return merger.merge(clips, out)
