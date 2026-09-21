"""Vẽ widget bo góc mượt cho ttk.

tkinter không vẽ được góc bo, nên cách duy nhất là dựng sẵn ảnh rồi gắn vào ttk
theo kiểu 9 ô: bốn góc giữ nguyên, bốn cạnh và ruột được lát kín theo kích thước
widget. Ruột phải rộng (xem ``INTERIOR``) - lát ruột hẹp là treo cả cửa sổ.

Góc dùng **đường siêu ellipse** ``|x|^n + |y|^n = 1`` với n = 5, chứ không phải
cung tròn. Đây là chỗ khác nhau giữa "bo tròn" và "bo mượt" kiểu iOS: cung tròn
đổi hướng đột ngột ở hai đầu nên mắt nhìn thấy gãy, còn siêu ellipse chuyển dần
nên viền trôi liền một mạch.

PhotoImage của Tk không nhận alpha từng phần qua ``put``, nên màu nền được trộn
sẵn vào góc. App chỉ có vài màu nền cố định nên cách này đủ dùng và tránh được
một thư viện phụ thuộc. Riêng chấm trạng thái trong bảng dùng pixel trong suốt
hoàn toàn, vì nó phải nằm đẹp trên cả dòng trắng, dòng sọc lẫn dòng đang chọn.
"""

from __future__ import annotations

import math
import tkinter as tk

SQUIRCLE = 5.0      # số mũ của đường siêu ellipse
SAMPLES = 4         # lấy mẫu 4x4 mỗi pixel để viền không răng cưa

# Bề rộng phần ruột của ảnh 9 ô. ttk không kéo giãn phần ruột mà **lát** nó,
# nên ruột càng hẹp thì càng phải lát nhiều lần. Ruột 3 pixel làm một thẻ
# 950x320 mất gần 10 giây mỗi lần vẽ và cả cửa sổ treo "Not Responding"; ruột
# 64 pixel thì còn 0,008 giây. Đừng hạ số này xuống.
INTERIOR = 64

# Tk thu hồi ảnh ngay khi không còn ai tham chiếu, giữ lại ở đây cho chắc.
_KEEP: list[tk.PhotoImage] = []
_MASKS: dict[tuple[int, float], list[list[float]]] = {}


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _blend(under: tuple[int, int, int], over: tuple[int, int, int],
           amount: float) -> tuple[int, int, int]:
    return tuple(round(u + (o - u) * amount) for u, o in zip(under, over))  # type: ignore


def corner_mask(radius: int, exponent: float = SQUIRCLE) -> list[list[float]]:
    """Độ phủ 0..1 của từng pixel trong ô vuông góc trên trái.

    Ba góc còn lại chỉ là ảnh phản chiếu nên không cần tính lại.
    """
    if radius <= 0:
        return []
    key = (radius, exponent)
    if key in _MASKS:
        return _MASKS[key]

    step = 1.0 / SAMPLES
    offset = step / 2
    mask: list[list[float]] = []
    for y in range(radius):
        row = []
        for x in range(radius):
            inside = 0
            for sy in range(SAMPLES):
                v = (radius - (y + offset + sy * step)) / radius
                for sx in range(SAMPLES):
                    u = (radius - (x + offset + sx * step)) / radius
                    if u ** exponent + v ** exponent <= 1.0:
                        inside += 1
            row.append(inside / (SAMPLES * SAMPLES))
        mask.append(row)

    _MASKS[key] = mask
    return mask


def _corner_block(radius: int, fill: str, background: str,
                  border: str | None, border_width: int) -> list[list[str]]:
    """Màu từng pixel của góc trên trái, cạnh ``radius``."""
    bg_rgb, fill_rgb = _hex(background), _hex(fill)
    outer = corner_mask(radius)

    if not border or border_width <= 0:
        return [["#%02x%02x%02x" % _blend(bg_rgb, fill_rgb, outer[y][x])
                 for x in range(radius)] for y in range(radius)]

    # Hình bên trong lùi vào ``border_width`` pixel, bán kính nhỏ đi tương ứng.
    border_rgb = _hex(border)
    inner_radius = max(radius - border_width, 0)
    inner = corner_mask(inner_radius)

    block = []
    for y in range(radius):
        row = []
        for x in range(radius):
            colour = _blend(bg_rgb, border_rgb, outer[y][x])
            iy, ix = y - border_width, x - border_width
            if iy >= 0 and ix >= 0:
                coverage = inner[iy][ix] if iy < inner_radius and ix < inner_radius else 1.0
                colour = _blend(colour, fill_rgb, coverage)
            row.append("#%02x%02x%02x" % colour)
        block.append(row)
    return block


