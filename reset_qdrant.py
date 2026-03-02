"""Qdrant koleksiyonunu sifirlayip, DB'deki is_indexed bayraklarini sifirlar."""
import sqlite3
from qdrant_client import QdrantClient
from qdrant_client.http import models

# 1. Qdrant koleksiyonunu sifirla
print("1. Qdrant koleksiyonu siliniyor...")
q = QdrantClient(url="http://localhost:6333")
try:
    q.delete_collection("ladyspecial_products")
    print("   Silindi.")
except Exception as e:
    print(f"   Silinirken hata (muhtemelen yok): {e}")

print("2. Yeni koleksiyon olusturuluyor...")
q.create_collection(
    "ladyspecial_products",
    vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE)
)
print("   Olusturuldu.")

# 2. DB'deki product_images is_indexed bayragini sifirla
print("3. DB product_images is_indexed sifirlaniyor...")
conn = sqlite3.connect("ladyspecial.db")
conn.execute("UPDATE product_images SET is_indexed = 0, qdrant_point_id = NULL")
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM product_images WHERE is_indexed = 0").fetchone()[0]
conn.close()
print(f"   {count} gorsel yeniden indexlenecek.")

print("\nTamamlandi! Sunucuyu baslat: python main.py")
