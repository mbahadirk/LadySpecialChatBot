"""
LadySpecial ChatBot - Veritabanı Modelleri

SQLite veritabanı şeması ve bağlantı yönetimi.
Kullanıcıları, mesaj geçmişlerini ve ürün kataloğunu saklar.
"""

import sqlite3
import os
from datetime import datetime


# Veritabanı dosya yolu
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ladyspecial.db")


def get_connection() -> sqlite3.Connection:
    """SQLite bağlantısı döndürür. Row factory ile dict-benzeri erişim sağlar."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """
    Veritabanı tablolarını oluşturur (yoksa).
    Uygulama başlatılırken bir kez çağrılmalıdır.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ─── Kullanıcılar Tablosu ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            whatsapp_id     TEXT UNIQUE,
            instagram_id    TEXT UNIQUE,
            display_name    TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ─── Mesajlar Tablosu ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            platform        TEXT NOT NULL,
            role            TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content         TEXT NOT NULL,
            intent          TEXT,
            image_path      TEXT,
            message_id      TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ─── Ürünler Tablosu (ikas XML'den senkronize) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            slug            TEXT,
            description     TEXT,
            category        TEXT,
            price           REAL DEFAULT 0,
            currency        TEXT DEFAULT 'TRY',
            total_stock     INTEGER DEFAULT 0,
            image_url       TEXT,
            all_image_urls  TEXT,
            variants_json   TEXT,
            is_active       INTEGER DEFAULT 1,
            last_synced     TEXT NOT NULL DEFAULT (datetime('now')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ─── Ürün Görselleri (Qdrant indexleme takibi) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_images (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id      TEXT NOT NULL,
            image_url       TEXT NOT NULL,
            qdrant_point_id INTEGER,
            is_indexed      INTEGER DEFAULT 0,
            is_main         INTEGER DEFAULT 0,
            sort_order      INTEGER DEFAULT 0,
            media_type      TEXT DEFAULT 'image',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            UNIQUE(product_id, image_url)
        )
    """)

    # ─── Migration: Eski tabloya image_path ekleme ───
    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN image_path TEXT")
        print("[DB] image_path kolonu eklendi (migration).")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN message_id TEXT")
        print("[DB] message_id kolonu eklendi (migration).")
    except Exception:
        pass

    # ─── İndeksler ───
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_whatsapp ON users(whatsapp_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_instagram ON users(instagram_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_slug ON products(slug)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_active ON products(is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_images_product ON product_images(product_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_images_indexed ON product_images(is_indexed)")

    conn.commit()
    conn.close()
    print(f"[OK] Veritabani hazir: {DB_PATH}")