def make(radius: int, fill: str, background: str,
         border: str | None = None, border_width: int = 1,
         interior: int = INTERIOR, height: int | None = None) -> tk.PhotoImage:
    """Ảnh 9 ô cho một khối bo góc, rộng ``2 * radius + interior``.

    Mặc định ảnh vuông; ``height`` cho ảnh dẹt như thanh tiến trình.

    Chỉ bốn góc cần tính từng pixel; phần còn lại tô cả mảng bằng lệnh gốc của
    Tk nên ảnh to cũng dựng gần như tức thì.
    """
    width = 2 * radius + interior
    height = width if height is None else height
    image = tk.PhotoImage(width=width, height=height)
    image.put(fill, to=(0, 0, width, height))

    if border and border_width > 0:
        edge = border_width
        image.put(border, to=(0, 0, width, edge))                   # trên
        image.put(border, to=(0, height - edge, width, height))     # dưới
        image.put(border, to=(0, 0, edge, height))                  # trái
        image.put(border, to=(width - edge, 0, width, height))      # phải

    if radius > 0:
        block = _corner_block(radius, fill, background, border, border_width)
        right, bottom = width - radius, height - radius
        image.put(block, to=(0, 0))                                              # trên trái
        image.put([row[::-1] for row in block], to=(right, 0))                   # trên phải
        image.put(block[::-1], to=(0, bottom))                                   # dưới trái
        image.put([row[::-1] for row in block[::-1]], to=(right, bottom))        # dưới phải

    _KEEP.append(image)
    return image


def _min(radius: int) -> int:
    """Kích thước tối thiểu mà element đòi: vừa đủ chứa hai góc."""
    return 2 * radius + 2


CHECK_GAP = 7    # khoảng trống giữa ô tích và chữ


def _with_gap(box: tk.PhotoImage, size: int, background: str) -> tk.PhotoImage:
    """Nới ảnh ô tích sang phải một khoảng, để chữ không dính sát vào ô."""
    wide = tk.PhotoImage(width=size + CHECK_GAP, height=size)
    wide.put(background, to=(0, 0, size + CHECK_GAP, size))
    wide.tk.call(str(wide), "copy", str(box), "-to", 0, 0)
    _KEEP.append(wide)
    return wide


def _draw_tick(image: tk.PhotoImage, size: int, colour: str = "#FFFFFF") -> None:
    """Vẽ dấu tích trắng lên ô tích đang bật, nét dày 2 pixel."""
    s = size / 15.0
    points = [(3.5, 7.5), (6.0, 10.0), (11.5, 4.5)]
    points = [(x * s, y * s) for x, y in points]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 3) + 1
        for i in range(steps + 1):
            t = i / steps
            x = round(x0 + (x1 - x0) * t)
            y = round(y0 + (y1 - y0) * t)
            for dx, dy in ((0, 0), (0, 1), (1, 0)):
                if 0 <= x + dx < size and 0 <= y + dy < size:
                    image.put(colour, to=(x + dx, y + dy))


def button_style(style, name: str, radius: int, background: str,
                 states: dict[str, tuple[str, str | None]],
                 padding: tuple[int, int]) -> None:
    """Dựng một kiểu nút bo góc.

    ``states`` ánh xạ trạng thái ttk sang (màu nền, màu viền); khoá ``""`` là
    trạng thái thường.
    """
    images = {
        state: make(radius, fill, background, border)
        for state, (fill, border) in states.items()
    }
    element = f"{name}.body"
    specs = [images[""]]
    for state in ("pressed", "active", "disabled"):
        if state in images:
            specs.append((state, images[state]))

    style.element_create(element, "image", *specs,
                         border=radius + 1, sticky="nsew", padding=padding,
                         width=_min(radius), height=_min(radius))
    style.layout(name, [(element, {"sticky": "nsew", "children": [
        ("Button.label", {"sticky": "nsew"})]})])


