from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from engine import DeleteResult, ProcessProgress, ProcessResult, ScanResult, delete_archive, process_root, scan_root
from media_tools import MediaToolError


class ShrinkMediaApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Second Cut")
        self.root.geometry("1280x820")
        self.root.minsize(1024, 680)

        self.selected_root = tk.StringVar()
        self.log_search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a folder, then scan or process it.")
        self.summary_vars = {
            "images": tk.StringVar(value="0"),
            "videos": tk.StringVar(value="0"),
            "scanned": tk.StringVar(value="0"),
            "shrunk": tk.StringVar(value="0"),
            "skipped": tk.StringVar(value="0"),
            "failed": tk.StringVar(value="0"),
            "archived": tk.StringVar(value="0"),
        }

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self._tree_sort_state: dict[str, bool] = {}
        self._log_search_index = "1.0"
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

        self.delete_button = ttk.Button(button_row, text="Delete .to-be-deleted", command=self._start_delete)
        self.delete_button.pack(side=tk.LEFT)

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
        ]
        for index, (label, key) in enumerate(summary_items):
            ttk.Label(summary, text=label).grid(row=0, column=index * 2, sticky="w", padx=(0, 6))
            ttk.Label(summary, textvariable=self.summary_vars[key], width=8).grid(
                row=0, column=index * 2 + 1, sticky="w", padx=(0, 16)
            )

        content_pane = ttk.Panedwindow(frame, orient=tk.VERTICAL)
        content_pane.pack(fill=tk.BOTH, expand=True, pady=(12, 12))

        results_frame = ttk.LabelFrame(content_pane, text="Results", padding=12)
        content_pane.add(results_frame, weight=3)

        ttk.Label(
            results_frame,
            text="Scroll vertically or horizontally to inspect the full result list.",
        ).pack(anchor="w", pady=(0, 8))

        columns = ("status", "action", "kind", "path", "detail")
        tree_container = ttk.Frame(results_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)

        self.results_tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=16)
        for name, title, width in (
            ("status", "Status", 110),
            ("action", "Action", 110),
            ("kind", "Type", 90),
            ("path", "Path", 540),
            ("detail", "Detail", 600),
        ):
            self.results_tree.heading(name, text=title, command=lambda column=name: self._sort_results_by(column))
            self.results_tree.column(name, width=width, anchor=tk.W)

        tree_scroll_y = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.results_tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.results_tree.xview)
        self.results_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(0, weight=1)

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
        self._run_worker("scan", lambda: scan_root(folder))

    def _start_process(self) -> None:
        folder = self._require_folder()
        if folder is None:
            return
        self._clear_results()
        self._append_log(f"Processing {folder}")
        self.status_var.set("Processing files in the background...")
        self._run_worker("process", lambda: process_root(folder, log=self._queue_log, progress=self._queue_progress))

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

    def _poll_queue(self) -> None:
        while True:
            try:
                event_type, payload = self.queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(str(payload))
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
            self.status_var.set(f"Scan complete. Found {len(result.work_items)} supported file(s).")
            self._append_log(f"Scan complete. Found {len(result.work_items)} supported file(s).")
            self._populate_scan_results(result)
            return

        if action == "process" and isinstance(result, ProcessResult):
            self.summary_vars["images"].set(str(result.image_count))
            self.summary_vars["videos"].set(str(result.video_count))
            self.summary_vars["scanned"].set(str(result.scanned))
            self.summary_vars["shrunk"].set(str(result.shrunk))
            self.summary_vars["skipped"].set(str(result.skipped))
            self.summary_vars["failed"].set(str(result.failed))
            self.summary_vars["archived"].set(str(result.archived))
            self.status_var.set(
                f"Process complete. Shrunk={result.shrunk}, Skipped={result.skipped}, Failed={result.failed}, Archived={result.archived}"
            )
            self._append_log(
                f"Process complete. Shrunk={result.shrunk}, Skipped={result.skipped}, Failed={result.failed}, Archived={result.archived}"
            )
            return

        if action == "delete" and isinstance(result, DeleteResult):
            self.status_var.set(result.detail)
            self._append_log(result.detail)
            messagebox.showinfo(title="Delete .to-be-deleted", message=result.detail)
            return

    def _populate_scan_results(self, result: ScanResult) -> None:
        for item in result.work_items:
            self.results_tree.insert(
                "",
                tk.END,
                values=(
                    "ready",
                    "scan",
                    item.media_kind,
                    self._format_display_path(item.source_path),
                    "Supported file found.",
                ),
            )

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
        self._insert_process_result(progress.latest_result)

        if progress.phase == "cleanup":
            self.status_var.set("Resolving legacy tmp-/new- leftovers...")
        else:
            self.status_var.set(
                f"Processed {progress.completed} of {progress.scanned}. "
                f"Shrunk={progress.shrunk}, Skipped={progress.skipped}, Failed={progress.failed}, Archived={progress.archived}"
            )

    def _insert_process_result(self, item) -> None:
        self.results_tree.insert(
            "",
            tk.END,
            values=(
                item.status,
                item.action,
                item.media_kind,
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
        try:
            return (0, float(value))
        except ValueError:
            return (1, value.lower())

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
        self.summary_vars["scanned"].set("0")
        self.summary_vars["images"].set("0")
        self.summary_vars["videos"].set("0")

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
