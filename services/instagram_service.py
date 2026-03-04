"""
LadySpecial ChatBot - Instagram DM Servisi

Meta Graph API ile Instagram Direct mesajlarını alır ve gönderir.
WhatsApp servisine paralel çalışır.
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()


class InstagramService:
    """Instagram Graph API ile mesaj gönderme ve medya indirme servisi."""

    API_VERSION = "v21.0"
    # Instagram Messaging API ayrı bir domain kullanıyor!
    IG_BASE_URL = f"https://graph.instagram.com/{API_VERSION}"
    FB_BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

    def __init__(self):
        # Instagram kendi tokenını kullanır, yoksa META_ACCESS_TOKEN'a düşer
        self.access_token = (
            os.getenv("INSTAGRAM_ACCESS_TOKEN") or
            os.getenv("META_ACCESS_TOKEN")
        )
        if not self.access_token:
            print("⚠️ Instagram erişim tokeni ayarlanmamış!")
        else:
            token_source = "INSTAGRAM_ACCESS_TOKEN" if os.getenv("INSTAGRAM_ACCESS_TOKEN") else "META_ACCESS_TOKEN"
            print(f"[IG] Token kaynağı: {token_source}")

    async def send_text_message(self, recipient_id: str, text: str) -> bool:
        """
        Instagram DM üzerinden metin mesajı gönderir.

        Args:
            recipient_id: Alıcının Instagram-scoped user ID'si (IGSID)
            text: Gönderilecek mesaj

        Returns:
            Başarılı ise True
        """
        # Instagram Messaging API endpoint
        url = f"{self.IG_BASE_URL}/me/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }

        IG_MAX_LENGTH = 1000

        # Instagram 1000 karakter limiti — uzun mesajları böl
        if len(text) > IG_MAX_LENGTH:
            chunks = self._split_message(text, IG_MAX_LENGTH)
            print(f"[IG-SEND] Mesaj {len(text)} karakter, {len(chunks)} parçaya bölünüyor")
            success = True
            async with httpx.AsyncClient(timeout=30.0) as client:
                for i, chunk in enumerate(chunks):
                    payload = {
                        "recipient": {"id": recipient_id},
                        "message": {"text": chunk},
                    }
                    try:
                        response = await client.post(url, json=payload, headers=headers)
                        print(f"[IG-SEND] Parça {i+1}/{len(chunks)}: {response.status_code}")
                        if response.status_code not in (200, 201):
                            print(f"[IG-SEND] ❌ Parça hatası: {response.text}")
                            success = False
                        import asyncio
                        await asyncio.sleep(0.5)  # Rate limiting
                    except Exception as e:
                        print(f"[IG-SEND] ❌ Bağlantı hatası: {e}")
                        success = False
            return success

        print(f"[IG-SEND] To: {recipient_id}")
        print(f"[IG-SEND] Text length: {len(text)}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                print(f"[IG-SEND] Status: {response.status_code}")

                if response.status_code in (200, 201):
                    print(f"[IG-SEND] ✅ Başarılı → {recipient_id}")
                    return True
                else:
                    print(f"[IG-SEND] ❌ Başarısız: {response.text}")
                    return False
            except Exception as e:
                print(f"[IG-SEND] ❌ Bağlantı hatası: {e}")
                return False

    @staticmethod
    def _split_message(text: str, max_len: int) -> list[str]:
        """Uzun mesajı paragraf/satır sınırlarından böler."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break

            # Önce çift satır sonundan bölmeyi dene (paragraf sönü)
            split_pos = text.rfind("\n\n", 0, max_len)
            if split_pos == -1:
                # Tek satır sonundan bölmeyi dene
                split_pos = text.rfind("\n", 0, max_len)
            if split_pos == -1:
                # Son çare: boşluktan böl
                split_pos = text.rfind(" ", 0, max_len)
            if split_pos == -1:
                # Hiç bulunamadı, zorla böl
                split_pos = max_len

            chunks.append(text[:split_pos].strip())
            text = text[split_pos:].strip()

        return [c for c in chunks if c]

    async def download_media(self, media_url: str) -> bytes | None:
        """
        Instagram'dan medya dosyası indirir.

        Args:
            media_url: Medya URL'si

        Returns:
            Dosya içeriği (bytes) veya None
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(media_url, headers=headers)
                if response.status_code == 200:
                    print(f"📥 IG medya indirildi ({len(response.content)} bytes)")
                    return response.content
                else:
                    print(f"❌ IG medya indirme hatası: {response.status_code}")
                    return None
            except Exception as e:
                print(f"❌ IG medya indirme bağlantı hatası: {e}")
                return None

    @staticmethod
    def parse_incoming_message(data: dict) -> dict | None:
        """
        Instagram webhook verisinden mesaj bilgisini ayrıştırır.

        Instagram messaging webhook yapısı:
        {
            "object": "instagram",
            "entry": [{
                "id": "<page_id>",
                "time": ...,
                "messaging": [{
                    "sender": {"id": "<igsid>"},
                    "recipient": {"id": "<page_igsid>"},
                    "timestamp": ...,
                    "message": {
                        "mid": "...",
                        "text": "merhaba",
                        "attachments": [{"type": "image", "payload": {"url": "..."}}]
                    }
                }]
            }]
        }

        Returns:
            {
                "from": "igsid_123",
                "type": "text" | "image" | ...,
                "text": "mesaj metni",
                "media_url": "...",
                "message_id": "mid...",
            }
            veya None
        """
        try:
            entries = data.get("entry", [])
            if not entries:
                return None

            for entry in entries:
                messaging_list = entry.get("messaging", [])
                if not messaging_list:
                    continue

                msg_event = messaging_list[0]
                sender_id = msg_event.get("sender", {}).get("id", "")
                message = msg_event.get("message", {})

                if not message or not sender_id:
                    continue

                msg_id = message.get("mid", "")
                text = message.get("text", "")

                # Attachment kontrolü
                attachments = message.get("attachments", [])
                media_url = None
                ig_post_media_id = None
                msg_type = "text"

                if attachments:
                    for att in attachments:
                        att_type = att.get("type", "")
                        payload = att.get("payload", {})
                        payload_url = payload.get("url")
                        ig_mid = payload.get("ig_post_media_id") or payload.get("reel_video_id")
                        
                        if ig_mid:
                            ig_post_media_id = ig_mid
                            
                        # Eğer reel ise başlığı (caption'ı) direkt payload içinde veriyor!
                        title = payload.get("title", "")
                        if title and title not in text:
                            text = f"{text}\n\n{title}".strip()
                            
                        if att_type == "image":
                            msg_type = "share" if msg_type == "share" else "image"
                            media_url = payload_url
                        elif att_type in ("video", "ig_post", "ig_reel", "share"):
                            msg_type = "share"
                            if not media_url:
                                media_url = payload_url
                            
                            # Eğer type=share ise ve insta url'si payload'da varsa ekstra text olarak ekle
                            if att_type == "share" and payload_url and "instagram.com" in payload_url and payload_url not in text:
                                text = f"{text} {payload_url}".strip()

                reply_to = message.get("reply_to", {})
                reply_to_mid = reply_to.get("mid", None) if isinstance(reply_to, dict) else None

                return {
                    "from": sender_id,
                    "type": msg_type,
                    "text": text,
                    "media_url": media_url,
                    "ig_post_media_id": ig_post_media_id,
                    "message_id": msg_id,
                    "reply_to_mid": reply_to_mid,
                }

            return None

        except Exception as e:
            print(f"❌ IG mesaj parse hatası: {e}")
            return None
