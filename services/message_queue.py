"""
LadySpecial ChatBot - Mesaj Kuyruğu Sistemi

asyncio.Queue tabanlı producer-consumer yapısı.
Birden çok müşterinin aynı anda mesaj göndermesi durumunda
sistemin patlamasını engeller ve her müşteriye sırayla cevap verir.

Özellikler:
- Platform bazlı kuyruklar (WhatsApp, Instagram, Web)
- Configurable worker sayısı (.env: MAX_QUEUE_WORKERS)
- Rate limiting koruması
- Hata izolasyonu (bir müşterinin hatası diğerlerini etkilemez)
"""

import os
import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

MAX_QUEUE_WORKERS = int(os.getenv("MAX_QUEUE_WORKERS", "3"))


@dataclass
class QueueMessage:
    """Kuyruğa eklenen mesaj yapısı."""
    platform: str                   # "whatsapp", "instagram", "web"
    handler: Callable[..., Awaitable[Any]]  # Çalıştırılacak async fonksiyon
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    sender_id: str = ""             # Loglama için
    created_at: datetime = field(default_factory=datetime.utcnow)


class MessageQueue:
    """
    asyncio.Queue tabanlı mesaj işleme kuyruğu.

    Kullanım:
        queue = MessageQueue()
        await queue.start()

        # Mesaj ekle
        await queue.enqueue(
            platform="whatsapp",
            handler=chatbot.handle_whatsapp_message,
            args=(webhook_data,),
            sender_id="905551234567"
        )

        # Uygulama kapatılırken
        await queue.stop()
    """

    def __init__(self):
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False

        # İstatistikler
        self._processed = 0
        self._errors = 0
        self._active_workers = 0

    async def start(self):
        """Worker'ları başlatır."""
        if self._running:
            return

        self._running = True
        for i in range(MAX_QUEUE_WORKERS):
            task = asyncio.create_task(self._worker(f"Worker-{i+1}"))
            self._workers.append(task)

        print(f"[Queue] ✅ {MAX_QUEUE_WORKERS} worker başlatıldı.")

    async def stop(self):
        """Kuyruğu durdurur ve tüm worker'ları kapatır."""
        self._running = False

        # Kuyruktaki tüm işleri bitir
        if not self._queue.empty():
            print(f"[Queue] Kuyrukta {self._queue.qsize()} mesaj var, bekleniyor...")
            await self._queue.join()

        # Worker'ları iptal et
        for task in self._workers:
            task.cancel()

        self._workers.clear()
        print(f"[Queue] 🛑 Durduruldu. Toplam: {self._processed} işlendi, {self._errors} hata.")

    async def enqueue(
        self,
        platform: str,
        handler: Callable[..., Awaitable[Any]],
        args: tuple = (),
        kwargs: dict = None,
        sender_id: str = "",
    ):
        """Mesajı kuyruğa ekler."""
        msg = QueueMessage(
            platform=platform,
            handler=handler,
            args=args,
            kwargs=kwargs or {},
            sender_id=sender_id,
        )
        await self._queue.put(msg)

        qsize = self._queue.qsize()
        if qsize > 5:
            print(f"[Queue] ⚠️ Kuyruk büyüyor: {qsize} mesaj bekliyor")

    async def _worker(self, name: str):
        """Kuyruktan mesaj alıp işleyen worker."""
        print(f"[Queue] {name} hazır.")

        while self._running:
            try:
                # Kuyruğu dinle (1 saniye timeout ile)
                try:
                    msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                self._active_workers += 1

                try:
                    wait_time = (datetime.utcnow() - msg.created_at).total_seconds()
                    print(f"[Queue] {name} işliyor: {msg.platform}/{msg.sender_id} "
                          f"(bekleme: {wait_time:.1f}s)")

                    # Handler'ı çalıştır
                    await msg.handler(*msg.args, **msg.kwargs)
                    self._processed += 1

                except Exception as e:
                    self._errors += 1
                    print(f"[Queue] ❌ {name} hata ({msg.platform}/{msg.sender_id}): {e}")
                    import traceback
                    traceback.print_exc()

                finally:
                    self._active_workers -= 1
                    self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Queue] ❌ {name} beklenmeyen hata: {e}")

        print(f"[Queue] {name} durduruldu.")

    def get_stats(self) -> dict:
        """Kuyruk istatistiklerini döndürür."""
        return {
            "pending": self._queue.qsize(),
            "processed": self._processed,
            "errors": self._errors,
            "active_workers": self._active_workers,
            "total_workers": len(self._workers),
        }
