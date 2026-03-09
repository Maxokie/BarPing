import threading
import subprocess
import sys
import time
import json
import os
import webbrowser
from dataclasses import dataclass, field
from typing import Dict, Optional

import pystray
from PIL import Image, ImageDraw, ImageFont
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import winreg
except ImportError:
    winreg = None

PING_INTERVAL_SECONDS = 5
ICON_SIZE = 32
INSTANCES_FILE = os.path.join(os.path.dirname(__file__), "instances.json")
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "BarPing"


def center_window(window):
    window.update_idletasks()
    width = window.winfo_width()
    height = window.winfo_height()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = (screen_width // 2) - (width // 2)
    y = (screen_height // 2) - (height // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


@dataclass
class PingInstance:
    name: str
    address: str
    icon: Optional[pystray.Icon] = None
    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = field(default_factory=threading.Event)


class BarPingApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("BarPing - Instances")
        self.instances: Dict[str, PingInstance] = {}

        self._build_main_ui()
        self.root.update_idletasks()
        try:
            content_width = self.tree.winfo_reqwidth() + 40
            height = self.root.winfo_height()
            self.root.geometry(f"{content_width}x{height}")
        except Exception:
            pass
        center_window(self.root)
        self._load_instances()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_main_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            main_frame,
            columns=("name", "address"),
            show="headings",
            height=8,
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("address", text="IP / Host")
        self.tree.column("name", width=120)
        self.tree.column("address", width=160)
        self.tree.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(8, 0))

        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)

        add_btn = ttk.Button(btn_frame, text="Add instance", command=self.add_instance_dialog)
        add_btn.grid(row=0, column=0, sticky="ew")

        edit_btn = ttk.Button(btn_frame, text="Edit selected", command=self.edit_selected_instance)
        edit_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        remove_btn = ttk.Button(btn_frame, text="Remove selected", command=self.remove_selected_instance)
        remove_btn.grid(row=0, column=2, sticky="ew", padx=(5, 0))

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill="x", pady=(8, 0))

        self.startup_var = tk.BooleanVar(value=self._is_startup_enabled())
        startup_cb = ttk.Checkbutton(
            bottom_frame,
            text="Start BarPing with Windows",
            variable=self.startup_var,
            command=self._on_toggle_startup,
        )
        startup_cb.pack(side="left")

        about_btn = ttk.Button(bottom_frame, text="About", command=self._show_about)
        about_btn.pack(side="right")

    def add_instance_dialog(self):
        InstanceDialog(self.root, title="Add instance", on_save=self._create_instance)

    def edit_selected_instance(self):
        item_id = self._get_selected_item_id()
        if not item_id:
            messagebox.showinfo("Edit instance", "Select an instance first.")
            return
        inst = self.instances[item_id]
        InstanceDialog(
            self.root,
            title="Edit instance",
            name_initial=inst.name,
            address_initial=inst.address,
            on_save=lambda name, addr: self._update_instance(item_id, name, addr),
        )

    def remove_selected_instance(self):
        item_id = self._get_selected_item_id()
        if not item_id:
            messagebox.showinfo("Remove instance", "Select an instance first.")
            return
        self._remove_instance(item_id)

    def _get_selected_item_id(self) -> Optional[str]:
        selection = self.tree.selection()
        if not selection:
            return None
        return selection[0]

    def _create_instance(self, name: str, address: str) -> bool:
        if not name or not address:
            messagebox.showerror("Invalid data", "Name and address are required.")
            return False

        inst_id = f"inst-{len(self.instances)+1}-{time.time_ns()}"
        inst = PingInstance(name=name, address=address)
        self.instances[inst_id] = inst
        self.tree.insert("", "end", iid=inst_id, values=(name, address))

        self._start_tray_for_instance(inst_id)
        self._save_instances()
        return True

    def _update_instance(self, inst_id: str, name: str, address: str) -> bool:
        if not name or not address:
            messagebox.showerror("Invalid data", "Name and address are required.")
            return False

        inst = self.instances.get(inst_id)
        if not inst:
            return False

        inst.name = name
        inst.address = address
        self.tree.item(inst_id, values=(name, address))

        self._stop_tray_for_instance(inst_id)
        self._start_tray_for_instance(inst_id)
        self._save_instances()
        return True

    def _remove_instance(self, inst_id: str):
        inst = self.instances.pop(inst_id, None)
        if not inst:
            return

        self.tree.delete(inst_id)
        self._stop_tray_for_instance(inst_id, instance=inst)
        self._save_instances()
        if not self.instances:
            self._request_exit()

    def _save_instances(self):
        try:
            data = [
                {"id": inst_id, "name": inst.name, "address": inst.address}
                for inst_id, inst in self.instances.items()
            ]
            with open(INSTANCES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_instances(self):
        if not os.path.exists(INSTANCES_FILE):
            return
        try:
            with open(INSTANCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        if not isinstance(data, list):
            return

        for item in data:
            try:
                inst_id = str(item.get("id") or f"inst-{len(self.instances)+1}-{time.time_ns()}")
                name = str(item.get("name") or "").strip()
                addr = str(item.get("address") or "").strip()
                if not name or not addr or inst_id in self.instances:
                    continue
                inst = PingInstance(name=name, address=addr)
                self.instances[inst_id] = inst
                self.tree.insert("", "end", iid=inst_id, values=(name, addr))
                self._start_tray_for_instance(inst_id)
            except Exception:
                continue

    def _start_tray_for_instance(self, inst_id: str):
        inst = self.instances[inst_id]

        image = self._create_icon_image(inst.name, online=False)

        icon = pystray.Icon(
            name=f"BarPing-{inst_id}",
            title=f"{inst.name} - {inst.address}",
            icon=image,
            menu=pystray.Menu(
                pystray.MenuItem(
                    "Open instances window",
                    lambda icon, item: self._show_main_window(),
                ),
                pystray.MenuItem(
                    "Remove this instance",
                    lambda icon, item: self._remove_instance_from_tray(inst_id),
                ),
                pystray.MenuItem(
                    "Exit BarPing",
                    lambda icon, item: self._request_exit(),
                ),
            ),
        )

        inst.icon = icon
        inst.stop_event.clear()

        t = threading.Thread(target=self._ping_loop, args=(inst_id,), daemon=True)
        inst.thread = t
        t.start()

        icon.run_detached()

    def _stop_tray_for_instance(self, inst_id: str, instance: Optional[PingInstance] = None):
        inst = instance or self.instances.get(inst_id)
        if not inst:
            return
        inst.stop_event.set()
        if inst.icon:
            try:
                inst.icon.visible = False
                inst.icon.stop()
            except Exception:
                pass
            inst.icon = None

    def _remove_instance_from_tray(self, inst_id: str):
        def do_remove():
            if inst_id in self.instances:
                self._remove_instance(inst_id)

        self.root.after(0, do_remove)

    def _show_main_window(self):
        def do_show():
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

        self.root.after(0, do_show)

    def _is_startup_enabled(self) -> bool:
        if winreg is None or sys.platform != "win32":
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, STARTUP_REG_NAME)
                return bool(value)
        except OSError:
            return False

    def _show_about(self):
        about_win = tk.Toplevel(self.root)
        about_win.title("About BarPing")
        about_win.resizable(False, False)

        frame = ttk.Frame(about_win, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="BarPing Version 1 by Maxokie").pack(anchor="w")

        link = ttk.Label(frame, text="https://maxokie.win", foreground="blue", cursor="hand2")
        link.pack(anchor="w", pady=(4, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open("https://maxokie.win"))

        ttk.Button(frame, text="Close", command=about_win.destroy).pack(pady=(10, 0))

        center_window(about_win)

    def _on_toggle_startup(self):
        enabled = bool(self.startup_var.get())
        if enabled:
            self._enable_startup()
        else:
            self._disable_startup()

    def _enable_startup(self):
        if winreg is None or sys.platform != "win32":
            self.startup_var.set(False)
            return
        exe = sys.executable
        script = os.path.join(os.path.dirname(__file__), "barping.py")
        command = f'"{exe}" "{script}"'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, command)
        except OSError:
            self.startup_var.set(False)

    def _disable_startup(self):
        if winreg is None or sys.platform != "win32":
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, STARTUP_REG_NAME)
        except OSError:
            pass

    def _request_exit(self):
        self.root.after(0, self._exit_now)

    def _exit_now(self):
        for inst_id, inst in list(self.instances.items()):
            self._stop_tray_for_instance(inst_id, instance=inst)
        self.instances.clear()
        self.root.destroy()

    def _open_instance_settings_from_tray(self, inst_id: str):
        def do_open():
            inst = self.instances.get(inst_id)
            if not inst:
                return
            InstanceDialog(
                self.root,
                title=f"Edit {inst.name}",
                name_initial=inst.name,
                address_initial=inst.address,
                on_save=lambda name, addr: self._update_instance(inst_id, name, addr),
            )

        self.root.after(0, do_open)

    def _ping_loop(self, inst_id: str):
        while True:
            inst = self.instances.get(inst_id)
            if not inst or inst.stop_event.is_set():
                break

            is_online = self._ping(inst.address)
            if inst.icon:
                try:
                    img = self._create_icon_image(inst.name, online=is_online)
                    inst.icon.icon = img
                    inst.icon.title = f"{inst.name} - {inst.address} ({'online' if is_online else 'offline'})"
                except Exception:
                    pass

            for _ in range(PING_INTERVAL_SECONDS * 10):
                if inst.stop_event.is_set():
                    break
                time.sleep(0.1)
            if inst.stop_event.is_set():
                break

    @staticmethod
    def _ping(address: str) -> bool:
        try:
            completed = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", address],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return completed.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _create_icon_image(name: str, online: bool) -> Image.Image:
        bg_color = (0, 140, 0, 255) if online else (0, 0, 0, 255)
        text_color = (255, 255, 255, 255)
        img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), bg_color)
        draw = ImageDraw.Draw(img)

        initial = name.strip()[0].upper() if name.strip() else "?"

        try:
            font = ImageFont.truetype("arial.ttf", 22)
        except Exception:
            font = ImageFont.load_default()

        try:
            bbox = draw.textbbox((0, 0), initial, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except Exception:
            w, h = ICON_SIZE // 2, ICON_SIZE // 2
        x = (ICON_SIZE - w) / 2
        y = (ICON_SIZE - h) / 2

        draw.text((x, y), initial, font=font, fill=text_color)
        return img

    def on_close(self):
        if self.instances:
            self.root.withdraw()
        else:
            self._exit_now()


class InstanceDialog(tk.Toplevel):
    def __init__(
        self,
        master,
        title: str,
        on_save,
        name_initial: str = "",
        address_initial: str = "",
    ):
        super().__init__(master)
        self.on_save = on_save

        self.title(title)
        self.resizable(False, False)

        self.grab_set()
        self.transient(master)

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Name:").grid(row=0, column=0, sticky="e", padx=(0, 5), pady=(0, 5))
        self.name_var = tk.StringVar(value=name_initial)
        ttk.Entry(frame, textvariable=self.name_var, width=30).grid(row=0, column=1, pady=(0, 5))

        ttk.Label(frame, text="IP / Host:").grid(row=1, column=0, sticky="e", padx=(0, 5), pady=(0, 5))
        self.addr_var = tk.StringVar(value=address_initial)
        ttk.Entry(frame, textvariable=self.addr_var, width=30).grid(row=1, column=1, pady=(0, 5))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(10, 0))

        ttk.Button(btn_frame, text="Save", command=self._save).pack(side="left")
        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side="left", padx=(5, 0))

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self._cancel())

        center_window(self)

    def _save(self):
        name = self.name_var.get().strip()
        addr = self.addr_var.get().strip()
        success = self.on_save(name, addr)
        if success:
            self.destroy()

    def _cancel(self):
        self.destroy()


def main():
    root = tk.Tk()
    app = BarPingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()




