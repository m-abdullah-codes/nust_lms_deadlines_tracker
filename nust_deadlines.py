import tkinter as tk
from tkinter import messagebox
import threading
import time
import json
import requests
import urllib3
from datetime import datetime, date
import webbrowser
import os
import sys
import winreg
import ctypes
from ctypes import wintypes, Structure, c_uint, c_long, POINTER

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import pystray
    from PIL import Image
except Exception:
    pystray = None
    Image = None

try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────
LMS_BASE = "https://lms.nust.edu.pk/portal"
TOKEN_URL = LMS_BASE + "/login/token.php"
CALENDAR_URL = LMS_BASE + "/webservice/rest/server.php"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nust_config.json")
MAX_LIMITNUM = 50
AUTOSTART_VALUE = "NUSTDeadlinesTracker"

APP_WIDTH  = 560
APP_HEIGHT = 720

# ── Colors ────────────────────────────────────────────────────────────────────
COVE_DARK   = "#006BBB"
COVE_LIGHT  = "#30A0E0"
COVE_YELLOW = "#FFC872"
COVE_PEACH  = "#FFE3B3"
BG          = "#F8FAFC"
BG2         = "#FFFFFF"
TEXT        = "#0F172A"
DIM         = "#64748B"
RED         = "#FF4757"
GREEN       = "#10B981"
CARD_BG     = "#FFFFFF"
STATS_BG    = "#FFFFFF"

if CTK_AVAILABLE:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")


def status_color(days_left):
    if days_left < 0:  return RED,        "OVERDUE"
    if days_left == 0: return GREEN,       "TODAY"
    if days_left == 1: return COVE_YELLOW, "TOMORROW"
    if days_left <= 7: return COVE_LIGHT,  f"{days_left}D LEFT"
    return DIM, f"{days_left}D LEFT"


# ── Windows work-area helper ───────────────────────────────────────────────────
def _tk_work_area(tk_widget):
    """
    Returns (left, top, right, bottom) of the usable work area (taskbar excluded)
    in the same coordinate space as winfo_screenwidth/height — safe to use
    directly in geometry strings regardless of DPI-awareness state.

    Strategy: treat winfo_screenwidth/height as the canonical coordinate space.
    Use Win32 GetSystemMetrics for the physical screen size and SPI_GETWORKAREA
    for the physical work area, then convert by the ratio (Tk / physical).
    This avoids any assumption about DPI scale or awareness mode.
    """
    sw = tk_widget.winfo_screenwidth()
    sh = tk_widget.winfo_screenheight()
    try:
        phys_sw = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
        phys_sh = ctypes.windll.user32.GetSystemMetrics(1)   # SM_CYSCREEN
        rect    = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
        sx = sw / phys_sw if phys_sw > 0 else 1.0
        sy = sh / phys_sh if phys_sh > 0 else 1.0
        return (round(rect.left   * sx),
                round(rect.top    * sy),
                round(rect.right  * sx),
                round(rect.bottom * sy))
    except Exception:
        return 0, 0, sw, sh - 48   # safe fallback: assume 48-px taskbar


# ── API / Config / Notify ─────────────────────────────────────────────────────
def get_token(username, password):
    r = requests.post(TOKEN_URL,
                      data={"username": username, "password": password,
                            "service": "moodle_mobile_app"},
                      timeout=20, verify=False)
    data = r.json()
    if "token" in data:
        return data["token"]
    raise Exception(data.get("error", data.get("message", "Login failed — check credentials")))


