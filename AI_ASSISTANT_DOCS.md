# 🤖 LadySpecial AI Satış Asistanı — Dokümantasyon

## Genel Bakış

LadySpecial AI Satış Asistanı, WhatsApp (ve ilerleyen dönemde Instagram) üzerinden müşterilerle otomatik iletişim kuran bir yapay zeka chatbot'udur. Müşterilerin sorularını anlar, ürünler hakkında bilgi verir ve sipariş vermek isteyenleri web sitesine yönlendirir.

---

## 🏗️ Mimari

Sistem **OOP (Object-Oriented Programming)** prensiplerine göre tasarlanmıştır. Her servis kendi dosyasında, tek bir sorumluluk üstlenir.

```
radiant-galaxy/
├── main.py                          # FastAPI sunucusu (giriş noktası)
├── models/
│   ├── __init__.py
│   └── database.py                  # SQLite veritabanı şeması ve bağlantı
├── services/
│   ├── __init__.py
│   ├── chatbot.py                   # 🎯 Ana orkestratör (tüm servisleri yönetir)
│   ├── user_service.py              # Kullanıcı CRUD işlemleri
│   ├── conversation_service.py      # Mesaj geçmişi (hafıza) yönetimi
│   ├── product_service.py           # Ürün arama ve sorgulama
│   ├── llm_service.py               # OpenAI GPT ile intent + cevap üretme
│   ├── whatsapp_service.py          # WhatsApp Cloud API iletişimi
│   └── prompt_manager.py            # Prompt dosya yöneticisi
├── prompts/                         # ✏️ LLM prompt dosyaları (kolay düzenlenebilir)
│   ├── system_prompt.txt            # Ana karakter ve davranış kuralları
│   ├── intent_classification_prompt.txt  # Mesaj sınıflandırma kuralları
│   ├── product_response_prompt.txt  # Ürün sorusu yanıt formatı
│   ├── order_response_prompt.txt    # Sipariş yönlendirme formatı
│   └── greeting_response_prompt.txt # Karşılama mesajı formatı
├── ladyspecial.db                   # SQLite veritabanı (otomatik oluşur)
├── chatbot_database.json            # Ürün veritabanı (indexer.py tarafından üretilir)
├── .env                             # Ortam değişkenleri
└── ...
```

---

## 📊 Veritabanı Yapısı (SQLite)

### `users` Tablosu
Müşterileri saklar. Bir müşteri hem WhatsApp hem de Instagram'dan yazabilir.

| Kolon          | Tip     | Açıklama                         |
|----------------|---------|----------------------------------|
| `id`           | INTEGER | Otomatik artan birincil anahtar  |
| `whatsapp_id`  | TEXT    | WhatsApp telefon numarası (unique) |
| `instagram_id` | TEXT    | Instagram kullanıcı ID'si (unique) |
| `display_name` | TEXT    | Gösterim adı                     |
| `created_at`   | TEXT    | İlk kayıt tarihi                |
| `updated_at`   | TEXT    | Son güncelleme tarihi            |

### `messages` Tablosu
Tüm mesaj geçmişini saklar. Yapay zeka bu geçmişi kullanarak bağlamı hatırlar.

| Kolon      | Tip     | Açıklama                                          |
|------------|---------|---------------------------------------------------|
| `id`       | INTEGER | Otomatik artan birincil anahtar                   |
| `user_id`  | INTEGER | users tablosuna FK                                |
| `platform` | TEXT    | 'whatsapp' veya 'instagram'                       |
| `role`     | TEXT    | 'user' (müşteri) veya 'assistant' (bot)           |
| `content`  | TEXT    | Mesaj içeriği                                     |
| `intent`   | TEXT    | Sınıflandırma sonucu (product_inquiry, order_request vb.) |
| `created_at` | TEXT  | Mesaj tarihi                                      |

---

## 🔄 Mesaj İşleme Akışı

```
Müşteri WhatsApp'tan mesaj yazar
        │
        ▼
   ┌─────────────┐
   │  main.py    │  → Webhook yakalar
   └──────┬──────┘
          │
          ▼
   ┌─────────────────┐
   │  ChatBot        │  → Ana orkestratör
   │  (chatbot.py)   │
   └──────┬──────────┘
          │
   ┌──────┴──────────────────────┐
   │                             │
   ▼                             ▼
┌───────────┐            ┌────────────────┐
│UserService│            │ConversationSvc │
│ Kullanıcı │            │ Geçmiş mesajlar│
│ bul/oluşt │            │ al & kaydet    │
└─────┬─────┘            └───────┬────────┘
      │                          │
      └──────────┬───────────────┘
                 │
                 ▼
        ┌─────────────────┐
        │   LLMService    │  → Intent sınıflandır
        │ classify_intent │
        └────────┬────────┘
                 │
     ┌───────────┼───────────┬──────────────┐
     │           │           │              │
     ▼           ▼           ▼              ▼
 product     order       greeting       general
 _inquiry    _request                   _chat
     │           │           │              │
     ▼           │           │              │
┌──────────┐    │           │              │
│ProductSvc│    │           │              │
│ Ürün ara │    │           │              │
└────┬─────┘    │           │              │
     │           │           │              │
     ▼           ▼           ▼              ▼
┌─────────────────────────────────────────────┐
│           LLMService                        │
│     Prompt + Ürün bilgisi + Geçmiş          │
│     ile doğal dilde cevap üret              │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
          ┌─────────────────┐
          │ WhatsAppService │  → Cevabı gönder
          └─────────────────┘
                   │
                   ▼
          ┌─────────────────┐
          │ConversationSvc  │  → Cevabı kaydet
          └─────────────────┘
```

