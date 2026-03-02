"""
LadySpecial ChatBot - Mesaj Kuyruk Yöneticisi

Birden fazla müşterinin aynı anda mesaj göndermesini güvenli şekilde yönetir.
asyncio.Semaphore ile eşzamanlı işleme limitler.
"""

import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_CHATS", "5"))


class QueueManager:
    """Mesaj işleme kuyruğu — eşzamanlı müşteri limitleyici."""

    def __init__(self, max_concurrent: int = None):
        self._semaphore = asyncio.Semaphore(max_concurrent or MAX_CONCURRENT)
        self._active_count = 0
        self._total_processed = 0

    async def process(self, coro):
        """
        Bir mesaj işleme coroutine'ini kuyrukta çalıştırır.
        Eşzamanlı limit aşılırsa bekler.

        Usage:
            result = await queue_manager.process(
                chatbot.handle_whatsapp_message(data)
            )
        """
        async with self._semaphore:
            self._active_count += 1
            try:
                result = await coro
                self._total_processed += 1
                return result
            finally:
                self._active_count -= 1

    @property
    def active_count(self) -> int:
        """Şu an aktif olarak işlenen mesaj sayısı."""
        return self._active_count

    @property
    def total_processed(self) -> int:
        """Toplam işlenen mesaj sayısı."""
        return self._total_processed

    def get_stats(self) -> dict:
        """Kuyruk istatistikleri."""
        return {
            "active": self._active_count,
            "max_concurrent": MAX_CONCURRENT,
            "total_processed": self._total_processed,
        }
