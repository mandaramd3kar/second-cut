from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from engine import (
    DEFAULT_ADD_VIDEO_THUMBNAILS,
    DEFAULT_MIN_IMAGE_MEGABYTES,
    DEFAULT_MIN_VIDEO_MEGABYTES_PER_10_SECONDS,
    DEFAULT_VIDEO_PRESET,
    DEFAULT_VIDEO_QUALITY,
    MEDIA_MODE_BOTH,
    MEDIA_MODE_IMAGES,
    MEDIA_MODE_VIDEOS,
    VIDEO_PRESET_OPTIONS,
    AppSettings,
    DeleteResult,
    ScanProgress,
    ProcessProgress,
    ProcessResult,
    ScanResult,
    add_thumbnails_root,
    delete_archive,
    process_root,
    scan_root,
)
from media_tools import MediaToolError


class ShrinkMediaApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Second Cut")
        self.root.geometry("1280x820")
        self.root.minsize(1024, 680)

        self.selected_root = tk.StringVar()
        self.log_search_var = tk.StringVar()
        self.media_mode_var = tk.StringVar(value=MEDIA_MODE_BOTH)
        self.min_image_mb_var = tk.DoubleVar(value=DEFAULT_MIN_IMAGE_MEGABYTES)
        self.min_video_mb_per_10s_var = tk.DoubleVar(value=DEFAULT_MIN_VIDEO_MEGABYTES_PER_10_SECONDS)
        self.video_preset_index_var = tk.IntVar(value=VIDEO_PRESET_OPTIONS.index(DEFAULT_VIDEO_PRESET))
        self.video_quality_var = tk.IntVar(value=DEFAULT_VIDEO_QUALITY)
        self.add_video_thumbnails_var = tk.BooleanVar(value=DEFAULT_ADD_VIDEO_THUMBNAILS)
        self.image_threshold_label_var = tk.StringVar()
        self.video_threshold_label_var = tk.StringVar()
        self.video_preset_label_var = tk.StringVar()
        self.video_quality_label_var = tk.StringVar()
        self.video_codec_path_label_var = tk.StringVar(
            value="Video codec path: hevc_qsv first, libx265 fallback. Decoding is auto-selected by ffmpeg from the source media."
        )
        self.status_var = tk.StringVar(value="Choose a folder, then scan or process it.")
        self.summary_vars = {
            "images": tk.StringVar(value="0"),
            "videos": tk.StringVar(value="0"),
            "scanned": tk.StringVar(value="0"),
            "shrunk": tk.StringVar(value="0"),
            "skipped": tk.StringVar(value="0"),
            "failed": tk.StringVar(value="0"),
            "archived": tk.StringVar(value="0"),
            "thumbnails": tk.StringVar(value="0"),
            "saved": tk.StringVar(value="0.00 MB"),
        }

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.advanced_window: tk.Toplevel | None = None
        self.results_menu: tk.Menu | None = None
        self._tree_sort_state: dict[str, bool] = {}
        self._log_search_index = "1.0"
        self._saved_bytes_total = 0
        self._refresh_advanced_labels()
        self._configure_style()
        self._build_ui()
        self.root.after(100, self._poll_queue)

    def _configure_style(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Status.TLabel", padding=(10, 6))

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        root_row = ttk.Frame(frame)
        root_row.pack(fill=tk.X)

        ttk.Label(root_row, text="Folder").pack(side=tk.LEFT)
        ttk.Entry(root_row, textvariable=self.selected_root).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(root_row, text="Browse", command=self._choose_folder).pack(side=tk.LEFT)

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, pady=(12, 12))

        self.scan_button = ttk.Button(button_row, text="Scan", command=self._start_scan)
        self.scan_button.pack(side=tk.LEFT)

        self.process_button = ttk.Button(button_row, text="Process", command=self._start_process)
        self.process_button.pack(side=tk.LEFT, padx=8)

        self.thumbnail_button = ttk.Button(button_row, text="Add Thumbnails", command=self._start_thumbnails)
        self.thumbnail_button.pack(side=tk.LEFT, padx=(0, 8))

        self.delete_button = ttk.Button(button_row, text="Delete .to-be-deleted", command=self._start_delete)
        self.delete_button.pack(side=tk.LEFT)

        ttk.Separator(button_row, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        ttk.Label(button_row, text="Mode").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(button_row, text="Both", value=MEDIA_MODE_BOTH, variable=self.media_mode_var).pack(side=tk.LEFT)
        ttk.Radiobutton(button_row, text="Images only", value=MEDIA_MODE_IMAGES, variable=self.media_mode_var).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Radiobutton(button_row, text="Videos only", value=MEDIA_MODE_VIDEOS, variable=self.media_mode_var).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(button_row, text="Advanced...", command=self._open_advanced_settings).pack(side=tk.RIGHT)

        video_options_row = ttk.Frame(frame)
        video_options_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Checkbutton(
            video_options_row,
            text="Add a thumbnail after compacting each video",
            variable=self.add_video_thumbnails_var,
        ).pack(side=tk.LEFT)
        ttk.Label(
            video_options_row,
            text="The Add Thumbnails button processes existing MP4s without compacting them.",
        ).pack(side=tk.LEFT, padx=(16, 0))

        summary = ttk.LabelFrame(frame, text="Summary", padding=12)
        summary.pack(fill=tk.X)

        summary_items = [
            ("Images", "images"),
            ("Videos", "videos"),
            ("Scanned", "scanned"),
            ("Shrunk", "shrunk"),
            ("Skipped", "skipped"),
            ("Failed", "failed"),
            ("Archived", "archived"),
            ("Thumbnails", "thumbnails"),
            ("Saved", "saved"),
        ]
        for index, (label, key) in enumerate(summary_items):
            row = index // 5
            column = (index % 5) * 2
            ttk.Label(summary, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=2)
            ttk.Label(summary, textvariable=self.summary_vars[key], width=8).grid(
                row=row, column=column + 1, sticky="w", padx=(0, 16), pady=2
            )

        content_pane = ttk.Panedwindow(frame, orient=tk.VERTICAL)
        content_pane.pack(fill=tk.BOTH, expand=True, pady=(12, 12))

        results_frame = ttk.LabelFrame(content_pane, text="Results", padding=12)
        content_pane.add(results_frame, weight=3)

        ttk.Label(
            results_frame,
            text="Scroll vertically or horizontally to inspect the full result list.",
        ).pack(anchor="w", pady=(0, 8))

        columns = ("status", "action", "kind", "size", "rate", "new_size", "new_rate", "resolution", "new_resolution", "path", "detail")
        tree_container = ttk.Frame(results_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)

        self.results_tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=16)
        for name, title, width in (
            ("status", "Status", 45),
            ("action", "Action", 72),
            ("kind", "Type", 60),
            ("size", "Size", 90),
            ("rate", "MB/10s", 100),
            ("new_size", "New Size", 90),
            ("new_rate", "New MB/10s", 100),
            ("resolution", "Res", 95),
            ("new_resolution", "New Res", 95),
            ("path", "Path", 310),
            ("detail", "Detail", 460),
        ):
            self.results_tree.heading(name, text=title, anchor=tk.W, command=lambda column=name: self._sort_results_by(column))
            self.results_tree.column(name, width=width, minwidth=width, anchor=tk.W, stretch=name in {"path", "detail"})

        tree_scroll_y = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.results_tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.results_tree.xview)
        self.results_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)
        self.results_tree.bind("<Control-c>", self._copy_selected_results)
        self.results_tree.bind("<Button-3>", self._show_results_context_menu)
        self.results_menu = tk.Menu(self.root, tearoff=0)
        self.results_menu.add_command(label="Copy", command=self._copy_selected_results)

        log_frame = ttk.LabelFrame(content_pane, text="Log", padding=12)
        content_pane.add(log_frame, weight=2)

        log_search_row = ttk.Frame(log_frame)
        log_search_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(log_search_row, text="Search").pack(side=tk.LEFT)
        log_search_entry = ttk.Entry(log_search_row, textvariable=self.log_search_var)
        log_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        log_search_entry.bind("<Return>", lambda _event: self._find_next_log())
        ttk.Button(log_search_row, text="Find Next", command=self._find_next_log).pack(side=tk.LEFT)
        ttk.Button(log_search_row, text="Clear", command=self._clear_log_search).pack(side=tk.LEFT, padx=(8, 0))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("search_match", background="#fff2a8")
        self.log_text.tag_configure("search_current", background="#ffd24d")
        self.log_search_var.trace_add("write", self._on_log_search_changed)

        status_bar = ttk.Label(frame, textvariable=self.status_var, style="Status.TLabel", relief=tk.SUNKEN, anchor="w")
        status_bar.pack(fill=tk.X)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose a folder to scan and process")
        if selected:
            self.selected_root.set(selected)

    def _start_scan(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._clear_results()
        self._append_log(f"Scanning {folder}")
        self.status_var.set("Scanning folder tree...")
        self._run_worker("scan", lambda: scan_root(folder, self._build_settings(), progress=self._queue_scan_progress))

    def _start_process(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._clear_results()
        self._append_log(f"Processing {folder}")
        self.status_var.set("Processing files in the background...")
        self._run_worker(
            "process",
            lambda: process_root(folder, self._build_settings(), log=self._queue_log, progress=self._queue_progress),
        )

    def _start_thumbnails(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._clear_results()
        self._append_log(f"Adding missing MP4 thumbnails under {folder}")
        self.status_var.set("Inspecting MP4 files and adding missing thumbnails...")
        self._run_worker(
            "thumbnails",
            lambda: add_thumbnails_root(folder, log=self._queue_log, progress=self._queue_progress),
        )

    def _start_delete(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        if not messagebox.askyesno(
            title="Delete .to-be-deleted",
            message=f"Delete {Path(folder) / '.to-be-deleted'} ?",
        ):
            return
        self._append_log(f"Deleting archive folder under {folder}")
        self.status_var.set("Deleting .to-be-deleted...")
        self._run_worker("delete", lambda: delete_archive(folder))

    def _require_folder(self) -> str | None:
        folder = self.selected_root.get().strip()
        if not folder:
            messagebox.showerror(title="No folder selected", message="Choose a folder first.")
            return None
        if not Path(folder).exists():
            messagebox.showerror(title="Folder not found", message=f"Folder not found:\n{folder}")
            return None
        return folder

    def _run_worker(self, action: str, fn) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(title="Busy", message="Wait for the current action to finish.")
            return

        self._set_busy(True)

        def worker() -> None:
            try:
                result = fn()
                self.queue.put(("result", (action, result)))
            except Exception as exc:
                self.queue.put(("error", exc))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _queue_log(self, message: str) -> None:
        self.queue.put(("log", message))

    def _queue_progress(self, progress: ProcessProgress) -> None:
        self.queue.put(("progress", progress))

    def _queue_scan_progress(self, progress: ScanProgress) -> None:
        self.queue.put(("scan_progress", progress))

    def _poll_queue(self) -> None:
        while True:
            try:
                event_type, payload = self.queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(str(payload))
            elif event_type == "scan_progress":
                self._handle_scan_progress(payload)
            elif event_type == "progress":
                self._handle_progress(payload)
            elif event_type == "error":
                self._set_busy(False)
                self.status_var.set("Action failed.")
                self._append_log(f"Error: {payload}")
                messagebox.showerror(title="Action failed", message=str(payload))
            elif event_type == "result":
                self._set_busy(False)
                action, result = payload
                self._handle_result(action, result)

        self.root.after(100, self._poll_queue)

    def _handle_result(self, action: str, result: object) -> None:
        if action == "scan" and isinstance(result, ScanResult):
            self.summary_vars["images"].set(str(result.image_count))
            self.summary_vars["videos"].set(str(result.video_count))
            self.summary_vars["scanned"].set(str(len(result.work_items)))
            self.summary_vars["saved"].set("0.00 MB")
            self.status_var.set(
                f"Scan complete. Qualified {len(result.work_items)} of {len(result.items)} supported file(s)."
            )
            self._append_log(f"Scan complete. Qualified {len(result.work_items)} of {len(result.items)} supported file(s).")
            return

        if action == "process" and isinstance(result, ProcessResult):
            self.summary_vars["images"].set(str(result.image_count))
            self.summary_vars["videos"].set(str(result.video_count))
            self.summary_vars["scanned"].set(str(result.scanned))
            self.summary_vars["shrunk"].set(str(result.shrunk))
            self.summary_vars["skipped"].set(str(result.skipped))
            self.summary_vars["failed"].set(str(result.failed))
            self.summary_vars["archived"].set(str(result.archived))
            self.summary_vars["thumbnails"].set(str(result.thumbnails_added))
            self._saved_bytes_total = self._calculate_saved_bytes_from_results(result.results)
            self.summary_vars["saved"].set(self._format_size_delta(self._saved_bytes_total))
            self.status_var.set(
                f"Process complete. Shrunk={result.shrunk}, Skipped={result.skipped}, Failed={result.failed}, Archived={result.archived}"
            )
            self._append_log(
                f"Process complete. Shrunk={result.shrunk}, Skipped={result.skipped}, Failed={result.failed}, Archived={result.archived}"
            )
            return

        if action == "thumbnails" and isinstance(result, ProcessResult):
            self.summary_vars["images"].set("0")
            self.summary_vars["videos"].set(str(result.video_count))
            self.summary_vars["scanned"].set(str(result.scanned))
            self.summary_vars["shrunk"].set("0")
            self.summary_vars["skipped"].set(str(result.skipped))
            self.summary_vars["failed"].set(str(result.failed))
            self.summary_vars["archived"].set(str(result.archived))
            self.summary_vars["thumbnails"].set(str(result.thumbnails_added))
            self._saved_bytes_total = self._calculate_saved_bytes_from_results(result.results)
            self.summary_vars["saved"].set(self._format_size_delta(self._saved_bytes_total))
            self.status_var.set(
                "Thumbnail pass complete. "
                f"Added={result.thumbnails_added}, Skipped={result.skipped}, Failed={result.failed}"
            )
            self._append_log(
                "Thumbnail pass complete. "
                f"Added={result.thumbnails_added}, Skipped={result.skipped}, Failed={result.failed}, "
                f"Archived={result.archived}"
            )
            return

        if action == "delete" and isinstance(result, DeleteResult):
            self.status_var.set(result.detail)
            self._append_log(result.detail)
            messagebox.showinfo(title="Delete .to-be-deleted", message=result.detail)
            return

    def _populate_scan_results(self, result: ScanResult) -> None:
        for item in result.items:
            self._insert_scan_item(item)

    def _populate_process_results(self, result: ProcessResult) -> None:
        for item in result.results:
            self._insert_process_result(item)

    def _handle_progress(self, progress: ProcessProgress) -> None:
        self.summary_vars["images"].set(str(progress.image_count))
        self.summary_vars["videos"].set(str(progress.video_count))
        self.summary_vars["scanned"].set(str(progress.scanned))
        self.summary_vars["shrunk"].set(str(progress.shrunk))
        self.summary_vars["skipped"].set(str(progress.skipped))
        self.summary_vars["failed"].set(str(progress.failed))
        self.summary_vars["archived"].set(str(progress.archived))
        self.summary_vars["thumbnails"].set(str(progress.thumbnails_added))
        self._saved_bytes_total += self._saved_bytes_for_item(progress.latest_result)
        self.summary_vars["saved"].set(self._format_size_delta(self._saved_bytes_total))
        self._insert_process_result(progress.latest_result)

        if progress.phase == "cleanup":
            self.status_var.set("Resolving legacy tmp-/new- leftovers...")
        elif progress.phase == "thumbnail":
            self.status_var.set(
                f"Checked {progress.completed} of {progress.scanned} MP4s. "
                f"Added={progress.thumbnails_added}, Skipped={progress.skipped}, Failed={progress.failed}"
            )
        else:
            self.status_var.set(
                f"Processed {progress.completed} of {progress.scanned}. "
                f"Shrunk={progress.shrunk}, Skipped={progress.skipped}, Failed={progress.failed}, Archived={progress.archived}"
            )

    def _handle_scan_progress(self, progress: ScanProgress) -> None:
        self.summary_vars["images"].set(str(progress.image_count))
        self.summary_vars["videos"].set(str(progress.video_count))
        self.summary_vars["scanned"].set(str(progress.scanned))
        self._insert_scan_item(progress.latest_item)
        self.status_var.set(
            f"Scanning... checked {progress.scanned} file(s). Qualified Images={progress.image_count}, Videos={progress.video_count}"
        )

    def _insert_scan_item(self, item) -> None:
        self.results_tree.insert(
            "",
            tk.END,
            values=(
                "ready" if item.ready else "skipped",
                "scan",
                item.media_kind,
                self._format_size(item.size_bytes),
                self._format_rate(item.mb_per_10_seconds),
                "",
                "",
                self._format_resolution(item.resolution),
                "",
                self._format_display_path(item.source_path),
                item.detail,
            ),
        )

    def _insert_process_result(self, item) -> None:
        self.results_tree.insert(
            "",
            tk.END,
            values=(
                item.status,
                item.action,
                item.media_kind,
                self._format_size(item.size_bytes),
                self._format_rate(item.mb_per_10_seconds),
                self._format_size(item.reduced_size_bytes),
                self._format_rate(item.reduced_mb_per_10_seconds),
                self._format_resolution(item.resolution),
                self._format_resolution(item.reduced_resolution),
                self._format_display_path(item.source_path),
                item.detail,
            ),
        )

    def _format_display_path(self, path: Path) -> str:
        folder = self.selected_root.get().strip()
        if not folder:
            return str(path)
        try:
            return str(path.resolve().relative_to(Path(folder).resolve()))
        except ValueError:
            return str(path)

    def _sort_results_by(self, column: str) -> None:
        descending = self._tree_sort_state.get(column, False)
        items = [(self.results_tree.set(item_id, column), item_id) for item_id in self.results_tree.get_children("")]
        items.sort(key=lambda pair: self._sort_value(pair[0]), reverse=descending)

        for index, (_, item_id) in enumerate(items):
            self.results_tree.move(item_id, "", index)

        self._tree_sort_state[column] = not descending

    def _sort_value(self, value: str):
        stripped = value.strip()
        if stripped.endswith(" MB"):
            stripped = stripped[:-3]
        try:
            return (0, float(stripped))
        except ValueError:
            return (1, value.lower())

    def _build_settings(self) -> AppSettings:
        return AppSettings(
            media_mode=self.media_mode_var.get(),
            min_image_megabytes=round(self.min_image_mb_var.get(), 1),
            min_video_megabytes_per_10_seconds=round(self.min_video_mb_per_10s_var.get(), 1),
            video_preset=VIDEO_PRESET_OPTIONS[self.video_preset_index_var.get()],
            video_quality=int(self.video_quality_var.get()),
            add_video_thumbnails=bool(self.add_video_thumbnails_var.get()),
        )

    def _open_advanced_settings(self) -> None:
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            self.advanced_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        window.title("Advanced Settings")
        window.transient(self.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._close_advanced_settings)
        self.advanced_window = window

        frame = ttk.Frame(window, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Scan thresholds and video encoding").pack(anchor="w")
        ttk.Label(
            frame,
            text="These settings affect which files qualify during scan and how videos are encoded during processing.",
            wraplength=460,
        ).pack(anchor="w", pady=(4, 12))

        ttk.Label(frame, textvariable=self.image_threshold_label_var).pack(anchor="w")
        tk.Scale(
            frame,
            from_=0.1,
            to=20.0,
            resolution=0.1,
            orient=tk.HORIZONTAL,
            length=460,
            variable=self.min_image_mb_var,
            command=self._on_advanced_scale_changed,
        ).pack(anchor="w")

        ttk.Label(frame, textvariable=self.video_threshold_label_var).pack(anchor="w", pady=(10, 0))
        tk.Scale(
            frame,
            from_=0.1,
            to=20.0,
            resolution=0.1,
            orient=tk.HORIZONTAL,
            length=460,
            variable=self.min_video_mb_per_10s_var,
            command=self._on_advanced_scale_changed,
        ).pack(anchor="w")

        ttk.Label(frame, textvariable=self.video_preset_label_var).pack(anchor="w", pady=(10, 0))
        tk.Scale(
            frame,
            from_=0,
            to=len(VIDEO_PRESET_OPTIONS) - 1,
            resolution=1,
            orient=tk.HORIZONTAL,
            showvalue=False,
            length=460,
            variable=self.video_preset_index_var,
            command=self._on_advanced_scale_changed,
        ).pack(anchor="w")

        ttk.Label(frame, textvariable=self.video_quality_label_var).pack(anchor="w", pady=(10, 0))
        tk.Scale(
            frame,
            from_=20,
            to=40,
            resolution=1,
            orient=tk.HORIZONTAL,
            length=460,
            variable=self.video_quality_var,
            command=self._on_advanced_scale_changed,
        ).pack(anchor="w")

        ttk.Label(
            frame,
            textvariable=self.video_codec_path_label_var,
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(12, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(actions, text="Reset Defaults", command=self._reset_advanced_defaults).pack(side=tk.LEFT)
        ttk.Button(actions, text="Close", command=self._close_advanced_settings).pack(side=tk.RIGHT)

        self._refresh_advanced_labels()
        self._center_window(window, width=520, height=420)
        window.grab_set()

    def _close_advanced_settings(self) -> None:
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            self.advanced_window.destroy()
        self.advanced_window = None

    def _reset_advanced_defaults(self) -> None:
        self.min_image_mb_var.set(DEFAULT_MIN_IMAGE_MEGABYTES)
        self.min_video_mb_per_10s_var.set(DEFAULT_MIN_VIDEO_MEGABYTES_PER_10_SECONDS)
        self.video_preset_index_var.set(VIDEO_PRESET_OPTIONS.index(DEFAULT_VIDEO_PRESET))
        self.video_quality_var.set(DEFAULT_VIDEO_QUALITY)
        self.add_video_thumbnails_var.set(DEFAULT_ADD_VIDEO_THUMBNAILS)
        self._refresh_advanced_labels()

    def _on_advanced_scale_changed(self, _value: str) -> None:
        self._refresh_advanced_labels()

    def _refresh_advanced_labels(self) -> None:
        self.image_threshold_label_var.set(
            f"Minimum image size to qualify: {self.min_image_mb_var.get():.1f} MB"
        )
        self.video_threshold_label_var.set(
            f"Minimum video density to qualify: {self.min_video_mb_per_10s_var.get():.1f} MB per 10 seconds"
        )
        self.video_preset_label_var.set(
            f"Video preset: {VIDEO_PRESET_OPTIONS[self.video_preset_index_var.get()]}"
        )
        self.video_quality_label_var.set(f"Video quality: {int(self.video_quality_var.get())}")

    def _format_size(self, size_bytes: int | None) -> str:
        if size_bytes is None:
            return ""
        return f"{size_bytes / (1024 * 1024):.2f} MB"

    def _format_rate(self, mb_per_10_seconds: float | None) -> str:
        if mb_per_10_seconds is None:
            return ""
        return f"{mb_per_10_seconds:.2f}"

    def _format_resolution(self, resolution: tuple[int, int] | None) -> str:
        if resolution is None:
            return ""
        return f"{resolution[0]} x {resolution[1]}"

    def _center_window(self, window: tk.Toplevel, width: int, height: int) -> None:
        self.root.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()
        x = root_x + max((root_width - width) // 2, 0)
        y = root_y + max((root_height - height) // 2, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _saved_bytes_for_item(self, item) -> int:
        if item.size_bytes is None or item.reduced_size_bytes is None:
            return 0
        return item.size_bytes - item.reduced_size_bytes

    def _calculate_saved_bytes_from_results(self, results) -> int:
        return sum(self._saved_bytes_for_item(item) for item in results)

    def _format_size_delta(self, size_bytes: int) -> str:
        sign = "-" if size_bytes < 0 else ""
        size_megabytes = abs(size_bytes) / (1024 * 1024)
        if size_megabytes > 999:
            return f"{sign}{size_megabytes / 1024:.2f} GB"
        return f"{sign}{size_megabytes:.2f} MB"

    def _copy_selected_results(self, _event=None) -> str | None:
        selected = self.results_tree.selection()
        if not selected:
            self.status_var.set("No result rows selected to copy.")
            return "break"

        rows: list[str] = []
        for item_id in selected:
            values = self.results_tree.item(item_id, "values")
            rows.append("\t".join(str(value) for value in values))

        text = "\n".join(rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"Copied {len(selected)} result row(s) to the clipboard.")
        return "break"

    def _show_results_context_menu(self, event) -> str:
        row_id = self.results_tree.identify_row(event.y)
        if row_id:
            if row_id not in self.results_tree.selection():
                self.results_tree.selection_set(row_id)
            self.results_tree.focus(row_id)
        if self.results_menu is not None:
            self.results_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _on_log_search_changed(self, *_args) -> None:
        self._highlight_log_matches(self.log_search_var.get().strip())
        self._log_search_index = "1.0"

    def _highlight_log_matches(self, term: str) -> None:
        self.log_text.tag_remove("search_match", "1.0", tk.END)
        self.log_text.tag_remove("search_current", "1.0", tk.END)
        if not term:
            return

        start = "1.0"
        while True:
            index = self.log_text.search(term, start, stopindex=tk.END, nocase=True)
            if not index:
                break
            end = f"{index}+{len(term)}c"
            self.log_text.tag_add("search_match", index, end)
            start = end

    def _find_next_log(self) -> None:
        term = self.log_search_var.get().strip()
        if not term:
            return

        self._highlight_log_matches(term)
        self.log_text.tag_remove("search_current", "1.0", tk.END)

        index = self.log_text.search(term, self._log_search_index, stopindex=tk.END, nocase=True)
        if not index:
            index = self.log_text.search(term, "1.0", stopindex=tk.END, nocase=True)
            if not index:
                self.status_var.set(f'No log matches for "{term}".')
                return

        end = f"{index}+{len(term)}c"
        self.log_text.tag_add("search_current", index, end)
        self.log_text.see(index)
        self.log_text.mark_set(tk.INSERT, end)
        self._log_search_index = end
        self.status_var.set(f'Log search: "{term}"')

    def _clear_log_search(self) -> None:
        self.log_search_var.set("")
        self.log_text.tag_remove("search_match", "1.0", tk.END)
        self.log_text.tag_remove("search_current", "1.0", tk.END)
        self._log_search_index = "1.0"

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.scan_button.config(state=state)
        self.process_button.config(state=state)
        self.thumbnail_button.config(state=state)
        self.delete_button.config(state=state)
        if busy:
            self.root.config(cursor="watch")
        else:
            self.root.config(cursor="")

    def _clear_results(self) -> None:
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self._tree_sort_state = {}
        self.summary_vars["shrunk"].set("0")
        self.summary_vars["skipped"].set("0")
        self.summary_vars["failed"].set("0")
        self.summary_vars["archived"].set("0")
        self.summary_vars["thumbnails"].set("0")
        self.summary_vars["scanned"].set("0")
        self.summary_vars["images"].set("0")
        self.summary_vars["videos"].set("0")
        self.summary_vars["saved"].set("0.00 MB")
        self._saved_bytes_total = 0

    def _append_log(self, message: str) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        if self.log_search_var.get().strip():
            self._highlight_log_matches(self.log_search_var.get().strip())
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)


def main() -> None:
    try:
        app_root = tk.Tk()
        ShrinkMediaApp(app_root)
        app_root.mainloop()
    except MediaToolError as exc:
        messagebox.showerror(title="Missing tools", message=str(exc))
        raise


if __name__ == "__main__":
    main()
