"""Bảng màu, font và kiểu widget cho giao diện.

Lấy hệ màu và nhịp bố cục của iOS: nền xám nhạt, nội dung nằm trong các thẻ
trắng bo góc mượt, chữ phân ba cấp đậm nhạt, một màu xanh duy nhất cho hành
động. Góc bo bằng đường siêu ellipse (xem ``rounded.py``) chứ không phải cung
tròn - đó là khác biệt giữa bo tròn thường và bo mượt kiểu iOS.

Hai quyết định đáng nói:

* Dùng theme ``clam`` làm nền thay vì ``vista``. Theme gốc của Windows bỏ qua
  gần hết thuộc tính màu mà ttk khai báo, và không cho thay phần tử bằng ảnh.
* **Màu chỉ dành cho trạng thái và hành động.** Cả cửa sổ giữ xám trung tính,
  chỗ có màu là cột trạng thái và nút Bắt đầu. Liếc mắt từ xa là thấy ngay file
  nào xong, file nào hỏng - đúng cái người dùng cần khi để app chạy hàng giờ.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import rounded

# --- màu -----------------------------------------------------------------
# Mọi con số dưới đây đều do máy đo, không phải ước lượng bằng mắt: tương phản
# WCAG cho chữ, và công cụ kiểm bảng màu của skill dataviz cho màu trạng thái
# (mô phỏng mù màu đỏ-lục, khoảng cách màu OKLab).

INK = "#1C1C1E"        # chữ chính               17,0:1 trên thẻ trắng
SECONDARY = "#3C3C43"  # chữ phụ, nhãn ô nhập     9,8:1 trên nền xám
MUTED = SECONDARY      # tên cũ, giữ cho các chỗ đang dùng
FAINT = "#636366"      # chú thích                5,4:1 trên nền xám (cũ 2,9:1 - trượt)
LINE = "#D1D1D6"
HAIRLINE = "#E3E3E8"
SURFACE = "#F2F2F7"    # nền cửa sổ, systemGroupedBackground của iOS
PANEL = "#FFFFFF"      # thẻ nội dung
ZEBRA = "#F7F7FA"
SELECT = "#DCE8F8"

# Xanh iOS gốc #007AFF chỉ đạt 4,0:1 với chữ trắng - trượt chuẩn cho nút chữ
# đậm cỡ 10. Hạ một bậc về #0066D6 được 5,4:1 mà vẫn giữ đúng sắc xanh iOS.
ACCENT = "#0066D6"
ACCENT_DEEP = "#0052AD"
ACCENT_SOFT = "#EAF2FD"
# Rãnh của vòng tiến độ là một bậc nhạt của chính màu xanh (luật "meter" của
# dataviz), để phần đã chạy và phần còn lại đọc thành một khối liền.
ACCENT_TRACK = "#D1E3F8"

# Màu trạng thái: bộ cố định của dataviz. Cam và đỏ cũ chỉ cách nhau ΔE 1,2
# khi mô phỏng mù màu đỏ-lục - không phân biệt nổi. Bộ này đạt ΔE 11,3.
# Màu trạng thái luôn đi kèm nhãn chữ, không bao giờ đứng một mình; vàng cảnh
# báo dưới 3:1 trên nền sáng là cố ý và được bù bằng chính cái nhãn đó.
GOOD = "#0CA30C"
WARNING = "#FAB219"
CRITICAL = "#D03B3B"
NEUTRAL = "#C7C7CC"

# Chấm trạng thái trong bảng. Chữ trong bảng giữ màu mực, chỉ chấm mang màu.
STATUS_DOTS = {
    "todo": NEUTRAL,
    "wait": WARNING,
    "running": ACCENT,
    "have": GOOD,
    "error": CRITICAL,
}
# Nền nhạt cho dòng đang chạy và dòng lỗi, để mắt tìm thấy ngay.
ROW_TINTS = {
    "running": ACCENT_SOFT,
    "error": "#FCEEEE",
}
STATE_COLORS = STATUS_DOTS   # tên cũ

# Chữ trong nhật ký phải đủ tương phản để đọc, nên không dùng thẳng màu trạng
# thái (xanh lá 3,4:1, vàng 1,8:1) mà dùng bậc đậm của cùng sắc.
# Bậc đậm của cam cảnh báo, đủ tương phản để làm chữ (vàng trạng thái chỉ 1,8:1).
WARN_TEXT = "#8A5A00"

LOG_COLORS = {
    "info": INK,
    "warn": WARN_TEXT,
    "error": "#B42318",
    "success": "#006300",
}

# --- hình khối ------------------------------------------------------------
R_BUTTON = 9         # nút
R_FIELD = 9          # ô nhập
R_CARD = 13          # thẻ bọc bảng và nhật ký
BAR_THICKNESS = 8

# --- font -----------------------------------------------------------------
# Chữ đơn cách cho số liệu, mốc thời gian và nhật ký: đó là ngôn ngữ vốn có của
# nghề phụ đề, và cột số canh thẳng hàng thì dễ đọc hơn hẳn.
UI = "Segoe UI"
UI_SEMIBOLD = "Segoe UI Semibold"   # con số lớn: đậm vừa, không đậm hẳn
MONO = "Cascadia Mono"

SIZE_HINT = 9       # cũ 8 - quá nhỏ, là một nửa lý do chữ khó đọc
SIZE_BODY = 10
SIZE_TITLE = 11
SIZE_STATUS = 12
SIZE_LARGE_TITLE = 20   # tiêu đề lớn đầu cửa sổ, kiểu iOS
HERO_PX = 48            # con số chủ đạo trong vòng tiến độ, >= 48px theo dataviz


def pick_font(preferred: str, fallback: str) -> str:
    try:
        return preferred if preferred in set(tkfont.families()) else fallback
    except tk.TclError:  # pragma: no cover - chỉ xảy ra khi chưa có Tk
        return fallback


def apply(root: tk.Misc) -> dict[str, str]:
    """Dựng toàn bộ kiểu widget. Trả về tên font thật sự dùng được."""
    ui = pick_font(UI, "Arial")
    semibold = pick_font(UI_SEMIBOLD, ui)
    mono = pick_font(MONO, "Consolas")

    rounded.forget_images()
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=SURFACE)

    style.configure(".", background=SURFACE, foreground=INK,
                    font=(ui, SIZE_BODY), borderwidth=0, focuscolor=SURFACE)
    style.configure("TFrame", background=SURFACE)
    style.configure("TLabel", background=SURFACE, foreground=INK)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Hint.TLabel", foreground=FAINT, font=(ui, SIZE_HINT))
    style.configure("Warn.TLabel", foreground=WARN_TEXT, font=(ui, SIZE_HINT))
    style.configure("Status.TLabel", font=(ui, SIZE_STATUS))
    style.configure("Clock.TLabel", foreground=MUTED, font=(mono, SIZE_HINT))
    style.configure("Summary.TLabel", foreground=MUTED, font=(mono, SIZE_HINT))

    # Nhãn nằm trên thẻ trắng phải đổi nền theo, nếu không có mảng xám lạc lõng.
    style.configure("OnCard.TLabel", background=PANEL)
    style.configure("CardTitle.TLabel", background=PANEL, foreground=INK,
                    font=(ui, SIZE_TITLE, "bold"))
    style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("CardHint.TLabel", background=PANEL, foreground=FAINT,
                    font=(ui, SIZE_HINT))
    # Khung con nằm trong thẻ: chỉ là nền trắng. Dùng lại Card.TFrame cho khung
    # con thì mỗi khung lại vẽ thêm một thẻ có viền lồng vào nhau.
    style.configure("OnCard.TFrame", background=PANEL)

    # Tiêu đề lớn đầu cửa sổ, kiểu large title của iOS.
    style.configure("LargeTitle.TLabel", foreground=INK,
                    font=(ui, SIZE_LARGE_TITLE, "bold"))
    style.configure("Subtitle.TLabel", foreground=FAINT, font=(ui, SIZE_BODY))

    # Thẻ Tiến độ.
    style.configure("TileLabel.TLabel", background=PANEL, foreground=FAINT,
                    font=(ui, SIZE_HINT))
    style.configure("TileValue.TLabel", background=PANEL, foreground=INK,
                    font=(semibold, SIZE_STATUS))
    style.configure("FileName.TLabel", background=PANEL, foreground=INK,
                    font=(ui, SIZE_BODY))
    style.configure("Stage.TLabel", background=PANEL, foreground=SECONDARY,
                    font=(ui, SIZE_HINT))

    # Bảng trống: một lời mời hành động thay vì một khoảng trắng.
    style.configure("EmptyTitle.TLabel", background=PANEL, foreground=INK,
                    font=(ui, SIZE_TITLE, "bold"))
    style.configure("EmptyHint.TLabel", background=PANEL, foreground=FAINT,
                    font=(ui, SIZE_BODY))

    _buttons(style, ui)
    _fields(style)
    _checks(style)

    style.configure("TSeparator", background=HAIRLINE)
    style.configure("TPanedwindow", background=SURFACE)
    style.configure("Sash", sashthickness=10, gripcount=0)

    rounded.bar_style(style, "Pill.Horizontal.TProgressbar", BAR_THICKNESS,
                      SURFACE, LINE, ACCENT)
    # Thanh nằm trên thẻ trắng: rãnh là bậc nhạt của chính màu xanh.
    rounded.bar_style(style, "CardPill.Horizontal.TProgressbar", BAR_THICKNESS,
                      PANEL, ACCENT_TRACK, ACCENT)
    rounded.card_style(style, "Card.TFrame", R_CARD, SURFACE, PANEL, HAIRLINE)

    # clam vẽ viền đen quanh Treeview qua bordercolor, phải tô trùng màu thẻ.
    style.configure("Cue.Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, rowheight=30, borderwidth=0, relief="flat",
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL,
                    font=(ui, SIZE_BODY))
    style.map("Cue.Treeview",
              background=[("selected", SELECT)], foreground=[("selected", INK)])
    style.configure("Cue.Treeview.Heading", background=PANEL, foreground=MUTED,
                    relief="flat", borderwidth=0, padding=(10, 9),
                    font=(ui, SIZE_HINT, "bold"))
    style.map("Cue.Treeview.Heading", background=[("active", ZEBRA)])

    style.configure("Vertical.TScrollbar", background=LINE, troughcolor=PANEL,
                    bordercolor=PANEL, arrowcolor=MUTED, gripcount=0,
                    arrowsize=12, width=11)
    style.map("Vertical.TScrollbar", background=[("active", FAINT)])

    return {"ui": ui, "mono": mono, "semibold": semibold}


def _buttons(style: ttk.Style, ui: str) -> None:
    # Tất cả kiểu bo góc mang tên riêng. Đừng thay layout của kiểu gốc
    # (TButton, TEntry, TCombobox, TCheckbutton): ttk còn dùng chúng cho
    # bộ phận bên trong Treeview, Combobox và PanedWindow, thay là hỏng
    # dây chuyền và cả cửa sổ vẽ ra trống trơn.
    # Nút thường: thẻ trắng bo mượt, viền mảnh - như nút trong bảng cài đặt iOS.
    rounded.button_style(
        style, "Rounded.TButton", R_BUTTON, SURFACE,
        {
            "": (PANEL, LINE),
            "active": (ACCENT_SOFT, ACCENT),
            "pressed": (LINE, LINE),
            "disabled": (SURFACE, HAIRLINE),
        },
        padding=(13, 7),
    )
    style.configure("Rounded.TButton", foreground=INK, font=(ui, SIZE_BODY),
                    anchor="center")
    style.map("Rounded.TButton", foreground=[("disabled", FAINT)])

    # Nút hành động chính: chỗ duy nhất ngoài cột trạng thái được phép có màu.
    rounded.button_style(
        style, "Accent.TButton", R_BUTTON, SURFACE,
        {
            "": (ACCENT, ACCENT),
            "active": (ACCENT_DEEP, ACCENT_DEEP),
            "pressed": (ACCENT_DEEP, ACCENT_DEEP),
            "disabled": ("#B9D4F7", "#B9D4F7"),
        },
        padding=(24, 10),
    )
    style.configure("Accent.TButton", foreground="#FFFFFF", anchor="center",
                    font=(ui, SIZE_BODY, "bold"))
    style.map("Accent.TButton", foreground=[("disabled", "#FFFFFF")])

    # Cùng hai nút ấy nhưng nằm trên thẻ trắng - góc phải trộn với nền trắng.
    rounded.button_style(
        style, "CardAccent.TButton", R_BUTTON, PANEL,
        {
            "": (ACCENT, ACCENT),
            "active": (ACCENT_DEEP, ACCENT_DEEP),
            "pressed": (ACCENT_DEEP, ACCENT_DEEP),
            "disabled": ("#B9CFEA", "#B9CFEA"),
        },
        padding=(24, 11),
    )
    style.configure("CardAccent.TButton", foreground="#FFFFFF", anchor="center",
                    font=(ui, SIZE_TITLE, "bold"))
    style.map("CardAccent.TButton", foreground=[("disabled", "#FFFFFF")])

    rounded.button_style(
        style, "CardRounded.TButton", R_BUTTON, PANEL,
        {
            "": (PANEL, LINE),
            "active": (ACCENT_SOFT, ACCENT),
            "pressed": (LINE, LINE),
            "disabled": (PANEL, HAIRLINE),
        },
        padding=(13, 8),
    )
    style.configure("CardRounded.TButton", foreground=INK, anchor="center",
                    font=(ui, SIZE_BODY))
    style.map("CardRounded.TButton", foreground=[("disabled", FAINT)])

    # Nút nhỏ trên thẻ: chỉ là chữ xanh, không khung - kiểu nút phụ của iOS.
    rounded.button_style(
        style, "Quiet.TButton", R_BUTTON, PANEL,
        {
            "": (PANEL, None),
            "active": (ACCENT_SOFT, None),
            "pressed": (LINE, None),
            "disabled": (PANEL, None),
        },
        padding=(10, 5),
    )
    style.configure("Quiet.TButton", foreground=ACCENT, font=(ui, SIZE_HINT),
                    anchor="center")
    style.map("Quiet.TButton", foreground=[("disabled", FAINT)])


def _fields(style: ttk.Style) -> None:
    rounded.field_style(style, "Rounded.TEntry", R_FIELD, SURFACE, PANEL, LINE, ACCENT,
                        padding=(10, 7))
    style.configure("Rounded.TEntry", foreground=INK, insertcolor=INK,
                    selectbackground=SELECT, selectforeground=INK)

    rounded.field_style(style, "Rounded.TCombobox", R_FIELD, SURFACE, PANEL, LINE, ACCENT,
                        padding=(10, 6), arrow=True)
    style.configure("Rounded.TCombobox", foreground=INK, arrowcolor=MUTED,
                    selectbackground=PANEL, selectforeground=INK)
    style.map("Rounded.TCombobox", foreground=[("disabled", FAINT)])

    style.configure("TSpinbox", fieldbackground=PANEL, background=PANEL,
                    foreground=INK, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, arrowcolor=MUTED, padding=5)


def _checks(style: ttk.Style) -> None:
    rounded.check_style(style, "Rounded.TCheckbutton", 15, SURFACE, PANEL, LINE, ACCENT)
    style.configure("Rounded.TCheckbutton", background=SURFACE, foreground=INK,
                    padding=(0, 4, 8, 4))
    style.map("Rounded.TCheckbutton", background=[("active", SURFACE)])

    rounded.check_style(style, "OnCard.TCheckbutton", 15, PANEL, PANEL, LINE, ACCENT)
    style.configure("OnCard.TCheckbutton", background=PANEL, foreground=INK,
                    padding=(0, 4, 8, 4))
    style.map("OnCard.TCheckbutton", background=[("active", PANEL)])
