"""Chạy bằng dòng lệnh, dùng chung nhân xử lý với giao diện."""

from __future__ import annotations

import argparse
import sys
import threading

from . import pipeline
from .config import CHINESE_VARIANTS, COMPUTE_TYPES, MODELS, Settings

_PREFIX = {"info": "  ", "warn": "!  ", "error": "x  ", "success": "OK "}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="videoocr",
        description="Quét thư mục video, nhận dạng giọng nói và xuất phụ đề SRT.",
    )
    parser.add_argument("folder", nargs="?", help="Thư mục chứa video")
    parser.add_argument("--gui", action="store_true", help="Mở giao diện cửa sổ")
    parser.add_argument("--model", choices=MODELS, help="Model Whisper (mặc định large-v3)")
    parser.add_argument("--language", "-l", help="Mã ngôn ngữ, vd: zh, en. Bỏ qua để tự phát hiện")
    parser.add_argument("--auto-language", action="store_true", help="Tự phát hiện ngôn ngữ")
    parser.add_argument("--chinese", choices=[code for _, code in CHINESE_VARIANTS],
                        help="Kiểu chữ Trung: s giản thể, t phồn thể, none giữ nguyên")
    parser.add_argument("--compute-type", choices=COMPUTE_TYPES)
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--beam-size", type=int)
    parser.add_argument("--no-recursive", action="store_true", help="Không quét thư mục con")
    parser.add_argument("--overwrite", action="store_true", help="Ghi đè file .srt đã có")
    parser.add_argument("--no-vad", action="store_true", help="Tắt lọc khoảng lặng")
    parser.add_argument("--keep-junk", action="store_true", help="Không lọc câu rác")
    parser.add_argument("--prompt", help="Câu mồi cho model")
    parser.add_argument("--model-dir",
                        help="Thư mục lưu model. Bỏ trống là models\ cạnh app")
    group = parser.add_argument_group("Dịch tiếng Việt bằng Gemini")
    group.add_argument("--translate", action="store_true",
                       help="Dịch sang tiếng Việt sau khi nhận dạng, xuất ra phim.vi.srt")
    group.add_argument("--translate-only", action="store_true",
                       help="Chỉ dịch các file .srt đã có, không nhận dạng lại")
    group.add_argument("--gemini-key", action="append", metavar="KEY",
                       help="API key Gemini. Lặp lại tham số này để thêm nhiều key")
    group.add_argument("--gemini-model", help="Model Gemini (mặc định gemini-2.5-flash)")
    group.add_argument("--translate-batch", type=int, metavar="N",
                       help="Số dòng gửi mỗi lượt gọi API (mặc định 40)")

    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.load()

    if args.folder:
        settings.input_dir = args.folder
    if args.model:
        settings.model = args.model
    if args.auto_language:
        settings.language = None
    elif args.language:
        settings.language = args.language
    if args.chinese:
        settings.chinese_variant = args.chinese
    if args.compute_type:
        settings.compute_type = args.compute_type
    if args.device:
        settings.device = args.device
    if args.batch_size:
        settings.batch_size = args.batch_size
    if args.beam_size:
        settings.beam_size = args.beam_size
    if args.prompt is not None:
        settings.initial_prompt = args.prompt
    if args.model_dir is not None:
        settings.model_dir = args.model_dir
    if args.gemini_key:
        settings.gemini_keys = list(args.gemini_key)
    if args.gemini_model:
        settings.gemini_model = args.gemini_model
    if args.translate_batch:
        settings.translate_batch_size = args.translate_batch
    if args.translate or args.translate_only:
        settings.translate_enabled = True

    if args.no_recursive:
        settings.recursive = False
    if args.overwrite:
        settings.overwrite = True
    if args.no_vad:
        settings.vad_filter = False
    if args.keep_junk:
        settings.filter_hallucinations = False

    return settings


def force_utf8_console() -> None:
    """Console Windows mặc định là cp1252, in tiếng Việt vào đó sẽ văng lỗi."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _report(event: pipeline.Event) -> None:
    if event.kind == "file_progress":
        return  # Thanh tiến trình chỉ có ý nghĩa trong GUI.
    if not event.message:
        return
    print(f"{_PREFIX.get(event.level, '  ')}{event.message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    args = build_parser().parse_args(argv)

    if args.gui or not args.folder:
        from .gui import main as gui_main

        return gui_main()

    settings = settings_from_args(args)
    settings.save()

    action = pipeline.translate_existing if args.translate_only else pipeline.run
    try:
        summary = action(settings, emit=_report, cancel=threading.Event())
    except KeyboardInterrupt:
        print("\nĐã huỷ.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1

    for video, reason in summary.failed:
        print(f"x  {video.name}: {reason}", file=sys.stderr)

    return 1 if summary.failed else 0
