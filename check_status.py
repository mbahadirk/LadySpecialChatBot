import sqlite3
try:
    conn = sqlite3.connect('ladyspecial.db', timeout=5)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Kalan görsel sayısı
    c.execute("SELECT COUNT(*) as cnt FROM product_images WHERE is_indexed=0 AND media_type='image'")
    unindexed = c.fetchone()["cnt"]
    print(f"Indexlenmemis gorsel: {unindexed}")
    
    # Toplam
    c.execute("SELECT COUNT(*) as cnt FROM product_images")
    total = c.fetchone()["cnt"]
    print(f"Toplam gorsel: {total}")
    
    # Qdrant durum kontrolü
    from qdrant_client import QdrantClient
    print("Qdrant'a baglaniliyor...")
    q = QdrantClient(url='http://localhost:6333', timeout=3.0)
    colls = q.get_collections()
    print("Koleksiyonlar:", [c.name for c in colls.collections])
    
except Exception as e:
    print('HATA:', e)
