import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from models.database import get_connection

load_dotenv()

class InstagramSync:
    """Instagram Basic Display API veya Graph API uzerinden postlari senkronize eder."""

    def __init__(self):
        self.token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
        self.url = f"https://graph.instagram.com/me/media?fields=id,caption,media_url,permalink&access_token={self.token}"

    def sync(self) -> dict:
        if not self.token:
            print("⚠️ INSTAGRAM_ACCESS_TOKEN bulunamadi.")
            return {"synced": 0}

        print("[IG Sync] Postlar senkronize ediliyor...")
        count = 0
        url = self.url
        conn = get_connection()
        
        while url:
            try:
                res = requests.get(url, timeout=30)
                if res.status_code != 200:
                    print(f"[IG Sync] Hata: {res.text}")
                    break

                data = res.json()
                for item in data.get("data", []):
                    # Extract shortcode from permalink (e.g. https://www.instagram.com/p/DF2u1b1R1Xv/)
                    permalink = item.get("permalink", "")
                    shortcode = ""
                    if "/p/" in permalink:
                        shortcode = permalink.split("/p/")[-1].strip("/")
                    elif "/reel/" in permalink:
                        shortcode = permalink.split("/reel/")[-1].strip("/")
                        
                    if not shortcode:
                        continue
                        
                    caption = item.get("caption", "")
                    media_url = item.get("media_url", "")
                    media_id = item.get("id", "")

                    try:
                        # Önce bu post zaten var mı kontrol et
                        existing = conn.execute(
                            "SELECT id FROM instagram_posts WHERE shortcode = ?", (shortcode,)
                        ).fetchone()
                        
                        conn.execute("""
                            INSERT INTO instagram_posts (id, shortcode, caption, media_url, permalink, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(shortcode) DO UPDATE SET 
                                caption=excluded.caption,
                                media_url=excluded.media_url,
                                updated_at=excluded.updated_at
                        """, (
                            media_id, shortcode, caption, media_url, permalink,
                            datetime.utcnow().isoformat(), datetime.utcnow().isoformat()
                        ))
                        conn.commit()
                        if not existing:
                            count += 1  # Sadece yeni eklenenler sayılır
                    except Exception as e:
                        print(f"[IG Sync] Kayit hatasi: {e}")

                url = data.get("paging", {}).get("next")
            except Exception as e:
                print(f"[IG Sync] HTTP Hatasi: {e}")
                break

        conn.close()
        print(f"[IG Sync] Bitti. Yeni eklenen post: {count}")
        return {"synced": count}

if __name__ == "__main__":
    sync = InstagramSync()
    sync.sync()
