"""Windows desktop entry point for PKU replay -> Tingwu -> Word."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from core import Cancelled, VIDEO_EXTENSIONS, clean_error, validate_course_url
from catalog import current_term, term_label
from credentials import CredentialStore, SITES
from engine import Workflow
from settings import load_settings, save_settings
from runtime_paths import app_home, resource_path
from version import VERSION

ROOT = app_home()


class App:
    def __init__(self, window):
        self.window = window
        window.title(f"课程文字稿 {VERSION} · 北大回放 × 通义听悟")
        icon = resource_path('assets/app.ico')
        if icon.is_file():
            window.iconbitmap(str(icon))
        window.geometry("940x840")
        window.minsize(800, 730)
        self.queue = queue.Queue()
        self.workflow = None
        self.thread = None
        self.folder = ROOT / "output"
        self.existing = None
        self.courses = []
        self.visible_courses = []
        self.replays = []
        self.pending_replays = False
        self.busy = False
        self.browser_headless = True
        self.browser_available = False
        self.waiting_for_user = False
        self.replay_loading = False
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f5f7")
        style.configure("TLabel", background="#f4f5f7", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"), foreground="#18293d")
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(14, 8))
        style.configure("Accent.TButton", background="#225e9b", foreground="white")
        outer = ttk.Frame(window, padding=25)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(heading, text="把一节课，留成文字。", style="Title.TLabel").pack(side="left")
        ttk.Button(heading, text="账号与自动登录", command=self.accounts).pack(side="right")
        ttk.Button(heading, text='使用说明', command=self.help).pack(side='right', padx=8)
        ttk.Label(outer, text="北大教学网回放 → 本机下载 → 通义听悟网页转写 → Word", foreground="#576779").pack(anchor="w", pady=(6, 21))
        browser_row = ttk.Frame(outer)
        browser_row.pack(fill="x", pady=(0, 10))
        self.background = tk.BooleanVar(value=load_settings(ROOT)["background_browser"])
        self.background_check = ttk.Checkbutton(browser_row, text="无窗口后台运行", variable=self.background, command=self.save_background)
        self.background_check.pack(side="left")
        self.browser_button = ttk.Button(browser_row, text="显示浏览器", command=self.show_browser, state="disabled")
        self.browser_button.pack(side="right")
        self.hide_browser_button = ttk.Button(browser_row, text="收起浏览器", command=self.hide_browser, state="disabled")
        self.hide_browser_button.pack(side="right", padx=8)
        self.sources = ttk.Notebook(outer)
        self.sources.pack(fill="x", pady=(0, 10))
        course_tab = ttk.Frame(self.sources, padding=12)
        url_tab = ttk.Frame(self.sources, padding=12)
        file_tab = ttk.Frame(self.sources, padding=12)
        self.sources.add(course_tab, text="选择课程回放")
        self.sources.add(url_tab, text="输入回放网址")
        self.sources.add(file_tab, text="转写已有视频")
        self.sources.bind('<<NotebookTabChanged>>', self.source_changed)
        semester_row = ttk.Frame(course_tab)
        semester_row.pack(fill="x")
        ttk.Label(semester_row, text="学期").pack(side="left")
        self.term_codes = [current_term()]
        self.term = ttk.Combobox(semester_row, state="readonly", width=29, values=[term_label(current_term()) + "（本学期）"])
        self.term.current(0)
        self.term.pack(side="left", padx=8)
        self.term.bind('<<ComboboxSelected>>', self.filter_courses)
        self.refresh_button = ttk.Button(semester_row, text="读取 / 刷新课程", command=self.refresh_courses)
        self.refresh_button.pack(side="right")
        self.course = ttk.Combobox(course_tab, state="readonly", values=[])
        self.course.pack(fill="x", pady=8)
        self.course.bind('<<ComboboxSelected>>', self.load_course_replays)
        self.catalog_status = tk.StringVar(value='读取课程后，可选择一门课程查看回放。')
        ttk.Label(course_tab, textvariable=self.catalog_status, foreground='#576779', wraplength=790).pack(anchor='w', pady=(0,6))
        self.replay_list = ttk.Treeview(course_tab, columns=('name', 'date', 'teacher'), show='headings', height=5, selectmode='browse')
        for column, label, width in [('name','回放课次',290), ('date','录制时间',180), ('teacher','教师',100)]:
            self.replay_list.heading(column, text=label)
            self.replay_list.column(column, width=width, minwidth=70)
        self.replay_list.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(course_tab, orient='vertical', command=self.replay_list.yview)
        scroll.pack(side='right', fill='y')
        self.replay_list.configure(yscrollcommand=scroll.set)
        self.replay_list.bind('<<TreeviewSelect>>', self.select_replay)
        ttk.Label(url_tab, text="课程回放网址").pack(anchor="w")
        self.url = tk.StringVar()
        pending = ROOT / ".private" / "pending_url.txt"
        if pending.exists():
            self.url.set(pending.read_text(encoding="utf-8").strip())
        self.url_entry = ttk.Entry(url_tab, textvariable=self.url, font=("Microsoft YaHei UI", 10))
        self.url_entry.pack(fill="x", pady=(6, 12), ipady=6)
        row = ttk.Frame(outer)
        row.pack(fill="x")
        title_area = ttk.Frame(row)
        title_area.pack(side="left", fill="x", expand=True)
        ttk.Label(title_area, text="课程名称（可选）").pack(anchor="w")
        self.title = tk.StringVar()
        ttk.Entry(title_area, textvariable=self.title).pack(fill="x", pady=(5, 10), ipady=5)
        self.file_label = ttk.Label(file_tab, text="选择本机完整课堂视频后开始转写。", wraplength=760)
        self.file_label.pack(anchor='w', pady=8)
        ttk.Button(file_tab, text="选择视频…", command=self.pick_existing).pack(anchor='w')
        self.source_label = ttk.Label(outer, text="直接上传完整课程视频，使用现有听悟网页账号。", foreground="#576779")
        self.source_label.pack(anchor="w")
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=16)
        self.start = ttk.Button(controls, text="下载并转写", style="Accent.TButton", command=self.begin, state='disabled')
        self.start.pack(side="left")
        self.continue_button = ttk.Button(controls, text="继续", command=self.resume, state="disabled")
        self.continue_button.pack(side="left", padx=8)
        self.stop = ttk.Button(controls, text="停止", command=self.cancel, state="disabled")
        self.stop.pack(side="left")
        ttk.Button(controls, text="打开输出文件夹", command=self.open_folder).pack(side="right")
        self.status = tk.StringVar(value="点击“读取 / 刷新课程”，选择本学期课程和回放后开始处理。")
        ttk.Label(outer, textvariable=self.status, wraplength=735, foreground="#183d68").pack(anchor="w", fill="x", pady=(0, 8))
        self.choice = ttk.Combobox(outer, state="disabled")
        self.choice.pack(fill="x", pady=(0, 8))
        self.log = scrolledtext.ScrolledText(outer, height=12, font=("Microsoft YaHei UI", 9), relief="flat", bg="#ffffff", fg="#344256", state="disabled")
        self.log.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"v{VERSION}  ·  视频和文字稿保存在 output 文件夹；登录状态保存在本机。", foreground="#667587").pack(anchor="w", pady=(12, 0))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(100, self.drain)

    def save_background(self):
        save_settings(ROOT, background_browser=self.background.get())

    def help(self):
        guide = resource_path('使用说明.txt')
        if guide.is_file():
            os.startfile(str(guide))
        else:
            messagebox.showinfo('使用说明', '保存账号 → 读取课程 → 选择回放 → 下载并转写。\n也可输入回放网址或选择本机完整视频。', parent=self.window)

    def show_browser(self):
        if self.workflow:
            self.workflow.request_browser(True)

    def hide_browser(self):
        if self.workflow:
            self.workflow.request_browser(False)

    def update_start_state(self):
        if not hasattr(self, 'start'):
            return
        ready = not self.busy
        mode = self.sources.index(self.sources.select())
        if mode == 0:
            ready = ready and self.selected_replay() is not None
        elif mode == 2:
            ready = ready and self.existing is not None
        self.start.configure(state='normal' if ready else 'disabled')

    def update_browser_buttons(self):
        ready = self.browser_available
        self.browser_button.configure(state='normal' if ready and (not self.browser_headless or self.waiting_for_user) else 'disabled')
        self.hide_browser_button.configure(state='normal' if ready and not self.browser_headless else 'disabled')

    def selected_course(self):
        index = self.course.current()
        return self.visible_courses[index] if 0 <= index < len(self.visible_courses) else None

    def selected_replay(self):
        selected = self.replay_list.selection()
        if selected:
            index = int(selected[0])
            if index < len(self.replays):
                return self.replays[index]
        return None

    def clear_replays(self):
        self.replays = []
        self.replay_list.delete(*self.replay_list.get_children())
        self.title.set('')
        self.catalog_status.set('请先选择课程并读取回放。')
        self.source_label.configure(text='选择一节回放后开始下载转写。')
        self.update_start_state()

    def filter_courses(self, event=None, *, load=True):
        code = self.term_codes[max(0, self.term.current())]
        self.visible_courses = [item for item in self.courses if item.term == code]
        self.course.configure(values=[item.name for item in self.visible_courses])
        self.course.set('')
        self.clear_replays()
        if self.visible_courses:
            self.course.current(0)
            if load:
                self.load_course_replays()
        else:
            self.status.set('这个学期尚未读到课程，可刷新课程或切换学期。')
            self.catalog_status.set('这个学期尚未读到课程。')

    def start_worker(self, action):
        if self.thread and self.thread.is_alive():
            return
        self.workflow = Workflow(ROOT, self.emit)
        self.busy = True
        self.start.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.term.configure(state="disabled")
        self.course.configure(state="disabled")
        self.background_check.configure(state="disabled")
        self.stop.configure(state="normal")
        workflow = self.workflow
        def work():
            try:
                asyncio.run(action(workflow))
            except Cancelled as error:
                self.emit("stopped", str(error))
            except Exception as error:
                self.emit("error", clean_error(error))
            finally:
                self.queue.put(("idle", None))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def refresh_courses(self):
        async def action(workflow):
            self.emit('courses', await workflow.load_courses())
        self.start_worker(action)

    def load_course_replays(self, event=None):
        course = self.selected_course()
        if not course or (self.thread and self.thread.is_alive()):
            return
        self.clear_replays()
        self.replay_loading = True
        self.catalog_status.set('正在读取该课程的回放…')
        async def action(workflow):
            listing = await workflow.load_replays(course)
            self.emit('replays', {'course_id':course.id, 'items':listing.items, 'message':listing.message, 'state':listing.state})
        self.start_worker(action)

    def select_replay(self, event=None):
        course, replay = self.selected_course(), self.selected_replay()
        if course and replay:
            self.title.set(course.name + '_' + replay.name)
            self.source_label.configure(text='所选回放：' + replay.label)
        self.update_start_state()

    def source_changed(self, event=None):
        if not hasattr(self, 'start'):
            return
        mode = self.sources.index(self.sources.select())
        self.start.configure(text='开始转写已有视频' if mode == 2 else '下载并转写')
        if mode == 0:
            self.select_replay()
        elif mode == 1:
            self.source_label.configure(text='使用输入的网址下载完整视频并转写。')
        elif self.existing:
            self.source_label.configure(text='将上传完整视频：' + self.existing.name)
        self.update_start_state()

    def accounts(self):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("任务正在运行", "请在当前任务结束后设置账号，新设置将在下次任务中使用。")
            return
        dialog = tk.Toplevel(self.window)
        dialog.title("账号与自动登录")
        dialog.geometry("640x620")
        dialog.resizable(False, False)
        dialog.transient(self.window)
        dialog.grab_set()
        container = ttk.Frame(dialog, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="密码保存在 Windows 凭据管理器中", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        ttk.Label(container, text="优先使用已登录状态；过期时再尝试密码登录。", foreground="#576779").pack(anchor="w", pady=(5, 12))
        store = CredentialStore(ROOT)
        auto = tk.BooleanVar(value=load_settings(ROOT)["auto_login"])

        def preference():
            try:
                save_settings(ROOT, auto_login=auto.get())
            except OSError:
                auto.set(not auto.get())
                messagebox.showerror("设置未保存", "无法保存自动登录设置，请检查本工具目录的写入权限。", parent=dialog)
        ttk.Checkbutton(container, text="使用保存的账号自动登录", variable=auto, command=preference).pack(anchor="w", pady=(0, 6))
        for site, label in SITES.items():
            section = ttk.LabelFrame(container, text=label, padding=12)
            section.pack(fill="x", pady=8)
            try:
                stored_user = store.username(site)
            except Exception:
                stored_user = None
            user = tk.StringVar(value=stored_user or "")
            password = tk.StringVar()
            state = tk.StringVar(value="已保存账号；密码留空可保留原密码。" if stored_user else "尚未保存账号。")
            ttk.Label(section, text="账号").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=3)
            ttk.Entry(section, textvariable=user, width=39).grid(row=0, column=1, sticky="ew")
            ttk.Label(section, text="密码").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=3)
            ttk.Entry(section, textvariable=password, show="●", width=39).grid(row=1, column=1, sticky="ew")
            section.columnconfigure(1, weight=1)

            def save(site=site, user=user, password=password, state=state):
                try:
                    account = user.get().strip()
                    if not account:
                        raise ValueError("请填写账号。")
                    if password.get():
                        store.save(site, account, password.get())
                    elif store.username(site) != account:
                        raise ValueError("保存新账号时请同时填写密码。")
                    password.set("")
                    state.set("已保存账号；密码留空可保留原密码。")
                except Exception as error:
                    messagebox.showerror("账号未保存", str(error), parent=dialog)

            def remove(site=site, user=user, password=password, state=state):
                try:
                    store.delete(site)
                    user.set("")
                    password.set("")
                    state.set("已删除本工具保存的账号；现有网页登录状态仍可使用。")
                except Exception as error:
                    messagebox.showerror("删除未完成", str(error), parent=dialog)
            buttons = ttk.Frame(section)
            buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
            ttk.Button(buttons, text="保存账号", command=save).pack(side="left")
            ttk.Button(buttons, text="删除保存项", command=remove).pack(side="left", padx=8)
            ttk.Label(section, textvariable=state, foreground="#576779", wraplength=510).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(container, text="听悟在提供密码登录时使用已保存账号；短信或扫码登录复用登录状态。验证码和二次验证由本人完成，完成后任务自动继续。", wraplength=560, foreground="#576779").pack(anchor="w", pady=(8, 0))
        ttk.Button(container, text="关闭", command=dialog.destroy).pack(anchor="e", pady=(10, 0))

    def pick_existing(self):
        if self.thread and self.thread.is_alive():
            return
        value = filedialog.askopenfilename(title="选择完整课程视频", filetypes=[("视频", " ".join("*" + suffix for suffix in sorted(VIDEO_EXTENSIONS)))])
        if value:
            self.existing = Path(value)
            self.title.set(self.existing.stem)
            self.source_label.configure(text="将上传完整视频：" + self.existing.name)
            self.file_label.configure(text=str(self.existing))
            self.sources.select(2)
            self.start.configure(text="开始转写已有视频")
            self.update_start_state()

    def begin(self):
        if self.thread and self.thread.is_alive():
            return
        mode = self.sources.index(self.sources.select())
        existing = self.existing if mode == 2 else None
        course = self.selected_course() if mode == 0 else None
        replay = self.selected_replay() if mode == 0 else None
        try:
            url = self.url.get().strip()
            if mode == 0:
                if not course or not replay:
                    raise ValueError('请先读取教学网课程，再选择一门课程和一节回放。')
            elif mode == 2 and not existing:
                raise ValueError('请先选择本机视频文件。')
            elif not existing:
                validate_course_url(url)
            elif not existing.is_file():
                raise ValueError("选择的文件已移动，请重新选择。")
            elif existing.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("请选择完整课程视频，例如 MP4、MKV 或 MOV。")
        except ValueError as error:
            messagebox.showinfo("请检查输入", str(error))
            return
        title = self.title.get().strip()
        async def action(workflow):
            await workflow.run(url, title, ROOT / 'output', existing,
                               course=course, replay_key=replay.key if replay else None)
        self.start_worker(action)

    def emit(self, kind, value):
        self.queue.put((kind, value))
        state_dir = ROOT / "data"
        state_dir.mkdir(parents=True, exist_ok=True)
        if kind in {"status", "pause", "folder", "done", "error", "stopped"}:
            message = value.get("message", "") if isinstance(value, dict) else str(value)
            with (state_dir / "last-run.log").open("a", encoding="utf-8") as log:
                log.write(kind + ": " + message + "\n")
            (state_dir / "last-status.json").write_text(json.dumps({"kind": kind, "value": value}, ensure_ascii=False), encoding="utf-8")

    def resume(self):
        if self.workflow:
            self.workflow.choice = max(0, self.choice.current())
            self.workflow.proceed.set()
            self.continue_button.configure(state="disabled")

    def cancel(self):
        if self.workflow:
            self.workflow.cancel.set()
            self.status.set("正在停止当前操作，已保存文件会保留。")
            self.stop.configure(state="disabled")

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def drain(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind in {"status", "stopped", "error"}:
                    self.status.set(value)
                    self.append(value)
                    if kind == 'error' and self.replay_loading:
                        self.catalog_status.set('回放读取失败，可重新选择课程或刷新重试。')
                elif kind == "pause":
                    self.waiting_for_user = True
                    self.update_browser_buttons()
                    self.status.set(value["message"])
                    self.append(value["message"])
                    choices = value["choices"]
                    self.choice.configure(values=choices, state="readonly" if choices else "disabled")
                    if choices:
                        self.choice.current(0)
                    self.continue_button.configure(state="normal")
                    self.window.bell()
                elif kind == "resumed":
                    self.waiting_for_user = False
                    self.update_browser_buttons()
                    self.continue_button.configure(state="disabled")
                    self.choice.configure(state="disabled")
                elif kind == "folder":
                    self.folder = Path(value)
                elif kind == 'browser_ready':
                    self.browser_available = bool(value)
                    self.update_browser_buttons()
                elif kind == 'browser_mode':
                    self.browser_headless = value == 'headless'
                    self.update_browser_buttons()
                elif kind == 'courses':
                    self.courses = value
                    self.term_codes = sorted({current_term()} | {item.term for item in value}, reverse=True)
                    self.term.configure(values=[term_label(code) + ('（本学期）' if code == current_term() else '') for code in self.term_codes])
                    self.term.current(self.term_codes.index(current_term()))
                    self.filter_courses(load=False)
                    self.pending_replays = bool(self.visible_courses)
                elif kind == 'replays':
                    selected = self.selected_course()
                    if selected and selected.id == value['course_id']:
                        self.clear_replays()
                        self.replays = value['items']
                        self.catalog_status.set(value.get('message') or ('请选择一节回放。' if self.replays else '该课程暂无课堂回放。'))
                        for index, replay in enumerate(self.replays):
                            self.replay_list.insert('', 'end', iid=str(index), values=(replay.name,replay.recorded_at,replay.teacher))
                        if self.replays:
                            self.replay_list.selection_set('0')
                            self.select_replay()
                        else:
                            self.status.set(self.catalog_status.get())
                            self.source_label.configure(text=self.catalog_status.get())
                        self.replay_loading = False
                        self.update_start_state()
                elif kind == "done":
                    self.status.set("处理完成：" + value)
                    self.append("已保存 Word：" + value)
                    messagebox.showinfo("文字稿已就绪", "Word 文件已保存：\n" + value)
                elif kind == "idle":
                    self.busy = False
                    self.replay_loading = False
                    self.waiting_for_user = False
                    self.update_start_state()
                    self.update_browser_buttons()
                    self.stop.configure(state="disabled")
                    self.continue_button.configure(state="disabled")
                    self.choice.configure(state="disabled")
                    self.refresh_button.configure(state='normal')
                    self.term.configure(state='readonly')
                    self.course.configure(state='readonly')
                    self.background_check.configure(state='normal')
                    if self.pending_replays:
                        self.pending_replays = False
                        self.window.after(150, self.load_course_replays)
        except queue.Empty:
            pass
        self.window.after(100, self.drain)

    def open_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.folder))

    def close(self):
        if self.thread and self.thread.is_alive():
            if not messagebox.askokcancel("结束当前任务", "关闭后将停止处理，已下载文件会保留。确认关闭？"):
                return
            self.cancel()
            self.status.set("正在结束任务并关闭浏览器……")
            self.window.after(250, self.wait_close)
        else:
            self.window.destroy()

    def wait_close(self):
        if self.thread and self.thread.is_alive():
            self.window.after(250, self.wait_close)
        else:
            self.window.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
