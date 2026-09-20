"""Dò GPU và nạp DLL của CUDA cài qua pip.

Trên Windows, CTranslate2 nạp cuBLAS/cuDNN theo đường dẫn DLL của tiến trình.
Các gói nvidia-*-cu12 cài qua pip lại đặt DLL trong site-packages, không nằm
trong PATH - nên phải tự khai báo trước khi import ctranslate2.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Driver tối thiểu cho CUDA 12 trên Windows.
MIN_DRIVER_VERSION = 525.60


def register_cuda_dll_directories() -> list[str]:
    """Khai báo thư mục DLL của các gói nvidia-*-cu12. Trả về danh sách đã thêm."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return []

    added: list[str] = []
    for site_dir in sys.path:
        nvidia_root = Path(site_dir) / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for lib_dir in nvidia_root.glob("*/bin"):
            try:
                os.add_dll_directory(str(lib_dir))
                added.append(str(lib_dir))
            except OSError:
                continue
    return added


def driver_version() -> float | None:
    """Phiên bản driver NVIDIA qua nvidia-smi, None nếu không dò được."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip().splitlines()[0].strip())
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def gpu_name() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip().splitlines()[0].strip()
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


def cuda_available() -> bool:
    return driver_version() is not None


def check_driver() -> str | None:
    """Trả về lời cảnh báo nếu driver quá cũ cho CUDA 12, ngược lại None."""
    version = driver_version()
    if version is None:
        return None
    if version < MIN_DRIVER_VERSION:
        return (
            f"Driver NVIDIA {version} quá cũ cho CUDA 12 "
            f"(cần từ {MIN_DRIVER_VERSION}). Hãy cập nhật driver, "
            f"nếu không app sẽ phải chạy bằng CPU và chậm hơn nhiều."
        )
    return None


def resolve_device(preference: str = "auto") -> str:
    """'auto' -> dùng cuda nếu có, không thì cpu."""
    if preference == "auto":
        return "cuda" if cuda_available() else "cpu"
    return preference


def resolve_compute_type(device: str, preference: str) -> str:
    """CPU không chạy được float16, tự đổi sang int8 cho nhanh."""
    if device == "cpu" and preference in {"float16", "int8_float16"}:
        return "int8"
    return preference
