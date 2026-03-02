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

load_dotenv()

# Configuration
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "ladyspecial_products"

# Initialize Client
qdrant_client = QdrantClient(url=QDRANT_URL)

def load_model():
    print("Loading CLIP model...")
    return SentenceTransformer('sentence-transformers/clip-ViT-L-14')

def clean_html(raw_html):
    """HTML etiketlerini temizler."""
    if not isinstance(raw_html, str):
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

def setup_qdrant(client):
    # Check if collection exists and has correct dimension
    try:
        info = client.get_collection(COLLECTION_NAME)
        if info.config.params.vectors.size != 768:
            print(f"⚠️ Dimension mismatch (Expected 768, Got {info.config.params.vectors.size}). Recreating collection...")
            client.delete_collection(COLLECTION_NAME)
            raise Exception("Recreate needed")
        print(f"Collection '{COLLECTION_NAME}' exists and is valid.")
    except Exception:
        print(f"Creating collection '{COLLECTION_NAME}' with 768 dim...")
        client.recreate_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE),
        )

def process_csv_for_chatbot(csv_path="ikas-urunler.csv"):
    print("📂 CSV dosyası okunuyor...")
    if not os.path.exists(csv_path):
        print(f"⚠️ Dosya bulunamadı: {csv_path}")
        return []

    df = pd.read_csv(csv_path)

    # 1. Ön Hazırlık: Stok temizliği
    if 'Stok:avstic' in df.columns:
        df['Stok:avstic'] = df['Stok:avstic'].fillna(0)
    else:
        df['Stok:avstic'] = 0

    grouped = df.groupby('Ürün Grup ID')
    chatbot_products = []
    
    # Qdrant Setup
    setup_qdrant(qdrant_client)
    model = load_model()
    points = []

    print("🔄 Veriler işleniyor (Görsel ve Metin)...")

    for group_id, group_df in grouped:
        try:
            main_row = group_df.iloc[0]

            # Resim URL'lerini ayıkla
            raw_images = str(main_row.get('Resim URL', '')).split(';')
            valid_images = [img for img in raw_images if img.startswith('http')]

            # Toplam Stok
            total_stock = group_df['Stok:avstic'].sum()

            # Fiyat
            prices = group_df['Satış Fiyatı'].tolist()
            min_price = min(prices) if prices else 0

            # Product Object (JSON için)
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

            # Variants
            for _, row in group_df.iterrows():
                raw_stock = row.get('Stok:avstic', 0)
                safe_stock = 0 if pd.isna(raw_stock) else raw_stock
                variant_info = {
                    "variant_id": str(row['Varyant ID']),
                    "sku": str(row.get('SKU', '')),
                    "price": float(row.get('Satış Fiyatı', 0)),
                    "stock": int(safe_stock),  
                    "option1": f"{row.get('Varyant Tip 1')}: {row.get('Varyant Değer 1')}",
                }
                product_data["variants"].append(variant_info)

            chatbot_products.append(product_data)

            # ---------------------------------------------------------
            # QDRANT INDEXING (Görsel Vektörü)
            # ---------------------------------------------------------
            if valid_images:
                try:
                    # Sadece ana resmi indirip vektörleştirelim (Performans için)
                    img_url = valid_images[0]
                    # Check if already indexed? (Optional optimization)
                    
                    # Download
                    # print(f"Processing Image: {img_url[:30]}...")
                    response = requests.get(img_url, timeout=5)
                    if response.status_code == 200:
                        image = Image.open(BytesIO(response.content))
                        embedding = model.encode(image)

                        point = models.PointStruct(
                            id=int(hash(str(group_id)) % 10**8), # Simple ID gen (Better: UUID/IntID from source)
                            vector=embedding.tolist(),
                            payload={
                                "id": product_data["id"],
                                "name": product_data["name"],
                                "price": product_data["price"],
                                "stock": product_data["stock"], # Critical for filtering
                                "image_url": img_url,
                                "description": product_data["description"][:200]
                            }
                        )
                        points.append(point)
                except Exception as img_err:
                    print(f"Skipping Image {group_id}: {img_err}")
            
            # Batch upsert every 10 items? No, simple list first.

        except Exception as e:
            print(f"⚠️ Hata (Grup ID: {group_id}): {e}")
            continue

    # JSON Save
    with open("chatbot_database.json", "w", encoding="utf-8") as f:
        json.dump(chatbot_products, f, ensure_ascii=False, indent=2)

    # Qdrant Upsert
    if points:
        print(f"Upserting {len(points)} image vectors to Qdrant...")
        qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
        print("✅ Indexing (JSON + Qdrant) complete!")
    else:
        print("⚠️ No vectors generated.")

    return chatbot_products

if __name__ == "__main__":
    process_csv_for_chatbot()