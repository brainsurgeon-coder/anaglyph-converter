#!/usr/bin/env python3
"""
Anaglyph Converter
Drag and drop top-and-bottom 3D videos (e.g. Zeiss Kinevo 900 recordings)
and convert them to red-cyan anaglyph MP4s using FFmpeg.

Requires: FFmpeg (brew install ffmpeg)
Optional: tkinterdnd2 for drag-and-drop (pip install tkinterdnd2)
"""

import os
import re
import shutil
import subprocess
import threading
import queue
from collections import deque
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi"}

EYE_OPTIONS = {
    "Left eye on top (ab2l)": "ab2l",
    "Right eye on top (ab2r)": "ab2r",
}
COLOUR_OPTIONS = {
    "Full colour – Dubois (arcd)": "arcd",
    "Half colour (arch)": "arch",
    "Grey (arcg)": "arcg",
}
QUALITY_OPTIONS = {
    "High (CRF 18)": 18,
    "Standard (CRF 23)": 23,
    "Smaller file (CRF 28)": 28,
}


def find_tool(name):
    """Find an FFmpeg tool even when launched outside Terminal (PATH may be minimal)."""
    path = shutil.which(name)
    if path:
        return path
    for folder in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            return candidate
    return None


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")
FFPLAY = find_tool("ffplay")


def get_duration(path):
    """Video duration in seconds, or None if unknown."""
    if not FFPROBE:
        return None
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def output_path_for(src):
    """case1.mp4 -> case1_anaglyph.mp4 (never overwrites an existing file)."""
    base, _ = os.path.splitext(src)
    dst = base + "_anaglyph.mp4"
    n = 2
    while os.path.exists(dst):
        dst = f"{base}_anaglyph_{n}.mp4"
        n += 1
    return dst


