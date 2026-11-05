"""Desktop GUI for clean_mod_texture_suffixes.py.

Requires a regular Python installation with Tkinter and Blender with USD/pxr.
The cleanup script is run by Blender in the background. Conflicting DDS
names are skipped and reported, while other safety checks remain active.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SCRIPT_NAME = "clean_mod_texture_suffixes.py"
SUMMARY_PATTERN = re.compile(r"^\s*(\d+) texture\(s\), (\d+) layer\(s\)\s*$")
SKIPPED_PATTERN = re.compile(r"^\s*(\d+) skipped collision\(s\)\s*$")
BACKUP_FOLDER = ".suffix-cleanup-backups"

BG = "#f4f6fa"
PANEL = "#ffffff"
TEXT = "#172033"
MUTED = "#64748b"
ACCENT = "#2759bd"
ACCENT_HOVER = "#194aa6"
BORDER = "#dce2ed"
LOG_BG = "#131d30"
LOG_TEXT = "#e2e8f0"


def discover_blender() -> str:
    """Return the first plausible local Blender executable, if available."""
    from_path = shutil.which("blender") or shutil.which("blender.exe")
    if from_path:
        return from_path

    candidates: list[Path] = []
    if sys.platform == "win32":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            parent = os.environ.get(env_name)
            if parent:
                candidates.extend(Path(parent).glob("Blender Foundation/Blender */blender.exe"))
    elif sys.platform == "darwin":
        candidates.extend(
            [Path("/Applications/Blender.app/Contents/MacOS/Blender"),
             Path.home() / "Applications/Blender.app/Contents/MacOS/Blender"]
        )
    else:
        candidates.extend([Path("/usr/bin/blender"), Path("/usr/local/bin/blender"),
                           Path("/snap/bin/blender")])

    existing = [path for path in candidates if path.is_file()]
    return str(sorted(existing, key=lambda path: str(path).casefold(), reverse=True)[0]) if existing else ""


def ru_plural(number: int, singular: str, few: str, many: str) -> str:
    """Return the appropriate Russian noun form for an integer count."""
    if 11 <= number % 100 <= 14:
        return many
    last = number % 10
    if last == 1:
        return singular
    if 2 <= last <= 4:
        return few
    return many


def make_command(blender: Path, script: Path, mod_folder: Path, apply: bool) -> list[str]:
    """Build an argument list, without a shell (paths may contain spaces)."""
    cmd = [str(blender), "--background", "--python", str(script), "--", str(mod_folder)]
    if apply:
        cmd.append("--apply")
    return cmd


class TextureCleanerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Texture Suffix Cleaner — GUI")
        self.root.geometry("980x720")
        self.root.minsize(780, 560)
        self.root.configure(background=BG)
        self.script_path = Path(__file__).resolve().with_name(SCRIPT_NAME)

        self.blender_var = tk.StringVar(value=discover_blender())
        self.folder_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="Проверка еще не выполнялась")
        self.status_var = tk.StringVar(value="Готов к проверке. Файлы пока не изменяются.")
        self.is_running = False
        self.running_mode: str | None = None
        self.last_analysis: tuple[str, str] | None = None
        self.planned_counts: tuple[int, int] | None = None
        self.skipped_count = 0
        self.current_output: list[str] = []
        self.all_output: list[str] = []
        self.events: queue.Queue[tuple[str, str | int]] = queue.Queue()

        self._configure_style()
        self._build_interface()
        self.blender_var.trace_add("write", self._paths_changed)
        self.folder_var.trace_add("write", self._paths_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_buttons()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Main.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Header.TLabel", background=BG, foreground=TEXT,
                        font=("Segoe UI", 21, "bold"))
        style.configure("Description.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 10))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Summary.TLabel", background=PANEL, foreground=TEXT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", padding=(10, 8), fieldbackground="#ffffff",
                        foreground=TEXT, bordercolor=BORDER)
        style.configure("TButton", font=("Segoe UI", 10), padding=(14, 10))
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        font=("Segoe UI", 10, "bold"), bordercolor=ACCENT)
        style.map("Accent.TButton", background=[("disabled", "#a5b5d7"),
                                                  ("active", ACCENT_HOVER)])
        style.configure("Apply.TButton", background="#16744d", foreground="white",
                        font=("Segoe UI", 10, "bold"), bordercolor="#16744d")
        style.map("Apply.TButton", background=[("disabled", "#9fb9aa"),
                                                 ("active", "#0f5f3d")])
        style.configure("TProgressbar", background=ACCENT, troughcolor=BORDER)

    def _build_interface(self) -> None:
        main = ttk.Frame(self.root, style="Main.TFrame", padding=22)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Очистка суффиксов текстур", style="Header.TLabel").pack(anchor="w")
        ttk.Label(main, text="Проверка и переименование DDS-текстур с обновлением ссылок в USD-слоях",
                  style="Description.TLabel").pack(anchor="w", pady=(3, 17))

        panel = ttk.Frame(main, style="Panel.TFrame", padding=(16, 14))
        panel.pack(fill="x")
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text="ПАПКА МОДА", style="Section.TLabel").grid(
            row=0, column=0, sticky="w")
        mod_row = ttk.Frame(panel, style="Panel.TFrame")
        mod_row.grid(row=1, column=0, sticky="ew", pady=(6, 13))
        mod_row.columnconfigure(0, weight=1)
        self.mod_entry = ttk.Entry(mod_row, textvariable=self.folder_var)
        self.mod_entry.grid(row=0, column=0, sticky="ew")
        self.mod_button = ttk.Button(mod_row, text="Обзор…", command=self._choose_mod)
        self.mod_button.grid(row=0, column=1, padx=(9, 0))

        ttk.Label(panel, text="BLENDER (blender.exe / Blender)", style="Section.TLabel").grid(
            row=2, column=0, sticky="w")
        blender_row = ttk.Frame(panel, style="Panel.TFrame")
        blender_row.grid(row=3, column=0, sticky="ew", pady=(6, 9))
        blender_row.columnconfigure(0, weight=1)
        self.blender_entry = ttk.Entry(blender_row, textvariable=self.blender_var)
        self.blender_entry.grid(row=0, column=0, sticky="ew")
        self.blender_button = ttk.Button(blender_row, text="Обзор…", command=self._choose_blender)
        self.blender_button.grid(row=0, column=1, padx=(9, 0))
        ttk.Label(panel, text="Blender нужен для pxr. Конфликты имён DDS пропускаются автоматически.",
                  style="Hint.TLabel").grid(row=4, column=0, sticky="w")

        controls = ttk.Frame(main, style="Main.TFrame")
        controls.pack(fill="x", pady=(17, 13))
        self.analyze_button = ttk.Button(controls, text="1. Проверить изменения",
                                         style="Accent.TButton", command=self._analyze)
        self.analyze_button.pack(side="left", padx=(0, 10))
        self.apply_button = ttk.Button(controls, text="2. Применить изменения",
                                       style="Apply.TButton", command=self._apply)
        self.apply_button.pack(side="left")
        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=135)
        self.progress.pack(side="right")

        summary_panel = ttk.Frame(main, style="Panel.TFrame", padding=(16, 11))
        summary_panel.pack(fill="x", pady=(0, 14))
        ttk.Label(summary_panel, textvariable=self.summary_var,
                  style="Summary.TLabel").pack(anchor="w")
        ttk.Label(summary_panel, text="При применении автоматически создается ZIP-копия исходных файлов. Capture-слои не изменяются.",
                  style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

        log_head = ttk.Frame(main, style="Main.TFrame")
        log_head.pack(fill="x", pady=(0, 7))
        ttk.Label(log_head, text="ЖУРНАЛ И ПРЕДПРОСМОТР ИЗМЕНЕНИЙ",
                  style="Status.TLabel").pack(side="left")
        self.backup_button = ttk.Button(log_head, text="Папка копий", command=self._open_backups)
        self.backup_button.pack(side="right")
        self.save_button = ttk.Button(log_head, text="Сохранить журнал", command=self._save_log)
        self.save_button.pack(side="right", padx=(0, 8))

        log_frame = ttk.Frame(main, style="Panel.TFrame")
        log_frame.pack(fill="both", expand=True)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        mono = ("Consolas" if sys.platform == "win32" else "DejaVu Sans Mono", 10)
        self.log = tk.Text(log_frame, font=mono, background=LOG_BG, foreground=LOG_TEXT,
                           insertbackground=LOG_TEXT, borderwidth=0, padx=14, pady=13,
                           wrap="none", state="disabled", undo=False)
        self.log.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.log.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.log.tag_configure("rename", foreground="#7dd3fc")
        self.log.tag_configure("update", foreground="#fbbf24")
        self.log.tag_configure("skip", foreground="#fdba74")
        self.log.tag_configure("added", foreground="#86efac")
        self.log.tag_configure("removed", foreground="#fda4af")
        self.log.tag_configure("error", foreground="#fda4af")
        self.log.tag_configure("header", foreground="#c4b5fd")
        self.log.tag_configure("info", foreground="#94a3b8")
        self._write("Выберите папку мода и нажмите «Проверить изменения».\n", "info")

        status = ttk.Frame(main, style="Main.TFrame")
        status.pack(fill="x", pady=(12, 0))
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w")

    def _paths_changed(self, *_args: object) -> None:
        if self.last_analysis != (self.blender_var.get(), self.folder_var.get()):
            self.last_analysis = None
            self.planned_counts = None
            self.skipped_count = 0
            if not self.is_running:
                self.summary_var.set("Параметры изменены — повторите проверку")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        state = "disabled" if self.is_running else "normal"
        for widget in (self.mod_entry, self.mod_button, self.blender_entry, self.blender_button):
            widget.configure(state=state)
        self.analyze_button.configure(state=state)
        can_apply = (not self.is_running and self.last_analysis ==
                     (self.blender_var.get(), self.folder_var.get()) and
                     self.planned_counts is not None and any(self.planned_counts))
        self.apply_button.configure(state="normal" if can_apply else "disabled")
        self.save_button.configure(state="normal" if self.all_output else "disabled")

    def _choose_mod(self) -> None:
        chosen = filedialog.askdirectory(title="Выберите корневую папку мода",
                                         initialdir=self.folder_var.get() or str(Path.home()))
        if chosen:
            self.folder_var.set(chosen)

    def _choose_blender(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Выберите исполняемый файл Blender",
            filetypes=[("Blender", "blender.exe"), ("Все файлы", "*")],
            initialdir=str(Path(self.blender_var.get()).parent) if self.blender_var.get() else str(Path.home()),
        )
        if chosen:
            self.blender_var.set(chosen)

    def _write(self, value: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", value, tag)
        self.log.configure(state="disabled")

    def _add_output(self, line: str) -> None:
        self.current_output.append(line)
        self.all_output.append(line)
        if line.startswith("RENAME "):
            tag = "rename"
        elif line.startswith("UPDATE "):
            tag = "update"
        elif line.startswith("SKIP "):
            tag = "skip"
        elif line.startswith("+") and not line.startswith("+++ "):
            tag = "added"
        elif line.startswith("-") and not line.startswith("--- "):
            tag = "removed"
        elif "ERROR:" in line or "Traceback" in line:
            tag = "error"
        elif line.startswith(("@@", "--- ", "+++ ", "Backup:")):
            tag = "header"
        else:
            tag = None
        self._write(line, tag)

    def _validate_paths(self) -> tuple[Path, Path, Path] | None:
        mod_text = self.folder_var.get().strip().strip('"')
        blender_text = self.blender_var.get().strip().strip('"')
        if not mod_text or not Path(mod_text).is_dir():
            messagebox.showerror("Ошибка", "Выберите существующую папку мода.")
            return None
        if not blender_text or not Path(blender_text).is_file():
            messagebox.showerror("Ошибка", "Укажите исполняемый файл Blender (blender.exe или Blender).")
            return None
        if not self.script_path.is_file():
            messagebox.showerror("Не найден скрипт",
                                 f"Поместите {SCRIPT_NAME} рядом с файлом GUI:\n{self.script_path.parent}")
            return None
        return Path(blender_text).resolve(), self.script_path, Path(mod_text).resolve()

    def _analyze(self) -> None:
        paths = self._validate_paths()
        if paths is not None:
            self._start("analyze", paths)

    def _apply(self) -> None:
        if (self.last_analysis != (self.blender_var.get(), self.folder_var.get())
                or self.planned_counts is None or not any(self.planned_counts)):
            return
        paths = self._validate_paths()
        if paths is None:
            return
        textures, layers = self.planned_counts
        confirmed = messagebox.askyesno(
            "Подтвердить изменения",
            f"Будут переименованы текстуры: {textures}\n"
            f"Будут обновлены USD-слои: {layers}\n"
            f"Будут пропущены конфликты имён: {self.skipped_count}\n\n"
            "Перед изменениями скрипт создаст ZIP-копию исходных файлов.\n"
            "В момент применения план будет рассчитан заново.\n\n"
            "Применить изменения?",
            icon="warning",
        )
        if confirmed:
            self._start("apply", paths)

    def _start(self, mode: str, paths: tuple[Path, Path, Path]) -> None:
        if self.is_running:
            return
        blender, script, mod_folder = paths
        self.is_running = True
        self.running_mode = mode
        self.current_output = []
        if mode == "analyze":
            self.last_analysis = None
            self.planned_counts = None
            self.skipped_count = 0
            self.all_output.clear()
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
            self.summary_var.set("Выполняется проверка…")
            self.status_var.set("Анализ файлов; никаких изменений не вносится.")
            self.all_output.append("ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА\n\n")
            self._write("ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА\n\n", "header")
        else:
            self.summary_var.set("Выполняется применение изменений…")
            self.status_var.set("Обработка файлов и создание резервной копии…")
            self.all_output.append("\nПРИМЕНЕНИЕ ИЗМЕНЕНИЙ\n\n")
            self._write("\nПРИМЕНЕНИЕ ИЗМЕНЕНИЙ\n\n", "header")
        self.progress.start(12)
        self._refresh_buttons()
        cmd = make_command(blender, script, mod_folder, mode == "apply")
        thread = threading.Thread(target=self._run_process, args=(cmd,), daemon=True)
        thread.start()
        self.root.after(70, self._process_events)

    def _run_process(self, cmd: list[str]) -> None:
        try:
            kwargs: dict[str, object] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace",
                                  bufsize=1, **kwargs) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    self.events.put(("line", line))
                result = process.wait()
            self.events.put(("finished", result))
        except (OSError, ValueError) as exc:
            self.events.put(("exception", str(exc)))

    def _process_events(self) -> None:
        consumed = 0
        ended = False
        while consumed < 350:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break
            consumed += 1
            if event_type == "line":
                self._add_output(str(payload))
            elif event_type == "finished":
                self._finish(int(payload))
                ended = True
            elif event_type == "exception":
                self._add_output(f"ERROR: {payload}\n")
                self._finish(1)
                ended = True
            if ended:
                break
        if consumed:
            self.log.see("end")
        if self.is_running:
            self.root.after(70, self._process_events)

    def _finish(self, returncode: int) -> None:
        mode = self.running_mode
        self.is_running = False
        self.running_mode = None
        self.progress.stop()
        self.progress.configure(value=0)
        if returncode == 0 and mode == "analyze":
            counts = None
            self.skipped_count = 0
            for line in self.current_output:
                match = SUMMARY_PATTERN.match(line)
                if match:
                    counts = (int(match.group(1)), int(match.group(2)))
                skipped_match = SKIPPED_PATTERN.match(line)
                if skipped_match:
                    self.skipped_count = int(skipped_match.group(1))
            if counts is None:
                self.summary_var.set("Не удалось определить количество изменений")
                self.status_var.set("Проверьте журнал; применение через GUI заблокировано.")
            else:
                self.planned_counts = counts
                self.last_analysis = (self.blender_var.get(), self.folder_var.get())
                textures, layers = counts
                self.summary_var.set(
                    f"Найдено: {textures} {ru_plural(textures, 'текстура', 'текстуры', 'текстур')} "
                    f"для переименования; {layers} "
                    f"{ru_plural(layers, 'USD-слой', 'USD-слоя', 'USD-слоёв')} для обновления; "
                    f"пропущено конфликтов: {self.skipped_count}"
                )
                if not any(counts):
                    self.status_var.set("Изменений нет. Конфликты пропущены." if self.skipped_count
                                        else "Изменений нет.")
                else:
                    self.status_var.set("Проверка завершена. Можно применить изменения.")
        elif returncode == 0 and mode == "apply":
            skipped_matches = [SKIPPED_PATTERN.match(line) for line in self.current_output]
            self.skipped_count = next((int(m.group(1)) for m in reversed(skipped_matches) if m), 0)
            backup = next((line.strip()[len("Backup: "):] for line in self.current_output
                           if line.startswith("Backup: ")), None)
            self.summary_var.set(f"Изменения применены; пропущено конфликтов: {self.skipped_count}")
            self.status_var.set(f"Резервная копия: {backup}" if backup else
                                "Операция завершена; подробности в журнале.")
            self.last_analysis = None
            self.planned_counts = None
        else:
            self.summary_var.set("Операция завершилась с ошибкой")
            self.status_var.set("Ошибка при проверке — изменений нет." if mode == "analyze" else
                                "Ошибка при применении — проверьте журнал и резервные копии.")
            self.last_analysis = None
            self.planned_counts = None
            messagebox.showerror("Ошибка выполнения",
                                 "Blender или скрипт завершился с ошибкой.\n"
                                 "Подробности смотрите в журнале ниже.")
        self._refresh_buttons()

    def _save_log(self) -> None:
        if not self.all_output:
            return
        chosen = filedialog.asksaveasfilename(title="Сохранить журнал",
                                              defaultextension=".txt",
                                              filetypes=[("Текстовый файл", "*.txt")],
                                              initialfile="texture_cleanup_log.txt")
        if chosen:
            try:
                Path(chosen).write_text("".join(self.all_output), encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Ошибка сохранения", str(exc))

    def _open_backups(self) -> None:
        folder = self.folder_var.get().strip().strip('"')
        backup_dir = Path(folder) / BACKUP_FOLDER if folder else None
        if backup_dir is None or not backup_dir.is_dir():
            messagebox.showinfo("Резервные копии", "Папка копий еще не создана для выбранного мода.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(backup_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(backup_dir)])
            else:
                subprocess.Popen(["xdg-open", str(backup_dir)])
        except OSError as exc:
            messagebox.showerror("Не удалось открыть папку", str(exc))

    def _on_close(self) -> None:
        if self.is_running:
            messagebox.showwarning("Операция выполняется",
                                   "Дождитесь завершения анализа или применения изменений.")
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    TextureCleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
