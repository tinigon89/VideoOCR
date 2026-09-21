"""Cửa sổ điều khiển bằng tkinter.

Phần xử lý nặng chạy ở thread riêng và đẩy sự kiện qua queue; cửa sổ chỉ đọc
queue định kỳ. Nhờ vậy giao diện không bị đơ trong lúc nhận dạng.

Bố cục nhắm tới màn hình thấp: hai khung tuỳ chọn gập lại được, còn bảng danh
sách và nhật ký nằm trong một vùng kéo chia đôi được bằng chuột.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import pipeline
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
from .scanner import Entry, format_size, has_subtitle, scan_entries
from .merger import MergeCancelled, default_output, inspect, merge
from .translator import SUGGESTED_MODELS, check_key, fetch_models

POLL_INTERVAL_MS = 100

LEVEL_COLORS = {
    "info": "#1f1f1f",
    "warn": "#a86400",
    "error": "#c02020",
    "success": "#1a7f37",
}

# Màu cho cột trạng thái trong bảng.
STATUS_COLORS = {
    "todo": "#1f1f1f",
    "have": "#1a7f37",
    "running": "#0b5cab",
    "error": "#c02020",
}

# Hai cột trạng thái: bản nguyên ngữ .stt (trung gian) và .srt (bản cuối cùng).
STT_TODO = "Chưa có"
STT_HAVE = "Đã có"
STT_GONE = "Đã xoá"
STT_NONE = "—"      # chưa từng có, hoặc phụ đề do nơi khác làm
SRT_TODO = "Chưa có"
SRT_HAVE = "Đã có"

HINT_COLOR = "#666666"

# Chiều cao tối thiểu dành cho vùng bảng + nhật ký, tính bằng pixel.
LIST_MIN_HEIGHT = 190


def mask_key(key: str) -> str:
    """AIzaSyDhE2...RKGI1R1s -> AIza••••••••1R1s"""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * min(len(key) - 8, 24) + key[-4:]


class Collapsible(ttk.Frame):
    """Khung có tiêu đề bấm được để gập/mở phần thân."""

    def __init__(self, master, title: str, expanded: bool = True, on_toggle=None) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.title = title
        self._expanded = expanded
        self._on_toggle = on_toggle

        self.header = ttk.Button(self, style="Section.TButton", command=self.toggle)
        self.header.grid(row=0, column=0, sticky="ew")

        self.body = ttk.Frame(self, padding=(12, 6, 4, 8))
        self.body.grid(row=1, column=0, sticky="ew")

        self._sync()

    @property
    def expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, value: bool, notify: bool = True) -> None:
        """``notify=False`` dùng cho lúc app tự gập để vừa màn hình - không
        ghi đè lựa chọn người dùng đã lưu."""
        if value == self._expanded:
            return
        self._expanded = value
        self._sync()
        if notify and self._on_toggle is not None:
            self._on_toggle()

    def _sync(self) -> None:
        self.header.configure(text=f"  {'▼' if self._expanded else '▶'}   {self.title}")
        if self._expanded:
            self.body.grid()
        else:
            self.body.grid_remove()


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
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
        # Mỗi file giữ hai trạng thái riêng: phụ đề gốc và bản dịch.
        self.entries: list[Entry] = []
        self.by_path: dict[str, Entry] = {}
        self.stt_state: dict[str, tuple[str, str]] = {}   # khoá -> (chữ, tag màu)
        self.srt_state: dict[str, tuple[str, str]] = {}
        self.has_srt: dict[str, bool] = {}

        # Mốc thời gian của lượt chạy hiện tại.
        self.run_started: float | None = None
        self.timer_job: str | None = None

        # Nguồn sự thật của danh sách key; ô nhập có thể đang hiện dạng che.
        self._keys: list[str] = []
        self.keys_hidden = True

        self._build()
        self._apply_settings()
        self.pack(fill="both", expand=True)
        self.after(POLL_INTERVAL_MS, self._drain)

        # Đo lại mỗi khi cửa sổ đổi kích thước. Chỉ đo một lần lúc khởi động là
        # không đủ: geometry thường được áp sau khi widget dựng xong.
        self._fit_job: str | None = None
        self._fit_announced = False
        self.master.bind("<Configure>", self._on_window_resize)

        # Nhật ký để trống khi mới mở. Thông tin card đồ hoạ sẽ hiện lúc chạy,
        # trong dòng báo nạp model.
        if self.var_dir.get():
            self._start_scan()

    # ----- dựng giao diện -------------------------------------------------

    def _build(self) -> None:
        style = ttk.Style()
        style.configure("Section.TButton", anchor="w", padding=(4, 5))

        self.columnconfigure(0, weight=1)
        # minsize để bảng danh sách không bao giờ bị các khung phía trên ép về 0.
        self.rowconfigure(3, weight=1, minsize=LIST_MIN_HEIGHT)

        self._build_folder()
        self._build_options()
        self._build_translate()
        self._build_middle()
        self._build_progress()

    def _build_folder(self) -> None:
        frame = ttk.LabelFrame(self, text="Thư mục video", padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.var_dir = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_dir).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(frame, text="Chọn...", command=self._pick_dir).grid(row=0, column=1)
        ttk.Button(frame, text="Quét lại", command=self._start_scan).grid(
            row=0, column=2, padx=(4, 0))

        self.var_recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Quét cả thư mục con", variable=self.var_recursive,
                        command=self._start_scan).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.var_overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Ghi đè file .srt đã có", variable=self.var_overwrite).grid(
            row=1, column=1, sticky="w", pady=(6, 0))

        self.btn_merge = ttk.Button(frame, text="Gộp video...", command=self._merge_videos)
        self.btn_merge.grid(row=1, column=2, sticky="e", pady=(6, 0), padx=(4, 0))

    def _build_options(self) -> None:
        self.panel_options = Collapsible(self, "Tuỳ chọn nhận dạng",
                                         on_toggle=self._save_layout)
        self.panel_options.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        frame = self.panel_options.body
        for col in (1, 3):
            frame.columnconfigure(col, weight=1)

        self.var_language = tk.StringVar()
        self._combo(frame, 0, 0, "Ngôn ngữ:", self.var_language,
                    [label for label, _ in LANGUAGES], self._on_language_change)

        self.var_model = tk.StringVar()
        self._combo(frame, 0, 2, "Model:", self.var_model, MODELS, self._on_model_change)

        self.lbl_advice = self._hint(frame, "")
        self.lbl_advice.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        self.var_variant = tk.StringVar()
        self._combo(frame, 1, 0, "Kiểu chữ Trung:", self.var_variant,
                    [label for label, _ in CHINESE_VARIANTS])

        self.var_compute = tk.StringVar()
        self._combo(frame, 1, 2, "Compute type:", self.var_compute, COMPUTE_TYPES)

        ttk.Label(frame, text="Batch size:").grid(row=2, column=0, sticky="w", pady=4)
        self.var_batch = tk.IntVar(value=8)
        ttk.Spinbox(frame, from_=1, to=32, textvariable=self.var_batch,
                    width=8).grid(row=2, column=1, sticky="w", pady=4)

        self.var_vad = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Lọc khoảng lặng (VAD)",
                        variable=self.var_vad).grid(row=2, column=2, sticky="w", pady=4)

        self.var_filter = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Lọc câu rác",
                        variable=self.var_filter).grid(row=2, column=3, sticky="w", pady=4)

        ttk.Label(frame, text="Thư mục model:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        row = ttk.Frame(frame)
        row.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(0, 12))
        row.columnconfigure(0, weight=1)

        self.var_model_dir = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_model_dir).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="Chọn...", command=self._pick_model_dir).grid(
            row=0, column=1, padx=(6, 0))
        ttk.Button(row, text="Mặc định", command=self._reset_model_dir).grid(
            row=0, column=2, padx=(4, 0))

        self._hint(frame, "Để trống là dùng thư mục models\\ cạnh app. Trỏ vào cache "
                          "HuggingFace nếu muốn dùng lại model đã tải cho phần mềm khác."
                   ).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(3, 0))

    def _build_translate(self) -> None:
        self.panel_translate = Collapsible(self, "Dịch sang tiếng Việt (Google Gemini)",
                                           on_toggle=self._save_layout)
        self.panel_translate.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        frame = self.panel_translate.body
        frame.columnconfigure(1, weight=1)

        self.var_translate = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Dịch tự động sau khi nhận dạng xong (bản cuối là phim.srt)",
            variable=self.var_translate,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        self.var_keep_stt = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Giữ lại bản nguyên ngữ .stt",
                        variable=self.var_keep_stt).grid(row=0, column=2, sticky="e")

        ttk.Label(frame, text="API key:").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        keys_wrap = ttk.Frame(frame)
        keys_wrap.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        keys_wrap.columnconfigure(0, weight=1)

        self.keys_box = tk.Text(keys_wrap, height=3, wrap="none", font=("Consolas", 9))
        self.keys_box.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(keys_wrap, orient="vertical", command=self.keys_box.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.keys_box.configure(yscrollcommand=scroll.set)

        self.btn_eye = ttk.Button(keys_wrap, text="Hiện key", width=10,
                                  command=self._toggle_key_visibility)
        self.btn_eye.grid(row=0, column=2, sticky="n", padx=(6, 0))

        self._hint(frame, "Mỗi dòng một key. Hết hạn mức thì tự chuyển sang key kế tiếp, "
                          "key hỏng thì loại khỏi vòng quay. Google tính hạn mức theo "
                          "project, nên nhiều key trong cùng một project vẫn dùng chung "
                          "quota - muốn cộng dồn thì mỗi key phải khác project."
                   ).grid(row=2, column=1, columnspan=2, sticky="ew", pady=(3, 0))

        ttk.Label(frame, text="Hướng dẫn dịch:").grid(
            row=3, column=0, sticky="nw", pady=(8, 0))
        prompt_wrap = ttk.Frame(frame)
        prompt_wrap.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        prompt_wrap.columnconfigure(0, weight=1)

        self.prompt_box = tk.Text(prompt_wrap, height=3, wrap="word")
        self.prompt_box.grid(row=0, column=0, sticky="ew")
        pscroll = ttk.Scrollbar(prompt_wrap, orient="vertical",
                                command=self.prompt_box.yview)
        pscroll.grid(row=0, column=1, sticky="ns")
        self.prompt_box.configure(yscrollcommand=pscroll.set)

        self.var_preset = tk.StringVar(value=PROMPT_PRESETS[0][0])
        preset = ttk.Combobox(prompt_wrap, textvariable=self.var_preset, width=24,
                              state="readonly",
                              values=[label for label, _ in PROMPT_PRESETS])
        preset.grid(row=0, column=2, sticky="n", padx=(6, 0))
        preset.bind("<<ComboboxSelected>>", self._apply_preset)

        self._hint(frame, "Dặn người dịch về xưng hô và văn phong - chỗ máy dịch "
                          "hay sai nhất, vì tiếng Trung và tiếng Anh không phân biệt "
                          "vai vế như tiếng Việt. Chọn mẫu bên phải rồi sửa lại cho "
                          "hợp phim của bạn."
                   ).grid(row=4, column=1, columnspan=2, sticky="ew", pady=(3, 0))

        bottom = ttk.Frame(frame)
        bottom.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(2, weight=1)

        ttk.Label(bottom, text="Model Gemini:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.var_gemini_model = tk.StringVar(value=DEFAULT_GEMINI_MODEL)
        self.combo_gemini = ttk.Combobox(bottom, textvariable=self.var_gemini_model,
                                         values=SUGGESTED_MODELS, width=28)
        self.combo_gemini.grid(row=0, column=1, sticky="w")

        self.btn_models = ttk.Button(bottom, text="Lấy danh sách",
                                     command=self._refresh_models)
        self.btn_models.grid(row=0, column=2, sticky="w", padx=(6, 0))

        self.btn_check_keys = ttk.Button(bottom, text="Kiểm tra key", command=self._check_keys)
        self.btn_check_keys.grid(row=0, column=3, padx=(6, 0))
        self.btn_translate = ttk.Button(bottom, text="Dịch bản .stt đã có",
                                        command=self._translate_existing)
        self.btn_translate.grid(row=0, column=4, padx=(4, 0))

    def _build_middle(self) -> None:
        """Bảng danh sách và nhật ký chia nhau chỗ còn lại, kéo được bằng chuột."""
        self.split = ttk.PanedWindow(self, orient="vertical")
        self.split.grid(row=3, column=0, sticky="nsew", pady=(8, 0))

        self._build_list(self.split)
        self._build_log(self.split)
        self.split.add(self.list_frame, weight=4)
        self.split.add(self.log_frame, weight=1)

    def _build_list(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Danh sách video", padding=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.list_frame = frame

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)

        self.var_summary = tk.StringVar(value="Chưa chọn thư mục.")
        ttk.Label(header, textvariable=self.var_summary).grid(row=0, column=0, sticky="w")

        self.var_only_todo = tk.BooleanVar(value=False)
        ttk.Checkbutton(header, text="Chỉ hiện file chưa có SRT",
                        variable=self.var_only_todo,
                        command=self._render_tree).grid(row=0, column=1, sticky="e")

        # height thấp để bảng không đòi nhiều chỗ trên màn hình bé; weight trong
        # PanedWindow sẽ cho nó nở ra khi còn chỗ.
        columns = ("name", "size", "stt", "srt")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=6)
        self.tree.heading("name", text="Tên file")
        self.tree.heading("size", text="Dung lượng")
        self.tree.heading("stt", text="Nguyên ngữ (.stt)")
        self.tree.heading("srt", text="Phụ đề (.srt)")
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("size", width=95, anchor="e", stretch=False)
        self.tree.column("stt", width=150, anchor="w", stretch=False)
        self.tree.column("srt", width=150, anchor="w", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        for tag, color in STATUS_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)

    def _build_log(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Nhật ký", padding=4)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log_frame = frame

        ttk.Button(frame, text="Xoá nhật ký", command=self._clear_log).grid(
            row=0, column=0, columnspan=2, sticky="e", pady=(0, 3))

        self.log = tk.Text(frame, height=4, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        for level, color in LEVEL_COLORS.items():
            self.log.tag_configure(level, foreground=color)

    def _build_progress(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        frame.columnconfigure(1, weight=1)

        self.var_status = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(frame, textvariable=self.var_status).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.var_clock = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.var_clock, foreground=HINT_COLOR).grid(
            row=0, column=2, sticky="e", pady=(0, 4))

        ttk.Label(frame, text="File này:").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.bar_file = ttk.Progressbar(frame, maximum=100)
        self.bar_file.grid(row=1, column=1, sticky="ew")

        ttk.Label(frame, text="Tổng:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self.bar_total = ttk.Progressbar(frame, maximum=100)
        self.bar_total.grid(row=2, column=1, sticky="ew", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=2, rowspan=2, padx=(10, 0))
        self.btn_start = ttk.Button(buttons, text="Bắt đầu", command=self._start)
        self.btn_start.pack(fill="x")
        self.btn_stop = ttk.Button(buttons, text="Dừng", command=self._stop, state="disabled")
        self.btn_stop.pack(fill="x", pady=(4, 0))

    def _combo(self, parent, row, col, label, variable, values, callback=None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=4, padx=(0, 6))
        combo = ttk.Combobox(parent, textvariable=variable, values=values,
                             state="readonly", width=24)
        combo.grid(row=row, column=col + 1, sticky="ew", pady=4, padx=(0, 12))
        if callback is not None:
            combo.bind("<<ComboboxSelected>>", callback)

    def _hint(self, parent, text: str) -> ttk.Label:
        """Dòng chú thích mờ, tự xuống dòng theo bề rộng thật của cửa sổ."""
        label = ttk.Label(parent, text=text, foreground=HINT_COLOR, justify="left")
        label.bind(
            "<Configure>",
            lambda event, w=label: w.configure(wraplength=max(event.width - 8, 200)),
        )
        return label

    # ----- quét thư mục ---------------------------------------------------

    def _start_scan(self) -> None:
        """Quét ở thread riêng - thư mục trên ổ mạng có thể mất vài giây."""
        self.scan_generation += 1
        token = self.scan_generation

        folder = self.var_dir.get().strip()
        if not folder:
            self.entries = []
            self._render_tree()
            self.var_summary.set("Chưa chọn thư mục.")
            return

        self.var_summary.set("Đang quét thư mục...")
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
            self.entries = []
            self.by_path.clear()
            self.stt_state.clear()
            self.srt_state.clear()
            self.has_srt.clear()
            self._render_tree()
            self.var_summary.set(f"Không quét được: {result}")
            return

        self.entries = result
        self.by_path = {str(e.video): e for e in result}
        self.stt_state = {}
        self.srt_state = {}
        self.has_srt = {}
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
            # Bản cuối đã có mà không thấy .stt. Có thể app đã dọn sau khi dịch,
            # cũng có thể phụ đề do nơi khác làm - đừng khẳng định là "đã xoá".
            self.stt_state[key] = (STT_NONE, "have")
        else:
            self.stt_state[key] = (STT_TODO, "todo")

    @staticmethod
    def _row_tag(srt_tag: str, vi_tag: str) -> str:
        """Treeview chỉ tô màu được cả dòng, nên lấy trạng thái đáng chú ý nhất."""
        for tag in ("error", "running", "todo"):
            if srt_tag == tag or vi_tag == tag:
                return tag
        return "have"

    def _row_values(self, entry: Entry) -> tuple:
        key = str(entry.video)
        stt_text, _ = self.stt_state.get(key, (STT_TODO, "todo"))
        srt_text, _ = self.srt_state.get(key, (SRT_TODO, "todo"))
        return (entry.relative, format_size(entry.size), stt_text, srt_text)

    def _render_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())

        only_todo = self.var_only_todo.get()
        for entry in self.entries:
            key = str(entry.video)
            if only_todo and self.has_srt.get(key, False):
                continue
            _, stt_tag = self.stt_state.get(key, (STT_TODO, "todo"))
            _, srt_tag = self.srt_state.get(key, (SRT_TODO, "todo"))
            self.tree.insert(
                "", "end", iid=key,
                values=self._row_values(entry),
                tags=(self._row_tag(stt_tag, srt_tag),),
            )

        self._update_summary()

    def _update_summary(self) -> None:
        if not self.entries:
            self.var_summary.set("Không tìm thấy video nào trong thư mục.")
            return

        total_size = sum(e.size for e in self.entries)
        done = sum(1 for e in self.entries if self.has_srt.get(str(e.video), False))
        waiting = sum(1 for e in self.entries
                      if self.stt_state.get(str(e.video), ("", ""))[0] == STT_HAVE
                      and not self.has_srt.get(str(e.video), False))
        parts = [f"{len(self.entries)} video", format_size(total_size),
                 f"{done} xong", f"{len(self.entries) - done} chưa"]
        if waiting:
            parts.append(f"{waiting} chờ dịch")
        self.var_summary.set(" · ".join(parts))

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

            # Ẩn dòng đã xong nếu đang bật bộ lọc, ngược lại chỉ cập nhật tại chỗ.
            if self.var_only_todo.get() and self.has_srt.get(key, False) \
                    and row_tag not in ("running", "error"):
                self.tree.delete(key)
                self._update_summary()
                return
            self.tree.set(key, column, text)
            self.tree.item(key, tags=(row_tag,))
            self.tree.see(key)
        self._update_summary()

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
            messagebox.showinfo("Chưa có key", "Hãy dán ít nhất một API key.")
            return

        self.btn_models.configure(state="disabled")
        self._log("Đang lấy danh sách model từ Google...")

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
        """Hỏi Gemini xem từng key còn dùng được không."""
        keys = self._read_keys()
        if not keys:
            messagebox.showinfo("Chưa có key", "Hãy dán ít nhất một API key.")
            return

        self.btn_check_keys.configure(state="disabled")
        self._log(f"Đang kiểm tra {len(keys)} key...")

        def work() -> None:
            for position, key in enumerate(keys, start=1):
                tail = key[-4:] if len(key) >= 4 else "????"
                ok, note = check_key(key)
                self.events.put(pipeline.Event(
                    kind="log",
                    level="success" if ok else "error",
                    message=f"key #{position} (...{tail}): {note}",
                ))
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
        s.gemini_keys = self._read_keys()
        s.gemini_model = self.var_gemini_model.get().strip() or DEFAULT_GEMINI_MODEL
        s.translate_prompt = self.prompt_box.get("1.0", "end").strip()
        s.language = dict(LANGUAGES).get(self.var_language.get())
        s.chinese_variant = dict(CHINESE_VARIANTS).get(self.var_variant.get(), "s")
        s.panel_options_open = self.panel_options.expanded
        s.panel_translate_open = self.panel_translate.expanded
        s.hide_keys = self.keys_hidden
        return s

    def _on_window_resize(self, event) -> None:
        """Gom nhiều sự kiện resize liên tiếp thành một lần đo."""
        if event.widget is not self.master:
            return
        if self._fit_job is not None:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(150, self._fit_to_window)

    def _fit_to_window(self) -> None:
        """Cửa sổ không đủ cao thì gập bớt khung cho bảng danh sách có chỗ.

        Gập khung Dịch trước vì nó ít dùng thường xuyên hơn khung nhận dạng.
        Chỉ gập chứ không bao giờ tự mở, và không ghi đè lựa chọn đã lưu - kéo
        cửa sổ to ra rồi bấm tiêu đề là mở lại được.
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

        if folded and not self._fit_announced:
            self._fit_announced = True
            self._log(
                "Cửa sổ hơi thấp nên đã gập tạm: " + ", ".join(folded)
                + ". Bấm vào tiêu đề để mở lại, hoặc kéo cửa sổ cao hơn.",
                "info",
            )

    def _save_layout(self) -> None:
        """Ghi nhớ trạng thái gập/mở ngay để lần mở sau giữ nguyên."""
        self.settings.panel_options_open = self.panel_options.expanded
        self.settings.panel_translate_open = self.panel_translate.expanded
        self.settings.save()

    # ----- sự kiện giao diện ----------------------------------------------

    def _pick_dir(self) -> None:
        chosen = filedialog.askdirectory(title="Chọn thư mục chứa video",
                                         initialdir=self.var_dir.get() or None)
        if chosen:
            self.var_dir.set(chosen)
            self._start_scan()

    def _pick_model_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="Chọn thư mục lưu model",
            initialdir=resolve_model_dir(self.var_model_dir.get()),
        )
        if chosen:
            self.var_model_dir.set(chosen)

    def _reset_model_dir(self) -> None:
        self.var_model_dir.set("")
        self._log(f"Thư mục model trở về mặc định: {default_model_dir()}")

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
            self._log("Model này chỉ hiểu tiếng Anh, đã chuyển ngôn ngữ sang English.", "warn")
        self._refresh_advice()

    def _apply_preset(self, _event=None) -> None:
        """Đổ mẫu hướng dẫn vào ô để người dùng sửa tiếp."""
        text = dict(PROMPT_PRESETS).get(self.var_preset.get(), "")
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", text)

    def _refresh_advice(self) -> None:
        """Cập nhật câu gợi ý model cho ngôn ngữ đang chọn."""
        language = dict(LANGUAGES).get(self.var_language.get())
        text, level = model_advice(language, self.var_model.get())
        self.lbl_advice.configure(
            text=text,
            foreground=LEVEL_COLORS["warn"] if level == "warn" else HINT_COLOR,
        )

    def _busy(self, busy: bool) -> None:
        for button in (self.btn_start, self.btn_translate, self.btn_merge):
            button.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def _launch(self, action, status: str) -> None:
        """Chuẩn bị rồi chạy một công việc dài ở thread riêng."""
        settings = self._collect_settings()
        settings.save()
        self.cancel.clear()
        self.bar_file["value"] = 0
        self.bar_total["value"] = 0
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

    def _running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _folder_ready(self) -> bool:
        folder = self.var_dir.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Thiếu thư mục", "Hãy chọn một thư mục chứa video.")
            return False
        return True

    def _start(self) -> None:
        if self._running() or not self._folder_ready():
            return
        self._launch(pipeline.run, "Đang chuẩn bị...")

    def _translate_existing(self) -> None:
        """Dịch các file .srt có sẵn, không nhận dạng lại."""
        if self._running() or not self._folder_ready():
            return
        if not self._read_keys():
            messagebox.showerror("Thiếu API key", "Hãy dán ít nhất một API key của Gemini.")
            return
        self._launch(pipeline.translate_existing, "Đang dịch...")

    def _merge_videos(self) -> None:
        """Gộp toàn bộ video trong danh sách thành một file."""
        if self._running() or not self._folder_ready():
            return

        videos = [e.video for e in self.entries]
        if len(videos) < 2:
            messagebox.showinfo(
                "Chưa đủ video",
                "Cần ít nhất hai video trong danh sách mới có gì để gộp.")
            return

        # Dò trước để biết nối thẳng được hay phải mã hoá lại, rồi mới hỏi.
        try:
            _, can_copy, note = inspect(videos)
        except Exception as exc:
            messagebox.showerror("Không gộp được", str(exc))
            return

        folder = Path(self.var_dir.get().strip())
        suggested = default_output(folder)
        answer = messagebox.askokcancel(
            "Gộp video",
            f"Gộp {len(videos)} video theo đúng thứ tự trong bảng.\n\n{note}\n\n"
            f"Kết quả lưu vào:\n{suggested}\n\nTiếp tục?",
        )
        if not answer:
            return

        chosen = filedialog.asksaveasfilename(
            title="Lưu file gộp", defaultextension=".mp4",
            initialdir=str(suggested.parent), initialfile=suggested.name,
            filetypes=[("Video MP4", "*.mp4"), ("Tất cả", "*.*")],
        )
        if not chosen:
            return

        self.cancel.clear()
        self.bar_file["value"] = 0
        self.bar_total["value"] = 0
        self._busy(True)
        self._start_clock()
        self.var_status.set(f"Đang gộp {len(videos)} video...")
        self._log(note, "info" if can_copy else "warn")

        output = Path(chosen)

        def work() -> None:
            def report(fraction: float) -> None:
                self.events.put(pipeline.Event(
                    kind="file_progress", progress=fraction))

            try:
                result, summary = merge(
                    videos, output, on_progress=report, should_cancel=self.cancel.is_set)
            except MergeCancelled:
                self.events.put(pipeline.Event(
                    kind="finished", level="warn", message="Đã dừng, chưa gộp xong."))
                return
            except Exception as exc:
                self.events.put(pipeline.Event(
                    kind="finished", level="error", message=f"Gộp hỏng: {exc}"))
                return

            self.events.put(pipeline.Event(kind="log", level="success", message=summary))
            self.events.put(pipeline.Event(
                kind="finished", level="success",
                message=f"Đã gộp xong: {result.name}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Đang dừng, chờ file hiện tại kết thúc...")

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
            self._log(event.message, event.level)

        elif event.kind == "file_start":
            self.bar_file["value"] = 0
            self.var_status.set(f"[{event.index}/{event.total}] {event.message}")
            if event.stage == "translate":
                self._set_state(event.path, "srt", "Đang dịch...", "running")
            else:
                self._set_state(event.path, "stt", "Đang nhận dạng...", "running")

        elif event.kind == "file_progress":
            self.bar_file["value"] = event.progress * 100

        elif event.kind == "translating":
            self._set_state(event.path, "srt", "Đang dịch...", "running")
            self._log(event.message, event.level)

        elif event.kind == "keys_checked":
            self.btn_check_keys.configure(state="normal")

        elif event.kind == "file_done":
            self.bar_file["value"] = 100
            if event.total:
                self.bar_total["value"] = event.index / event.total * 100
            self._apply_file_done(event)
            self._log(f"[{event.index}/{event.total}] {event.message}", event.level)

        elif event.kind == "finished":
            if event.level == "success":
                self.bar_total["value"] = 100
            self.var_status.set(event.message)
            self._log(event.message, event.level)
            self._stop_clock()
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

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _start_clock(self) -> None:
        self.run_started = time.monotonic()
        self.started_at = datetime.now()
        self._tick()

    def _tick(self) -> None:
        """Cập nhật đồng hồ mỗi giây trong lúc đang chạy."""
        if self.run_started is None or not self.winfo_exists():
            return
        self.var_clock.set(
            f"Bắt đầu {self.started_at:%H:%M:%S} · "
            f"đã chạy {pipeline.format_duration(time.monotonic() - self.run_started)}"
        )
        self.timer_job = self.after(1000, self._tick)

    def _stop_clock(self) -> None:
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        if self.run_started is None:
            return
        total = pipeline.format_duration(time.monotonic() - self.run_started)
        finished = datetime.now()
        self.var_clock.set(
            f"Bắt đầu {self.started_at:%H:%M:%S} · kết thúc {finished:%H:%M:%S} · "
            f"tổng {total}"
        )
        self.run_started = None

    def _log(self, message: str, level: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self) -> None:
        if self._running():
            if not messagebox.askokcancel("Đang chạy", "Đang xử lý dở. Thoát luôn?"):
                return
            self.cancel.set()
        self._collect_settings().save()
        self.master.destroy()


def main() -> int:
    root = tk.Tk()
    root.title("VideoOCR - Quét video xuất phụ đề SRT")

    # Vừa với màn hình thật thay vì cố định một kích thước có thể tràn ra ngoài.
    height = min(880, max(560, root.winfo_screenheight() - 160))
    width = min(1000, max(760, root.winfo_screenwidth() - 120))
    root.geometry(f"{width}x{height}")
    # Đủ chỗ cho thư mục + hai tiêu đề gập + bảng danh sách + thanh tiến trình.
    root.minsize(720, 600)

    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0