class App:
    def __init__(self, root):
        self.root = root
        root.title("Anaglyph Converter")
        root.geometry("780x580")
        root.minsize(660, 500)

        self.msgq = queue.Queue()
        self.items = {}      # tree item id -> source path
        self.outputs = {}    # tree item id -> converted file path
        self.proc = None
        self.cancel_flag = threading.Event()
        self.running = False

        self.build_ui()
        self.root.after(100, self.poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if not FFMPEG:
            messagebox.showerror(
                "FFmpeg not found",
                "FFmpeg isn't installed.\n\nOpen Terminal and run:\n    brew install ffmpeg",
            )

    # ---------- UI ----------
    def build_ui(self):
        pad = {"padx": 14, "pady": 6}

        if DND_AVAILABLE:
            drop_text = "Drop 3D videos here\n(or click to choose files)"
        else:
            drop_text = ("Click here to choose videos\n"
                         "(install tkinterdnd2 to enable drag-and-drop)")
        self.drop = tk.Label(
            self.root, text=drop_text, relief="ridge", bd=2, height=5,
            font=("Helvetica", 16), bg="#eaf1f8", fg="#2b3a4a", cursor="hand2",
        )
        self.drop.pack(fill="x", padx=14, pady=(14, 6))
        self.drop.bind("<Button-1>", lambda e: self.browse())

        # Settings
        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)

        self.eye_var = tk.StringVar(value=list(EYE_OPTIONS)[0])
        self.colour_var = tk.StringVar(value=list(COLOUR_OPTIONS)[0])
        self.quality_var = tk.StringVar(value=list(QUALITY_OPTIONS)[0])

        for col, (label, var, choices) in enumerate([
            ("Eye order", self.eye_var, EYE_OPTIONS),
            ("Colour mode", self.colour_var, COLOUR_OPTIONS),
            ("Quality", self.quality_var, QUALITY_OPTIONS),
        ]):
            ttk.Label(opts, text=label).grid(row=0, column=col, sticky="w", padx=(0, 10))
            ttk.Combobox(opts, textvariable=var, values=list(choices),
                         state="readonly", width=26).grid(row=1, column=col, sticky="w", padx=(0, 10))

        # File list
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(list_frame, columns=("file", "status"),
                                 show="headings", selectmode="extended")
        self.tree.heading("file", text="Video")
        self.tree.heading("status", text="Status")
        self.tree.column("file", width=380, anchor="w")
        self.tree.column("status", width=300, anchor="w")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        self.tree.bind("<Double-1>", lambda e: self.reveal())

        if DND_AVAILABLE:
            for widget in (self.drop, self.tree):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)

        # Progress
        prog = ttk.Frame(self.root)
        prog.pack(fill="x", **pad)
        self.status_label = ttk.Label(prog, text="Add videos to begin.")
        self.status_label.pack(anchor="w")
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))

        # Buttons
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=14, pady=(6, 14))
        self.add_btn = ttk.Button(btns, text="Add videos…", command=self.browse)
        self.preview_btn = ttk.Button(btns, text="Preview 3D", command=self.preview)
        self.start_btn = ttk.Button(btns, text="Convert", command=self.start)
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self.cancel, state="disabled")
        self.reveal_btn = ttk.Button(btns, text="Show in Finder", command=self.reveal)
        self.clear_btn = ttk.Button(btns, text="Clear finished", command=self.clear_finished)
        for b in (self.add_btn, self.preview_btn, self.start_btn, self.cancel_btn):
            b.pack(side="left", padx=(0, 6))
        for b in (self.clear_btn, self.reveal_btn):
            b.pack(side="right", padx=(6, 0))

    # ---------- Adding files ----------
    def on_drop(self, event):
        self.add_files(self.root.tk.splitlist(event.data))

    def browse(self):
        paths = filedialog.askopenfilenames(
            title="Choose 3D videos",
            filetypes=[("Video files", "*.mp4 *.mov *.m4v *.mkv *.avi"), ("All files", "*.*")],
        )
        self.add_files(paths)

    def add_files(self, paths):
        existing = set(self.items.values())
        added = skipped = 0
        for p in paths:
            if os.path.isdir(p):
                # Allow dropping a whole folder
                for name in sorted(os.listdir(p)):
                    full = os.path.join(p, name)
                    if os.path.splitext(name)[1].lower() in VIDEO_EXTS and full not in existing:
                        self._insert(full)
                        existing.add(full)
                        added += 1
                continue
            if os.path.splitext(p)[1].lower() not in VIDEO_EXTS:
                skipped += 1
                continue
            if p in existing:
                continue
            self._insert(p)
            existing.add(p)
            added += 1
        if added and not self.running:
            self.status_label.config(text=f"{len(self.items)} video(s) ready. Click Convert.")
        if skipped:
            messagebox.showinfo("Skipped", f"{skipped} file(s) skipped — not a video.")

    def _insert(self, path):
        iid = self.tree.insert("", "end", values=(os.path.basename(path), "Waiting"))
        self.items[iid] = path

    def remove_selected(self):
        if self.running:
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
            self.outputs.pop(iid, None)

    def clear_finished(self):
        for iid in list(self.tree.get_children()):
            if self.tree.set(iid, "status").startswith("Done"):
                self.tree.delete(iid)
                self.items.pop(iid, None)
                self.outputs.pop(iid, None)

    # ---------- Settings ----------
    def current_filter(self):
        eye = EYE_OPTIONS[self.eye_var.get()]
        colour = COLOUR_OPTIONS[self.colour_var.get()]
        return f"stereo3d={eye}:{colour}"

    def selected_or_first(self):
        sel = self.tree.selection()
        if sel:
            return sel[0]
        children = self.tree.get_children()
        return children[0] if children else None

    # ---------- Preview ----------
    def preview(self):
        if not FFPLAY:
            messagebox.showerror("ffplay not found", "Install FFmpeg with: brew install ffmpeg")
            return
        iid = self.selected_or_first()
        if not iid:
            messagebox.showinfo("Preview", "Add a video first.")
            return
        subprocess.Popen([
            FFPLAY, "-loglevel", "error", "-window_title",
            "3D preview — press Q to close", "-vf", self.current_filter(), self.items[iid],
        ])

    # ---------- Conversion ----------
    def start(self):
        if self.running:
            return
        if not FFMPEG:
            messagebox.showerror("FFmpeg not found", "Install it with: brew install ffmpeg")
            return
        jobs = [(iid, self.items[iid]) for iid in self.tree.get_children()
                if self.tree.set(iid, "status") in ("Waiting", "Failed", "Cancelled")]
        if not jobs:
            messagebox.showinfo("Convert", "No videos waiting to convert.")
            return
        settings = (self.current_filter(), QUALITY_OPTIONS[self.quality_var.get()])
        self.cancel_flag.clear()
        self.set_running(True)
        threading.Thread(target=self.run_batch, args=(jobs, *settings), daemon=True).start()

    def cancel(self):
        self.cancel_flag.set()
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
        self.status_label.config(text="Cancelling…")

    def set_running(self, running):
        self.running = running
        self.start_btn.config(state="disabled" if running else "normal")
        self.cancel_btn.config(state="normal" if running else "disabled")
        self.clear_btn.config(state="disabled" if running else "normal")

    def run_batch(self, jobs, vf, crf):
        total = len(jobs)
        done_ok = 0
        for n, (iid, src) in enumerate(jobs, 1):
            if self.cancel_flag.is_set():
                self.msgq.put(("status", iid, "Cancelled"))
                continue
            dst = output_path_for(src)
            self.msgq.put(("status", iid, "Converting…"))
            self.msgq.put(("label", f"Converting {n} of {total}: {os.path.basename(src)}"))
            self.msgq.put(("progress", 0))

            ok, err = self.convert(src, dst, vf, crf)

            if ok:
                done_ok += 1
                self.msgq.put(("output", iid, dst))
                self.msgq.put(("status", iid, f"Done ✓  {os.path.basename(dst)}"))
            else:
                if os.path.exists(dst):
                    try:
                        os.remove(dst)  # remove partial file
                    except OSError:
                        pass
                if self.cancel_flag.is_set():
                    self.msgq.put(("status", iid, "Cancelled"))
                else:
                    self.msgq.put(("status", iid, "Failed"))
                    self.msgq.put(("error", os.path.basename(src), err))
        self.msgq.put(("finished", done_ok, total))

    def convert(self, src, dst, vf, crf):
        duration = get_duration(src)
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", src,
            "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats",
            dst,
        ]
        tail = deque(maxlen=15)
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in self.proc.stdout:
                line = line.strip()
                if line.startswith("out_time_us="):
                    val = line.split("=", 1)[1]
                    if val.isdigit() and duration:
                        pct = min(100.0, int(val) / 1_000_000 / duration * 100)
                        self.msgq.put(("progress", pct))
                elif re.match(r"^[a-z_0-9]+=", line):
                    pass  # other progress keys
                elif line:
                    tail.append(line)
            rc = self.proc.wait()
        except Exception as exc:
            return False, str(exc)
        finally:
            self.proc = None
        return rc == 0, "\n".join(tail) or f"FFmpeg exited with code {rc}"

    # ---------- Queue polling (UI thread) ----------
    def poll_queue(self):
        try:
            while True:
                msg = self.msgq.get_nowait()
                kind = msg[0]
                if kind == "status":
                    _, iid, text = msg
                    if self.tree.exists(iid):
                        self.tree.set(iid, "status", text)
                elif kind == "label":
                    self.status_label.config(text=msg[1])
                elif kind == "progress":
                    self.progress["value"] = msg[1]
                elif kind == "output":
                    self.outputs[msg[1]] = msg[2]
                elif kind == "error":
                    _, name, err = msg
                    messagebox.showerror("Conversion failed", f"{name}\n\n{err[-1500:]}")
                elif kind == "finished":
                    _, ok, total = msg
                    self.set_running(False)
                    self.progress["value"] = 100 if ok == total else self.progress["value"]
                    self.status_label.config(
                        text=f"Finished: {ok} of {total} converted. Files saved next to the originals.")
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    # ---------- Misc ----------
    def reveal(self):
        iid = self.selected_or_first()
        if not iid:
            return
        target = self.outputs.get(iid) or self.items.get(iid)
        if target:
            subprocess.run(["open", "-R", target])

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("Quit", "A conversion is running. Stop it and quit?"):
                return
            self.cancel()
        self.root.destroy()


def main():
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
