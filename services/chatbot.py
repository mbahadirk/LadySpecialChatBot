"""
LadySpecial ChatBot - Ana Orkestratör

Tüm servisleri koordine eden merkezi sınıf.

Görsel Mekanizması (çift güvenlikli):
- Görsel + caption → Anında cevap
- Görsel (captionsız) → IMAGE_WAIT_TIMEOUT_SECONDS saniye bekle
  - Süre içinde mesaj gelirse → Görsel + mesajı birlikte işle
  - Süre geçerse → Görseli otomatik olarak ürün veritabanında arat
- Koordinasyon: In-memory dict + DB fallback (çift kontrol)
- Timer, çalışmadan önce DB'de görselin zaten yanıtlanıp yanıtlanmadığını kontrol eder

Desteklenen platformlar: WhatsApp, Instagram, Web
"""

import os
import re
from datetime import datetime, timedelta
import asyncio
import httpx

from dotenv import load_dotenv

from services.user_service import UserService
from services.conversation_service import ConversationService
from services.product_service import ProductService
from services.llm_service import LLMService
from services.whatsapp_service import WhatsAppService
from services.instagram_service import InstagramService
from services.image_service import ImageService
from services.order_service import OrderService

load_dotenv()

# Konfigürasyon
IMAGE_WAIT_TIMEOUT = int(os.getenv("IMAGE_WAIT_SECONDS", "60"))
ADMIN_TAKEOVER_TIMEOUT = int(os.getenv("ADMIN_TAKEOVER_TIMEOUT_MINUTES", "30"))


