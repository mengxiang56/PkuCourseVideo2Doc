"""Windowed application entry; the source launcher remains available separately."""
import ctypes
import hashlib
from pathlib import Path
import sys
import traceback

from runtime_paths import app_home


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--self-check':
        from package_check import run_check
        return run_check(Path(sys.argv[2]))
    root = app_home()
    log_dir = root / 'data'
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir/'app-error.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    mutex = None
    api = None
    try:
        import tkinter as tk
        from tkinter import messagebox
        from app import App
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateMutexW.argtypes = [ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        api.CreateMutexW.restype = ctypes.c_void_p
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        identity = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16]
        mutex = api.CreateMutexW(None, False, 'Local\\PKUVideo2Doc-'+identity)
        already_open = ctypes.get_last_error() == 183
        window = tk.Tk()
        if already_open:
            window.withdraw()
            messagebox.showinfo('课程文字稿', '工具已经在运行，请从任务栏打开现有窗口。', parent=window)
            window.destroy()
            return 0
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CourseTranscript.Desktop.1')
        except Exception:
            pass
        def callback_error(*error):
            traceback.print_exception(*error, file=log)
            messagebox.showerror('操作未完成', '请重试当前操作。详细记录保存在 data/app-error.log。', parent=window)
        window.report_callback_exception = callback_error
        App(window)
        window.mainloop()
        return 0
    finally:
        if mutex and api:
            api.CloseHandle(mutex)


if __name__ == '__main__':
    try:
        result = main()
    except Exception:
        log_dir = app_home()/'data'
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir/'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror('课程文字稿启动未完成', '请将整个程序文件夹解压后再启动。\n详细记录：'+str(log_dir/'startup-error.log'), parent=window)
        window.destroy()
        result = 1
    raise SystemExit(result)
