#!/usr/bin/env python3
import os
import json
import csv
import threading
import ctypes
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime, timedelta
from mutagen.mp3 import MP3
import vlc

# --- Constants for media keys ---
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

CONFIG_PATH = os.path.join(os.path.expanduser("~"), "jingle_scheduler_config.json")


def send_play_pause():
    """Send the Play/Pause media key to the system."""
    ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)


class JingleScheduler(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jingle Scheduler (Windows) with Status Details")
        self.geometry("850x650")
        self._load_config()

        # Live clock
        self.clock_label = tk.Label(self, text="", font=("Helvetica", 14))
        self.clock_label.grid(row=0, column=0, columnspan=6, pady=5, sticky='w')
        self._update_clock()

        # File pickers
        self._make_file_picker("Start jingle (MP3/WAV):", "start_jingle", 1)
        self._make_file_picker("End jingle   (MP3/WAV):", "end_jingle",   2)
        self._make_file_picker("Timetable CSV:",         "timetable",    3, filetypes=[("CSV files", "*.csv")])

        # Start scheduling & manual controls
        tk.Button(self, text="Start Scheduling", command=self.start).grid(row=4, column=0, pady=10, sticky='w')
        tk.Button(self, text="Pause Music", command=self._manual_pause).grid(row=4, column=1, padx=5)
        tk.Button(self, text="Resume Music", command=self._manual_resume).grid(row=4, column=2, padx=5)

        # Jingle info labels
        tk.Label(self, text="Start Jingle Length:").grid(row=5, column=0, sticky='e', padx=5)
        self.start_len_label = tk.Label(self, text="N/A")
        self.start_len_label.grid(row=5, column=1, sticky='w')
        tk.Label(self, text="End Jingle Length:").grid(row=5, column=2, sticky='e', padx=5)
        self.end_len_label = tk.Label(self, text="N/A")
        self.end_len_label.grid(row=5, column=3, sticky='w')

        # Next and remaining time
        tk.Label(self, text="Time until next jingle:").grid(row=6, column=0, sticky='e', padx=5)
        self.next_jingle_label = tk.Label(self, text="N/A")
        self.next_jingle_label.grid(row=6, column=1, sticky='w')
        tk.Label(self, text="Current jingle remaining:").grid(row=6, column=2, sticky='e', padx=5)
        self.current_rem_label = tk.Label(self, text="N/A")
        self.current_rem_label.grid(row=6, column=3, sticky='w')

        # Events list
        tk.Label(self, text="Loaded Events:").grid(row=7, column=0, sticky='nw', padx=5, pady=(10,0))
        self.events_listbox = tk.Listbox(self, height=10, width=60)
        self.events_listbox.grid(row=8, column=0, columnspan=4, padx=5, sticky='w')

        # Status summary
        self.status_label = tk.Label(self, text="Status: Idle", font=("Helvetica", 12, 'bold'))
        self.status_label.grid(row=9, column=0, columnspan=6, sticky='w', padx=5, pady=5)

        # Debug output
        tk.Label(self, text="Debug Output:").grid(row=10, column=0, sticky='nw', padx=5)
        self.log_text = scrolledtext.ScrolledText(self, height=8, width=100, state='disabled')
        self.log_text.grid(row=11, column=0, columnspan=6, padx=5, pady=5)

        # Initial state
        self.events = []
        self.start_length = 0
        self.end_length = 0
        self._periodic_update()

    def _make_file_picker(self, label, key, row, filetypes=None):
        tk.Label(self, text=label).grid(row=row, column=0, sticky="e", padx=5, pady=5)
        ent = tk.Entry(self, width=60)
        ent.grid(row=row, column=1, padx=5, pady=5, columnspan=3)
        ent.insert(0, self.config.get(key, ""))
        types = filetypes or [("Audio files", "*.mp3 *.wav"), ("All files", "*.*")]
        btn = tk.Button(self, text="Browse",
                        command=lambda k=key, e=ent, ft=types: self._browse(k, e, ft))
        btn.grid(row=row, column=4, padx=5, pady=5)
        setattr(self, f"entry_{key}", ent)

    def _browse(self, key, entry, filetypes):
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)
            self.config[key] = path
            self._save_config()

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                self.config = json.load(f)
        else:
            self.config = {"start_jingle": "", "end_jingle": "", "timetable": ""}

    def _save_config(self):
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)

    def start(self):
        # Save paths
        for key in ("start_jingle", "end_jingle", "timetable"):
            self.config[key] = getattr(self, f"entry_{key}").get().strip()
        self._save_config()

        # Load timetable
        try:
            self.events = self._read_timetable(self.config["timetable"])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read timetable:\n{e}")
            return

        # Read jingle lengths
        try:
            self.start_length = MP3(self.config["start_jingle"]).info.length
            self.end_length = MP3(self.config["end_jingle"]).info.length
        except:
            self.start_length = self.end_length = 0
        self.start_len_label.config(text=f"{self.start_length:.1f}s")
        self.end_len_label.config(text=f"{self.end_length:.1f}s")

        # Populate list and schedule
        self._populate_events()
        self._log(f"Loaded {len(self.events)} events.")
        self._schedule_events()
        self.status_label.config(text="Status: Events Scheduled")

    def _read_timetable(self, path):
        events = []
        with open(path, newline="") as csvfile:
            rdr = csv.reader(csvfile)
            for row in rdr:
                date_s, start_s, end_s = row
                dt_start = datetime.strptime(f"{date_s} {start_s}", "%Y-%m-%d %H:%M")
                dt_end   = datetime.strptime(f"{date_s} {end_s}",   "%Y-%m-%d %H:%M")
                # compute end jingle start
                end_start = dt_end - timedelta(seconds=self.end_length)
                events.append({"start_dt": dt_start, "end_dt": dt_end, "end_start": end_start})
        return events

    def _schedule_events(self):
        now = datetime.now()
        for ev in self.events:
            # start jingle
            if ev["start_dt"] > now:
                delay = (ev["start_dt"] - now).total_seconds()
                threading.Timer(delay, self._play_event, args=(ev, 'start')).start()
                self._log(f"Scheduled start at {ev['start_dt']}")
            # end jingle
            if ev["end_start"] > now:
                delay = (ev["end_start"] - now).total_seconds()
                threading.Timer(delay, self._play_event, args=(ev, 'end')).start()
                self._log(f"Scheduled end to start at {ev['end_start']}")

    def _populate_events(self):
        self.events_listbox.delete(0, tk.END)
        for ev in self.events:
            entry = f"{ev['start_dt'].strftime('%Y-%m-%d %H:%M')} → {ev['end_dt'].strftime('%Y-%m-%d %H:%M')}"
            self.events_listbox.insert(tk.END, entry)
        self._update_event_colors()

    def _update_event_colors(self):
        now = datetime.now()
        past, current, future = 0, 0, 0
        for idx, ev in enumerate(self.events):
            if now < ev['start_dt']:
                color = 'white'; future += 1
            elif ev['start_dt'] <= now <= ev['end_dt']:
                color = 'lightgreen'; current += 1
            else:
                color = 'lightgray'; past += 1
            self.events_listbox.itemconfig(idx, bg=color)
        self.status_label.config(text=f"Status: Past={past}  Current={current}  Future={future}")

    def _update_status_details(self):
        now = datetime.now()
        # time until next jingle
        next_times = []
        for ev in self.events:
            next_times.extend([ev['start_dt'], ev['end_start']])
        future_times = [t for t in next_times if t > now]
        if future_times:
            nxt = min(future_times)
            dt = nxt - now
            self.next_jingle_label.config(text=str(dt).split('.')[0])
        else:
            self.next_jingle_label.config(text="None")
        # current jingle remaining
        rem = None
        for ev in self.events:
            # check start jingle playing
            sj_end = ev['start_dt'] + timedelta(seconds=self.start_length)
            if ev['start_dt'] <= now < sj_end:
                rem = sj_end - now; break
            # check end jingle playing
            if ev['end_start'] <= now < ev['end_dt']:
                rem = ev['end_dt'] - now; break
        self.current_rem_label.config(text=str(rem).split('.')[0] if rem else "None")

    def _periodic_update(self):
        self._update_event_colors()
        self._update_status_details()
        self.after(1000, self._periodic_update)

    def _update_clock(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.clock_label.config(text=f"Current Time: {now}")
        self.after(1000, self._update_clock)

    def _log(self, msg: str):
        self.log_text.config(state='normal')
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def _play_event(self, event: dict, which: str):
        jinglename = 'start_jingle' if which=='start' else 'end_jingle'
        path = self.config[jinglename]
        self._log(f"Triggering {which} jingle: {path}")
        send_play_pause(); self._log("Paused music")
        player = vlc.MediaPlayer(path); player.play()
        length = MP3(path).info.length; time.sleep(length + 0.2)
        self._log(f"Played {which} ({length:.1f}s)")
        send_play_pause(); self._log("Resumed music")

    def _manual_pause(self):
        send_play_pause(); self._log("Manual pause")

    def _manual_resume(self):
        send_play_pause(); self._log("Manual resume")


if __name__ == "__main__":
    try:
        import mutagen, vlc
    except ImportError:
        tk.messagebox.showerror(
            "Missing Dependencies",
            "pip install mutagen python-vlc\nand ensure VLC is installed on Windows."
        )
        exit(1)
    app = JingleScheduler()
    app.mainloop()
