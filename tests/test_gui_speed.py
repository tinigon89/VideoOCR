"""Canh chừng lỗi treo "Not Responding" khi mở app.

Lỗi này đã xảy ra hai lần, cả hai đều do ảnh bo góc trong ``rounded.py``:

* Ruột ảnh 9 ô quá hẹp - ttk lát ruột chứ không kéo giãn, nên vẽ một thẻ lớn
  mất gần 10 giây và cả cửa sổ mất 42 giây.
* Ảnh dẹt của thanh tiến trình có viền trên dưới dày hơn chính nó - ruột âm
  làm Tk quay vòng.

Cả hai lần app vẫn chạy đúng về chức năng và mọi test khác vẫn đạt, chỉ có vẽ
là treo. Nên phải có test đo thời gian vẽ thật.
"""

import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root(tmp_path, monkeypatch):
    # Không đụng vào cấu hình thật của người dùng.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("không có màn hình để dựng cửa sổ")
    window.geometry("980x800")
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


def test_first_draw_is_fast(root):
    from videoocr.gui import App

    App(root)
    started = time.perf_counter()
    for _ in range(5):
        root.update()
    elapsed = time.perf_counter() - started

    # Windows báo "Not Responding" sau khoảng 5 giây. Máy chậm cũng phải dư sức.
    assert elapsed < 2.0, f"vẽ lần đầu mất {elapsed:.1f} giây - ảnh bo góc lại lát chậm?"


def test_resizing_stays_fast(root):
    from videoocr.gui import App

    App(root)
    root.update()
    started = time.perf_counter()
    for height in (650, 900, 700, 850):
        root.geometry(f"980x{height}")
        root.update()
    assert time.perf_counter() - started < 2.0


def test_every_image_has_room_to_tile():
    """Ruột ảnh phải đủ rộng, nếu không ttk lát hàng trăm nghìn lần mỗi lần vẽ."""
    from videoocr import rounded

    assert rounded.INTERIOR >= 32