def fetch_deadlines(token):
    now = int(time.time())
    r = requests.get(CALENDAR_URL, params={
        "wstoken": token,
        "wsfunction": "core_calendar_get_action_events_by_timesort",
        "moodlewsrestformat": "json",
        "timesortfrom": now - 14 * 86400,
        "timesortto": now + 90 * 86400,
        "limitnum": MAX_LIMITNUM,
    }, timeout=20, verify=False)
    data = r.json()
    if "exception" in data:
        raise Exception(data.get("message", "API error"))
    events = []
    for ev in data.get("events", []):
        ts = ev.get("timesort", 0)
        dt = datetime.fromtimestamp(ts)
        name   = ev["name"].replace(" is due", "").strip().strip('"')
        course = ev.get("course", {}).get("fullnamedisplay", "")
        course = course.split("2K24")[0].strip().rstrip("-").strip()
        events.append({
            "name":   name,
            "course": course,
            "date":   dt.date(),
            "time":   dt.strftime("%H:%M"),
            "url":    ev.get("action", {}).get("url") or ev.get("url", ""),
            "ts":     ts,
        })
    events.sort(key=lambda e: e["ts"])
    return events


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

def update_config(patch):
    cfg = load_config(); cfg.update(patch); save_config(cfg)

def serialize_events(events):
    return [{**e, "date": e["date"].isoformat()} for e in events]

def deserialize_events(events):
    return [{**e, "date": date.fromisoformat(e["date"])} for e in (events or [])]

def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            v, _ = winreg.QueryValueEx(k, AUTOSTART_VALUE)
            return bool(v)
    except:
        return False

def _get_startup_command():
    script_path = os.path.abspath(__file__)
    executable = sys.executable
    exe_name = os.path.basename(executable).lower()

    # Use pythonw.exe for autostart so no console window appears.
    if exe_name == "python.exe":
        pythonw_candidate = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(pythonw_candidate):
            executable = pythonw_candidate

    return f'"{executable}" "{script_path}"'

def set_autostart(enabled):
    cmd = _get_startup_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, AUTOSTART_VALUE, 0, winreg.REG_SZ, cmd)
        else:
            try: winreg.DeleteValue(k, AUTOSTART_VALUE)
            except: pass

def notify(title, message):
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=8)
    except:
        pass


