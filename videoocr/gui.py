"""Cửa sổ điều khiển.

Phần xử lý nặng chạy ở thread riêng và đẩy sự kiện qua queue; cửa sổ chỉ đọc
queue định kỳ. Nhờ vậy giao diện không bị đơ trong lúc nhận dạng.

Bố cục lấy bảng danh sách làm trung tâm - đó là thứ người dùng nhìn suốt cả
tiếng đồng hồ khi để app chạy. Hai khung thiết lập gập lại được, và khi gập thì
tiêu đề hiện luôn thiết lập đang dùng, để gập rồi vẫn biết mình sắp chạy bằng gì.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import pipeline, rounded, theme
from .config import (
    CHINESE_VARIANTS,
    COMPUTE_TYPES,
    DEFAULT_GEMINI_MODEL,
    ENGLISH_ONLY_MODELS,
    LANGUAGES,
    MODELS,
    PROMPT_PRESETS,
    Settings,
    default_model_dir,
    model_advice,
    resolve_model_dir,
)
from .merger import MergeCancelled, default_output, inspect, merge
from .scanner import Entry, format_size, has_subtitle, scan_entries
from .translator import SUGGESTED_MODELS, check_key, fetch_models

POLL_INTERVAL_MS = 100

# Hai cột trạng thái: bản nguyên ngữ .stt (trung gian) và .srt (bản cuối cùng).
STT_TODO = "Chưa có"
STT_HAVE = "Đã có"
STT_GONE = "Đã xoá"
STT_NONE = "—"      # chưa từng có, hoặc phụ đề do nơi khác làm
SRT_TODO = "Chưa có"
SRT_HAVE = "Đã có"

# Chiều cao tối thiểu dành cho vùng bảng + nhật ký, tính bằng pixel.
LIST_MIN_HEIGHT = 200


def mask_key(key: str) -> str:
    """AIzaSyDhE2...RKGI1R1s -> AIza••••••••1R1s"""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * min(len(key) - 8, 24) + key[-4:]


def _shorten(text: str, limit: int) -> str:
    """Cắt ở giữa để còn thấy cả đầu lẫn đuôi - thường là số tập."""
    if len(text) <= limit:
        return text
    keep = limit - 1
    return text[: keep // 2] + "…" + text[-(keep - keep // 2):]


class Section(ttk.Frame):
    """Khung gập được. Lúc gập, tiêu đề vẫn cho biết thiết lập bên trong."""

    def __init__(self, master, title: str, on_toggle=None) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.title = title
        self._expanded = True
        self._on_toggle = on_toggle

        header = ttk.Frame(self, padding=(2, 5))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(2, weight=1)

        self.arrow = ttk.Label(header, text="▾", foreground=theme.MUTED, width=2)
        self.arrow.grid(row=0, column=0)
        self.label = ttk.Label(header, text=title, font=(theme.UI, theme.SIZE_TITLE, "bold"))
        self.label.grid(row=0, column=1, sticky="w")
        self.summary = ttk.Label(header, text="", style="Summary.TLabel")
        self.summary.grid(row=0, column=2, sticky="w", padx=(14, 0))

        for widget in (header, self.arrow, self.label, self.summary):
            widget.bind("<Button-1>", lambda _e: self.toggle())
            widget.configure(cursor="hand2")

        ttk.Separator(self, orient="horizontal").grid(row=1, column=0, sticky="ew")

        self.body = ttk.Frame(self, padding=(24, 10, 4, 12))
        self.body.grid(row=2, column=0, sticky="ew")

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_summary(self, text: str) -> None:
        # Lúc mở thì phần thân đã nói hết rồi, nhắc lại chỉ thêm rối.
        self.summary.configure(text="" if self._expanded else text)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, value: bool, notify: bool = True) -> None:
        """``notify=False`` dùng khi app tự gập cho vừa màn hình - không ghi đè
        lựa chọn người dùng đã lưu."""
        if value == self._expanded:
            return
        self._expanded = value
        self.arrow.configure(text="▾" if value else "▸")
        if value:
            self.body.grid()
            self.summary.configure(text="")
        else:
            self.body.grid_remove()
        if notify and self._on_toggle is not None:
            self._on_toggle()


class Ring(tk.Canvas):
    """Vòng tiến độ: con số chủ đạo ở giữa, một dòng chú thích bên dưới."""

    def __init__(self, master, size: int, thickness: int, fonts: dict) -> None:
        super().__init__(master, width=size, height=size, background=theme.PANEL,
                         highlightthickness=0, borderwidth=0)
        self.size, self.thickness = size, thickness
        centre = size // 2
        self._image = self.create_image(centre, centre)
        # Cỡ âm là tính bằng pixel: dataviz yêu cầu con số chủ đạo >= 48px.
        self._hero = self.create_text(centre, centre - 8, text="", fill=theme.INK,
                                      font=(fonts["semibold"], -theme.HERO_PX))
        self._caption = self.create_text(centre, centre + 30, text="",
                                         fill=theme.FAINT,
                                         font=(fonts["ui"], theme.SIZE_HINT))
        self.show(0.0, "", "")

    def show(self, fraction: float, hero: str, caption: str) -> None:
        image = rounded.ring_image(self.size, self.thickness, fraction,
                                   theme.ACCENT_TRACK, theme.ACCENT, theme.PANEL)
        self.itemconfigure(self._image, image=image)
        self.itemconfigure(self._hero, text=hero)
        self.itemconfigure(self._caption, text=caption)


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        # Áp theme trước khi dựng bất kỳ widget nào. ttk gán kiểu cho widget lúc
        # nó được tạo, nên widget sinh ra trước khi đổi theme sẽ giữ kiểu cũ và
        # vẽ ra một khoảng trống.
        self.fonts = theme.apply(master)
        super().__init__(master, padding=(14, 12))
        self.master = master
        self.settings = Settings.load()

        self.events: queue.Queue[pipeline.Event] = queue.Queue()
        self.scan_results: queue.Queue[tuple[int, list[Entry] | Exception]] = queue.Queue()
        self.model_results: queue.Queue[tuple[list[str], str] | Exception] = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        # Mỗi lần quét mang một số thứ tự; kết quả của lần quét cũ bị bỏ qua.
        # Nếu không, đổi thư mục lúc lần quét trước chưa xong sẽ hiện nhầm danh sách.
        self.scan_generation = 0

        # Trạng thái bảng. Khoá là đường dẫn tuyệt đối dạng chuỗi.
        self.entries: list[Entry] = []
        self.by_path: dict[str, Entry] = {}
        self.order: list[str] = []      # thứ tự hiển thị, cũng là thứ tự nối video
        self.stt_state: dict[str, tuple[str, str]] = {}
        self.srt_state: dict[str, tuple[str, str]] = {}
        self.has_srt: dict[str, bool] = {}
        self._dragging: str | None = None

        self.run_started: float | None = None
        self.started_at = datetime.now()
        self.timer_job: str | None = None

        # Nguồn sự thật của danh sách key; ô nhập có thể đang hiện dạng che.
        self._keys: list[str] = []
        self.keys_hidden = True

        self._build()
        self._apply_settings()
        self.pack(fill="both", expand=True)
        self.after(POLL_INTERVAL_MS, self._drain)

        # Chỉ bắt đầu canh chỗ sau khi cửa sổ đã hiện hẳn. Lúc vừa dựng, Tk báo
        # kích thước tạm rất nhỏ trước khi áp geometry thật, nên nếu đo ngay thì
        # khung nào cũng bị gập oan dù màn hình dư chỗ.
        self._fit_job: str | None = None
        self._fit_armed = False
        self.after(900, self._arm_fit)
        self.master.bind("<Configure>", self._on_window_resize)

        if self.var_dir.get():
            self._start_scan()

    # ----- dựng giao diện -------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        # minsize để bảng danh sách không bao giờ bị các khung trên ép về 0.
        self.rowconfigure(4, weight=1, minsize=LIST_MIN_HEIGHT)

        self._build_header()
        self._build_folder()
        self._build_options()
        self._build_translate()
        self._build_middle()

    def _build_header(self) -> None:
        """Tiêu đề lớn kiểu iOS, dòng phụ nói đang làm việc với thư mục nào."""
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(frame, text="VideoOCR", style="LargeTitle.TLabel").grid(
            row=0, column=0, sticky="w")
        self.var_subtitle = tk.StringVar(
            value="Nhận dạng lời thoại trong video, dịch sang tiếng Việt")
        ttk.Label(frame, textvariable=self.var_subtitle, style="Subtitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(1, 0))

    def _build_folder(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Thư mục", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))

        self.var_dir = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_dir,
                  font=(self.fonts["mono"], theme.SIZE_BODY), style="Rounded.TEntry").grid(
            row=0, column=1, sticky="ew")
        ttk.Button(frame, text="Chọn", command=self._pick_dir, style="Rounded.TButton").grid(
            row=0, column=2, padx=(8, 0))
        ttk.Button(frame, text="Quét lại", command=self._start_scan, style="Rounded.TButton").grid(
            row=0, column=3, padx=(6, 0))

        row = ttk.Frame(frame)
        row.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        row.columnconfigure(2, weight=1)

        self.var_recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Cả thư mục con", variable=self.var_recursive,
                        command=self._start_scan, style="Rounded.TCheckbutton").grid(row=0, column=0, sticky="w")
        self.var_overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Làm lại file đã có phụ đề",
                        variable=self.var_overwrite, style="Rounded.TCheckbutton").grid(
            row=0, column=1, sticky="w", padx=(18, 0))

        self.btn_merge = ttk.Button(row, text="Gộp video…", command=self._merge_videos, style="Rounded.TButton")
        self.btn_merge.grid(row=0, column=3, sticky="e")

    def _build_options(self) -> None:
        self.panel_options = Section(self, "Nhận dạng", on_toggle=self._on_section_toggle)
        self.panel_options.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        frame = self.panel_options.body
        for col in (1, 3):
            frame.columnconfigure(col, weight=1)

        self.var_language = tk.StringVar()
        self._combo(frame, 0, 0, "Ngôn ngữ", self.var_language,
                    [label for label, _ in LANGUAGES], self._on_language_change)
        self.var_model = tk.StringVar()
        self._combo(frame, 0, 2, "Model", self.var_model, MODELS, self._on_model_change)

        self.var_variant = tk.StringVar()
        self._combo(frame, 1, 0, "Chữ Trung", self.var_variant,
                    [label for label, _ in CHINESE_VARIANTS])
        self.var_compute = tk.StringVar()
        self._combo(frame, 1, 2, "Compute type", self.var_compute, COMPUTE_TYPES)

        self.lbl_advice = ttk.Label(frame, text="", style="Hint.TLabel", justify="left")
        self.lbl_advice.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 10))
        self._wrap(self.lbl_advice)

        tools = ttk.Frame(frame)
        tools.grid(row=3, column=0, columnspan=4, sticky="ew")
        ttk.Label(tools, text="Batch size", style="Muted.TLabel").grid(row=0, column=0)
        self.var_batch = tk.IntVar(value=8)
        ttk.Spinbox(tools, from_=1, to=32, textvariable=self.var_batch, width=5).grid(
            row=0, column=1, padx=(8, 22))
        self.var_vad = tk.BooleanVar(value=True)
        ttk.Checkbutton(tools, text="Lọc khoảng lặng", variable=self.var_vad, style="Rounded.TCheckbutton").grid(
            row=0, column=2, padx=(0, 18))
        self.var_filter = tk.BooleanVar(value=True)
        ttk.Checkbutton(tools, text="Lọc câu rác", variable=self.var_filter, style="Rounded.TCheckbutton").grid(
            row=0, column=3)

        ttk.Label(frame, text="Thư mục model", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(12, 0))
        row = ttk.Frame(frame)
        row.grid(row=4, column=1, columnspan=3, sticky="ew", pady=(12, 0))
        row.columnconfigure(0, weight=1)
        self.var_model_dir = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_model_dir,
                  font=(self.fonts["mono"], theme.SIZE_HINT), style="Rounded.TEntry").grid(
            row=0, column=0, sticky="ew")
        ttk.Button(row, text="Chọn", command=self._pick_model_dir, style="Rounded.TButton").grid(
            row=0, column=1, padx=(8, 0))
        ttk.Button(row, text="Mặc định", command=self._reset_model_dir, style="Rounded.TButton").grid(
            row=0, column=2, padx=(6, 0))

        hint = ttk.Label(frame, style="Hint.TLabel", justify="left", text=(
            "Để trống là dùng models\\ cạnh app. Trỏ vào cache HuggingFace nếu "
            "muốn dùng lại model đã tải cho phần mềm khác."))
        hint.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        self._wrap(hint)

    def _build_translate(self) -> None:
        self.panel_translate = Section(self, "Dịch tiếng Việt",
                                       on_toggle=self._on_section_toggle)
        self.panel_translate.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        frame = self.panel_translate.body
        frame.columnconfigure(1, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=3, sticky="ew")
        top.columnconfigure(1, weight=1)
        self.var_translate = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Dịch sau khi nhận dạng xong",
                        variable=self.var_translate, style="Rounded.TCheckbutton").grid(row=0, column=0, sticky="w")
        self.var_keep_stt = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Giữ lại bản nguyên ngữ .stt",
                        variable=self.var_keep_stt, style="Rounded.TCheckbutton").grid(row=0, column=2, sticky="e")

        ttk.Label(frame, text="API key", style="Muted.TLabel").grid(
            row=1, column=0, sticky="nw", pady=(12, 0))
        keys = ttk.Frame(frame)
        keys.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(12, 0))
        keys.columnconfigure(0, weight=1)
        self.keys_box = self._text(keys, height=3, mono=True)
        self.keys_box.grid(row=0, column=0, sticky="ew")
        self._scrollbar(keys, self.keys_box).grid(row=0, column=1, sticky="ns")
        self.btn_eye = ttk.Button(keys, text="Hiện key", width=10,
                                  command=self._toggle_key_visibility, style="Rounded.TButton")
        self.btn_eye.grid(row=0, column=2, sticky="n", padx=(8, 0))

        hint = ttk.Label(frame, style="Hint.TLabel", justify="left", text=(
            "Mỗi dòng một key. Hết hạn mức thì tự chuyển key kế tiếp. Google tính "
            "hạn mức theo project, nên nhiều key cùng project vẫn chung quota."))
        hint.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(5, 0))
        self._wrap(hint)

        ttk.Label(frame, text="Xưng hô", style="Muted.TLabel").grid(
            row=3, column=0, sticky="nw", pady=(12, 0))
        prompt = ttk.Frame(frame)
        prompt.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(12, 0))
        prompt.columnconfigure(0, weight=1)
        self.prompt_box = self._text(prompt, height=3)
        self.prompt_box.grid(row=0, column=0, sticky="ew")
        self._scrollbar(prompt, self.prompt_box).grid(row=0, column=1, sticky="ns")

        self.var_preset = tk.StringVar(value=PROMPT_PRESETS[0][0])
        preset = ttk.Combobox(prompt, textvariable=self.var_preset, width=22,
                              state="readonly",
                              values=[label for label, _ in PROMPT_PRESETS], style="Rounded.TCombobox")
        preset.grid(row=0, column=2, sticky="n", padx=(8, 0))
        preset.bind("<<ComboboxSelected>>", self._apply_preset)

        hint2 = ttk.Label(frame, style="Hint.TLabel", justify="left", text=(
            "Tiếng Trung không phân biệt vai vế, máy dịch phải tự đoán xưng hô. "
            "Dặn trước một câu là khác hẳn. Chọn mẫu bên phải rồi sửa lại."))
        hint2.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(5, 0))
        self._wrap(hint2)

        bottom = ttk.Frame(frame)
        bottom.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        bottom.columnconfigure(2, weight=1)
        ttk.Label(bottom, text="Model Gemini", style="Muted.TLabel").grid(row=0, column=0)
        self.var_gemini_model = tk.StringVar(value=DEFAULT_GEMINI_MODEL)
        self.combo_gemini = ttk.Combobox(bottom, textvariable=self.var_gemini_model,
                                         values=SUGGESTED_MODELS, width=26, style="Rounded.TCombobox")
        self.combo_gemini.grid(row=0, column=1, padx=(8, 8))
        self.btn_models = ttk.Button(bottom, text="Lấy danh sách",
                                     command=self._refresh_models, style="Rounded.TButton")
        self.btn_models.grid(row=0, column=2, sticky="w")
        self.btn_check_keys = ttk.Button(bottom, text="Kiểm tra key",
                                         command=self._check_keys, style="Rounded.TButton")
        self.btn_check_keys.grid(row=0, column=3, padx=(0, 6))
        self.btn_translate = ttk.Button(bottom, text="Dịch bản .stt đã có",
                                        command=self._translate_existing, style="Rounded.TButton")
        self.btn_translate.grid(row=0, column=4)

        size = ttk.Frame(frame)
        size.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Label(size, text="Mỗi lần gửi", style="Muted.TLabel").grid(row=0, column=0)
        self.var_translate_batch = tk.IntVar(value=200)
        ttk.Spinbox(size, from_=20, to=500, increment=20, width=6,
                    textvariable=self.var_translate_batch).grid(row=0, column=1, padx=(8, 6))
        ttk.Label(size, text="dòng", style="Muted.TLabel").grid(row=0, column=2)
        batch_hint = ttk.Label(size, style="Hint.TLabel", justify="left", text=(
            "Lô to thì ít lần gọi, đỡ vướng hạn mức mỗi phút. Lô nào bị Gemini "
            "trả lời cụt sẽ tự chia đôi gửi lại. Mỗi lô gửi kèm vài câu vừa dịch "
            "ở lô trước để giữ nhất quán xưng hô."))
        batch_hint.grid(row=0, column=3, sticky="ew", padx=(16, 0))
        size.columnconfigure(3, weight=1)
        self._wrap(batch_hint)

    def _build_middle(self) -> None:
        body = ttk.Frame(self)
        body.grid(row=4, column=0, sticky="nsew", pady=(14, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.split = ttk.PanedWindow(body, orient="vertical")
        self.split.grid(row=0, column=0, sticky="nsew")
        self._build_list(self.split)
        self._build_log(self.split)
        self.split.add(self.list_frame, weight=5)
        self.split.add(self.log_frame, weight=1)

        self._build_progress_card(body)

    def _build_list(self, parent) -> None:
        # Thẻ trắng bo góc bọc cả phần danh sách, kiểu nhóm nội dung của iOS.
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10, 12, 12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.list_frame = frame

        head = ttk.Frame(frame, style="OnCard.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        head.columnconfigure(1, weight=1)

        self.var_summary = tk.StringVar(value="Chưa chọn thư mục")
        ttk.Label(head, textvariable=self.var_summary, style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w")

        self.var_only_todo = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="Chỉ hiện file chưa xong",
                        style="OnCard.TCheckbutton",
                        variable=self.var_only_todo,
                        command=self._render_tree).grid(row=0, column=2, sticky="e")

        box = ttk.Frame(frame, style="OnCard.TFrame")
        box.grid(row=1, column=0, columnspan=2, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        columns = ("no", "name", "size", "stt", "srt")
        self.tree = ttk.Treeview(box, columns=columns, show="tree headings",
                                 height=6, style="Cue.Treeview", selectmode="browse")
        # Cột đầu chỉ chứa chấm trạng thái. Màu nằm ở chấm, chữ luôn giữ màu
        # mực - chữ tô màu vừa khó đọc vừa bắt người đọc nhớ bảng mã màu.
        self.tree.column("#0", width=30, minwidth=30, stretch=False, anchor="center")
        self.tree.heading("#0", text="")
        self.dots = {name: rounded.dot_image(9, colour, box=14)
                     for name, colour in theme.STATUS_DOTS.items()}
        for key, title, width, anchor, stretch in (
            ("no", "#", 34, "e", False),
            ("name", "Tên file", 200, "w", True),
            ("size", "Dung lượng", 88, "e", False),
            ("stt", "Nguyên ngữ", 122, "w", False),
            ("srt", "Phụ đề", 122, "w", False),
        ):
            self.tree.heading(key, text=title, anchor=anchor)
            # Chỉ cột tên file co giãn. Các cột còn lại vừa khít nội dung dài nhất
            # ("Đang nhận dạng…", "Xong · 182 khối") để không bị cắt chữ.
            self.tree.column(key, width=width, minwidth=width if not stretch else 140,
                             anchor=anchor, stretch=stretch)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self._scrollbar(box, self.tree).grid(row=0, column=1, sticky="ns")

        # Thứ tự khai báo quyết định thứ tự ưu tiên: nền của dòng đang chạy và
        # dòng lỗi phải đè lên nền sọc.
        self.tree.tag_configure("odd", background=theme.ZEBRA)
        for name, tint in theme.ROW_TINTS.items():
            self.tree.tag_configure(name, background=tint)

        # Bảng trống thì hiện lời mời hành động thay vì một khoảng trắng.
        self.empty = ttk.Frame(box, style="OnCard.TFrame")
        ttk.Label(self.empty, text="Chưa có video nào",
                  style="EmptyTitle.TLabel").pack()
        self.var_empty = tk.StringVar(value="Chọn thư mục chứa video để bắt đầu.")
        ttk.Label(self.empty, textvariable=self.var_empty,
                  style="EmptyHint.TLabel").pack(pady=(4, 12))
        ttk.Button(self.empty, text="Chọn thư mục", style="CardRounded.TButton",
                   command=self._pick_dir).pack()

        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<B1-Motion>", self._drag_move)
        self.tree.bind("<ButtonRelease-1>", self._drag_end)

        tools = ttk.Frame(frame, style="OnCard.TFrame")
        tools.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tools.columnconfigure(0, weight=1)
        ttk.Label(tools, style="CardHint.TLabel", text=(
            "Kéo dòng để đổi thứ tự. Thứ tự này cũng là thứ tự nối khi gộp video."
        )).grid(row=0, column=0, sticky="w")
        ttk.Button(tools, text="↑ Lên", style="Quiet.TButton",
                   command=lambda: self._nudge(-1)).grid(row=0, column=1)
        ttk.Button(tools, text="↓ Xuống", style="Quiet.TButton",
                   command=lambda: self._nudge(1)).grid(row=0, column=2)
        ttk.Button(tools, text="Sắp lại theo tên", style="Quiet.TButton",
                   command=self._reset_order).grid(row=0, column=3)

    def _build_log(self, parent) -> None:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=(14, 8, 12, 12))
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log_frame = frame

        head = ttk.Frame(frame, style="OnCard.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="Nhật ký", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Button(head, text="Xoá", style="Quiet.TButton",
                   command=self._clear_log).grid(row=0, column=1, sticky="e")

        self.log = self._text(frame, height=4, mono=True, readonly=True, on_card=True)
        self.log.grid(row=1, column=0, sticky="nsew")
        self._scrollbar(frame, self.log).grid(row=1, column=1, sticky="ns")
        for level, color in theme.LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

    def _build_progress_card(self, parent) -> None:
        """Thẻ Tiến độ - thứ người dùng nhìn suốt hàng giờ khi để app chạy.

        Theo dạng "meter" của dataviz: vòng tròn cho một tỉ lệ duy nhất, rãnh là
        bậc nhạt của chính màu đó, và đúng một con số chủ đạo ở giữa.
        """
        parent.columnconfigure(1, minsize=292)
        card = ttk.Frame(parent, style="Card.TFrame", padding=(18, 14, 18, 18))
        card.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        card.columnconfigure(0, weight=1, uniform="tile")
        card.columnconfigure(1, weight=1, uniform="tile")

        ttk.Label(card, text="Tiến độ", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")

        self.ring = Ring(card, size=196, thickness=15, fonts=self.fonts)
        self.ring.grid(row=1, column=0, columnspan=2, pady=(14, 14))

        self.var_file = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.var_file, style="FileName.TLabel",
                  anchor="w").grid(row=2, column=0, columnspan=2, sticky="ew")
        self.var_status = tk.StringVar(value="Sẵn sàng")
        stage = ttk.Label(card, textvariable=self.var_status, style="Stage.TLabel",
                          anchor="w", justify="left", wraplength=250)
        stage.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        self.bar_file = ttk.Progressbar(card, maximum=100,
                                        style="CardPill.Horizontal.TProgressbar")
        self.bar_file.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 18))

        self.tiles: dict[str, tk.StringVar] = {}
        for index, (key, label) in enumerate((
            ("elapsed", "Đã chạy"), ("remaining", "Còn lại"),
            ("started", "Bắt đầu lúc"), ("finish", "Xong lúc"),
        )):
            row, col = 5 + (index // 2) * 2, index % 2
            ttk.Label(card, text=label, style="TileLabel.TLabel").grid(
                row=row, column=col, sticky="w")
            self.tiles[key] = tk.StringVar(value="—")
            ttk.Label(card, textvariable=self.tiles[key], style="TileValue.TLabel").grid(
                row=row + 1, column=col, sticky="w", pady=(0, 10))

        # Nút hành động chính nằm ngay dưới con số nó điều khiển.
        card.rowconfigure(9, weight=1)
        self.btn_start = ttk.Button(card, text="Bắt đầu", style="CardAccent.TButton",
                                    command=self._start)
        self.btn_start.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.btn_stop = ttk.Button(card, text="Dừng", style="CardRounded.TButton",
                                   command=self._stop, state="disabled")
        self.btn_stop.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    # ----- widget dùng lại ------------------------------------------------

    def _combo(self, parent, row, col, label, variable, values, callback=None) -> None:
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(
            row=row, column=col, sticky="w", pady=5, padx=(0, 10))
        combo = ttk.Combobox(parent, textvariable=variable, values=values,
                             state="readonly", width=22, style="Rounded.TCombobox")
        combo.grid(row=row, column=col + 1, sticky="ew", pady=5, padx=(0, 20))
        if callback is not None:
            combo.bind("<<ComboboxSelected>>", callback)

    def _text(self, parent, height: int, mono: bool = False,
              readonly: bool = False, on_card: bool = False) -> tk.Text:
        # relief="solid" của Tk luôn vẽ viền đen bất kể màu, nên viền mảnh có
        # màu phải đi qua highlight. Ô nằm sẵn trên thẻ trắng thì bỏ viền luôn.
        widget = tk.Text(
            parent, height=height, wrap="word", relief="flat", borderwidth=0,
            background=theme.PANEL, foreground=theme.INK,
            highlightthickness=0 if on_card else 1,
            highlightbackground=theme.LINE, highlightcolor=theme.ACCENT,
            padx=10, pady=8,
            insertbackground=theme.INK, selectbackground=theme.SELECT,
            font=(self.fonts["mono"] if mono else self.fonts["ui"],
                  theme.SIZE_HINT if mono else theme.SIZE_BODY),
        )
        widget.configure(state="disabled" if readonly else "normal")
        return widget

    @staticmethod
    def _scrollbar(parent, widget) -> ttk.Scrollbar:
        bar = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=bar.set)
        return bar

    @staticmethod
    def _wrap(label: ttk.Label) -> None:
        """Chú thích tự xuống dòng theo bề rộng thật của cửa sổ."""
        label.bind("<Configure>",
                   lambda e, w=label: w.configure(wraplength=max(e.width - 8, 220)))

    # ----- quét thư mục ---------------------------------------------------

    def _start_scan(self) -> None:
        """Quét ở thread riêng - thư mục trên ổ mạng có thể mất vài giây."""
        self.scan_generation += 1
        token = self.scan_generation

        folder = self.var_dir.get().strip()
        if not folder:
            self.entries = []
            self.order = []
            self._render_tree()
            self.var_summary.set("Chưa chọn thư mục")
            return

        self.var_summary.set("Đang quét…")
        recursive = self.var_recursive.get()

        def work() -> None:
            try:
                self.scan_results.put((token, scan_entries(Path(folder), recursive=recursive)))
            except Exception as exc:
                self.scan_results.put((token, exc))

        threading.Thread(target=work, daemon=True).start()

    def _apply_scan(self, token: int, result: list[Entry] | Exception) -> None:
        if token != self.scan_generation:
            return  # kết quả của lần quét đã bị thay thế

        if isinstance(result, Exception):
            self.entries, self.order = [], []
            self.by_path.clear()
            self.stt_state.clear()
            self.srt_state.clear()
            self.has_srt.clear()
            self._render_tree()
            self.var_summary.set(f"Không quét được: {result}")
            return

        self.entries = result
        self.by_path = {str(e.video): e for e in result}
        folder = Path(self.var_dir.get().strip())
        self.var_subtitle.set(f"Thư mục đang mở: {folder.name or folder}")
        self.meter_mode = "folder"
        self.order = [str(e.video) for e in result]
        self.stt_state, self.srt_state, self.has_srt = {}, {}, {}
        for entry in result:
            self._state_from_disk(entry)
        self._render_tree()

    def _state_from_disk(self, entry: Entry) -> None:
        """Đọc lại tình trạng hai file từ đĩa thay vì suy đoán."""
        key = str(entry.video)
        has_stt = has_subtitle(entry.stt)
        has_final = has_subtitle(entry.srt)

        self.has_srt[key] = has_final
        self.srt_state[key] = (SRT_HAVE, "have") if has_final else (SRT_TODO, "todo")
        if has_stt:
            self.stt_state[key] = (STT_HAVE, "have")
        elif has_final:
            # Có thể app đã dọn sau khi dịch, cũng có thể phụ đề do nơi khác
            # làm - đừng khẳng định là "đã xoá".
            self.stt_state[key] = (STT_NONE, "have")
        else:
            self.stt_state[key] = (STT_TODO, "todo")

    # ----- bảng -----------------------------------------------------------

    @staticmethod
    def _row_tag(stt_tag: str, srt_tag: str) -> str:
        """Trạng thái đáng chú ý nhất của một dòng."""
        for tag in ("error", "running", "todo"):
            if stt_tag == tag or srt_tag == tag:
                return tag
        return "have"

    def _row_status(self, key: str) -> str:
        """Trạng thái cho chấm màu: như ``_row_tag`` nhưng tách riêng "chờ dịch" -
        đã có bản nguyên ngữ, chỉ còn thiếu bước dịch."""
        stt_text, stt_tag = self.stt_state.get(key, (STT_TODO, "todo"))
        _, srt_tag = self.srt_state.get(key, (SRT_TODO, "todo"))
        status = self._row_tag(stt_tag, srt_tag)
        if status == "todo" and stt_text == STT_HAVE:
            return "wait"
        return status

    def _row_look(self, key: str, position: int) -> tuple[tk.PhotoImage, tuple]:
        """Chấm trạng thái và các tag nền cho một dòng."""
        status = self._row_status(key)
        zebra = "odd" if position % 2 else "even"
        return self.dots[status], (zebra, status)

    def _visible_keys(self) -> list[str]:
        only_todo = self.var_only_todo.get()
        return [key for key in self.order
                if not (only_todo and self.has_srt.get(key, False))]

    def _render_tree(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())

        for position, key in enumerate(self._visible_keys()):
            entry = self.by_path.get(key)
            if entry is None:
                continue
            stt_text, _ = self.stt_state.get(key, (STT_TODO, "todo"))
            srt_text, _ = self.srt_state.get(key, (SRT_TODO, "todo"))
            image, tags = self._row_look(key, position)
            self.tree.insert(
                "", "end", iid=key, image=image, tags=tags,
                values=(self.order.index(key) + 1, entry.relative,
                        format_size(entry.size), stt_text, srt_text),
            )

        for key in selected:
            if self.tree.exists(key):
                self.tree.selection_set(key)
        self._show_empty_state()
        self._update_summary()

    def _show_empty_state(self) -> None:
        """Bảng không còn dòng nào thì hiện lời mời hành động giữa bảng."""
        if self.tree.get_children():
            self.empty.place_forget()
            return
        if not self.var_dir.get().strip():
            self.var_empty.set("Chọn thư mục chứa video để bắt đầu.")
        elif not self.entries:
            self.var_empty.set("Thư mục này không có file video nào.")
        else:
            self.var_empty.set("Mọi video đều đã có phụ đề.")
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _update_summary(self) -> None:
        if not self.entries:
            self.var_summary.set("Không có video nào trong thư mục")
            return

        total = len(self.entries)
        size = sum(e.size for e in self.entries)
        done = sum(1 for e in self.entries if self.has_srt.get(str(e.video), False))
        waiting = sum(1 for e in self.entries
                      if self.stt_state.get(str(e.video), ("", ""))[0] == STT_HAVE
                      and not self.has_srt.get(str(e.video), False))

        text = f"{total} video, {format_size(size)} · {done} xong, {total - done} chưa"
        if waiting:
            text += f", {waiting} chờ dịch"
        self.var_summary.set(text)
        self._update_meter()

    def _set_state(self, path: Path | None, column: str, text: str, tag: str,
                   has_srt: bool | None = None) -> None:
        """Cập nhật một ô trạng thái. ``column`` là "stt" hoặc "srt"."""
        if path is None:
            return
        key = str(path)
        store = self.stt_state if column == "stt" else self.srt_state
        store[key] = (text, tag)
        if has_srt is not None:
            self.has_srt[key] = has_srt

        if self.tree.exists(key):
            _, stt_tag = self.stt_state.get(key, (STT_TODO, "todo"))
            _, srt_tag = self.srt_state.get(key, (SRT_TODO, "todo"))
            row_tag = self._row_tag(stt_tag, srt_tag)

            if self.var_only_todo.get() and self.has_srt.get(key, False) \
                    and row_tag not in ("running", "error"):
                self.tree.delete(key)
                self._update_summary()
                return

            image, tags = self._row_look(key, self.tree.index(key))
            self.tree.set(key, column, text)
            self.tree.item(key, image=image, tags=tags)
            self.tree.see(key)
        self._update_summary()

    # ----- đổi thứ tự -----------------------------------------------------

    def _sync_order(self) -> None:
        """Đưa thứ tự đang hiện trên bảng về lại danh sách đầy đủ.

        Lúc đang bật bộ lọc thì bảng chỉ có một phần; những dòng bị ẩn phải giữ
        nguyên chỗ cũ, nên chỉ hoán các vị trí mà dòng đang hiện chiếm giữ.
        """
        visible = list(self.tree.get_children())
        if not visible:
            return
        shown = set(visible)
        slots = [i for i, key in enumerate(self.order) if key in shown]
        for slot, key in zip(slots, visible):
            self.order[slot] = key

        for position, key in enumerate(visible):
            image, tags = self._row_look(key, position)
            self.tree.set(key, "no", self.order.index(key) + 1)
            self.tree.item(key, image=image, tags=tags)

    def _drag_start(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) == "heading":
            return
        self._dragging = self.tree.identify_row(event.y) or None

    def _drag_move(self, event) -> None:
        if self._dragging is None:
            return
        target = self.tree.identify_row(event.y)
        if target and target != self._dragging:
            self.tree.move(self._dragging, "", self.tree.index(target))

    def _drag_end(self, _event) -> None:
        if self._dragging is None:
            return
        self._dragging = None
        self._sync_order()

    def _nudge(self, step: int) -> None:
        """Đẩy dòng đang chọn lên hoặc xuống một bậc."""
        chosen = self.tree.selection()
        if not chosen:
            return
        key = chosen[0]
        target = self.tree.index(key) + step
        if 0 <= target < len(self.tree.get_children()):
            self.tree.move(key, "", target)
            self._sync_order()
            self.tree.see(key)

    def _reset_order(self) -> None:
        self.order = [str(e.video) for e in self.entries]
        self._render_tree()

    def merge_order(self) -> list[Path]:
        return [self.by_path[key].video for key in self.order if key in self.by_path]

    # ----- API key --------------------------------------------------------

    def _read_keys(self) -> list[str]:
        """Danh sách key hiện tại. Lúc đang che thì lấy từ bộ nhớ, không đọc ô."""
        if not self.keys_hidden:
            raw = self.keys_box.get("1.0", "end")
            self._keys = [line.strip() for line in raw.splitlines() if line.strip()]
        return list(self._keys)

    def _show_keys(self, hidden: bool) -> None:
        if hidden and not self.keys_hidden:
            self._read_keys()          # chốt nội dung trước khi che

        self.keys_hidden = hidden
        self.keys_box.configure(state="normal")
        self.keys_box.delete("1.0", "end")
        shown = [mask_key(k) for k in self._keys] if hidden else list(self._keys)
        self.keys_box.insert("1.0", "\n".join(shown))
        self.keys_box.configure(state="disabled" if hidden else "normal")
        self.btn_eye.configure(text="Hiện key" if hidden else "Ẩn key")

    def _toggle_key_visibility(self) -> None:
        self._show_keys(not self.keys_hidden)

    def _refresh_models(self) -> None:
        """Hỏi thẳng Google danh sách model thật thay vì tin vào danh sách chết."""
        keys = self._read_keys()
        if not keys:
            messagebox.showinfo("Chưa có key", "Dán ít nhất một API key vào ô trên.")
            return

        self.btn_models.configure(state="disabled")
        self._log("Đang lấy danh sách model từ Google…")

        def work() -> None:
            try:
                self.model_results.put(fetch_models(keys))
            except Exception as exc:
                self.model_results.put(exc)

        threading.Thread(target=work, daemon=True).start()

    def _apply_models(self, result: tuple[list[str], str] | Exception) -> None:
        self.btn_models.configure(state="normal")
        if isinstance(result, Exception):
            self._log(f"Không lấy được danh sách model: {result}", "error")
            return

        models, note = result
        self.combo_gemini.configure(values=models)
        self._log(note, "success")

        current = self.var_gemini_model.get().strip()
        if current not in models and models:
            self.var_gemini_model.set(models[0])
            self._log(f"'{current}' không có trong danh sách, đã chọn {models[0]}.", "warn")

    def _check_keys(self) -> None:
        keys = self._read_keys()
        if not keys:
            messagebox.showinfo("Chưa có key", "Dán ít nhất một API key vào ô trên.")
            return

        self.btn_check_keys.configure(state="disabled")
        self._log(f"Đang kiểm tra {len(keys)} key…")

        def work() -> None:
            for position, key in enumerate(keys, start=1):
                tail = key[-4:] if len(key) >= 4 else "????"
                ok, note = check_key(key)
                self.events.put(pipeline.Event(
                    kind="log", level="success" if ok else "error",
                    message=f"key #{position} (…{tail}): {note}"))
            self.events.put(pipeline.Event(kind="keys_checked"))

        threading.Thread(target=work, daemon=True).start()

    # ----- đồng bộ với Settings -------------------------------------------

    def _apply_settings(self) -> None:
        s = self.settings
        self.var_dir.set(s.input_dir)
        self.var_recursive.set(s.recursive)
        self.var_overwrite.set(s.overwrite)
        self.var_model.set(s.model if s.model in MODELS else MODELS[0])
        self.var_compute.set(s.compute_type if s.compute_type in COMPUTE_TYPES else "float16")
        self.var_batch.set(s.batch_size)
        self.var_vad.set(s.vad_filter)
        self.var_filter.set(s.filter_hallucinations)
        self.var_model_dir.set(s.model_dir)
        self.var_translate.set(s.translate_enabled)
        self.var_keep_stt.set(s.keep_stt)
        self.var_translate_batch.set(s.translate_batch_size)
        self.var_gemini_model.set(s.gemini_model or DEFAULT_GEMINI_MODEL)

        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", s.translate_prompt)

        self._keys = list(s.gemini_keys)
        # Có key sẵn thì che ngay, chưa có thì để mở cho dễ dán vào.
        self._show_keys(s.hide_keys and bool(self._keys))

        self.var_language.set(next(
            (label for label, code in LANGUAGES if code == s.language), LANGUAGES[0][0]))
        self.var_variant.set(next(
            (label for label, code in CHINESE_VARIANTS if code == s.chinese_variant),
            CHINESE_VARIANTS[0][0]))

        self.panel_options.set_expanded(s.panel_options_open, notify=False)
        self.panel_translate.set_expanded(s.panel_translate_open, notify=False)
        self._refresh_advice()

        # Tiêu đề khung lúc gập phải nói được thiết lập bên trong, nên bám theo
        # mọi ô có thể đổi.
        for var in (self.var_language, self.var_model, self.var_compute,
                    self.var_translate, self.var_gemini_model, self.var_keep_stt,
                    self.var_translate_batch):
            var.trace_add("write", lambda *_: self._refresh_summaries())
        self._refresh_summaries()

    def _collect_settings(self) -> Settings:
        s = self.settings
        s.input_dir = self.var_dir.get().strip()
        s.recursive = self.var_recursive.get()
        s.overwrite = self.var_overwrite.get()
        s.model = self.var_model.get()
        s.compute_type = self.var_compute.get()
        s.batch_size = max(1, int(self.var_batch.get()))
        s.vad_filter = self.var_vad.get()
        s.filter_hallucinations = self.var_filter.get()
        s.model_dir = self.var_model_dir.get().strip()
        s.translate_enabled = self.var_translate.get()
        s.keep_stt = self.var_keep_stt.get()
        try:
            s.translate_batch_size = max(10, int(self.var_translate_batch.get()))
        except (tk.TclError, ValueError):
            s.translate_batch_size = 200
        s.gemini_keys = self._read_keys()
        s.gemini_model = self.var_gemini_model.get().strip() or DEFAULT_GEMINI_MODEL
        s.translate_prompt = self.prompt_box.get("1.0", "end").strip()
        s.language = dict(LANGUAGES).get(self.var_language.get())
        s.chinese_variant = dict(CHINESE_VARIANTS).get(self.var_variant.get(), "s")
        s.panel_options_open = self.panel_options.expanded
        s.panel_translate_open = self.panel_translate.expanded
        s.hide_keys = self.keys_hidden
        return s

    def _refresh_summaries(self) -> None:
        language = self.var_language.get().split(" (")[0]
        self.panel_options.set_summary(
            f"{self.var_model.get()}, {language}, {self.var_compute.get()}")

        if not self.var_translate.get():
            self.panel_translate.set_summary("tắt")
        else:
            count = len(self._keys)
            keys = f"{count} key" if count else "chưa có key"
            keep = ", giữ .stt" if self.var_keep_stt.get() else ""
            try:
                batch = f", {int(self.var_translate_batch.get())} dòng/lần"
            except (tk.TclError, ValueError):
                batch = ""
            self.panel_translate.set_summary(
                f"{self.var_gemini_model.get()}, {keys}{batch}{keep}")
        self._refresh_advice()

    def _refresh_advice(self) -> None:
        language = dict(LANGUAGES).get(self.var_language.get())
        text, level = model_advice(language, self.var_model.get())
        self.lbl_advice.configure(
            text=text, style="Warn.TLabel" if level == "warn" else "Hint.TLabel")

    def _on_section_toggle(self) -> None:
        self._refresh_summaries()
        self.settings.panel_options_open = self.panel_options.expanded
        self.settings.panel_translate_open = self.panel_translate.expanded
        self.settings.save()

    # ----- bố cục co theo cửa sổ ------------------------------------------

    def _arm_fit(self) -> None:
        self._fit_armed = True
        self._fit_to_window()

    def _on_window_resize(self, event) -> None:
        if event.widget is not self.master or not self._fit_armed:
            return
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(150, self._fit_to_window)

    def _fit_to_window(self) -> None:
        """Cửa sổ không đủ cao thì gập bớt khung cho bảng danh sách có chỗ.

        Chỉ gập chứ không bao giờ tự mở, và không ghi đè lựa chọn đã lưu.
        """
        self._fit_job = None
        if not self.winfo_exists():
            return
        self.update_idletasks()
        available = self.winfo_height()
        if available <= 1:
            return

        folded = []
        for panel in (self.panel_translate, self.panel_options):
            if self.winfo_reqheight() <= available:
                break
            if panel.expanded:
                panel.set_expanded(False, notify=False)
                folded.append(panel.title)
                self.update_idletasks()

        if folded:
            self._refresh_summaries()

    # ----- hành động ------------------------------------------------------

    def _pick_dir(self) -> None:
        chosen = filedialog.askdirectory(title="Chọn thư mục chứa video",
                                         initialdir=self.var_dir.get() or None)
        if chosen:
            self.var_dir.set(chosen)
            self._start_scan()

    def _pick_model_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="Chọn thư mục lưu model",
            initialdir=resolve_model_dir(self.var_model_dir.get()))
        if chosen:
            self.var_model_dir.set(chosen)

    def _reset_model_dir(self) -> None:
        self.var_model_dir.set("")
        self._log(f"Thư mục model trở về mặc định: {default_model_dir()}")

    def _apply_preset(self, _event=None) -> None:
        text = dict(PROMPT_PRESETS).get(self.var_preset.get(), "")
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", text)

    def _on_language_change(self, _event=None) -> None:
        """Model distil chỉ biết tiếng Anh, tự đưa về large-v3 khi chọn tiếng khác."""
        code = dict(LANGUAGES).get(self.var_language.get())
        if code != "en" and self.var_model.get() in ENGLISH_ONLY_MODELS:
            self.var_model.set("large-v3")
            self._log("Model distil-* chỉ hiểu tiếng Anh, đã chuyển về large-v3.", "warn")
        self._refresh_advice()

    def _on_model_change(self, _event=None) -> None:
        code = dict(LANGUAGES).get(self.var_language.get())
        if self.var_model.get() in ENGLISH_ONLY_MODELS and code != "en":
            self.var_language.set(next(
                label for label, lang in LANGUAGES if lang == "en"))
            self._log("Model này chỉ hiểu tiếng Anh, đã chuyển ngôn ngữ sang English.",
                      "warn")
        self._refresh_advice()

    def _busy(self, busy: bool) -> None:
        for button in (self.btn_start, self.btn_translate, self.btn_merge):
            button.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def _running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _folder_ready(self) -> bool:
        folder = self.var_dir.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Chưa có thư mục", "Chọn thư mục chứa video trước đã.")
            return False
        return True

    def _launch(self, action, status: str) -> None:
        """Chuẩn bị rồi chạy một công việc dài ở thread riêng."""
        settings = self._collect_settings()
        settings.save()
        self.cancel.clear()
        self.bar_file["value"] = 0
        self._busy(True)
        self.var_status.set(status)
        self._start_clock()

        def work() -> None:
            try:
                action(settings, emit=self.events.put, cancel=self.cancel)
            except Exception as exc:
                self.events.put(pipeline.Event(kind="finished", level="error",
                                               message=f"Dừng vì lỗi: {exc}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _start(self) -> None:
        if self._running() or not self._folder_ready():
            return
        self._launch(pipeline.run, "Đang chuẩn bị…")

    def _translate_existing(self) -> None:
        if self._running() or not self._folder_ready():
            return
        if not self._read_keys():
            messagebox.showerror("Chưa có API key", "Dán API key của Gemini vào ô trên.")
            return
        self._launch(pipeline.translate_existing, "Đang dịch…")

    def _merge_videos(self) -> None:
        """Gộp video theo đúng thứ tự đang hiện trong bảng."""
        if self._running() or not self._folder_ready():
            return

        videos = self.merge_order()
        if len(videos) < 2:
            messagebox.showinfo("Chưa đủ video",
                                "Cần ít nhất hai video trong danh sách mới gộp được.")
            return

        try:
            _, can_copy, note = inspect(videos)
        except Exception as exc:
            messagebox.showerror("Không gộp được", str(exc))
            return

        suggested = default_output(Path(self.var_dir.get().strip()))
        order = "\n".join(f"  {i}. {v.name}" for i, v in enumerate(videos[:6], 1))
        if len(videos) > 6:
            order += f"\n  … còn {len(videos) - 6} file nữa"

        if not messagebox.askokcancel("Gộp video", (
                f"Nối {len(videos)} video theo thứ tự này:\n\n{order}\n\n{note}\n\n"
                f"Lưu vào:\n{suggested}")):
            return

        chosen = filedialog.asksaveasfilename(
            title="Lưu file gộp", defaultextension=".mp4",
            initialdir=str(suggested.parent), initialfile=suggested.name,
            filetypes=[("Video MP4", "*.mp4"), ("Tất cả", "*.*")])
        if not chosen:
            return

        self.cancel.clear()
        self.bar_file["value"] = 0
        self._busy(True)
        self._start_clock(total=1)
        self.var_file.set(_shorten(Path(chosen).name, 38))
        self.var_status.set(f"Đang gộp {len(videos)} video…")
        self._log(note, "info" if can_copy else "warn")
        output = Path(chosen)

        def work() -> None:
            def report(fraction: float) -> None:
                self.events.put(pipeline.Event(kind="file_progress", progress=fraction))

            try:
                result, summary = merge(videos, output, on_progress=report,
                                        should_cancel=self.cancel.is_set)
            except MergeCancelled:
                self.events.put(pipeline.Event(kind="finished", level="warn",
                                               message="Đã dừng, chưa gộp xong."))
                return
            except Exception as exc:
                self.events.put(pipeline.Event(kind="finished", level="error",
                                               message=f"Gộp hỏng: {exc}"))
                return

            self.events.put(pipeline.Event(kind="log", level="success", message=summary))
            self.events.put(pipeline.Event(kind="finished", level="success",
                                           message=f"Đã gộp xong: {result.name}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Đang dừng, chờ file hiện tại xong…")

    # ----- đồng hồ và nhật ký ---------------------------------------------

    def _start_clock(self, total: int = 0) -> None:
        self.run_started = time.monotonic()
        self.started_at = datetime.now()
        self.finished_at: datetime | None = None
        self.run_total = total
        self.run_done = 0
        self.file_fraction = 0.0
        self.meter_mode = "run"
        self._tick()

    def _tick(self) -> None:
        if self.run_started is None or not self.winfo_exists():
            return
        self._update_meter()
        self.timer_job = self.after(1000, self._tick)

    def _stop_clock(self, succeeded: bool) -> None:
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        if self.run_started is None:
            return
        self.elapsed_total = time.monotonic() - self.run_started
        self.finished_at = datetime.now()
        self.run_started = None
        self.meter_mode = "done" if succeeded else "stopped"
        self._update_meter()

    def _overall(self) -> float:
        """Phần đã chạy của cả loạt, tính cả phần dở của file đang làm."""
        if not self.run_total:
            return self.file_fraction
        return min(1.0, (self.run_done + self.file_fraction) / self.run_total)

    def _update_meter(self) -> None:
        """Vẽ lại vòng tiến độ và bốn ô số liệu theo đúng tình trạng hiện tại."""
        if not hasattr(self, "ring"):
            return
        mode = getattr(self, "meter_mode", "folder")
        tiles = self.tiles

        if mode == "folder":
            # Lúc rảnh, vòng cho biết thư mục đã làm xong bao nhiêu phần.
            total = len(self.entries)
            done = sum(1 for e in self.entries if self.has_srt.get(str(e.video), False))
            if total:
                self.ring.show(done / total, f"{round(done / total * 100)}%",
                               f"{done} / {total} video đã xong")
            else:
                self.ring.show(0.0, "—", "chưa có video")
            for key in tiles:
                tiles[key].set("—")
            return

        overall = self._overall()
        percent = f"{int(overall * 100)}%"
        if mode == "run":
            elapsed = time.monotonic() - self.run_started
            current = min(self.run_done + 1, self.run_total) if self.run_total else 1
            caption = (f"video {current} / {self.run_total}"
                       if self.run_total > 1 else "đang chạy")
            self.ring.show(overall, percent, caption)
            tiles["elapsed"].set(pipeline.format_duration(elapsed))
            tiles["started"].set(f"{self.started_at:%H:%M}")
            # Chưa đủ dữ liệu thì đừng đoán bừa - vài phần trăm đầu ước tính rất lệch.
            if overall >= 0.03:
                remaining = elapsed * (1 - overall) / overall
                tiles["remaining"].set("~" + pipeline.format_duration(remaining))
                eta = datetime.now().timestamp() + remaining
                tiles["finish"].set(datetime.fromtimestamp(eta).strftime("%H:%M"))
            else:
                tiles["remaining"].set("đang ước tính")
                tiles["finish"].set("—")
            return

        # Đã xong hoặc đã dừng: giữ nguyên kết quả của lượt vừa chạy.
        caption = "hoàn tất" if mode == "done" else "đã dừng"
        self.ring.show(1.0 if mode == "done" else overall,
                       "100%" if mode == "done" else percent, caption)
        tiles["elapsed"].set(pipeline.format_duration(self.elapsed_total))
        tiles["remaining"].set("0 giây" if mode == "done" else "—")
        tiles["started"].set(f"{self.started_at:%H:%M}")
        tiles["finish"].set(f"{self.finished_at:%H:%M}")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log(self, message: str, level: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ----- nhận sự kiện từ thread xử lý -----------------------------------

    def _drain(self) -> None:
        if not self.winfo_exists():
            return  # cửa sổ đã đóng, đừng hẹn vòng kế tiếp
        for source, handler in ((self.scan_results, lambda r: self._apply_scan(*r)),
                                (self.model_results, self._apply_models),
                                (self.events, self._handle)):
            try:
                while True:
                    handler(source.get_nowait())
            except queue.Empty:
                pass
        self.after(POLL_INTERVAL_MS, self._drain)

    def _handle(self, event: pipeline.Event) -> None:
        if event.kind == "scan":
            self.run_total = event.total
            self._update_meter()
            self._log(event.message, event.level)

        elif event.kind == "file_start":
            self.bar_file["value"] = 0
            self.file_fraction = 0.0
            self.var_file.set(_shorten(event.message, 38))
            if event.stage == "translate":
                self.var_status.set("Đang dịch sang tiếng Việt…")
                self._set_state(event.path, "srt", "Đang dịch…", "running")
            else:
                self.var_status.set("Đang nhận dạng lời thoại…")
                self._set_state(event.path, "stt", "Đang nhận dạng…", "running")
            self._update_meter()

        elif event.kind == "file_progress":
            self.bar_file["value"] = event.progress * 100
            self.file_fraction = event.progress
            self._update_meter()

        elif event.kind == "translating":
            self.var_status.set("Đang dịch sang tiếng Việt…")
            self._set_state(event.path, "srt", "Đang dịch…", "running")
            self._log(event.message, event.level)

        elif event.kind == "keys_checked":
            self.btn_check_keys.configure(state="normal")

        elif event.kind == "file_done":
            self.bar_file["value"] = 100
            self.run_done = event.index
            self.file_fraction = 0.0
            self._apply_file_done(event)
            self._log(f"[{event.index}/{event.total}] {event.message}", event.level)
            self._update_meter()

        elif event.kind == "finished":
            self.var_file.set("")
            self.var_status.set(event.message)
            self._log(event.message, event.level)
            self._stop_clock(succeeded=event.level == "success")
            self._busy(False)

        else:
            self._log(event.message, event.level)

    def _apply_file_done(self, event: pipeline.Event) -> None:
        """Đổ kết quả một file vào đúng hai cột trạng thái."""
        only_translating = event.stage == "translate"

        if event.level == "error":
            column = "srt" if only_translating else "stt"
            self._set_state(event.path, column, "Lỗi", "error",
                            has_srt=None if only_translating else False)
            return

        if event.translated == "failed":
            # Bản nguyên ngữ vẫn còn nguyên, chỉ thiếu mỗi bước dịch.
            self._set_state(event.path, "stt", f"Xong · {event.cues} khối", "have")
            self._set_state(event.path, "srt", "Lỗi", "error")
            return

        self._set_state(event.path, "srt", f"Xong · {event.cues} khối",
                        "have", has_srt=True)

        # .stt còn hay đã dọn thì hỏi thẳng đĩa, khỏi đoán theo tuỳ chọn.
        entry = self.by_path.get(str(event.path))
        if entry is not None:
            self._set_state(event.path, "stt",
                            STT_HAVE if has_subtitle(entry.stt) else STT_GONE, "have")

    def on_close(self) -> None:
        if self._running():
            if not messagebox.askokcancel("Đang chạy", "Đang xử lý dở. Thoát luôn?"):
                return
            self.cancel.set()
        self._collect_settings().save()
        self.master.destroy()


def main() -> int:
    root = tk.Tk()
    root.title("VideoOCR")

    # Vừa với màn hình thật thay vì cố định một kích thước có thể tràn ra ngoài.
    height = min(900, max(600, root.winfo_screenheight() - 150))
    width = min(1040, max(780, root.winfo_screenwidth() - 120))
    root.geometry(f"{width}x{height}")
    root.minsize(760, 620)

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0
