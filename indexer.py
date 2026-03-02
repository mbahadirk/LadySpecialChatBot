import pandas as pd
import json
import re
import os
import requests
from io import BytesIO
from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

load_dotenv()

# Ayarlar
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "ladyspecial_products"

# Client Başlat
qdrant_client = QdrantClient(url=QDRANT_URL)

# Thread Lock (Sayaçlar için güvenli işlem)
lock = threading.Lock()


def load_model():
    print("Loading CLIP model (fashion-clip)...")
    from fashion_clip_wrapper import FashionCLIPWrapper
    return FashionCLIPWrapper()


def clean_html(raw_html):
    if not isinstance(raw_html, str):
        return ""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html).strip()


def setup_qdrant(client):
    try:
        info = client.get_collection(COLLECTION_NAME)
        # Eğer boyut uyuşmazlığı varsa (Eski 768, Yeni 512) koleksiyonu silip yeniden kur
        if info.config.params.vectors.size != 512:
            print(f"⚠️ Boyut uyuşmazlığı tespit edildi (Beklenen: 512). Koleksiyon yeniden oluşturuluyor...")
            client.delete_collection(COLLECTION_NAME)
            raise Exception("Recreate needed")
        print(f"✅ Koleksiyon '{COLLECTION_NAME}' mevcut ve geçerli.")
    except Exception:
        print(f"🔨 Koleksiyon '{COLLECTION_NAME}' 512 boyutunda oluşturuluyor...")
        client.recreate_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(size=512, distance=models.Distance.COSINE),
        )


def process_csv_for_chatbot(csv_path="ikas-urunler.csv"):
    print("📂 CSV dosyası okunuyor...")
    if not os.path.exists(csv_path):
        print(f"⚠️ Dosya bulunamadı: {csv_path}")
        return []

    df = pd.read_csv(csv_path)

    # Stok temizliği
    if 'Stok:avstic' in df.columns:
        df['Stok:avstic'] = df['Stok:avstic'].fillna(0)
    else:
        df['Stok:avstic'] = 0

    grouped = df.groupby('Ürün Grup ID')
    chatbot_products = []

    # Başlangıç
    setup_qdrant(qdrant_client)
    model = load_model()

    print("🚀 İşlem başlıyor... (Çoklu görsel işleme aktif)")

    # Bağlantı havuzu (Hızlı indirme için)
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    total_vectors_indexed = 0  # Global Sayaç

    def process_group(group_data):
        group_id, group_df = group_data
        local_points = []

        try:
            main_row = group_df.iloc[0]
            raw_images = str(main_row.get('Resim URL', '')).split(';')
            valid_images = [img for img in raw_images if img.startswith('http')]

            # JSON Verisi Hazırla
            total_stock = group_df['Stok:avstic'].sum()
            prices = group_df['Satış Fiyatı'].tolist()
            min_price = min(prices) if prices else 0

            product_data = {
                "id": str(group_id),
                "name": str(main_row['İsim']),
                "description": clean_html(main_row.get('Açıklama', '')),
                "price": float(min_price),
                "currency": "TL",
                "stock": int(total_stock),
                "image_url": valid_images[0] if valid_images else None,
                "all_images": valid_images,
                "category": str(main_row.get('Kategoriler', '')).replace(';', ', '),
                "slug": str(main_row.get('Slug', '')),
                "variants": []
            }

            for _, row in group_df.iterrows():
                raw_stock = row.get('Stok:avstic', 0)
                safe_stock = 0 if pd.isna(raw_stock) else raw_stock
                product_data["variants"].append({
                    "variant_id": str(row['Varyant ID']),
                    "sku": str(row.get('SKU', '')),
                    "price": float(row.get('Satış Fiyatı', 0)),
                    "stock": int(safe_stock),
                    "option1": f"{row.get('Varyant Tip 1')}: {row.get('Varyant Değer 1')}"
                })

            # --- QDRANT VECTOR CREATION ---
            # 'already_indexed' kontrolünü KALDIRDIK. Artık her çalıştığında resimleri günceller.
            # Böylece yeni eklenen açıları atlamaz.

            if valid_images:
                # Maksimum 5 resim işleyelim (Performans/Kalite dengesi)
                for img_idx, img_url in enumerate(valid_images[:5]):
                    try:
                        # Resmi indir
                        response = session.get(img_url, timeout=5)
                        if response.status_code == 200:
                            image = Image.open(BytesIO(response.content))

                            # Vektör oluştur
                            embedding = model.encode(image)

                            # Benzersiz ID oluştur (GrupID + ResimIndex)
                            unique_id_str = f"{group_id}_{img_idx}"
                            point_id = int(hash(unique_id_str) % (2 ** 63 - 1))

                            point = models.PointStruct(
                                id=point_id,
                                vector=embedding.tolist(),
                                payload={
                                    "id": product_data["id"],
                                    "name": product_data["name"],
                                    "price": product_data["price"],
                                    "stock": product_data["stock"],
                                    "image_url": img_url,
                                    "description": product_data["description"][:200],
                                    "is_main": (img_idx == 0)  # Ana resim mi?
                                }
                            )
                            local_points.append(point)
                    except Exception:
                        pass  # Bozuk resimleri atla

            return product_data, local_points

        except Exception as e:
            print(f"⚠️ Hata (Grup ID: {group_id}): {e}")
            return None, []

    # Paralel Çalıştırma (5 Worker)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_group, g) for g in grouped]

        count = 0
        total = len(grouped)

        for future in as_completed(futures):
            p_data, p_points = future.result()
            count += 1

            if p_data:
                chatbot_products.append(p_data)

                # ANLIK UPSERT (Vektörleri bekletmeden gönder)
                if p_points:
                    try:
                        qdrant_client.upsert(
                            collection_name=COLLECTION_NAME,
                            points=p_points
                        )
                        # Toplam sayacı güncelle
                        with lock:
                            total_vectors_indexed += len(p_points)
                    except Exception as up_err:
                        print(f"Upsert Hatası: {up_err}")

            if count % 10 == 0:
                print(f"İlerleme: {count}/{total} ürün işlendi...", flush=True)

    # JSON Kayıt
    with open("chatbot_database.json", "w", encoding="utf-8") as f:
        json.dump(chatbot_products, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 40)
    if total_vectors_indexed > 0:
        print(f"✅ İŞLEM TAMAMLANDI!")
        print(f"📊 Toplam Ürün: {len(chatbot_products)}")
        print(f"🖼️  Qdrant'a Yüklenen Vektör Sayısı: {total_vectors_indexed}")
        print("(Her ürünün farklı açıları da yüklendiği için sayı ürün sayısından fazladır)")
    else:
        print("⚠️ HİÇBİR VEKTÖR OLUŞTURULAMADI. Resim URL'lerini veya internet bağlantını kontrol et.")
    print("=" * 40)

    return chatbot_products


if __name__ == "__main__":
    process_csv_for_chatbot()