class ChatBot:
    """LadySpecial AI Satış Asistanı."""

    def __init__(self):
        self.user_service = UserService()
        self.conversation_service = ConversationService()
        self.product_service = ProductService()
        self.llm_service = LLMService(model="gpt-4o-mini")
        self.whatsapp_service = WhatsAppService()
        self.instagram_service = InstagramService()
        self.image_service = ImageService()
        self.order_service = OrderService()

        # Bot'un kendi gönderdiği mesajların zamanları (platform_sender_id -> timestamp)
        # Bu, bot echo'ları ile admin mesajlarını ayırmak için kullanılır
        import time
        self._bot_last_send: dict[str, float] = {}

        # Bekleyen görseller (in-memory, hız için)
        # {platform_sender_id: {"image_path": str, "task": asyncio.Task, "user_id": int, "platform": str}}
        self._pending_images: dict = {}

        # Admin takeover: Yönetici bir müşteriyle konuşurken bot'u sustur
        # {platform_sender_id: {"started_at": datetime, "last_echo_at": datetime}}
        self._admin_takeover: dict = {}

        # İşlenmiş mesaj ID'leri (duplicate webhook koruması)
        self._processed_message_ids: set = set()

        print("[ChatBot] Baslatildi.")
        print(f"[ChatBot] Urun sayisi: {self.product_service.get_product_count()}")
        print(f"[ChatBot] Gorsel bekleme suresi: {IMAGE_WAIT_TIMEOUT}s")
        print(f"[ChatBot] Admin takeover timeout: {ADMIN_TAKEOVER_TIMEOUT} dakika")

    # ══════════════════════════════════════════
    #  ANA GİRİŞ NOKTALARI
    # ══════════════════════════════════════════

    async def handle_whatsapp_message(self, webhook_data: dict) -> str | None:
        """WhatsApp webhook verisini isler."""


        parsed = WhatsAppService.parse_incoming_message(webhook_data)
        if not parsed:
            print("[ChatBot] WA Parse sonucu: None")
            return None

        # Admin takeover: Eğer yönetici aktifse bu mesajı işleme
        if self._is_admin_active("whatsapp", parsed["from"]):
            print(f"[ChatBot] ⏸️ Admin takeover aktif (WA/{parsed['from']}), mesaj işlenmiyor.")
            return None

        sender_id = parsed["from"]
        msg_type = parsed["type"]
        msg_id = parsed.get("message_id", "")

        # Duplicate kontrolü
        if self._is_duplicate(msg_id):
            return None

        print(f"\n{'='*55}")
        print(f"[ChatBot] WA | Gonderen: {sender_id} | Tip: {msg_type} | ID: {msg_id}")
        print(f"{'='*55}")

        user = self.user_service.get_or_create_user("whatsapp", sender_id)
        user_id = user["id"]

        if msg_type == "image":
            return await self._handle_wa_image(sender_id, user_id, parsed)
        elif msg_type == "text" and parsed.get("text"):
            return await self._handle_text_message("whatsapp", sender_id, user_id, parsed)
        else:
            print(f"[ChatBot] Desteklenmeyen WA mesaj tipi: {msg_type}")
            return None

    async def handle_instagram_message(self, webhook_data: dict) -> str | None:
        """Instagram webhook verisini isler."""

        parsed = InstagramService.parse_incoming_message(webhook_data)
        if not parsed:
            print("[ChatBot] IG Parse sonucu: None")
            return None

        # Admin takeover: Eğer yönetici aktifse bu mesajı işleme
        if self._is_admin_active("instagram", parsed["from"]):
            print(f"[ChatBot] ⏸️ Admin takeover aktif (IG/{parsed['from']}), mesaj işlenmiyor.")
            return None

        sender_id = parsed["from"]
        msg_type = parsed["type"]
        msg_id = parsed.get("message_id", "")

        # Duplicate kontrolü
        if self._is_duplicate(msg_id):
            return None

        # Echo kontrolü — kendi mesajlarımıza cevap verme
        # (Instagram bazen kendi gönderdiğimiz mesajları da webhook olarak gönderir)

        print(f"\n{'='*55}")
        print(f"[ChatBot] IG | Gonderen: {sender_id} | Tip: {msg_type} | ID: {msg_id}")
        print(f"{'='*55}")

        user = self.user_service.get_or_create_user("instagram", sender_id)
        user_id = user["id"]

        if msg_type == "image":
            return await self._handle_ig_image(sender_id, user_id, parsed)
        elif msg_type == "share":
            # Share mesajlarında Meta bir önizleme görseli (media_url) sunar. 
            # Varsa bunu görsel tabanlı ürün araması olarak değerlendir.
            if parsed.get("media_url"):
                return await self._handle_ig_image(sender_id, user_id, parsed)
            else:
                # Eğer görsel yoksa, normal metin olarak link ayıklamaya gönder
                return await self._handle_text_message("instagram", sender_id, user_id, parsed)
        elif msg_type == "text" and parsed.get("text"):
            return await self._handle_text_message("instagram", sender_id, user_id, parsed)
        else:
            print(f"[ChatBot] Desteklenmeyen IG mesaj tipi: {msg_type}")
            return None

    # ══════════════════════════════════════════
    #  DUPLICATE KORUMASI
    # ══════════════════════════════════════════

    def _is_duplicate(self, msg_id: str) -> bool:
        """Mesaj ID tekrarı kontrolü."""
        if not msg_id:
            return False
        if msg_id in self._processed_message_ids:
            print(f"[ChatBot] DUPLICATE: {msg_id} zaten islendi, atlaniyor.")
            return True
        self._processed_message_ids.add(msg_id)
        # Bellekte en fazla 200 ID tut
        if len(self._processed_message_ids) > 200:
            self._processed_message_ids.pop()
        return False

    # ══════════════════════════════════════════
    #  WHATSAPP GÖRSEL İŞLEME
    # ══════════════════════════════════════════

    async def _handle_wa_image(self, sender_id: str, user_id: int, parsed: dict) -> str | None:
        """WhatsApp görsel mesajı işler."""
        media_id = parsed.get("media_id")
        caption = parsed.get("text", "")

        if not media_id:
            print("[ChatBot] media_id bulunamadi")
            return None

        print(f"[ChatBot] WA Gorsel indiriliyor... (media_id: {media_id})")
        image_bytes = await self.whatsapp_service.download_media(media_id)

        if not image_bytes:
            error_msg = "Gorseli indiremedim, lutfen tekrar gonderir misiniz?"
            await self.whatsapp_service.send_text_message(sender_id, error_msg)
            return error_msg

        image_path = self.image_service.save_image(user_id, image_bytes)
        print(f"[ChatBot] Gorsel kaydedildi: {image_path}")

        self.conversation_service.save_message(
            user_id=user_id, platform="whatsapp", role="user",
            content=f"[Gorsel gonderildi] {caption}" if caption else "[Gorsel gonderildi]",
            image_path=image_path,
            message_id=parsed.get("message_id")
        )

        if caption:
            print(f"[ChatBot] WA Gorsel + caption: '{caption}'")
            response = await self._process_image_with_text(user_id, image_path, caption)
            if response:
                await self.whatsapp_service.send_text_message(sender_id, response)
            return response
        else:
            print(f"[ChatBot] WA Gorsel captionsiz. {IMAGE_WAIT_TIMEOUT}s bekleme baslatiliyor...")
            self._set_pending_image("whatsapp", sender_id, user_id, image_path)
            return None

    # ══════════════════════════════════════════
    #  INSTAGRAM GÖRSEL İŞLEME
    # ══════════════════════════════════════════

    async def _handle_ig_image(self, sender_id: str, user_id: int, parsed: dict) -> str | None:
        """Instagram görsel mesajı işler."""
        media_url = parsed.get("media_url")
        text = parsed.get("text", "")

        if not media_url:
            print("[ChatBot] IG media_url bulunamadi")
            return None

        print(f"[ChatBot] IG Gorsel indiriliyor...")
        image_bytes = await self.instagram_service.download_media(media_url)

        if not image_bytes:
            error_msg = "Gorseli indiremedim, lutfen tekrar gonderir misiniz? 🙏"
            await self.instagram_service.send_text_message(sender_id, error_msg)
            return error_msg

        image_path = self.image_service.save_image(user_id, image_bytes)
        print(f"[ChatBot] IG Gorsel kaydedildi: {image_path}")

        self.conversation_service.save_message(
            user_id=user_id, platform="instagram", role="user",
            content=f"[Gorsel gonderildi] {text}" if text else "[Gorsel gonderildi]",
            image_path=image_path,
            message_id=parsed.get("message_id")
        )

        if text:
            print(f"[ChatBot] IG Gorsel + text: '{text}'")
            response = await self._process_image_with_text(user_id, image_path, text, base_platform="instagram", message_id=parsed.get("message_id"))
            if response:
                await self._send_message("instagram", sender_id, response)
            return response
        else:
            print(f"[ChatBot] IG Gorsel textsiz. {IMAGE_WAIT_TIMEOUT}s bekleme baslatiliyor...")
            self._set_pending_image("instagram", sender_id, user_id, image_path)
            return None

    # ══════════════════════════════════════════
    #  PENDING IMAGE MEKANIZMASI (Ortak)
    # ══════════════════════════════════════════

    def _get_pending_key(self, platform: str, sender_id: str) -> str:
        """Platform+sender bazlı benzersiz anahtar."""
        return f"{platform}_{sender_id}"

    def _set_pending_image(self, platform: str, sender_id: str, user_id: int, image_path: str):
        """Bekleyen görsel kaydı oluşturur ve timer başlatır."""
        key = self._get_pending_key(platform, sender_id)

        # Önceki bekleyen varsa iptal et
        if key in self._pending_images:
            old = self._pending_images[key]
            old["task"].cancel()
            print(f"[ChatBot] Onceki bekleyen gorsel iptal edildi: {key}")

        task = asyncio.create_task(
            self._auto_process_image_timer(platform, sender_id, user_id, image_path)
        )
        self._pending_images[key] = {
            "image_path": image_path,
            "task": task,
            "user_id": user_id,
            "platform": platform,
        }
        print(f"[ChatBot] Pending eklendi: {key} -> {image_path}")

    async def _auto_process_image_timer(self, platform: str, sender_id: str, user_id: int, image_path: str):
        """IMAGE_WAIT_TIMEOUT saniye sonra görseli otomatik işler."""
        key = self._get_pending_key(platform, sender_id)

        try:
            await asyncio.sleep(IMAGE_WAIT_TIMEOUT)

            # DB'de bu görsel zaten yanıtlanmış mı kontrol et
            unprocessed = self.conversation_service.get_unprocessed_recent_image(user_id)
            if not unprocessed:
                print(f"[ChatBot] Timer: Gorsel zaten yanitlanmis: {key}")
                return

            print(f"[ChatBot] {IMAGE_WAIT_TIMEOUT}s doldu, gorsel otomatik aratiliyor: {key}")

            search_results = self.image_service.search_by_image(image_path, max_results=5)
            history = self.conversation_service.get_conversation_history(user_id)

            response = self.llm_service.generate_image_search_response(
                search_results=search_results,
                conversation_history=history
            )

            self.conversation_service.save_message(
                user_id=user_id, platform=platform, role="assistant",
                content=response, intent="product_inquiry"
            )

            # Platforma göre gönder
            if response:
                await self._send_message(platform, sender_id, response)
                print(f"[ChatBot] Otomatik gorsel cevabi gonderildi: {key}")

        except asyncio.CancelledError:
            print(f"[ChatBot] Timer iptal edildi (mesaj geldi): {key}")

        finally:
            self._pending_images.pop(key, None)

    # ══════════════════════════════════════════
    #  METİN MESAJ İŞLEME (Ortak)
    # ══════════════════════════════════════════

    async def _handle_text_message(self, platform: str, sender_id: str, user_id: int, parsed: dict) -> str | None:
        """Metin mesajını işler (WhatsApp ve Instagram ortak)."""
        text = parsed.get("text", "")
        msg_id = parsed.get("message_id", "")
        reply_to_mid = parsed.get("reply_to_mid")
        key = self._get_pending_key(platform, sender_id)
        
        # Eğer bu mesaj bir eski gönderiye yanıtsa ve bu gönderi görsel isek bypass yap:
        if reply_to_mid:
            sys_img = self.conversation_service.get_image_by_message_id(reply_to_mid)
            if sys_img:
                print(f"[ChatBot] Eskiden gonderilen gorsele yanit algilandi: {sys_img}")
                response = await self._process_image_with_text(user_id, sys_img, text, base_platform=platform, message_id=msg_id)
                if response:
                    await self._send_message(platform, sender_id, response)
                return response

        # 1. LİNK KONTROLÜ (Ürün linki gönderildiyse görselini çek ve arat)
        url_match = re.search(r'(https?://[^\s]+)', text)
        if url_match:
            extracted_url = url_match.group(1)
            print(f"[ChatBot] Mesajda link bulundu: {extracted_url}")
            
            # Instagram Linki kontrolü
            ig_match = re.search(r'instagram\.com/(?:p|reel)/([^/?]+)', extracted_url)
            if ig_match:
                shortcode = ig_match.group(1)
                conn = get_connection()
                row = conn.execute("SELECT caption, media_url FROM instagram_posts WHERE shortcode = ?", (shortcode,)).fetchone()
                conn.close()
                if row and row["caption"]:
                    caption = row["caption"]
                    print(f"[ChatBot] IG DB Caption bulundu: {caption[:50]}...")
                    found_skus = self.product_service.extract_skus_from_text(caption)
                    if found_skus:
                        products = self.product_service.get_products_by_skus(found_skus)
                        if products:
                            print(f"[ChatBot] IG Gönderisinden ürünler bulundu: {found_skus}")
                            history = self.conversation_service.get_conversation_history(user_id)
                            response = self.llm_service.generate_instagram_link_response(
                                user_message=text,
                                search_results=products,
                                conversation_history=history
                            )
                            # Kullanıcının metniyle response kaydet
                            self.conversation_service.save_message(user_id=user_id, platform=platform, role="user", content=text, message_id=msg_id)
                            self.conversation_service.save_message(user_id=user_id, platform=platform, role="assistant", content=response, intent="product_inquiry")
                            if response:
                                await self._send_message(platform, sender_id, response)
                            return response
                    
                    # SKU bulunamadı ama IG DB'de media_url varsa → görseli indir ve arat
                    ig_media_url = row.get("media_url", "") if row else ""
                    if ig_media_url:
                        print(f"[ChatBot] IG SKU eşleşmedi, post görseli ile aranıyor...")
                        try:
                            async with httpx.AsyncClient(timeout=15.0) as client:
                                img_resp = await client.get(ig_media_url)
                                if img_resp.status_code == 200:
                                    image_path = self.image_service.save_image(user_id, img_resp.content)
                                    print(f"[ChatBot] IG post görseli kaydedildi: {image_path}")
                                    response = await self._process_image_with_text(user_id, image_path, text, base_platform=platform, message_id=msg_id)
                                    if response:
                                        await self._send_message(platform, sender_id, response)
                                    return response
                                else:
                                    print(f"[ChatBot] IG post görseli indirilemedi: {img_resp.status_code}")
                        except Exception as e:
                            print(f"[ChatBot] IG post görseli indirme hatası: {e}")
                            
            # (Fallback: diğer linklerin og:image arama mantığı)
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(extracted_url)
                    if resp.status_code == 200:
                        # og:image (Açık Grafik Görseli) ara
                        og_match = re.search(r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+?)["\']', resp.text, re.IGNORECASE)
                        if og_match:
                            img_url = og_match.group(1)
                            print(f"[ChatBot] Linkten görsel bulundu: {img_url}")
                            
                            # Görseli indir
                            img_resp = await client.get(img_url)
                            if img_resp.status_code == 200:
                                image_path = self.image_service.save_image(user_id, img_resp.content)
                                print(f"[ChatBot] Link görseli kaydedildi, modelden geçiriliyor: {image_path}")
                                
                                # Görsel ve metni birlikte işleyerek cevap ver
                                response = await self._process_image_with_text(user_id, image_path, text, base_platform=platform, message_id=msg_id)
                                if response:
                                    await self._send_message(platform, sender_id, response)
                                return response
            except Exception as e:
                print(f"[ChatBot] Link işleme hatası: {e}")
                # Hata olursa kırılma, normal metin işlemeye devam et

        # 0. SİPARİŞ OTURUMU KONTROLÜ (link kontrolünden SONRA)
        # Aktif sipariş oturumu varsa mesajı sipariş akışına yönlendir
        order_session = self.order_service.get_session(platform, sender_id)
        if order_session:
            print(f"[ChatBot] Aktif sipariş oturumu mevcut (aşama: {order_session.stage}), sipariş akışına yönlendiriliyor...")
            response = await self._process_order_flow(platform, sender_id, user_id, text, msg_id)
            if response:
                await self._send_message(platform, sender_id, response)
            return response

        # 2. RACE CONDITION (ÇİFT MESAJ) KORUMASI
        # Kullanıcılar "önce metin, sonra görsel" atarsa, iki ayrı bildirim gelir.
        # Metne "Anlamadım" cevabı gitmemesi için 4 saniye görsel gelmesini bekliyoruz.
        print(f"[ChatBot] '{text}' alındı. Görsel gelme ihtimaline karşı 4s bekleniyor...")
        await asyncio.sleep(4)

        # 3. In-memory dict kontrolü (Bekleyen görsel var mı?)
        pending_image = None
        if key in self._pending_images:
            pending = self._pending_images.pop(key)
            pending["task"].cancel()
            pending_image = pending["image_path"]
            print(f"[ChatBot] Dict'ten bekleyen gorsel bulundu: {pending_image}")

        # 2. DB fallback kontrolü
        if not pending_image:
            pending_image = self.conversation_service.get_unprocessed_recent_image(
                user_id, max_age_seconds=IMAGE_WAIT_TIMEOUT + 5
            )
            if pending_image:
                print(f"[ChatBot] DB'den bekleyen gorsel bulundu (fallback): {pending_image}")
                if key in self._pending_images:
                    self._pending_images.pop(key)["task"].cancel()

        # 3. Bekleyen görsel varsa: görsel + metin birlikte işle
        if pending_image:
            print(f"[ChatBot] Gorsel + metin birlestiriliyor: '{text}'")
            response = await self._process_image_with_text(user_id, pending_image, text, base_platform=platform, message_id=msg_id)
            if response:
                await self._send_message(platform, sender_id, response)
            return response

        # 4. Normal metin akışı
        print(f"[ChatBot] Normal metin isleniyor ({platform}): '{text}'")
        response = await self._process_text_only(
            platform=platform, sender_id=sender_id, user_id=user_id, user_message=text, message_id=msg_id
        )
        if response:
            await self._send_message(platform, sender_id, response)
        return response

    # ══════════════════════════════════════════
    #  İŞLEME METOTLARI
    # ══════════════════════════════════════════

    async def _process_image_with_text(self, user_id: int, image_path: str, text: str, base_platform: str = "whatsapp", message_id: str = None) -> str:
        """Görsel + metin birlikte işleme."""
        print("[ChatBot] Gorsel aratiliyor...")
        search_results = self.image_service.search_by_image(image_path, max_results=5)
        history = self.conversation_service.get_conversation_history(user_id)

        response = self.llm_service.generate_image_response(
            user_message=text,
            search_results=search_results,
            conversation_history=history
        )

        self.conversation_service.save_message(
            user_id=user_id, platform=base_platform, role="assistant",
            content=response, intent="product_inquiry", message_id=message_id
        )

        return response

    async def _process_text_only(self, platform: str, user_id: int, user_message: str, message_id: str = None, sender_id: str = "") -> str:
        """Sadece metin mesaj işleme akışı."""
        history = self.conversation_service.get_conversation_history(user_id)
        is_returning = self.conversation_service.is_returning_user(user_id)

        self.conversation_service.save_message(
            user_id=user_id, platform=platform, role="user", content=user_message, message_id=message_id
        )

        intent = self.llm_service.classify_intent(user_message)

        response = await self._route_intent(
            intent=intent, user_message=user_message, user_id=user_id,
            history=history, is_returning=is_returning,
            platform=platform, sender_id=sender_id
        )

        self.conversation_service.save_message(
            user_id=user_id, platform=platform, role="assistant",
            content=response, intent=intent
        )

        self._update_last_user_message_intent(user_id, intent)
        return response

    async def _route_intent(
        self, intent: str, user_message: str, user_id: int,
        history: list[dict], is_returning: bool,
        platform: str = "", sender_id: str = ""
    ) -> str:
        """Intent'e göre ilgili handler'a yönlendirir."""

        if intent == "product_inquiry":
            return self._handle_product_inquiry(user_message, user_id, history)
        elif intent == "order_request":
            return self._handle_order_request(platform, sender_id, user_message, user_id, history)
        elif intent == "greeting":
            return self.llm_service.generate_greeting_response(
                user_message, is_returning, history
            )
        elif intent == "complaint":
            return self.llm_service.generate_general_response(user_message, history)
        else:
            return self.llm_service.generate_general_response(user_message, history)

    def _handle_product_inquiry(self, user_message: str, user_id: int, history: list[dict]) -> str:
        """Ürün sorusu akışı."""
        search_query = self.llm_service.extract_product_query(user_message, conversation_history=history)
        
        # Eğer sorgu boş döndüyse (örneğin müşteri "bundan bahsetmiyorum" dedi), geçmişi kullanarak doğal cevap ver.
        if not search_query:
            print("[ChatBot] Urun sorgusu bos ('BOŞ' geldi). Dogal cevap uretiliyor...")
            return self.llm_service.generate_general_response(user_message, history)

        products = self.product_service.search_products(search_query, max_results=5)

        if products:
            print(f"[ChatBot] {len(products)} urun bulundu: {[p['name'] for p in products]}")
            return self.llm_service.generate_product_response(user_message, products, history)

        print(f"[ChatBot] Urun bulunamadi: '{search_query}'")
        return self.llm_service.generate_product_response(user_message, [], history)

    def _handle_order_request(self, platform: str, sender_id: str, user_message: str, user_id: int, history: list[dict]) -> str:
        """Sipariş isteği — oturum başlatır ve ürün arar."""
        # Ürünü bulmaya çalış
        search_query = self.llm_service.extract_product_query(user_message, conversation_history=history)
        products = []
        if search_query:
            products = self.product_service.search_products(search_query, max_results=3)

        # Sipariş oturumu başlat
        session = self.order_service.start_session(platform, sender_id, user_id)

        # Eğer ürün bulunduysa ilk ürünü oturuma ekle ve variant aşamasına geç
        if products and len(products) == 1:
            self.order_service.add_product(platform, sender_id, products[0])
            self.order_service.update_stage(platform, sender_id, "variant_selection")
        elif products:
            # Birden fazla ürün bulundu, seçim yaptıralım
            pass  # product_selection aşamasında kalacak, LLM seçim yaptıracak

        order_data = self.order_service.get_order_data(platform, sender_id)

        # Ürün bilgilerini order_data'ya ekle (LLM'in görmesi için)
        if products:
            product_info = self.llm_service._format_products_for_llm(products)
            user_message_with_products = f"{user_message}\n\nBulunan ürünler:\n{product_info}"
        else:
            user_message_with_products = user_message

        response = self.llm_service.generate_order_flow_response(
            user_message=user_message_with_products,
            stage=session.stage,
            order_data=order_data,
            conversation_history=history
        )
        return response

    # ══════════════════════════════════════════
    #  SİPARİŞ AKIŞI (Order Flow)
    # ══════════════════════════════════════════

    async def _process_order_flow(self, platform: str, sender_id: str, user_id: int, text: str, msg_id: str = None) -> str:
        """
        Aktif sipariş oturumunu yönetir.
        Her mesajda LLM'den bilgi çıkarır ve aşamayı ilerletir.
        """
        session = self.order_service.get_session(platform, sender_id)
        if not session:
            return None

        history = self.conversation_service.get_conversation_history(user_id)

        # Kullanıcının mesajını kaydet
        self.conversation_service.save_message(
            user_id=user_id, platform=platform, role="user",
            content=text, message_id=msg_id
        )

        # LLM ile mesajdan bilgi çıkar
        extracted = self.llm_service.extract_order_info(text, session.stage, history)

        # İptal kontrolü
        if extracted.get("wants_cancel"):
            self.order_service.cancel_session(platform, sender_id)
            response = "Siparişiniz iptal edildi. İstediğiniz zaman yeniden sipariş verebilirsiniz! 🌸"
            self.conversation_service.save_message(
                user_id=user_id, platform=platform, role="assistant",
                content=response, intent="order_request"
            )
            return response

        # Aşamaya göre işle
        stage = session.stage

        if stage == "product_selection":
            response = await self._order_product_selection(platform, sender_id, user_id, text, history, extracted)

        elif stage == "variant_selection":
            response = await self._order_variant_selection(platform, sender_id, user_id, text, history, extracted)

        elif stage == "payment_selection":
            response = await self._order_payment_selection(platform, sender_id, user_id, text, history, extracted)

        elif stage == "customer_info":
            response = await self._order_customer_info(platform, sender_id, user_id, text, history, extracted)

        elif stage == "confirmation":
            response = await self._order_confirmation(platform, sender_id, user_id, text, history, extracted)

        else:
            response = "Bir hata oluştu. Lütfen tekrar sipariş vermek istediğinizi belirtin."
            self.order_service.cancel_session(platform, sender_id)

        # Bot cevabını kaydet
        if response:
            self.conversation_service.save_message(
                user_id=user_id, platform=platform, role="assistant",
                content=response, intent="order_request"
            )

        return response

    async def _order_product_selection(self, platform, sender_id, user_id, text, history, extracted) -> str:
        """Ürün seçimi aşaması."""
        # Ürünü ara
        search_query = self.llm_service.extract_product_query(text, conversation_history=history)
        products = []
        if search_query:
            products = self.product_service.search_products(search_query, max_results=3)

        if products and len(products) == 1:
            # Tek ürün bulundu, ekle ve variant aşamasına geç
            self.order_service.add_product(platform, sender_id, products[0])
            self.order_service.update_stage(platform, sender_id, "variant_selection")
        elif products:
            # Birden fazla — LLM seçim yaptırsın
            pass

        order_data = self.order_service.get_order_data(platform, sender_id)
        product_info = self.llm_service._format_products_for_llm(products) if products else ""
        msg = f"{text}\n\nBulunan ürünler:\n{product_info}" if product_info else text

        session = self.order_service.get_session(platform, sender_id)
        return self.llm_service.generate_order_flow_response(
            user_message=msg,
            stage=session.stage if session else "product_selection",
            order_data=order_data,
            conversation_history=history
        )

    async def _order_variant_selection(self, platform, sender_id, user_id, text, history, extracted) -> str:
        """Beden/renk seçimi aşaması."""
        variant = extracted.get("variant")
        if variant:
            # Varyant seçildi — kaydet ve payment_selection aşamasına geç
            session = self.order_service.get_session(platform, sender_id)
            if session and session.items:
                self.order_service.set_variant(platform, sender_id, len(session.items) - 1, variant)
            self.order_service.update_stage(platform, sender_id, "payment_selection")

        order_data = self.order_service.get_order_data(platform, sender_id)
        session = self.order_service.get_session(platform, sender_id)
        return self.llm_service.generate_order_flow_response(
            user_message=text,
            stage=session.stage if session else "variant_selection",
            order_data=order_data,
            conversation_history=history
        )

    async def _order_payment_selection(self, platform, sender_id, user_id, text, history, extracted) -> str:
        """Ödeme yöntemi seçimi aşaması."""
        payment = extracted.get("payment_method")
        if payment and payment in ("kapida_odeme", "havale", "eft"):
            self.order_service.set_payment_method(platform, sender_id, payment)
            self.order_service.update_stage(platform, sender_id, "customer_info")

        order_data = self.order_service.get_order_data(platform, sender_id)
        session = self.order_service.get_session(platform, sender_id)
        return self.llm_service.generate_order_flow_response(
            user_message=text,
            stage=session.stage if session else "payment_selection",
            order_data=order_data,
            conversation_history=history
        )

    async def _order_customer_info(self, platform, sender_id, user_id, text, history, extracted) -> str:
        """Müşteri bilgileri toplama aşaması."""
        # Çıkarılan bilgileri kaydet
        info_update = {}
        if extracted.get("name"): info_update["name"] = extracted["name"]
        if extracted.get("phone"): info_update["phone"] = extracted["phone"]
        if extracted.get("address"): info_update["address"] = extracted["address"]

        if info_update:
            self.order_service.set_customer_info(platform, sender_id, info_update)

        # Tüm bilgiler tamam mı kontrol et
        session = self.order_service.get_session(platform, sender_id)
        info = session.customer_info if session else {}

        if info.get("name") and info.get("phone") and info.get("address"):
            # Tüm bilgiler tamam → confirmation aşamasına geç
            self.order_service.update_stage(platform, sender_id, "confirmation")
            # Sipariş özetini oluştur ve LLM'e ver
            order_summary = self.order_service.build_order_summary(platform, sender_id)
            order_data = self.order_service.get_order_data(platform, sender_id)
            return self.llm_service.generate_order_flow_response(
                user_message=f"{text}\n\n[SİSTEM: Tüm bilgiler tamam. Aşağıdaki sipariş özetini müşteriye göster ve onay iste:]\n{order_summary}",
                stage="confirmation",
                order_data=order_data,
                conversation_history=history
            )

        # Eksik bilgiler var
        order_data = self.order_service.get_order_data(platform, sender_id)
        return self.llm_service.generate_order_flow_response(
            user_message=text,
            stage="customer_info",
            order_data=order_data,
            conversation_history=history
        )

    async def _order_confirmation(self, platform, sender_id, user_id, text, history, extracted) -> str:
        """Sipariş onay aşaması."""
        # Değişiklik isteniyor mu?
        if extracted.get("wants_change"):
            change_field = extracted.get("change_field", "")
            if change_field in ("address", "phone", "name"):
                self.order_service.update_stage(platform, sender_id, "customer_info")
            elif change_field in ("variant",):
                self.order_service.update_stage(platform, sender_id, "variant_selection")
            elif change_field in ("product",):
                self.order_service.clear_products(platform, sender_id)
                self.order_service.update_stage(platform, sender_id, "product_selection")
            elif change_field in ("payment",):
                self.order_service.update_stage(platform, sender_id, "payment_selection")
            else:
                self.order_service.update_stage(platform, sender_id, "customer_info")

            # Değiştirmek istenen bilgiyi güncelle
            info_update = {}
            if extracted.get("name"): info_update["name"] = extracted["name"]
            if extracted.get("phone"): info_update["phone"] = extracted["phone"]
            if extracted.get("address"): info_update["address"] = extracted["address"]
            if info_update:
                self.order_service.set_customer_info(platform, sender_id, info_update)

            if extracted.get("variant"):
                session = self.order_service.get_session(platform, sender_id)
                if session and session.items:
                    self.order_service.set_variant(platform, sender_id, len(session.items) - 1, extracted["variant"])

            order_data = self.order_service.get_order_data(platform, sender_id)
            session = self.order_service.get_session(platform, sender_id)
            return self.llm_service.generate_order_flow_response(
                user_message=text,
                stage=session.stage if session else "customer_info",
                order_data=order_data,
                conversation_history=history
            )

        # Onay geldi mi?
        if extracted.get("confirms_order"):
            session = self.order_service.get_session(platform, sender_id)
            payment = session.payment_method if session else "kapida_odeme"
            order_id = self.order_service.complete_order(platform, sender_id)
            if order_id:
                if payment == "kapida_odeme":
                    response = (
                        f"Siparişiniz başarıyla oluşturuldu! 🎉\n\n"
                        f"Sipariş No: #{order_id}\n"
                        f"Ödeme Yöntemi: Kapıda Ödeme\n\n"
                        f"Siparişiniz en kısa sürede hazırlanıp kargoya verilecektir. "
                        f"Teşekkür ederiz! 💕\n\n"
                        f"Başka bir sorunuz olursa her zaman buradayım! 🌸"
                    )
                else:
                    # Havale veya EFT
                    method_label = "Havale" if payment == "havale" else "EFT"
                    response = (
                        f"Siparişiniz alınmıştır! 🎉\n\n"
                        f"Sipariş No: #{order_id}\n"
                        f"Ödeme Yöntemi: {method_label}\n\n"
                        f"Ödeme bilgileri için yöneticimiz kısa süre içinde size dönecektir. "
                        f"Teşekkür ederiz! 💕\n\n"
                        f"Başka bir sorunuz olursa her zaman buradayım! 🌸"
                    )
                return response
            else:
                return "Sipariş oluşturulurken bir hata oluştu. Lütfen tekrar deneyin."

        # Ne onay ne değişiklik — LLM'e sor
        order_summary = self.order_service.build_order_summary(platform, sender_id)
        order_data = self.order_service.get_order_data(platform, sender_id)
        return self.llm_service.generate_order_flow_response(
            user_message=f"{text}\n\n[Mevcut sipariş özeti:]\n{order_summary}",
            stage="confirmation",
            order_data=order_data,
            conversation_history=history
        )

    # ══════════════════════════════════════════
    #  YARDIMCI METOTLAR
    # ══════════════════════════════════════════

    async def _send_message(self, platform: str, sender_id: str, text: str):
        """Platforma göre mesaj gönderir."""
        import time
        self._bot_last_send[f"{platform}_{sender_id}"] = time.time()
        
        if platform == "whatsapp":
            await self.whatsapp_service.send_text_message(sender_id, text)
        elif platform == "instagram":
            await self.instagram_service.send_text_message(sender_id, text)
        else:
            print(f"[ChatBot] Bilinmeyen platform: {platform}")

    def _update_last_user_message_intent(self, user_id: int, intent: str):
        """Son kullanıcı mesajının intent'ini günceller."""
        try:
            from models.database import get_connection
            conn = get_connection()
            conn.execute(
                """UPDATE messages SET intent = ?
                   WHERE id = (
                       SELECT id FROM messages
                       WHERE user_id = ? AND role = 'user'
                       ORDER BY created_at DESC LIMIT 1
                   )""",
                (intent, user_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ChatBot] Intent guncelleme hatasi: {e}")

    # ══════════════════════════════════════════
    #  ADMIN TAKEOVER (Yönetici Devralma)
    # ══════════════════════════════════════════

    def is_recent_bot_message(self, platform: str, sender_id: str, window_seconds: int = 15) -> bool:
        """Belirtilen platformdaki kullanıcıya bot son window_seconds içinde mesaj gönderdi mi?"""
        import time
        key = f"{platform}_{sender_id}"
        last_time = self._bot_last_send.get(key, 0)
        return (time.time() - last_time) < window_seconds

    def activate_admin_takeover(self, platform: str, customer_sender_id: str):
        """
        Yönetici bir müşteriye mesaj attığında çağrılır.
        Bot, bu müşteri için ADMIN_TAKEOVER_TIMEOUT süresince susturulur.
        """
        key = self._get_pending_key(platform, customer_sender_id)
        now = datetime.utcnow()


        if key in self._admin_takeover:
            self._admin_takeover[key]["last_echo_at"] = now
            print(f"[ChatBot] 🔄 Admin takeover yenilendi: {key}")
        else:
            self._admin_takeover[key] = {
                "started_at": now,
                "last_echo_at": now,
            }
            print(f"[ChatBot] 🛑 Admin takeover AKTIF: {key} ({ADMIN_TAKEOVER_TIMEOUT} dk)")

        # Bekleyen görsel timer'ı da iptal et (admin cevap verecektir)
        if key in self._pending_images:
            self._pending_images.pop(key)["task"].cancel()
            print(f"[ChatBot] Pending görsel timer iptal edildi (admin devralma): {key}")

    def _is_admin_active(self, platform: str, sender_id: str) -> bool:
        """
        Bu müşteri için yönetici aktif mi kontrol eder.
        Süre dolmuşsa otomatik olarak temizler.
        """
        key = self._get_pending_key(platform, sender_id)

        if key not in self._admin_takeover:
            return False

        takeover = self._admin_takeover[key]
        elapsed = (datetime.utcnow() - takeover["last_echo_at"]).total_seconds()
        timeout_seconds = ADMIN_TAKEOVER_TIMEOUT * 60

        if elapsed > timeout_seconds:
            self._admin_takeover.pop(key, None)
            print(f"[ChatBot] ✅ Admin takeover süresi doldu: {key} ({elapsed:.0f}s geçti)")
            return False

        remaining = timeout_seconds - elapsed
        print(f"[ChatBot] ⏸️ Admin takeover aktif: {key} (kalan: {remaining:.0f}s)")
        return True

    def deactivate_admin_takeover(self, platform: str, sender_id: str):
        """Manuel olarak admin takeover'ı kaldırır (opsiyonel endpoint için)."""
        key = self._get_pending_key(platform, sender_id)
        if key in self._admin_takeover:
            self._admin_takeover.pop(key)
            print(f"[ChatBot] ✅ Admin takeover kaldırıldı: {key}")
            return True
        return False
