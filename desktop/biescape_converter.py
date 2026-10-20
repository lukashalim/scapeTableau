#!/usr/bin/env python3
"""BIEscape Converter — thin Tkinter GUI around extract_to_xlsx."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from engine.extract import extract_to_xlsx
from engine.schema import WorkbookParseError

APP_TITLE = "BIEscape Converter"
FILETYPES = [
    ("Tableau extracts", "*.hyper *.twbx *.tdsx"),
    ("Hyper", "*.hyper"),
    ("Packaged workbook", "*.twbx"),
    ("Packaged datasource", "*.tdsx"),
    ("All files", "*.*"),
]


class ConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.minsize(520, 360)
        self.geometry("640x420")
        self._source: Path | None = None
        self._build()

    def _build(self) -> None:
        pad = {"padx": 16, "pady": 8}
        ttk.Label(self, text="BIEscape Converter", font=("Helvetica", 18, "bold")).pack(
            anchor="w", **pad
        )
        ttk.Label(
            self,
            text=(
                "Open a .hyper, .twbx, or .tdsx file and save tables as Excel.\n"
                "One sheet per table (100k row cap).\n\n"
                "Unsigned v1: macOS Gatekeeper and Windows SmartScreen may warn. "
                "On Mac use right-click → Open; on Windows choose More info → Run anyway."
            ),
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self.open_btn = ttk.Button(
            self, text="Open Tableau extract…", command=self.open_file
        )
        self.open_btn.pack(fill="x", padx=16, pady=12)
        self.open_btn.configure(padding=16)

        self.path_var = tk.StringVar(value="No file selected")
        ttk.Label(self, textvariable=self.path_var, wraplength=600).pack(
            anchor="w", padx=16
        )

        self.status = tk.Text(self, height=10, wrap="word", state="disabled")
        self.status.pack(fill="both", expand=True, padx=16, pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        self.convert_btn = ttk.Button(
            btn_row,
            text="Convert & Save .xlsx…",
            command=self.convert_and_save,
            state="disabled",
        )
        self.convert_btn.pack(side="right")

    def _log(self, message: str) -> None:
        self.status.configure(state="normal")
        self.status.delete("1.0", "end")
        self.status.insert("end", message)
        self.status.configure(state="disabled")

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="Open extract", filetypes=FILETYPES)
        if path:
            self._set_source(Path(path))

    def _set_source(self, path: Path) -> None:
        if not path.is_file():
            messagebox.showerror(APP_TITLE, f"File not found:\n{path}")
            return
        if path.suffix.lower() not in {".hyper", ".twbx", ".tdsx"}:
            messagebox.showwarning(APP_TITLE, "Choose a .hyper, .twbx, or .tdsx file.")
            return
        self._source = path
        self.path_var.set(str(path))
        self.convert_btn.configure(state="normal")
        self._log(f"Ready: {path.name}\nClick Convert & Save .xlsx…")

    def convert_and_save(self) -> None:
        if self._source is None:
            return
        try:
            xlsx, inventory = extract_to_xlsx(self._source)
        except WorkbookParseError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            self._log(f"Error: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"Conversion failed:\n{exc}")
            self._log(traceback.format_exc())
            return

        default_name = self._source.with_suffix(".xlsx").name
        out = filedialog.asksaveasfilename(
            title="Save Excel workbook",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel", "*.xlsx")],
        )
        if not out:
            return
        Path(out).write_bytes(xlsx)
        sheets = ", ".join(inventory.converted_sheets) or "(none)"
        notes = "\n".join(inventory.notes) if inventory.notes else ""
        self._log(f"Saved {out}\n\nSheets: {sheets}\nHyper files: {len(inventory.hyper_files)}\n{notes}")
        messagebox.showinfo(APP_TITLE, f"Saved:\n{out}")


def main() -> None:
    ConverterApp().mainloop()


if __name__ == "__main__":
    main()
