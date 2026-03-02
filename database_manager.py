import time
import os
import threading
from indexer import process_csv_for_chatbot

CSV_PATH = "ikas-urunler.csv"

class DatabaseManager:
    def __init__(self, check_interval=60):
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self.last_mtime = 0
        self.thread = None

    def start_watching(self):
        """Arka planda dosya izlemeyi başlatır."""
        # Fix: Don't auto-index on startup for debugging speed.
        # Only index if file changes LATER.
        if os.path.exists(CSV_PATH):
             self.last_mtime = os.path.getmtime(CSV_PATH)
        
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        print("👀 Veritabanı izleme servisi başlatıldı (Otomatik başlangıç indeksi KAPALI).")

    def _watch_loop(self):
        while not self._stop_event.is_set():
            time.sleep(self.check_interval)
            self._check_and_update()

    def _check_and_update(self):
        if not os.path.exists(CSV_PATH):
            return

        try:
            current_mtime = os.path.getmtime(CSV_PATH)
            if current_mtime > self.last_mtime:
                print(f"♻️ {CSV_PATH} güncellendi. Veritabanı yenileniyor...")
                process_csv_for_chatbot(CSV_PATH)
                self.last_mtime = current_mtime
        except Exception as e:
            print(f"Watch error: {e}")

    def stop(self):
        self._stop_event.set()
        if self.thread:
            self.thread.join()
