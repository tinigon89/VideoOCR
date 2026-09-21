"""Kiểm tra phần quét thư mục và quyết định bỏ qua file đã có phụ đề."""

import pytest

from videoocr.scanner import (
    find_videos,
    natural_key,
    format_size,
    has_subtitle,
    plan_jobs,
    scan_entries,
    srt_path_for,
)


@pytest.fixture
def tree(tmp_path):
    """Cây thư mục mẫu: 2 video ở gốc, 1 video trong thư mục con, 1 file lạ."""
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.MKV").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("không phải video", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.avi").write_bytes(b"x")
    return tmp_path


class TestSrtPathFor:
    def test_replaces_extension(self, tmp_path):
        assert srt_path_for(tmp_path / "phim.mp4").name == "phim.srt"

    def test_sits_next_to_the_video(self, tmp_path):
        video = tmp_path / "sub" / "phim.mkv"
        assert srt_path_for(video).parent == video.parent

    def test_handles_dots_in_name(self, tmp_path):
        assert srt_path_for(tmp_path / "phim.s01.e02.mp4").name == "phim.s01.e02.srt"


class TestNaturalKey:
    """Thứ tự này quyết định luôn thứ tự nối khi gộp video, sai là ra file lộn."""

    def test_numbers_compare_as_numbers(self):
        assert natural_key("Tập 2") < natural_key("Tập 10")

    def test_hundreds_come_last(self):
        names = ["Tập 1", "Tập 100", "Tập 2", "Tập 20", "Tập 9"]
        assert sorted(names, key=natural_key) == [
            "Tập 1", "Tập 2", "Tập 9", "Tập 20", "Tập 100"]

    def test_ignores_letter_case(self):
        assert natural_key("EP01") == natural_key("ep01")

    def test_leading_zeros_do_not_matter(self):
        assert natural_key("Tập 007") == natural_key("Tập 7")

    def test_plain_text_still_alphabetical(self):
        assert sorted(["cam", "anh", "bo"], key=natural_key) == ["anh", "bo", "cam"]

    def test_several_numbers_in_one_name(self):
        assert natural_key("S1E2") < natural_key("S1E10") < natural_key("S2E1")

    def test_name_starting_with_a_number(self):
        assert sorted(["10.mp4", "2.mp4"], key=natural_key) == ["2.mp4", "10.mp4"]

    def test_mixed_names_do_not_raise(self):
        # Chữ và số so với nhau mà lệch kiểu thì Python ném TypeError.
        sorted(["a", "1", "a1", "1a", "", "a1b2"], key=natural_key)


class TestFindVideos:
    def test_recursive_finds_all(self, tree):
        assert len(find_videos(tree, recursive=True)) == 3

    def test_non_recursive_skips_subfolders(self, tree):
        names = [p.name for p in find_videos(tree, recursive=False)]
        assert names == ["a.mp4", "b.MKV"]

    def test_extension_match_is_case_insensitive(self, tree):
        assert any(p.name == "b.MKV" for p in find_videos(tree))

    def test_ignores_non_video_files(self, tree):
        assert all(p.suffix.lower() != ".txt" for p in find_videos(tree))

    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            find_videos(tmp_path / "không-tồn-tại")

    def test_empty_folder_returns_empty_list(self, tmp_path):
        assert find_videos(tmp_path) == []

    def test_numbered_episodes_come_out_in_order(self, tmp_path):
        for number in [1, 2, 3, 9, 10, 11, 20, 100]:
            (tmp_path / f"ShortKit-Tập {number}.mp4").write_bytes(b"x")

        found = [p.name for p in find_videos(tmp_path)]
        assert found == [f"ShortKit-Tập {n}.mp4" for n in [1, 2, 3, 9, 10, 11, 20, 100]]


class TestPlanJobs:
    def test_all_pending_when_no_srt(self, tree):
        todo, skipped = plan_jobs(find_videos(tree))
        assert len(todo) == 3
        assert skipped == []

    def test_skips_video_with_existing_srt(self, tree):
        (tree / "a.srt").write_text("1\n", encoding="utf-8")
        todo, skipped = plan_jobs(find_videos(tree))
        assert [p.name for p in skipped] == ["a.mp4"]
        assert len(todo) == 2

    def test_overwrite_ignores_existing_srt(self, tree):
        (tree / "a.srt").write_text("1\n", encoding="utf-8")
        todo, skipped = plan_jobs(find_videos(tree), overwrite=True)
        assert len(todo) == 3
        assert skipped == []

    def test_empty_srt_counts_as_missing(self, tree):
        # Tàn dư của lần chạy hỏng thì nên làm lại, không nên bỏ qua.
        (tree / "a.srt").write_bytes(b"")
        todo, skipped = plan_jobs(find_videos(tree))
        assert skipped == []
        assert len(todo) == 3

    def test_job_points_at_matching_srt(self, tree):
        todo, _ = plan_jobs(find_videos(tree, recursive=False))
        assert todo[0].srt == tree / "a.srt"


class TestHasSubtitle:
    def test_missing_file(self, tmp_path):
        assert not has_subtitle(tmp_path / "a.srt")

    def test_empty_file_does_not_count(self, tmp_path):
        target = tmp_path / "a.srt"
        target.write_bytes(b"")
        assert not has_subtitle(target)

    def test_file_with_content(self, tmp_path):
        target = tmp_path / "a.srt"
        target.write_text("1\n", encoding="utf-8")
        assert has_subtitle(target)

    def test_directory_does_not_count(self, tmp_path):
        (tmp_path / "a.srt").mkdir()
        assert not has_subtitle(tmp_path / "a.srt")


class TestFormatSize:
    def test_bytes(self):
        assert format_size(512) == "512 B"

    def test_kilobytes(self):
        assert format_size(2048) == "2,00 KB"

    def test_megabytes(self):
        assert format_size(5 * 1024 * 1024) == "5,00 MB"

    def test_gigabytes(self):
        assert format_size(int(2.5 * 1024**3)) == "2,50 GB"

    def test_zero(self):
        assert format_size(0) == "0 B"

    def test_uses_comma_as_decimal_separator(self):
        assert "." not in format_size(int(1.5 * 1024**3))


class TestScanEntries:
    def test_returns_one_entry_per_video(self, tree):
        assert len(scan_entries(tree)) == 3

    def test_reports_file_size(self, tree):
        entry = next(e for e in scan_entries(tree) if e.video.name == "a.mp4")
        assert entry.size == 1  # fixture ghi đúng 1 byte

    def test_marks_missing_subtitle(self, tree):
        assert all(not e.has_srt for e in scan_entries(tree))

    def test_marks_existing_subtitle(self, tree):
        (tree / "a.srt").write_text("1\n", encoding="utf-8")
        entry = next(e for e in scan_entries(tree) if e.video.name == "a.mp4")
        assert entry.has_srt

    def test_relative_path_includes_subfolder(self, tree):
        entry = next(e for e in scan_entries(tree) if e.video.name == "c.avi")
        assert entry.relative.replace("\\", "/") == "sub/c.avi"

    def test_relative_path_is_bare_name_at_root(self, tree):
        entry = next(e for e in scan_entries(tree) if e.video.name == "a.mp4")
        assert entry.relative == "a.mp4"

    def test_non_recursive_skips_subfolder(self, tree):
        assert len(scan_entries(tree, recursive=False)) == 2

    def test_empty_folder(self, tmp_path):
        assert scan_entries(tmp_path) == []
