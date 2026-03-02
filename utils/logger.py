"""
LadySpecial ChatBot - Loglama Yardımcısı

Windows terminal encoding sorunlarını çözer.
Emoji'li mesajları güvenli şekilde yazdırır.
"""

import sys
import io


def setup_encoding():
    """
    Windows terminalinde UTF-8 encoding sorunlarını çözer.
    Uygulama başlangıcında bir kez çağrılmalıdır.
    """
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding='utf-8', errors='replace'
            )
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding='utf-8', errors='replace'
            )
        except Exception:
            pass  # Zaten ayarlanmış olabilir


def log(message: str):
    """Encoding-safe print fonksiyonu."""
    try:
        print(message)
    except UnicodeEncodeError:
        # Emoji'leri kaldırıp tekrar dene
        safe_msg = message.encode('ascii', errors='replace').decode('ascii')
        print(safe_msg)
