#!/usr/bin/env python3
import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from tv_matcher.config import get_system_status, FFMPEG_BIN, load_settings, save_settings, add_recent_dir
from tv_matcher.pipeline import MatchingPipeline, SKIP

class TVBackupMatcherDesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TVBackupMatcher GUI (Desktop Edition)")
        self.root.geometry("1100x800")
        self.root.minsize(900, 650)

        self.settings = load_settings()
        self.pipeline = MatchingPipeline()
        self.preview_image: Optional[tk.PhotoImage] = None
        self._selected_file_path: str = ""

        self._build_ui()
        self._check_dependencies()
        self._update_undo_button()

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        main_frame = ttk.Frame(self.root, padding="14")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Header Frame
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        title_lbl = ttk.Label(header_frame, text="🎬 TVBackupMatcher GUI", font=("Helvetica", 18, "bold"))
        title_lbl.pack(side=tk.LEFT)

        self.status_badge = ttk.Label(header_frame, text="Checking tools...", font=("Helvetica", 10))
        self.status_badge.pack(side=tk.RIGHT)

        # 2. Folder & Options Frame
        cfg_frame = ttk.LabelFrame(main_frame, text="1. TV Series Backup Folder & Metadata", padding="10")
        cfg_frame.pack(fill=tk.X, pady=(0, 10))

        dir_row = ttk.Frame(cfg_frame)
        dir_row.pack(fill=tk.X, pady=3)

        ttk.Label(dir_row, text="Media Folder:", width=13).pack(side=tk.LEFT)
        self.dir_entry = ttk.Entry(dir_row)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        recent = self.settings.get("recent_dirs", [])
        if recent:
            self.dir_entry.insert(0, recent[0])

        browse_btn = ttk.Button(dir_row, text="Browse...", command=self._browse_folder)
        browse_btn.pack(side=tk.LEFT, padx=(0, 4))

        # Row 2: Metadata & IDs
        opt_row = ttk.Frame(cfg_frame)
        opt_row.pack(fill=tk.X, pady=3)

        ttk.Label(opt_row, text="Source:", width=13).pack(side=tk.LEFT)
        self.src_combo = ttk.Combobox(opt_row, values=["auto", "tvmaze", "tmdb", "local"], state="readonly", width=9)
        self.src_combo.set(self.settings.get("metadata_source", "auto"))
        self.src_combo.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(opt_row, text="Title Override:").pack(side=tk.LEFT, padx=(0, 4))
        self.name_entry = ttk.Entry(opt_row, width=22)
        self.name_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(opt_row, text="TVDB ID:").pack(side=tk.LEFT, padx=(0, 4))
        self.tvdb_entry = ttk.Entry(opt_row, width=10)
        self.tvdb_entry.pack(side=tk.LEFT, padx=(0, 12))

        self.scan_btn = ttk.Button(opt_row, text="🔍 Scan & Match Files", command=self._start_scan)
        self.scan_btn.pack(side=tk.RIGHT)

        self.cancel_btn = ttk.Button(opt_row, text="Cancel Scan", command=self._cancel_scan, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT, padx=(0, 6))

        # Advanced Settings toggle / row
        adv_row = ttk.Frame(cfg_frame)
        adv_row.pack(fill=tk.X, pady=(4, 0))

        ttk.Label(adv_row, text="TMDB Key:", width=13).pack(side=tk.LEFT)
        self.tmdb_entry = ttk.Entry(adv_row, width=24, show="*")
        if self.settings.get("tmdb_api_key"):
            self.tmdb_entry.insert(0, self.settings["tmdb_api_key"])
        self.tmdb_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(adv_row, text="Windows (s):").pack(side=tk.LEFT, padx=(0, 4))
        self.win_entry = ttk.Entry(adv_row, width=12)
        self.win_entry.insert(0, self.settings.get("frame_windows", "64-75"))
        self.win_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(adv_row, text="Workers:").pack(side=tk.LEFT, padx=(0, 4))
        self.workers_spin = ttk.Spinbox(adv_row, from_=1, to=8, width=4)
        self.workers_spin.set(str(self.settings.get("workers", 3)))
        self.workers_spin.pack(side=tk.LEFT)

        # 3. Progress Frame
        prog_frame = ttk.Frame(main_frame)
        prog_frame.pack(fill=tk.X, pady=(0, 10))

        self.prog_bar = ttk.Progressbar(prog_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.prog_bar.pack(fill=tk.X, pady=2)

        self.status_lbl = ttk.Label(prog_frame, text="Ready. Select a folder and click 'Scan & Match Files'.", font=("Helvetica", 11))
        self.status_lbl.pack(anchor=tk.W)

        # 4. Results & Preview Paned Window
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Left: Results Treeview
        tree_frame = ttk.LabelFrame(paned, text="2. Matched Episode Files (Click row to inspect preview)", padding="8")
        paned.add(tree_frame, weight=3)

        cols = ("code", "title", "score", "status", "target", "filename")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("code", text="Episode")
        self.tree.heading("title", text="Identified Title")
        self.tree.heading("score", text="Score")
        self.tree.heading("status", text="Status")
        self.tree.heading("target", text="Proposed Plex / Jellyfin Target")
        self.tree.heading("filename", text="Source File")

        self.tree.column("code", width=70, anchor=tk.CENTER)
        self.tree.column("title", width=190, anchor=tk.W)
        self.tree.column("score", width=55, anchor=tk.CENTER)
        self.tree.column("status", width=95, anchor=tk.CENTER)
        self.tree.column("target", width=220, anchor=tk.W)
        self.tree.column("filename", width=180, anchor=tk.W)

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Right: Preview & Override Frame
        detail_frame = ttk.LabelFrame(paned, text="3. Title Card Preview & Override", padding="8")
        paned.add(detail_frame, weight=2)

        self.preview_canvas = tk.Canvas(detail_frame, width=320, height=180, bg="#000000", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self.preview_info_lbl = ttk.Label(detail_frame, text="Select an episode to view frame", font=("Helvetica", 11, "bold"))
        self.preview_info_lbl.pack(anchor=tk.W)

        self.warning_lbl = ttk.Label(detail_frame, text="", font=("Helvetica", 10), foreground="#dc2626", wraplength=280)
        self.warning_lbl.pack(anchor=tk.W, pady=(2, 6))

        override_box = ttk.Frame(detail_frame)
        override_box.pack(fill=tk.X, pady=4)

        ttk.Label(override_box, text="Manual Reassign:").pack(anchor=tk.W, pady=(0, 2))
        self.ep_combo = ttk.Combobox(override_box, state="readonly")
        self.ep_combo.pack(fill=tk.X, pady=(0, 4))

        btn_row = ttk.Frame(override_box)
        btn_row.pack(fill=tk.X)
        self.override_btn = ttk.Button(btn_row, text="Update Match", command=self._apply_override)
        self.override_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.confirm_btn = ttk.Button(btn_row, text="✓ Confirm Match", command=self._confirm_match)
        self.confirm_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 5. Bottom Action Bar
        act_frame = ttk.Frame(main_frame)
        act_frame.pack(fill=tk.X)

        self.export_var = tk.BooleanVar(value=bool(self.settings.get("export_cards_dir")))
        self.export_chk = ttk.Checkbutton(act_frame, text="Export Title Cards to:", variable=self.export_var)
        self.export_chk.pack(side=tk.LEFT)

        self.export_entry = ttk.Entry(act_frame, width=28)
        self.export_entry.insert(0, self.settings.get("export_cards_dir", ""))
        self.export_entry.pack(side=tk.LEFT, padx=(4, 6))

        self.export_browse = ttk.Button(act_frame, text="Browse...", command=self._browse_export_dir)
        self.export_browse.pack(side=tk.LEFT, padx=(0, 12))

        self.include_unreviewed_var = tk.BooleanVar(value=False)
        self.include_unreviewed_chk = ttk.Checkbutton(act_frame, text="Include Unreviewed (< 60%)", variable=self.include_unreviewed_var)
        self.include_unreviewed_chk.pack(side=tk.LEFT)

        self.rename_btn = ttk.Button(act_frame, text="⚡ Apply Safe Staged Rename", command=self._confirm_apply)
        self.rename_btn.pack(side=tk.RIGHT)

        self.undo_btn = ttk.Button(act_frame, text="↩ Undo Last Rename", command=self._undo_last_rename, state=tk.DISABLED)
        self.undo_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def _check_dependencies(self):
        status = get_system_status()
        if status["ready"]:
            self.status_badge.config(text="✓ System Tools Ready", foreground="green")
        else:
            self.status_badge.config(text="✗ Missing ffmpeg/tesseract", foreground="red")
            messagebox.showwarning("Missing Tools", "ffmpeg, ffprobe, or tesseract not found.\nInstall via: brew install ffmpeg tesseract")

    def _browse_folder(self):
        init_dir = self.dir_entry.get().strip() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=init_dir, title="Select TV Show Folder")
        if chosen:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, chosen)
            self._update_undo_button()

    def _browse_export_dir(self):
        init_dir = self.export_entry.get().strip() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=init_dir, title="Select Title Cards Export Folder")
        if chosen:
            self.export_entry.delete(0, tk.END)
            self.export_entry.insert(0, chosen)
            self.export_var.set(True)

    def _update_undo_button(self):
        series_dir = self.dir_entry.get().strip()
        state = self.pipeline.get_state()
        if state.get("undo") or (series_dir and os.path.isdir(series_dir)):
            from tv_matcher.renamer import find_undoable_log
            found = find_undoable_log(series_dir)
            if found:
                self.undo_btn.config(state=tk.NORMAL)
                return
        self.undo_btn.config(state=tk.DISABLED)

    def _start_scan(self):
        target_dir = self.dir_entry.get().strip()
        if not target_dir or not os.path.isdir(target_dir):
            messagebox.showerror("Invalid Path", "Please select a valid directory containing media files.")
            return

        # Save settings
        save_settings({
            "metadata_source": self.src_combo.get(),
            "tmdb_api_key": self.tmdb_entry.get().strip(),
            "frame_windows": self.win_entry.get().strip(),
            "export_cards_dir": self.export_entry.get().strip(),
            "workers": int(self.workers_spin.get() or 3),
        })
        add_recent_dir(target_dir)

        self.scan_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.rename_btn.config(state=tk.DISABLED)
        self.prog_bar['value'] = 0
        self.tree.delete(*self.tree.get_children())
        self.warning_lbl.config(text="")

        name_override = self.name_entry.get().strip()
        tvdb_id = self.tvdb_entry.get().strip()
        src = self.src_combo.get()
        tmdb_key = self.tmdb_entry.get().strip()
        win = self.win_entry.get().strip() or "64-75"
        workers = int(self.workers_spin.get() or 3)

        try:
            self.pipeline.start_scan(
                target_dir,
                series_name=name_override,
                tvdb_id=tvdb_id,
                metadata_source=src,
                tmdb_api_key=tmdb_key,
                frame_windows=win,
                workers=workers,
            )
            self._poll_progress()
        except Exception as e:
            messagebox.showerror("Scan Error", str(e))
            self.scan_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)

    def _cancel_scan(self):
        self.pipeline.cancel_scan()
        self.cancel_btn.config(state=tk.DISABLED)

    def _poll_progress(self):
        state = self.pipeline.get_state()
        self.prog_bar['value'] = int(state["progress"] * 100)
        self.status_lbl.config(text=state["status_message"])

        if state["state"] in ["processing", "scanning"]:
            self.root.after(300, self._poll_progress)
        elif state["state"] == "review_ready":
            self.scan_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)
            self.rename_btn.config(state=tk.NORMAL)
            self._populate_results(state)
            self._update_undo_button()
        elif state["state"] == "error":
            self.scan_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)
            messagebox.showerror("Error", state["error_message"] or "An error occurred during scan.")

    def _populate_results(self, state):
        self.tree.delete(*self.tree.get_children())
        matches = state.get("matches", [])

        # Populate Combobox
        all_eps = state.get("available_episodes", [])
        combo_vals = ["-- Exclude / Skip File --"] + [f"{ep['code']} - {ep['title']}" for ep in all_eps]
        self.ep_combo['values'] = combo_vals

        for item in matches:
            score_str = f"{int(item['score'] * 100)}%" if item["score"] > 0 else "--"
            status_str = "Needs Review" if item["needs_review"] else ("Manual" if item["is_overridden"] else ("Skipped" if item["status"] == "skipped" else "Ready"))
            row_id = self.tree.insert(
                "",
                tk.END,
                values=(item["episode_code"] or "--", item["episode_title"], score_str, status_str, item.get("target_relpath") or "(None)", item["file_name"])
            )
            self.tree.item(row_id, tags=(item["file_path"], item.get("image_path", ""), str(item["needs_review"]), item.get("warning", "")))

        counts = state.get("counts", {})
        summary = f"Total: {len(matches)} files. Ready: {counts.get('will_rename', 0)}, Needs Review: {counts.get('needs_review', 0)}, Unmatched: {counts.get('unmatched', 0)}."
        self.status_lbl.config(text=summary)

    def _on_tree_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return

        item = self.tree.item(selected[0])
        tags = item.get("tags", [])
        if not tags:
            return

        file_path = tags[0]
        self._selected_file_path = file_path
        image_path = tags[1] if len(tags) > 1 else ""
        warning_msg = tags[3] if len(tags) > 3 else ""

        values = item.get("values", [])
        code = values[0] if len(values) > 0 else ""
        title = values[1] if len(values) > 1 else ""
        self.preview_info_lbl.config(text=f"{code} - {title}")
        self.warning_lbl.config(text=f"⚠️ {warning_msg}" if warning_msg else "")

        # Set combo
        for opt in self.ep_combo['values']:
            if opt.startswith(code):
                self.ep_combo.set(opt)
                break
        else:
            self.ep_combo.set("")

        # Display image preview
        if image_path and os.path.isfile(image_path):
            self._render_canvas_image(image_path)
        else:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(160, 90, text="No Preview Available", fill="#6b7280", font=("Helvetica", 12))

    def _render_canvas_image(self, img_path: str):
        try:
            ppm_path = img_path + ".ppm"
            if not os.path.exists(ppm_path):
                subprocess.run([
                    FFMPEG_BIN, "-nostdin", "-v", "error", "-y", "-i", img_path,
                    "-vf", "scale=320:180:force_original_aspect_ratio=decrease",
                    ppm_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if os.path.exists(ppm_path):
                self.preview_image = tk.PhotoImage(file=ppm_path)
                self.preview_canvas.delete("all")
                self.preview_canvas.create_image(160, 90, image=self.preview_image)
        except Exception as e:
            print("Canvas render error:", e)

    def _apply_override(self):
        if not self._selected_file_path:
            messagebox.showinfo("Select File", "Please select an episode file row in the table first.")
            return

        chosen = self.ep_combo.get().strip()
        if not chosen:
            return

        if chosen == "-- Exclude / Skip File --":
            code = SKIP
        else:
            code = chosen.split(" - ")[0]

        res = self.pipeline.set_override(self._selected_file_path, code)
        if res.get("success"):
            if res.get("displaced"):
                messagebox.showinfo("Reassigned", f"Assigned to file. Displaced from '{res['displaced']}'.")
            state = self.pipeline.get_state()
            self._populate_results(state)

    def _confirm_match(self):
        if not self._selected_file_path:
            return
        res = self.pipeline.confirm_match(self._selected_file_path)
        if res.get("success"):
            state = self.pipeline.get_state()
            self._populate_results(state)

    def _confirm_apply(self):
        state = self.pipeline.get_state()
        matches = state.get("matches", [])
        if not matches:
            messagebox.showwarning("No Matches", "No matches found to rename.")
            return

        export_dir = ""
        if self.export_var.get():
            export_dir = self.export_entry.get().strip()
            if export_dir:
                save_settings({"export_cards_dir": export_dir})

        include_unreviewed = self.include_unreviewed_var.get()
        counts = state.get("counts", {})
        to_rename = len(matches) if include_unreviewed else counts.get("will_rename", 0)

        if to_rename == 0:
            messagebox.showwarning("Review Needed", "All matches need review or are skipped. Confirm or override them first, or check 'Include Unreviewed'.")
            return

        msg = (
            f"Are you sure you want to reorganize {to_rename} file(s)?\n\n"
            f"• Files will be safely staged into Season folders with Plex/Jellyfin naming.\n"
            f"• Full validation prevents any file overwrites.\n"
            f"• Can be completely reverted at any time with 'Undo Last Rename'."
        )
        if export_dir:
            msg += f"\n• Title cards gallery will be exported to:\n  {export_dir}"

        if not messagebox.askyesno("Confirm Rename", msg):
            return

        self.rename_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="Executing safe two-pass file reorganisation...")

        def _do_rename():
            res = self.pipeline.apply_rename(export_cards_dir=export_dir, include_unreviewed=include_unreviewed)
            def _done():
                self.rename_btn.config(state=tk.NORMAL)
                if res.get("success"):
                    messagebox.showinfo("Success", f"Successfully reorganized {res.get('renamed_count')} file(s)!")
                    self.status_lbl.config(text="Reorganization completed successfully!")
                else:
                    err_msg = res.get("error", "Rename failed.")
                    if res.get("problems"):
                        err_msg += "\n\nProblems detected:\n" + "\n".join(res["problems"][:6])
                    messagebox.showerror("Error", err_msg)
                self._populate_results(self.pipeline.get_state())
                self._update_undo_button()
            self.root.after(0, _done)

        threading.Thread(target=_do_rename, daemon=True).start()

    def _undo_last_rename(self):
        target_dir = self.dir_entry.get().strip()
        if not target_dir:
            return
        if not messagebox.askyesno("Confirm Undo", f"Revert all renamed files in '{os.path.basename(target_dir)}' back to their original names?"):
            return

        self.undo_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="Restoring original file names...")

        def _do_undo():
            res = self.pipeline.undo_last_rename(target_dir)
            def _done():
                self._update_undo_button()
                if res.get("success"):
                    messagebox.showinfo("Undo Complete", f"Successfully restored {res.get('restored_count')} file(s) to original names!")
                    self.status_lbl.config(text=f"Restored {res.get('restored_count')} file(s).")
                else:
                    messagebox.showerror("Undo Failed", res.get("error", "Undo operation failed."))
                self._populate_results(self.pipeline.get_state())
            self.root.after(0, _done)

        threading.Thread(target=_do_undo, daemon=True).start()

def main():
    root = tk.Tk()
    app = TVBackupMatcherDesktopApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
