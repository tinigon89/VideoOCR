"""Cửa sổ điều khiển bằng tkinter.

Phần xử lý nặng chạy ở thread riêng và đẩy sự kiện qua queue; cửa sổ chỉ đọc
queue định kỳ. Nhờ vậy giao diện không bị đơ trong lúc nhận dạng.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
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
    Settings,
    default_model_dir,
    resolve_model_dir,
)
from .gpu import gpu_name
from .scanner import Entry, format_size, scan_entries
from .translator import SUGGESTED_MODELS, check_key

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

STATUS_TODO = "Chưa có SRT"
STATUS_HAVE = "Đã có SRT"
STATUS_HAVE_VI = "Đã có SRT + bản dịch"


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.settings = Settings.load()

        self.events: queue.Queue[pipeline.Event] = queue.Queue()
        self.scan_results: queue.Queue[tuple[int, list[Entry] | Exception]] = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        # Mỗi lần quét mang một số thứ tự; kết quả của lần quét cũ bị bỏ qua.
        # Nếu không, đổi thư mục lúc lần quét trước chưa xong sẽ hiện nhầm danh sách.
        self.scan_generation = 0

        # Trạng thái bảng. Khoá là đường dẫn tuyệt đối dạng chuỗi.
        self.entries: list[Entry] = []
        self.status: dict[str, tuple[str, str]] = {}   # khoá -> (chữ, tag màu)
        self.has_srt: dict[str, bool] = {}

        self._build()
        self._apply_settings()
        self.pack(fill="both", expand=True)
        self.after(POLL_INTERVAL_MS, self._drain)

        card = gpu_name()
        self._log(f"Card đồ hoạ: {card}" if card else
                  "Không dò được GPU NVIDIA - sẽ chạy bằng CPU và chậm hơn nhiều.",
                  "info" if card else "warn")

        if self.var_dir.get():
            self._start_scan()

    # ----- dựng giao diện -------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=3)   # bảng danh sách
        self.rowconfigure(5, weight=1)   # nhật ký

        self._build_folder()
        self._build_options()
        self._build_translate()
        self._build_list()
        self._build_progress()
        self._build_log()

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
            row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))

    def _build_options(self) -> None:
        frame = ttk.LabelFrame(self, text="Tuỳ chọn nhận dạng", padding=8)
        frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for col in (1, 3):
            frame.columnconfigure(col, weight=1)

        self.var_language = tk.StringVar()
        self._combo(frame, 0, 0, "Ngôn ngữ:", self.var_language,
                    [label for label, _ in LANGUAGES], self._on_language_change)

        self.var_model = tk.StringVar()
        self._combo(frame, 0, 2, "Model:", self.var_model, MODELS, self._on_model_change)

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
        model_row = ttk.Frame(frame)
        model_row.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(0, 12))
        model_row.columnconfigure(0, weight=1)

        self.var_model_dir = tk.StringVar()
        ttk.Entry(model_row, textvariable=self.var_model_dir).grid(row=0, column=0, sticky="ew")
        ttk.Button(model_row, text="Chọn...", command=self._pick_model_dir).grid(
            row=0, column=1, padx=(6, 0))
        ttk.Button(model_row, text="Mặc định", command=self._reset_model_dir).grid(
            row=0, column=2, padx=(4, 0))

        ttk.Label(frame, foreground="#666666", text=(
            "Để trống là dùng thư mục models\\ cạnh app. Trỏ vào cache HuggingFace "
            "nếu muốn dùng lại model đã tải cho phần mềm khác."
        )).grid(row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))

    def _build_translate(self) -> None:
        frame = ttk.LabelFrame(self, text="Dịch sang tiếng Việt (Google Gemini)", padding=8)
        frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(1, weight=1)

        self.var_translate = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Dịch tự động sau khi nhận dạng xong (xuất ra phim.vi.srt)",
            variable=self.var_translate,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="API key:").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.keys_box = tk.Text(frame, height=3, wrap="none", font=("Consolas", 9))
        self.keys_box.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        keys_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.keys_box.yview)
        keys_scroll.grid(row=1, column=2, sticky="ns", pady=(6, 0))
        self.keys_box.configure(yscrollcommand=keys_scroll.set)

        ttk.Label(frame, foreground="#666666", text=(
            "Mỗi dòng một key. Hết hạn mức hoặc key hỏng thì tự chuyển sang key kế tiếp. "
            "Lưu ý: Google tính hạn mức theo project, nhiều key cùng project vẫn chung quota."
        )).grid(row=2, column=1, columnspan=2, sticky="w", pady=(2, 0))

        bottom = ttk.Frame(frame)
        bottom.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(1, weight=1)

        ttk.Label(bottom, text="Model Gemini:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.var_gemini_model = tk.StringVar(value=DEFAULT_GEMINI_MODEL)
        ttk.Combobox(bottom, textvariable=self.var_gemini_model,
                     values=SUGGESTED_MODELS, width=24).grid(row=0, column=1, sticky="w")

        self.btn_check_keys = ttk.Button(bottom, text="Kiểm tra key",
                                         command=self._check_keys)
        self.btn_check_keys.grid(row=0, column=2, padx=(8, 0))
        self.btn_translate = ttk.Button(bottom, text="Dịch các SRT đã có",
                                        command=self._translate_existing)
        self.btn_translate.grid(row=0, column=3, padx=(4, 0))

    def _build_list(self) -> None:
        frame = ttk.LabelFrame(self, text="Danh sách video", padding=8)
        frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)

        self.var_summary = tk.StringVar(value="Chưa chọn thư mục.")
        ttk.Label(header, textvariable=self.var_summary).grid(row=0, column=0, sticky="w")

        self.var_only_todo = tk.BooleanVar(value=False)
        ttk.Checkbutton(header, text="Chỉ hiện file chưa có SRT",
                        variable=self.var_only_todo,
                        command=self._render_tree).grid(row=0, column=1, sticky="e")

        columns = ("name", "size", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        self.tree.heading("name", text="Tên file")
        self.tree.heading("size", text="Dung lượng")
        self.tree.heading("status", text="Trạng thái")
        self.tree.column("name", width=420, anchor="w")
        self.tree.column("size", width=110, anchor="e", stretch=False)
        self.tree.column("status", width=210, anchor="w", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        for tag, color in STATUS_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)

    def _build_progress(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(1, weight=1)

        self.var_status = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(frame, textvariable=self.var_status).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

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

    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self, text="Nhật ký", padding=4)
        frame.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.log = tk.Text(frame, height=7, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        for level, color in LEVEL_COLORS.items():
            self.log.tag_configure(level, foreground=color)

    def _combo(self, parent, row, col, label, variable, values, callback=None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=4, padx=(0, 6))
        combo = ttk.Combobox(parent, textvariable=variable, values=values,
                             state="readonly", width=24)
        combo.grid(row=row, column=col + 1, sticky="ew", pady=4, padx=(0, 12))
        if callback is not None:
            combo.bind("<<ComboboxSelected>>", callback)

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
            self.status.clear()
            self.has_srt.clear()
            self._render_tree()
            self.var_summary.set(f"Không quét được: {result}")
            return

        self.entries = result
        self.status = {}
        self.has_srt = {}
        for entry in result:
            key = str(entry.video)
            self.has_srt[key] = entry.has_srt
            if entry.has_srt:
                label = STATUS_HAVE_VI if entry.has_vi else STATUS_HAVE
                self.status[key] = (label, "have")
            else:
                self.status[key] = (STATUS_TODO, "todo")

        self._render_tree()

    def _render_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())

        only_todo = self.var_only_todo.get()
        for entry in self.entries:
            key = str(entry.video)
            if only_todo and self.has_srt.get(key, False):
                continue
            text, tag = self.status.get(key, (STATUS_TODO, "todo"))
            self.tree.insert(
                "", "end", iid=key,
                values=(entry.relative, format_size(entry.size), text),
                tags=(tag,),
            )

        self._update_summary()

    def _update_summary(self) -> None:
        if not self.entries:
            self.var_summary.set("Không tìm thấy video nào trong thư mục.")
            return

        total_size = sum(e.size for e in self.entries)
        done = sum(1 for e in self.entries if self.has_srt.get(str(e.video), False))
        missing = len(self.entries) - done
        self.var_summary.set(
            f"{len(self.entries)} video · {format_size(total_size)} · "
            f"{done} đã có SRT · {missing} chưa có"
        )

    def _set_status(self, path: Path | None, text: str, tag: str,
                    has_srt: bool | None = None) -> None:
        if path is None:
            return
        key = str(path)
        self.status[key] = (text, tag)
        if has_srt is not None:
            self.has_srt[key] = has_srt

        if self.tree.exists(key):
            # Ẩn dòng đã xong nếu đang bật bộ lọc, ngược lại chỉ cập nhật tại chỗ.
            if self.var_only_todo.get() and self.has_srt.get(key, False) and tag != "running":
                self.tree.delete(key)
                self._update_summary()
                return
            self.tree.set(key, "status", text)
            self.tree.item(key, tags=(tag,))
            self.tree.see(key)
        self._update_summary()

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
        self.var_gemini_model.set(s.gemini_model or DEFAULT_GEMINI_MODEL)
        self.keys_box.delete("1.0", "end")
        self.keys_box.insert("1.0", "\n".join(s.gemini_keys))

        self.var_language.set(next(
            (label for label, code in LANGUAGES if code == s.language), LANGUAGES[0][0]))
        self.var_variant.set(next(
            (label for label, code in CHINESE_VARIANTS if code == s.chinese_variant),
            CHINESE_VARIANTS[0][0]))

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
        s.gemini_keys = self._read_keys()
        s.gemini_model = self.var_gemini_model.get().strip() or DEFAULT_GEMINI_MODEL
        s.language = dict(LANGUAGES).get(self.var_language.get())
        s.chinese_variant = dict(CHINESE_VARIANTS).get(self.var_variant.get(), "s")
        return s

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

    def _on_model_change(self, _event=None) -> None:
        code = dict(LANGUAGES).get(self.var_language.get())
        if self.var_model.get() in ENGLISH_ONLY_MODELS and code != "en":
            self.var_language.set(next(
                label for label, lang in LANGUAGES if lang == "en"))
            self._log("Model này chỉ hiểu tiếng Anh, đã chuyển ngôn ngữ sang English.", "warn")

    def _start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return

        settings = self._collect_settings()
        if not settings.input_dir or not Path(settings.input_dir).is_dir():
            messagebox.showerror("Thiếu thư mục", "Hãy chọn một thư mục chứa video.")
            return

        settings.save()
        self.cancel.clear()
        self.bar_file["value"] = 0
        self.bar_total["value"] = 0
        self.btn_start.configure(state="disabled")
        self.btn_translate.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.var_status.set("Đang chuẩn bị...")

        self.worker = threading.Thread(target=self._work, args=(settings,), daemon=True)
        self.worker.start()

    def _read_keys(self) -> list[str]:
        raw = self.keys_box.get("1.0", "end")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _check_keys(self) -> None:
        """Hỏi Gemini xem từng key còn dùng được không và có model nào."""
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

    def _translate_existing(self) -> None:
        """Dịch các file .srt có sẵn, không nhận dạng lại."""
        if self.worker is not None and self.worker.is_alive():
            return

        settings = self._collect_settings()
        if not settings.input_dir or not Path(settings.input_dir).is_dir():
            messagebox.showerror("Thiếu thư mục", "Hãy chọn một thư mục chứa video.")
            return
        if not settings.gemini_keys:
            messagebox.showerror("Thiếu API key", "Hãy dán ít nhất một API key của Gemini.")
            return

        settings.save()
        self.cancel.clear()
        self.bar_file["value"] = 0
        self.bar_total["value"] = 0
        self.btn_start.configure(state="disabled")
        self.btn_translate.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.var_status.set("Đang dịch...")

        def work() -> None:
            try:
                pipeline.translate_existing(settings, emit=self.events.put, cancel=self.cancel)
            except Exception as exc:
                self.events.put(pipeline.Event(kind="finished", level="error",
                                               message=f"Dừng vì lỗi: {exc}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Đang dừng, chờ file hiện tại kết thúc...")

    def _work(self, settings: Settings) -> None:
        try:
            pipeline.run(settings, emit=self.events.put, cancel=self.cancel)
        except Exception as exc:
            self.events.put(pipeline.Event(kind="finished", level="error",
                                           message=f"Dừng vì lỗi: {exc}"))

    # ----- nhận sự kiện từ thread xử lý -----------------------------------

    def _drain(self) -> None:
        try:
            while True:
                self._apply_scan(*self.scan_results.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(POLL_INTERVAL_MS, self._drain)

    def _handle(self, event: pipeline.Event) -> None:
        if event.kind == "scan":
            self._log(event.message, event.level)

        elif event.kind == "file_start":
            self.bar_file["value"] = 0
            self.var_status.set(f"[{event.index}/{event.total}] {event.message}")
            self._set_status(event.path, "Đang xử lý...", "running")

        elif event.kind == "file_progress":
            self.bar_file["value"] = event.progress * 100

        elif event.kind == "file_done":
            self.bar_file["value"] = 100
            if event.total:
                self.bar_total["value"] = event.index / event.total * 100
            if event.level == "error":
                self._set_status(event.path, "Lỗi", "error", has_srt=False)
            else:
                suffix = " + dịch" if "đã dịch" in event.message else ""
                self._set_status(event.path, f"Xong · {event.cues} khối{suffix}",
                                 "have", has_srt=True)
            self._log(f"[{event.index}/{event.total}] {event.message}", event.level)

        elif event.kind == "translating":
            self._set_status(event.path, "Đang dịch...", "running")
            self._log(event.message, event.level)

        elif event.kind == "keys_checked":
            self.btn_check_keys.configure(state="normal")

        elif event.kind == "finished":
            if event.level == "success":
                self.bar_total["value"] = 100
            self.var_status.set(event.message)
            self._log(event.message, event.level)
            self.btn_start.configure(state="normal")
            self.btn_translate.configure(state="normal")
            self.btn_stop.configure(state="disabled")

        else:
            self._log(event.message, event.level)

    def _log(self, message: str, level: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askokcancel("Đang chạy", "Đang xử lý dở. Thoát luôn?"):
                return
            self.cancel.set()
        self._collect_settings().save()
        self.master.destroy()


def main() -> int:
    root = tk.Tk()
    root.title("VideoOCR - Quét video xuất phụ đề SRT")
    root.geometry("900x760")
    root.minsize(780, 640)

    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0
