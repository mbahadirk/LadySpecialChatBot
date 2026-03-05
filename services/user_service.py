"""
LadySpecial ChatBot - Kullanıcı Servisi

Kullanıcı oluşturma, bulma ve güncelleme işlemlerini yönetir.
"""

from models.database import get_connection
from datetime import datetime


class UserService:
    """Kullanıcı CRUD işlemleri."""

    @staticmethod
    def get_or_create_user(platform: str, platform_id: str) -> dict:
        """
        Platformdan gelen ID ile kullanıcıyı bulur veya yenisini oluşturur.

        Args:
            platform: 'whatsapp', 'instagram' veya 'web'
            platform_id: Platformdaki kullanıcı ID'si

        Returns:
            Kullanıcı bilgilerini içeren dict (id, whatsapp_id, instagram_id, ...)
        """
        # Güvenlik: Platformu doğrula ve SQL enjeksiyonunu önle
        allowed_platforms = {
            "whatsapp": "whatsapp_id",
            "instagram": "instagram_id",
            "web": "whatsapp_id"  # Web için geçici olarak whatsapp_id kullanılıyor olabilir veya ayrı bir sütun gerekebilir
        }
        
        if platform not in allowed_platforms:
            print(f"⚠️ Bilinmeyen platform: {platform}")
            # Fallback veya hata fırlatma (şimdilik mevcut mantığı koruyalım ama güvenli şekilde)
            column = "whatsapp_id" 
        else:
            column = allowed_platforms[platform]

        conn = get_connection()
        cursor = conn.cursor()

        # Önce mevcut kullanıcıyı ara
        cursor.execute(f"SELECT * FROM users WHERE {column} = ?", (platform_id,))
        row = cursor.fetchone()

        if row:
            user = dict(row)
            # updated_at güncelle
            cursor.execute(
                "UPDATE users SET updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), user["id"])
            )
            conn.commit()
            conn.close()
            return user

        # Yeni kullanıcı oluştur
        now = datetime.utcnow().isoformat()
        cursor.execute(
            f"INSERT INTO users ({column}, created_at, updated_at) VALUES (?, ?, ?)",
            (platform_id, now, now)
        )
        conn.commit()
        user_id = cursor.lastrowid

        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = dict(cursor.fetchone())
        conn.close()

        print(f"🆕 Yeni kullanıcı oluşturuldu: {platform}={platform_id} (DB ID: {user_id})")
        return user

    @staticmethod
    def link_platform(user_id: int, platform: str, platform_id: str):
        """
        Mevcut kullanıcıya ikinci bir platform ID'si bağlar.
        Örneğin WhatsApp ile kayıtlı kullanıcıya Instagram ID ekler.
        """
        allowed_platforms = {
            "whatsapp": "whatsapp_id",
            "instagram": "instagram_id"
        }
        
        if platform not in allowed_platforms:
            print(f"⚠️ Linkleme için geçersiz platform: {platform}")
            return

        column = allowed_platforms[platform]
        conn = get_connection()
        conn.execute(
            f"UPDATE users SET {column} = ?, updated_at = ? WHERE id = ?",
            (platform_id, datetime.utcnow().isoformat(), user_id)
        )
        conn.commit()
        conn.close()
        print(f"🔗 Kullanıcı {user_id} → {platform}={platform_id} bağlandı.")

    @staticmethod
    def get_user_by_id(user_id: int) -> dict | None:
        """ID ile kullanıcı bilgisini döndürür."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
