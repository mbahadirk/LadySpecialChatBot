"""
LadySpecial ChatBot - Ana Uygulama

FastAPI sunucusu:
- WhatsApp webhook → ChatBot
- Instagram webhook → ChatBot
- Arka plan ürün senkronizasyonu (delta sync)
- Çoklu müşteri kuyruk yönetimi
"""

import os
import json
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Windows encoding fix
from utils.logger import setup_encoding
setup_encoding()

from models.database import init_db
from services.chatbot import ChatBot
from services.product_sync import ProductSync
from services.queue_manager import QueueManager

load_dotenv()

# ─── Konfigürasyon ───
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL_MINUTES", "5"))

# ─── Global Instances ───
chatbot: ChatBot = None
sync_service: ProductSync = None
queue_manager: QueueManager = None
_sync_task: asyncio.Task = None


# ═══════════════════════════════════════════
#  LIFECYCLE (Startup / Shutdown)
# ═══════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Uygulama başlatma ve kapatma lifecycle'ı."""
    global chatbot, sync_service, queue_manager, _sync_task

    # ── Startup ──
    init_db()

    chatbot = ChatBot()
    sync_service = ProductSync(image_service=chatbot.image_service)
    queue_manager = QueueManager()

    # İlk senkronizasyonu başlat
    _sync_task = asyncio.create_task(_periodic_sync())
    print(f"[Main] Periyodik senkronizasyon başlatıldı (her {SYNC_INTERVAL} dakika)")

    yield

    # ── Shutdown ──
    if _sync_task:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
    print("[Main] Uygulama kapatılıyor...")


async def _periodic_sync():
    """Arka planda periyodik ürün ve instagram senkronizasyonu çalıştırır."""
    from services.instagram_sync import InstagramSync
    ig_sync_service = InstagramSync()
    
    # İlk sync'i hemen çalıştır
    while True:
        try:
            stats = await sync_service.sync()
            if not stats.get("skipped"):
                count = sync_service.get_product_count()
                print(f"[Sync] Aktif ürün: {count}")
                
            # Instagram postlarını senkronize et (thread blocklamamak için thread ekleyebiliriz ama sync() blocking. asyncio.to_thread kullanalım)
            await asyncio.to_thread(ig_sync_service.sync)
        except Exception as e:
            print(f"[Sync] Periyodik sync hatası: {e}")

        await asyncio.sleep(SYNC_INTERVAL * 60)

# ═══════════════════════════════════════════
#  FASTAPI UYGULAMASI
# ═══════════════════════════════════════════

app = FastAPI(
    title="LadySpecial ChatBot",
    description="WhatsApp & Instagram AI Satış Asistanı",
    version="3.0.0",
    lifespan=lifespan,
)

# Static dosyalar
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ═══════════════════════════════════════════
#  SAYFA ENDPOINT'LERİ
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def read_index():
    """Ana sayfayı döner."""
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return HTMLResponse("<h1>LadySpecial ChatBot v3.0</h1>")


@app.get("/privacy-policy", response_class=HTMLResponse)
@app.get("/privacy_policy")
async def read_privacy_policy():
    """Gizlilik politikasını döner."""
    if os.path.exists("static/privacy_policy.html"):
        return FileResponse("static/privacy_policy.html")
    return {"error": "Privacy policy file not found"}


# ═══════════════════════════════════════════
#  WEBHOOK DOĞRULAMA
# ═══════════════════════════════════════════

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook doğrulama — WhatsApp & Instagram ortak."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook doğrulandı!")
        return PlainTextResponse(content=challenge, status_code=200)

    raise HTTPException(status_code=403, detail="Doğrulama hatası")


# ═══════════════════════════════════════════
#  WEBHOOK MESAJ İŞLEME
# ═══════════════════════════════════════════

