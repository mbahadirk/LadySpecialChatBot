"""
LadySpecial ChatBot - Konuşma Hafıza Servisi

Mesaj geçmişini kaydetme ve geri çağırma işlemlerini yönetir.
LLM'e gönderilecek bağlam (context) penceresini oluşturur.
Görsel mesajları da takip eder.
"""

from models.database import get_connection
from datetime import datetime


class ConversationService:
    """Mesaj kaydetme ve geçmiş sorgulama."""

    # LLM'e gönderilecek maksimum geçmiş mesaj sayısı
    MAX_CONTEXT_MESSAGES = 20

    @staticmethod
    def save_message(
        user_id: int,
        platform: str,
        role: str,
        content: str,
        intent: str = None,
        image_path: str = None,
        message_id: str = None
    ):
        """
        Bir mesajı veritabanına kaydeder.

        Args:
            user_id: Veritabanındaki kullanıcı ID'si
            platform: 'whatsapp' veya 'instagram'
            role: 'user' (müşteri mesajı) veya 'assistant' (bot cevabı)
            content: Mesaj içeriği
            intent: Mesajın sınıflandırma sonucu (opsiyonel)
            image_path: Görsel dosya yolu (opsiyonel)
            message_id: Platforma ait orijinal mesaj ID'si (opsiyonel)
        """
        conn = get_connection()
        conn.execute(
            """INSERT INTO messages (user_id, platform, role, content, intent, image_path, message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, platform, role, content, intent, image_path, message_id, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()

    @staticmethod
    def get_conversation_history(user_id: int, limit: int = None) -> list[dict]:
        """
        Kullanıcının mesaj geçmişini döndürür.
        LLM'e gönderilecek formatta (role/content) liste döner.
        Görsel mesajlar özel etiketle işaretlenir.

        Args:
            user_id: Veritabanındaki kullanıcı ID'si
            limit: Maks mesaj sayısı (varsayılan: MAX_CONTEXT_MESSAGES)

        Returns:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        if limit is None:
            limit = ConversationService.MAX_CONTEXT_MESSAGES

        conn = get_connection()
        cursor = conn.cursor()

        # En son mesajları al (ters sırayla sorgula, sonra düzelt)
        cursor.execute(
            """SELECT role, content, image_path FROM messages
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit)
        )
        rows = cursor.fetchall()
        conn.close()

        # Kronolojik sıraya çevir ve görsel etiketlerini ekle
        messages = []
        for row in reversed(rows):
            content = row["content"]

            # Görsel mesajlara bağlam bilgisi ekle
            if row["image_path"] and row["role"] == "user":
                if content and not content.startswith("["):
                    content = f"[Musteri bir urun gorseli gonderdi] {content}"
                elif not content or content.strip() == "":
                    content = "[Musteri bir urun gorseli gonderdi]"

            messages.append({"role": row["role"], "content": content})

        return messages

    @staticmethod
    def get_last_image_path(user_id: int) -> str | None:
        """
        Kullanıcının en son gönderdiği görselin dosya yolunu döndürür.
        'Önceki görsel', 'az önceki resim' gibi referanslar için kullanılır.
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT image_path FROM messages
               WHERE user_id = ? AND image_path IS NOT NULL AND role = 'user'
               ORDER BY created_at DESC
               LIMIT 1""",
            (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row["image_path"] if row else None

    @staticmethod
    def get_image_by_message_id(message_id: str) -> str | None:
        """Belirtilen mesaj ID'sine (mid) sahip mesajın görselini döndürür."""
        if not message_id:
            return None
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT image_path FROM messages WHERE message_id = ? AND image_path IS NOT NULL LIMIT 1",
            (message_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row["image_path"] if row else None

    @staticmethod
    def get_unprocessed_recent_image(user_id: int, max_age_seconds: int = 65) -> str | None:
        """
        Son max_age_seconds saniye içinde gönderilmiş, henüz bot tarafından
        yanıtlanmamış görseli bulur.

        Returns:
            image_path veya None
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Son görsel mesajını bul
        cursor.execute(
            """SELECT id, image_path, created_at FROM messages
               WHERE user_id = ? AND image_path IS NOT NULL AND role = 'user'
               ORDER BY created_at DESC
               LIMIT 1""",
            (user_id,)
        )
        image_row = cursor.fetchone()

        if not image_row:
            conn.close()
            return None

        image_id = image_row["id"]
        image_path = image_row["image_path"]
        image_created = image_row["created_at"]

        # Bu görselden sonra bir assistant yanıtı var mı?
        cursor.execute(
            """SELECT id FROM messages
               WHERE user_id = ? AND role = 'assistant' AND id > ?
               LIMIT 1""",
            (user_id, image_id)
        )
        has_response = cursor.fetchone()
        conn.close()

        if has_response:
            return None  # Zaten yanıtlanmış

        # Yaş kontrolü
        try:
            image_time = datetime.fromisoformat(image_created)
            age = (datetime.utcnow() - image_time).total_seconds()
            if age > max_age_seconds:
                return None  # Çok eski
        except Exception:
            pass

        return image_path

    @staticmethod
    def get_message_count(user_id: int) -> int:
        """Kullanıcının toplam mesaj sayısını döndürür."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM messages WHERE user_id = ?", (user_id,))
        count = cursor.fetchone()["cnt"]
        conn.close()
        return count

    @staticmethod
    def is_returning_user(user_id: int) -> bool:
        """Kullanıcının daha önce mesaj atıp atmadığını kontrol eder."""
        return ConversationService.get_message_count(user_id) > 0
