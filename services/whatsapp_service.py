"""
LadySpecial ChatBot - WhatsApp Servisi

Meta WhatsApp Cloud API ile mesaj gönderme ve medya indirme işlemlerini yönetir.
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()


class WhatsAppService:
    """WhatsApp Cloud API ile iletişim servisi."""

    API_VERSION = "v19.0"
    BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

    def __init__(self):
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
        self.access_token = os.getenv("META_ACCESS_TOKEN")

        if not self.phone_number_id or self.phone_number_id == "your_phone_number_id":
            print("⚠️ META_PHONE_NUMBER_ID ayarlanmamış! .env dosyasını kontrol edin.")
        if not self.access_token:
            print("⚠️ META_ACCESS_TOKEN ayarlanmamış! .env dosyasını kontrol edin.")

    async def send_text_message(self, to: str, text: str) -> bool:
        """
        WhatsApp uzerinden metin mesaji gonderir.
        """
        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text}
        }

        print(f"[WA-SEND] URL: {url}")
        print(f"[WA-SEND] To: {to}")
        print(f"[WA-SEND] Text length: {len(text)}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                print(f"[WA-SEND] Status Code: {response.status_code}")
                print(f"[WA-SEND] Response Body: {response.text}")

                if response.status_code in (200, 201):
                    print(f"[WA-SEND] BASARILI -> {to}")
                    return True
                else:
                    print(f"[WA-SEND] BASARISIZ: {response.status_code} - {response.text}")
                    return False
            except Exception as e:
                print(f"[WA-SEND] BAGLANTI HATASI: {e}")
                import traceback
                traceback.print_exc()
                return False

    async def download_media(self, media_id: str) -> bytes | None:
        """
        WhatsApp'tan medya dosyası indirir (görsel, ses vb.)

        Args:
            media_id: Meta API'den gelen medya ID'si

        Returns:
            Dosya içeriği (bytes) veya None
        """
        # Önce medya URL'sini al
        url = f"{self.BASE_URL}/{media_id}"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        async with httpx.AsyncClient() as client:
            try:
                # 1. Medya URL'sini al
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    print(f"❌ Medya bilgisi alınamadı: {response.text}")
                    return None

                media_url = response.json().get("url")
                if not media_url:
                    return None

                # 2. Medyayı indir
                media_response = await client.get(media_url, headers=headers)
                if media_response.status_code == 200:
                    print(f"📥 Medya indirildi ({len(media_response.content)} bytes)")
                    return media_response.content
                else:
                    print(f"❌ Medya indirme hatası: {media_response.status_code}")
                    return None

            except Exception as e:
                print(f"❌ Medya indirme bağlantı hatası: {e}")
                return None

    @staticmethod
    def parse_incoming_message(data: dict) -> dict | None:
        """
        Gelen webhook verisinden mesaj bilgisini ayrıştırır.

        Returns:
            {
                "from": "905551234567",
                "type": "text" | "image" | ...,
                "text": "mesaj metni",
                "media_id": "...",         # görsel mesajlar için
                "message_id": "wamid...",
                "timestamp": "..."
            }
            veya mesaj yoksa None
        """
        try:
            entry = data.get("entry", [])
            if not entry:
                return None

            changes = entry[0].get("changes", [])
            if not changes:
                return None

            value = changes[0].get("value", {})
            messages = value.get("messages", [])

            if not messages:
                return None

            msg = messages[0]
            sender = msg.get("from", "")
            msg_type = msg.get("type", "")
            msg_id = msg.get("id", "")
            timestamp = msg.get("timestamp", "")

            context = msg.get("context", {})
            reply_to_mid = context.get("id", None) if isinstance(context, dict) else None

            result = {
                "from": sender,
                "type": msg_type,
                "message_id": msg_id,
                "timestamp": timestamp,
                "text": None,
                "media_id": None,
                "reply_to_mid": reply_to_mid,
            }

            if msg_type == "text":
                result["text"] = msg.get("text", {}).get("body", "")
            elif msg_type == "image":
                result["media_id"] = msg.get("image", {}).get("id")
                result["text"] = msg.get("image", {}).get("caption", "")
            elif msg_type == "sticker":
                result["media_id"] = msg.get("sticker", {}).get("id")

            return result

        except Exception as e:
            print(f"❌ Mesaj ayrıştırma hatası: {e}")
            return None