@app.post("/webhook")
async def receive_webhook(request: Request):
    """WhatsApp ve Instagram'dan gelen mesajları işler."""
    try:
        data = await request.json()
        obj_type = data.get("object")
        print(f"\n{'='*60}")
        print(f"WEBHOOK GELDI | object: {obj_type}")
        print(f"{'='*60}")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        # ─── WhatsApp ───
        if obj_type == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if value.get("statuses"):
                        # WhatsApp status bildirimi — yöneticinin gönderdiği mesajın durumu
                        for status in value.get("statuses", []):
                            if status.get("status") == "sent":
                                recipient = status.get("recipient_id", "")
                                if recipient:
                                    chatbot.activate_admin_takeover("whatsapp", recipient)
                                    print(f"[INFO] WA Admin takeover tetiklendi: {recipient}")
                        continue
                    if value.get("messages"):
                        print("[INFO] WhatsApp mesaji isleniyor...")
                        try:
                            response = await queue_manager.process(
                                chatbot.handle_whatsapp_message(data)
                            )
                            print(f"[INFO] ChatBot cevabi: {response}")
                        except Exception as e:
                            print(f"[HATA] WhatsApp isleme: {e}")
                            import traceback
                            traceback.print_exc()

        # ─── Instagram ───
        elif obj_type == "instagram":
            for entry in data.get("entry", []):
                messaging = entry.get("messaging", [])
                if not messaging:
                    continue

                # Echo kontrolü (kendi mesajlarımızı işleme)
                msg_event = messaging[0]
                if "message" not in msg_event:
                    print("[INFO] Instagram event (mesaj degil), atlaniyor.")
                    continue

                # Echo check — Sayfa tarafından gönderilen mesajlar
                is_echo = msg_event.get("message", {}).get("is_echo", False)
                if is_echo:
                    customer_id = msg_event.get("recipient", {}).get("id", "")
                    if customer_id:
                        # Eğer bot son 15 saniye içinde bu kullanıcıya mesaj gönderdiyse, bu bir bot echo'sudur
                        if chatbot.is_recent_bot_message("instagram", customer_id, window_seconds=15):
                            print(f"[INFO] Bot echo mesaji (zaman bazli), atlaniyor.")
                        else:
                            # Aksi takdirde yönetici tarafından manuel gönderilmiş bir mesajdır
                            chatbot.activate_admin_takeover("instagram", customer_id)
                            print(f"[INFO] IG Admin takeover tetiklendi: {customer_id}")
                    else:
                        print("[INFO] IG echo: recipient ID bulunamadı.")
                    continue

                print("[INFO] Instagram mesaji isleniyor...")
                try:
                    response = await queue_manager.process(
                        chatbot.handle_instagram_message(data)
                    )
                    print(f"[INFO] IG ChatBot cevabi: {response}")
                except Exception as e:
                    print(f"[HATA] Instagram isleme: {e}")
                    import traceback
                    traceback.print_exc()

        else:
            print(f"[UYARI] Bilinmeyen object: {obj_type}")

    except Exception as e:
        print(f"[HATA] Webhook: {e}")
        import traceback
        traceback.print_exc()

    return {"status": "received"}


# ═══════════════════════════════════════════
#  IKAS WEBHOOK MESAJ İŞLEME
# ═══════════════════════════════════════════

@app.post("/ikas/webhook/order")
async def receive_ikas_order_webhook(request: Request):
    """Ikas sipariş durum güncellemelerini alır."""
    # Güvenlik kontrolü (Ikas webhook header'ından client_id gelip gelmediği varsayımı)
    try:
        data = await request.json()
        print(f"\n{'='*60}")
        print("IKAS WEBHOOK GELDI")
        print(f"{'='*60}")
        client_id_header = request.headers.get("x-ikas-client-id", "")
        # İki tür client id tanımlı, sipariş için olanı kontrol edelim
        ikas_client_id = os.getenv("IKAS_ORDERING_CLIENT_ID", "")
        if ikas_client_id and client_id_header != ikas_client_id:
            # Şimdilik güvenlik uyarısi verip devam etsin, header yapısını bilmiyoruz
            print(f"[UYARI] Ikas Webhook: Header eşleşmedi. Gelen: {client_id_header}, Beklenen: {ikas_client_id}")

        print(json.dumps(data, indent=2, ensure_ascii=False))

        # Order tracking service'i güncelle
        if hasattr(chatbot, 'order_tracker') and chatbot.order_tracker:
            chatbot.order_tracker.update_order_from_webhook(data)
            print("[INFO] Ikas Webhook: Sipariş takip sistemi güncellendi.")

        return {"status": "success"}

    except Exception as e:
        print(f"[HATA] Ikas Webhook işleme: {e}")
        import traceback
        traceback.print_exc()
        # Ikas timeout olmasın diye 200 dönüyoruz
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════
#  YARDIMCI ENDPOINT'LER
# ═══════════════════════════════════════════

@app.get("/health")
async def health_check():
    """Sunucu sağlık kontrolü."""
    return {
        "status": "ok",
        "version": "3.0.0",
        "products": chatbot.product_service.get_product_count() if chatbot else 0,
        "queue": queue_manager.get_stats() if queue_manager else {},
        "sync_interval_minutes": SYNC_INTERVAL,
    }


@app.post("/test/chat")
@app.post("/api/chat")
async def test_chat(request: Request):
    """Test endpoint'i: Botu denemek için."""
    try:
        body = await request.json()
        message = body.get("message") or body.get("text") or ""
        user_id_str = body.get("user_id", "test_user_web")

        if not message:
            return {"error": "Mesaj bos olamaz"}

        user = chatbot.user_service.get_or_create_user("web", user_id_str)
        user_id = user["id"]

        response = await chatbot._process_text_only(
            platform="web",
            user_id=user_id,
            user_message=message,
            sender_id=user_id_str
        )

        return {
            "response": response,
            "bot_response": response,
            "user_message": message,
            "user_id": user_id_str
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """Web arayüzünden görsel yükleme."""
    try:
        contents = await file.read()
        user = chatbot.user_service.get_or_create_user("web", "test_user_web")

        image_path = chatbot.image_service.save_image(user["id"], contents)
        response = await chatbot._process_image_with_text(user["id"], image_path, "")

        return {"response": response}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


@app.post("/api/sync")
async def manual_sync():
    """Manuel senkronizasyon tetikleme."""
    try:
        stats = await sync_service.sync()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════
#  UYGULAMA BAŞLATMA
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)