def field_style(style, name: str, radius: int, background: str,
                fill: str, line: str, focus: str,
                padding: tuple[int, int], arrow: bool = False) -> None:
    """Dựng ô nhập bo góc. ``arrow=True`` cho combobox."""
    normal = make(radius, fill, background, line)
    focused = make(radius, fill, background, focus)
    element = f"{name}.field"
    style.element_create(element, "image", normal, ("focus", focused),
                         border=radius + 1, sticky="nsew", padding=padding,
                         width=_min(radius), height=_min(radius))

    children = [("Combobox.downarrow", {"side": "right", "sticky": "ns"})] if arrow else []
    children.append(("Entry.padding" if not arrow else "Combobox.padding",
                     {"sticky": "nsew", "children": [
                         ("Entry.textarea" if not arrow else "Combobox.textarea",
                          {"sticky": "nsew"})]}))
    style.layout(name, [(element, {"sticky": "nsew", "children": children})])


def bar_style(style, name: str, thickness: int, background: str,
              trough: str, fill: str) -> None:
    """Thanh tiến trình bo tròn hẳn hai đầu, kiểu viên thuốc."""
    # Bán kính bằng nửa bề dày thì hai đầu tròn hẳn. Ảnh dẹt đúng bằng bề dày,
    # chỉ dài ra theo chiều ngang - chiều duy nhất phải lát.
    radius = max(thickness // 2, 1)
    trough_img = make(radius, trough, background, height=thickness)
    fill_img = make(radius, fill, background, height=thickness)

    # Chỉ giữ hai đầu trái phải. Ảnh cao đúng bằng bề dày, nếu đặt thêm viền trên
    # dưới thì ruột theo chiều dọc âm và Tk quay vòng treo cả cửa sổ.
    ends = (radius, 0, radius, 0)
    style.element_create(f"{name}.trough", "image", trough_img,
                         border=ends, sticky="nsew",
                         width=_min(radius), height=thickness)
    style.element_create(f"{name}.pbar", "image", fill_img,
                         border=ends, sticky="nsew",
                         width=_min(radius), height=thickness)
    style.layout(name, [(f"{name}.trough", {"sticky": "nsew", "children": [
        (f"{name}.pbar", {"side": "left", "sticky": "ns"})]})])
    style.configure(name, thickness=thickness, borderwidth=0)


def card_style(style, name: str, radius: int, background: str,
               fill: str, line: str | None = None) -> None:
    """Khung nền bo góc, dùng làm thẻ bọc bảng và nhật ký."""
    image = make(radius, fill, background, line)
    element = f"{name}.body"
    style.element_create(element, "image", image, border=radius + 1, sticky="nsew",
                         width=_min(radius), height=_min(radius))
    style.layout(name, [(element, {"sticky": "nsew"})])


def check_style(style, name: str, size: int, background: str,
                off_fill: str, off_line: str, on_fill: str) -> None:
    """Ô tích bo góc kiểu iOS: vuông bo mượt, tích trắng trên nền xanh."""
    radius = max(size // 3, 3)
    exact = size - 2 * radius
    off = _with_gap(make(radius, off_fill, background, off_line, interior=exact),
                    size, background)
    on_box = make(radius, on_fill, background, on_fill, interior=exact)
    _draw_tick(on_box, size)
    on = _with_gap(on_box, size, background)

    element = f"{name}.indicator"
    style.element_create(element, "image", off, ("selected", on),
                         sticky="", width=size + CHECK_GAP, height=size)
    style.layout(name, [("Checkbutton.padding", {"sticky": "nsew", "children": [
        (element, {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nsew"})]})]})])


# --- vòng tiến độ ---------------------------------------------------------

_RING_GEOMETRY: dict[tuple[int, int], list] = {}
_RING_CACHE: dict[tuple, tk.PhotoImage] = {}


def forget_images() -> None:
    """Bỏ hết ảnh đã dựng. Ảnh gắn với trình thông dịch Tk đã tạo ra nó, nên
    dựng một cửa sổ Tk mới mà dùng lại ảnh cũ là Tk báo ảnh không tồn tại.
    Phần hình học thuần số thì vẫn giữ để khỏi tính lại."""
    _RING_CACHE.clear()
    _KEEP.clear()
RING_SAMPLES = 3


def _ring_geometry(size: int, thickness: int) -> list:
    """Các điểm lấy mẫu nằm trong vành khuyên, kèm góc của từng điểm.

    Hình học không đổi theo phần trăm nên chỉ tính một lần; mỗi lần vẽ chỉ còn
    so góc với phần đã chạy.
    """
    key = (size, thickness)
    if key in _RING_GEOMETRY:
        return _RING_GEOMETRY[key]

    centre = size / 2
    outer = size / 2 - 1
    inner = outer - thickness
    step = 1.0 / RING_SAMPLES
    offset = step / 2
    full = RING_SAMPLES * RING_SAMPLES

    pixels = []
    for y in range(size):
        for x in range(size):
            radius = math.hypot(x + 0.5 - centre, y + 0.5 - centre)
            if radius < inner - 1 or radius > outer + 1:
                continue
            samples = []
            for sy in range(RING_SAMPLES):
                py = y + offset + sy * step - centre
                for sx in range(RING_SAMPLES):
                    px = x + offset + sx * step - centre
                    if inner <= math.hypot(px, py) <= outer:
                        # Góc tính từ đỉnh vòng, theo chiều kim đồng hồ.
                        samples.append((math.atan2(px, -py) % math.tau, px, py))
            if samples:
                pixels.append((x, y, samples))

    _RING_GEOMETRY[key] = (pixels, full, (inner + outer) / 2)
    return _RING_GEOMETRY[key]


def ring_image(size: int, thickness: int, fraction: float,
               track: str, fill: str, background: str) -> tk.PhotoImage:
    """Vòng tiến độ khử răng cưa, hai đầu bo tròn như vòng hoạt động của iOS.

    Ảnh được nhớ theo từng phần trăm nguyên, nên cả một lượt chạy dài cũng chỉ
    vẽ tối đa 101 lần.
    """
    fraction = max(0.0, min(1.0, fraction))
    percent = round(fraction * 100)
    key = (size, thickness, percent, track, fill, background)
    if key in _RING_CACHE:
        return _RING_CACHE[key]

    pixels, full, mid = _ring_geometry(size, thickness)
    sweep = percent / 100 * math.tau
    cap = thickness / 2
    # Hai đầu bo tròn: tâm nằm giữa vành, ở góc bắt đầu và góc kết thúc.
    caps = []
    if 0 < percent < 100:
        caps = [(0.0, -mid), (mid * math.sin(sweep), -mid * math.cos(sweep))]

    bg, tr, fl = _hex(background), _hex(track), _hex(fill)
    image = tk.PhotoImage(width=size, height=size)
    image.put(background, to=(0, 0, size, size))

    for x, y, samples in pixels:
        done = 0
        for angle, px, py in samples:
            if percent and (angle <= sweep or any(
                    (px - cx) ** 2 + (py - cy) ** 2 <= cap * cap for cx, cy in caps)):
                done += 1
        todo = len(samples) - done
        colour = tuple(
            round(b + (t - b) * todo / full + (f - b) * done / full)
            for b, t, f in zip(bg, tr, fl)
        )
        image.put("#%02x%02x%02x" % colour, to=(x, y))

    _RING_CACHE[key] = image
    return image


# --- chấm trạng thái ------------------------------------------------------

def dot_image(diameter: int, colour: str, background: str = "#FFFFFF",
              box: int | None = None) -> tk.PhotoImage:
    """Chấm tròn trạng thái trên nền trong suốt, đặt giữa một ô ``box`` pixel.

    Nền trong suốt để chấm nằm đẹp trên cả dòng trắng, dòng sọc và dòng đang
    chọn. Viền mép trộn với ``background`` cho mượt.
    """
    box = box or diameter
    mask = corner_mask(diameter // 2, exponent=2.0)
    radius = diameter // 2
    fill, bg = _hex(colour), _hex(background)

    image = tk.PhotoImage(width=box, height=box)
    start = (box - 2 * radius) // 2
    for y in range(box):
        for x in range(box):
            image.transparency_set(x, y, True)

    for y in range(2 * radius):
        for x in range(2 * radius):
            my = y if y < radius else 2 * radius - 1 - y
            mx = x if x < radius else 2 * radius - 1 - x
            coverage = mask[my][mx]
            if coverage < 0.35:
                continue
            tone = _blend(bg, fill, min(1.0, coverage * 1.3))
            image.put("#%02x%02x%02x" % tone, to=(start + x, start + y))

    _KEEP.append(image)
    return image