---

## 🎯 Intent (Niyet) Sınıflandırma

Müşterinin her mesajı önce sınıflandırılır:

| Intent            | Açıklama                                    | Tetikleyici Örnekler                    |
|-------------------|---------------------------------------------|-----------------------------------------|
| `product_inquiry` | Ürün hakkında bilgi soruyor                 | "Kırmızı elbise var mı?", "Fiyatı ne?" |
| `order_request`   | Sipariş vermek istiyor                      | "Bunu almak istiyorum", "Nasıl sipariş verebilirim?" |
| `greeting`        | Selamlama                                   | "Merhaba", "İyi günler"                |
| `complaint`       | Şikayet/sorun bildiriyor                    | "Kargom gelmedi", "Ürün hasarlı geldi" |
| `general_chat`    | Yukarıdakilere uymayan genel sohbet         | "Nasılsınız?", "Teşekkürler"           |

### Intent'e Göre Davranış:

- **`product_inquiry`** → Mesajdan ürün sorgusu çıkarılır → `chatbot_database.json`'da aranır → Bulunan ürünlerin bilgileri (fiyat, stok, renk, URL) ile doğal bir cevap üretilir.
- **`order_request`** → Ürün bulunur → `ladyspecial.com.tr/{slug}` formatında URL paylaşılır → Müşteri satın almaya yönlendirilir.
- **`greeting`** → Sıcak bir karşılama mesajı üretilir. Geri dönen müşteriler farklı karşılanır.
- **`complaint`** → Anlayışlı ve yardımcı bir cevap üretilir.
- **`general_chat`** → Genel sohbet cevabı üretilir.

---

## 🧠 Hafıza Sistemi

Sistem her müşterinin tüm mesajlarını SQLite veritabanında saklar. LLM'e her istek gönderilirken, o müşterinin **son 20 mesajı** (konuşma geçmişi) da bağlam olarak eklenir.

Bu sayede:
- Bot, müşterinin daha önce sorduğu ürünleri hatırlar.
- "Bir önceki sorduğum elbise kaç TL'ydi?" gibi referanslara cevap verebilir.
- Geri dönen müşterileri tanır ve daha kişisel bir deneyim sunar.

**Not:** Geçmiş mesaj sayısı `ConversationService.MAX_CONTEXT_MESSAGES` ile ayarlanabilir. (Varsayılan: 20)

---

## ✏️ Prompt Düzenleme

Tüm LLM promptları `prompts/` klasöründe `.txt` dosyaları olarak tutulur. Bu dosyaları istediğiniz zaman bir metin editörü ile açıp düzenleyebilirsiniz.

| Dosya                              | Amaç                                 |
|------------------------------------|---------------------------------------|
| `system_prompt.txt`                | Bot'un karakteri ve genel kuralları   |
| `intent_classification_prompt.txt` | Mesaj sınıflandırma talimatları       |
| `product_response_prompt.txt`      | Ürün bilgisi cevap formatı            |
| `order_response_prompt.txt`        | Sipariş yönlendirme formatı           |
| `greeting_response_prompt.txt`     | Karşılama mesajı formatı              |

### Prompt Güncelledikten Sonra:
Sunucuyu yeniden başlatmanız gerekmez. Prompt cache'ini temizlemek için:
```python
from services.prompt_manager import PromptManager
PromptManager.clear_cache()
```

Veya sunucuyu yeniden başlatın.

---

## 🚀 Çalıştırma

### 1. Ortam Değişkenleri (.env)
```env
# Zorunlu
OPENAI_API_KEY=sk-...
META_PHONE_NUMBER_ID=123456789
META_ACCESS_TOKEN=EAATW...
META_VERIFY_TOKEN=sizin_verify_tokeniniz

# Opsiyonel (Görsel arama için)
QDRANT_URL=http://localhost:6333
IKAS_SHOP_NAME=avstic
IKAS_CLIENT_ID=...
IKAS_CLIENT_SECRET=...
```

### 2. Sunucuyu Başlat
```bash
python main.py
# veya
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Ngrok ile Tünel Aç (geliştirme için)
```bash
python start_ngrok.py
```

### 4. Test Endpoint'i
WhatsApp olmadan chatbot'u test edebilirsiniz:
```bash
curl -X POST http://localhost:8000/test/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Merhaba!", "user_id": "test_user_1"}'
```

---

## 🔧 Konfigürasyon

| Ayar                                      | Dosya/Yer                   | Varsayılan     |
|-------------------------------------------|-----------------------------|----------------|
| LLM Modeli                               | `services/llm_service.py`   | `gpt-4o-mini`  |
| Geçmiş mesaj limiti                       | `services/conversation_service.py` | 20       |
| Ürün arama max sonuç                      | `services/product_service.py` | 5            |
| Web sitesi base URL                       | `services/product_service.py` | `ladyspecial.com.tr` |
| Veritabanı yolu                           | `models/database.py`        | `ladyspecial.db` |

---

## 📝 Gelecek Geliştirmeler

- [ ] Instagram DM entegrasyonu aktifleştirilecek
- [ ] WhatsApp'tan gelen görsel mesajların işlenmesi (CLIP + Qdrant ile görsel arama)
- [ ] Sipariş takip entegrasyonu
- [ ] Müşteri memnuniyet puanlama sistemi
- [ ] Admin paneli (istatistikler, konuşma geçmişi görüntüleme)