# ── Main App ──────────────────────────────────────────────────────────────────
if CTK_AVAILABLE:
    class App(ctk.CTk):
        def __init__(self):
            super().__init__()
            self.title("NUST Deadlines")
            # Pre-position at bottom-right so Windows remembers the correct
            # restore location even before _show_panel() is called.
            try:
                wl, wt, wr, wb = _tk_work_area(self)
                _init_w = min(APP_WIDTH,  max(360, wr - wl - 20))
                _init_h = min(APP_HEIGHT, max(420, wb - wt - 20))
                _init_x = max(wl + 10, wr - _init_w - 10)
                _init_y = max(wt + 10, wb - _init_h - 10)
                self.geometry(f"{_init_w}x{_init_h}+{_init_x}+{_init_y}")
            except Exception:
                self.geometry(f"{APP_WIDTH}x{APP_HEIGHT}")
            self.attributes("-topmost", True)
            self.overrideredirect(False)
            self.withdraw()
            self.token    = None
            self.username = ""
            self.notified = set()
            self.deadlines = []
            self.tray_icon = None
            self.is_quitting = False

            cfg = load_config()
            self.username  = cfg.get("username", "")
            self.token     = cfg.get("token", "")
            self.deadlines = deserialize_events(cfg.get("events_cache", []))

            self._build_ui()
            self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
            self.bind("<Escape>", lambda e: self._hide_to_tray())
            self.bind("<FocusOut>", self._on_focus_out)

            # ── Smooth fast scroll ──────────────────────────────────────────
            # Bind at Tk root level with add=False so we fully replace CTk's handler.
            self.bind_all("<MouseWheel>", self._fast_scroll, add=False)

            self._setup_tray()
            if self.deadlines:
                self._update_ui(self.deadlines, None, from_cache=True)
            if self.token:
                self.after(250, self._ask_startup_preference_if_needed)
                self._start_refresh()
                self.after(300, self._hide_to_tray)
            else:
                self.after(300, self._show_login)

        # ── Scroll ─────────────────────────────────────────────────────────
        def _fast_scroll(self, event):
            """
            Fraction-based scroll: moves by a fixed % of total content per
            notch, so speed is consistent regardless of content length.
            yview_scroll("units") moves ~1-2 px; yview_moveto with a delta
            fraction moves a real visible chunk.
            """
            try:
                canvas = self.scroll_frame._parent_canvas
                # current position as fraction 0.0–1.0
                top, _ = canvas.yview()
                # 0.05 = 5% of total content per notch — fast & controllable
                delta = -0.05 if event.delta > 0 else 0.05
                canvas.yview_moveto(max(0.0, min(1.0, top + delta)))
            except Exception:
                pass
            return "break"

        # ── UI construction ─────────────────────────────────────────────────
        def _build_ui(self):
            self.configure(fg_color=BG)

            # ── Top bar ──
            bar = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
            bar.pack(fill="x", padx=20, pady=(20, 4))
            ctk.CTkLabel(bar, text="NUST Deadlines",
                         font=ctk.CTkFont(size=26, weight="bold"),
                         text_color=COVE_DARK).pack(side="left")

            btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
            btn_frame.pack(side="right")

            for text, cmd in [("⟳", self._manual_refresh),
                               ("⚙", self._show_login)]:
                ctk.CTkButton(btn_frame, text=text, width=38, height=38,
                              fg_color="transparent", text_color=DIM,
                              hover_color=COVE_PEACH,
                              font=ctk.CTkFont(size=22),
                              command=cmd).pack(side="right", padx=2)

            self.pin_btn = ctk.CTkButton(btn_frame, text="📌", width=38, height=38,
                                         fg_color="transparent", text_color=DIM,
                                         hover_color=COVE_PEACH,
                                         font=ctk.CTkFont(size=22),
                                         command=self._toggle_pin)
            self.pin_btn.pack(side="right", padx=2)

            # ── Status label ──
            self.status_var = ctk.StringVar(value="Not connected")
            ctk.CTkLabel(self, textvariable=self.status_var, text_color=DIM,
                         font=ctk.CTkFont(size=13), anchor="w").pack(
                fill="x", padx=22, pady=(0, 8))

            # ── Stats pills ──
            stats_frame = ctk.CTkFrame(self, fg_color="transparent")
            stats_frame.pack(fill="x", padx=14, pady=0)
            self.svars = {}
            for key, label, color in [
                ("overdue",   "Overdue",   RED),
                ("today",     "Today",     GREEN),
                ("tomorrow",  "Tomorrow",  COVE_YELLOW),
                ("upcoming",  "Upcoming",  COVE_LIGHT),
            ]:
                pill = ctk.CTkFrame(stats_frame, fg_color=STATS_BG,
                                    corner_radius=10,
                                    border_width=1, border_color="#E2E8F0")
                pill.pack(side="left", expand=True, fill="x", padx=3, pady=3)
                sv = ctk.StringVar(value="—")
                self.svars[key] = sv
                ctk.CTkLabel(pill, textvariable=sv,
                             font=ctk.CTkFont(size=32, weight="bold"),
                             text_color=color).pack(pady=(8, 0))
                ctk.CTkLabel(pill, text=label,
                             font=ctk.CTkFont(size=12, weight="bold"),
                             text_color=DIM).pack(pady=(0, 8))

            # ── Scrollable deadline list ──
            self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
            self.scroll_frame.pack(fill="both", expand=True, padx=6, pady=6)

            # ── Footer ──
            self.footer_var = ctk.StringVar(value="")
            ctk.CTkLabel(self, textvariable=self.footer_var, text_color=DIM,
                         font=ctk.CTkFont(size=12), height=18).pack(pady=4)

        # ── Tray ───────────────────────────────────────────────────────────
        def _setup_tray(self):
            if pystray is None or Image is None:
                self.status_var.set("Install tray support: pip install pystray pillow")
                return
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
            fallback  = r"C:\Users\LENOVO\.cursor\projects\d-works-Utility-Developments-LMS-deadlines-tracker\assets\d__works_Utility_Developments_LMS_deadlines_tracker_icon.png"
            if not os.path.exists(icon_path):
                icon_path = fallback if os.path.exists(fallback) else ""
            if not icon_path:
                self.status_var.set("icon.png not found"); return

            image = Image.open(icon_path)
            menu  = pystray.Menu(
                pystray.MenuItem("Show deadlines",       self._tray_toggle_panel, default=True),
                pystray.MenuItem("Refresh now",          self._tray_refresh),
                pystray.MenuItem("Start with Windows",   self._tray_toggle_startup,
                                 checked=lambda _: is_autostart_enabled()),
                pystray.MenuItem("Quit",                 self._tray_quit),
            )
            self.tray_icon = pystray.Icon("nust_deadlines", image, "NUST Deadlines", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

        def _ask_startup_preference_if_needed(self):
            if load_config().get("autostart_asked"):
                return
            enabled = messagebox.askyesno(
                "Start with Windows",
                "Do you want this app to start automatically when Windows starts?")
            try:
                set_autostart(enabled)
                update_config({"autostart_asked": True, "autostart_enabled": enabled})
            except OSError as ex:
                update_config({"autostart_asked": True, "autostart_enabled": False})
                self.status_var.set(f"Startup setting failed: {ex}")

        # ── Login ──────────────────────────────────────────────────────────
        def _show_login(self):
            self._show_panel()
            dialog = ctk.CTkToplevel(self)
            dialog.title("Login")
            dialog.geometry("400x400")
            dialog.grab_set()
            dialog.attributes("-topmost", True)

            ctk.CTkLabel(dialog, text="Connect to LMS",
                         font=ctk.CTkFont(size=20, weight="bold"),
                         text_color=COVE_DARK).pack(pady=(26, 4))
            ctk.CTkLabel(dialog,
                         text="Your credentials stay completely local.",
                         text_color=DIM,
                         font=ctk.CTkFont(size=12)).pack()

            form = ctk.CTkFrame(dialog, fg_color="transparent")
            form.pack(fill="x", padx=26, pady=18)

            ctk.CTkLabel(form, text="Username", text_color=TEXT,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").pack(anchor="w")
            user_input = ctk.CTkEntry(form, placeholder_text="Your NUST username",
                                      height=40, border_color="#CBD5E1", fg_color=BG2)
            user_input.pack(fill="x", pady=(2, 14))
            user_input.insert(0, self.username)

            ctk.CTkLabel(form, text="Password", text_color=TEXT,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").pack(anchor="w")
            pass_input = ctk.CTkEntry(form, placeholder_text="LMS password",
                                      height=40, show="•",
                                      border_color="#CBD5E1", fg_color=BG2)
            pass_input.pack(fill="x", pady=(2, 0))

            error_var = ctk.StringVar()
            ctk.CTkLabel(dialog, textvariable=error_var, text_color=RED,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         wraplength=320).pack(pady=(4, 0))

            def do_login():
                username = user_input.get().strip()
                password = pass_input.get().strip()
                if not username or not password:
                    error_var.set("Please fill in both fields."); return
                login_btn.configure(state="disabled", text="Connecting...")
                dialog.update()
                def worker():
                    try:
                        token = get_token(username, password)
                        self.token    = token
                        self.username = username
                        update_config({"username": username, "token": token})
                        self.after(0, lambda: (
                            dialog.destroy(),
                            self._ask_startup_preference_if_needed(),
                            self._start_refresh()))
                    except Exception as ex:
                        self.after(0, lambda: (
                            error_var.set(str(ex)),
                            login_btn.configure(state="normal", text="Log In")))
                threading.Thread(target=worker, daemon=True).start()

            login_btn = ctk.CTkButton(dialog, text="Log In", height=40,
                                      fg_color=COVE_DARK, hover_color=COVE_LIGHT,
                                      font=ctk.CTkFont(size=14, weight="bold"),
                                      command=do_login)
            login_btn.pack(pady=14)
            user_input.bind("<Return>", lambda e: pass_input.focus())
            pass_input.bind("<Return>", lambda e: do_login())
            user_input.focus_set()

        def _toggle_pin(self):
            cur = self.attributes("-topmost")
            self.attributes("-topmost", not cur)
            self.pin_btn.configure(
                fg_color="transparent" if not cur else COVE_PEACH,
                text_color=DIM if not cur else COVE_DARK)

        # ── Refresh ────────────────────────────────────────────────────────
        def _start_refresh(self):
            self.status_var.set("Fetching from LMS…")
            threading.Thread(target=self._fetch_thread, daemon=True).start()

        def _manual_refresh(self):
            if not self.token: self._show_login()
            else: self._start_refresh()

        def _fetch_thread(self):
            try:
                events = fetch_deadlines(self.token)
                self.after(0, self._update_ui, events, None, False)
            except Exception as ex:
                self.after(0, self._update_ui, None, str(ex), False)

        # ── Update UI ──────────────────────────────────────────────────────
        def _update_ui(self, events, error, from_cache=False):
            if error:
                self.status_var.set(f"Error: {error}"); return
            self.deadlines = events
            update_config({"events_cache": serialize_events(events)})
            today = date.today()
            counts = {"overdue": 0, "today": 0, "tomorrow": 0, "upcoming": 0}
            for ev in events:
                diff = (ev["date"] - today).days
                if   diff < 0:  counts["overdue"]  += 1
                elif diff == 0: counts["today"]    += 1
                elif diff == 1: counts["tomorrow"] += 1
                else:           counts["upcoming"] += 1
            for k, v in counts.items():
                self.svars[k].set(str(v))
            for ev in events:
                if (ev["date"] - today).days == 1 and ev["ts"] not in self.notified:
                    notify("Deadline Tomorrow!", f"{ev['name']}\n{ev['course']}")
                    self.notified.add(ev["ts"])

            for w in self.scroll_frame.winfo_children():
                w.destroy()

            grouped = {}
            for ev in events:
                grouped.setdefault(ev["date"], []).append(ev)

            for gdate, items in sorted(grouped.items()):
                diff  = (gdate - today).days
                color, badge = status_color(diff)

                hdr = ctk.CTkFrame(self.scroll_frame, fg_color="transparent", height=26)
                hdr.pack(fill="x", padx=6, pady=(12, 2))
                ctk.CTkLabel(
                    hdr,
                    text=gdate.strftime("%A, %d %B %Y").upper(),
                    font=ctk.CTkFont(size=13, weight="bold"),
                    height=20,
                    text_color=color if color not in [COVE_YELLOW, DIM] else TEXT,
                ).pack(side="left")
                bf = ctk.CTkFrame(hdr, fg_color=color, corner_radius=6, height=22)
                bf.pack(side="left", padx=10)
                bf.pack_propagate(False)
                ctk.CTkLabel(
                    bf, text=badge,
                    text_color="#FFFFFF" if color != COVE_YELLOW else TEXT,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    height=18,
                ).place(relx=0.5, rely=0.5, anchor="center")

                for ev in items:
                    self._card(ev, color)

            self.status_var.set(
                f"Source: {'Cache' if from_cache else 'LMS'}  •  {len(events)} deadlines")
            self.footer_var.set(
                f"Last updated: {datetime.now().strftime('%I:%M %p')}")

        # ── Deadline card ──────────────────────────────────────────────────
        def _card(self, event, color):
            card = ctk.CTkFrame(self.scroll_frame, fg_color=CARD_BG,
                                corner_radius=10, border_width=1,
                                border_color="#E2E8F0", height=68)
            card.pack_propagate(False)
            card.pack(fill="x", padx=4, pady=3)

            left_strip = ctk.CTkFrame(card, fg_color=color, width=6, corner_radius=0)
            left_strip.pack(side="left", fill="y")

            text_container = ctk.CTkFrame(card, fg_color="transparent")
            text_container.pack(side="left", fill="both", expand=True, padx=12)

            ctk.CTkLabel(text_container, text=event["name"],
                         font=ctk.CTkFont(size=15, weight="bold"),
                         height=24, text_color=TEXT,
                         anchor="w", wraplength=360).pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(text_container, text=event["course"],
                         font=ctk.CTkFont(size=13),
                         height=18, text_color=DIM,
                         anchor="w").pack(fill="x")

            ctk.CTkLabel(card, text=event["time"],
                         font=ctk.CTkFont(size=14, weight="bold"),
                         height=18, text_color=DIM).pack(side="right", padx=14)

            def click(e):
                if event["url"]: webbrowser.open(event["url"])

            for widget in [card, text_container] + list(text_container.winfo_children()):
                widget.bind("<Button-1>", click)

        # ── Window management ──────────────────────────────────────────────
        def _on_focus_out(self, event):
            if self.winfo_viewable():
                self.after(150, self._hide_if_not_focused)

        def _hide_if_not_focused(self):
            try:    focused = self.focus_displayof()
            except: focused = None
            if self.winfo_viewable() and focused is None:
                self._hide_to_tray()

        def _hide_to_tray(self):
            self.withdraw()

        def _show_panel(self):
            self.update_idletasks()
            wl, wt, wr, wb = _tk_work_area(self)
            # Clamp panel size to work area so it never overflows
            panel_w = min(APP_WIDTH,  max(360, wr - wl - 20))
            panel_h = min(APP_HEIGHT, max(420, wb - wt - 20))
            # Place flush to bottom-right corner of work area
            x = wr - panel_w - 10
            y = wb - panel_h - 10
            # Hard clamp: never go past any edge
            x = max(wl + 10, min(x, wr - panel_w))
            y = max(wt + 10, min(y, wb - panel_h))
            self.geometry(f"{panel_w}x{panel_h}+{x}+{y}")
            self.attributes("-topmost", True)
            self.deiconify()
            self.geometry(f"{panel_w}x{panel_h}+{x}+{y}")   # re-apply after deiconify
            self.lift()
            self.focus_force()

        def _tray_toggle_panel(self, icon=None, item=None):
            if self.winfo_viewable(): self.after(0, self._hide_to_tray)
            else:                     self.after(0, self._show_panel)

        def _tray_refresh(self, icon=None, item=None):
            self.after(0, self._start_refresh)

        def _tray_toggle_startup(self, icon=None, item=None):
            try:
                enabled = is_autostart_enabled()
                set_autostart(not enabled)
                update_config({"autostart_asked": True,
                               "autostart_enabled": not enabled})
            except OSError as ex:
                self.after(0, lambda: self.status_var.set(
                    f"Startup setting failed: {ex}"))

        def _tray_quit(self, icon=None, item=None):
            self.after(0, self._quit_app)

        def _quit_app(self):
            self.is_quitting = True
            if self.tray_icon: self.tray_icon.stop()
            self.destroy()

else:
    class App:
        def __init__(self):
            root = tk.Tk(); root.withdraw()
            messagebox.showinfo(
                "Missing Module",
                "Install customtkinter for the modern UI:\n pip install customtkinter")
            sys.exit(0)


if __name__ == "__main__":
    if not CTK_AVAILABLE:
        tk.Tk().withdraw()
        messagebox.showinfo(
            "Missing Module",
            "Install customtkinter for the high‑end design:\n pip install customtkinter")
    else:
        App().mainloop()