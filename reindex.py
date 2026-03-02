import asyncio
import os
from models.database import get_connection
from services.product_sync import ProductSync
from services.image_service import ImageService

async def main():
    print("Veritabanina baglaniliyor...")
    conn = get_connection()
    
    # Check if there are any products or images
    products_count = conn.execute("SELECT COUNT(*) as cnt FROM products").fetchone()["cnt"]
    images_count = conn.execute("SELECT COUNT(*) as cnt FROM product_images").fetchone()["cnt"]
    print(f"Toplam urun sayisi: {products_count}")
    print(f"Toplam gorsel sayisi: {images_count}")
    
    # Qdrant'ın tamamen sıfırlandığı için, veritabanındaki kayıtları "indekslenmedi" olarak işaretliyoruz
    print("\nTum gorsellerin 'is_indexed' durumu siriflanacak...")
    conn.execute("UPDATE product_images SET is_indexed = 0")
    conn.commit()
    conn.close()
    print("Gorseller siriflandi, yeniden indexleme kuyruguna alindi.")
    
    print("\nIndeksleme servisi baslatiliyor...")
    image_service = ImageService()
    sync_service = ProductSync(image_service)
    
    images_queued = sync_service._queue_new_images()
    print(f"Indexlenmeyi bekleyen {images_queued} adet gorsel kuyrukta.\n")
    
    if images_queued > 0:
        total = 0
        while True:
            indexed = await sync_service._index_pending_images(batch_size=20)
            if not indexed or indexed == 0:
                break
            total += indexed
            print(f">>> {total}/{images_queued} gorsel Qdrant'a yazildi...")
        
        print("\nRe-index islemi basariyla tamamlandi! Qdrant guncel.")
    else:
        print("Indexlenecek gorsel bulunamadi. (Urun gorseli tablosu bos olabilir)")

if __name__ == "__main__":
    asyncio.run(main())
