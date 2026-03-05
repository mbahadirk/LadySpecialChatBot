import hmac
import hashlib
import os
from fastapi import Request, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from dotenv import load_dotenv

load_dotenv()

# Configuration
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "default_secret_key_change_me")
API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def verify_meta_signature(payload: bytes, signature: str) -> bool:
    """
    Verifies the signature sent by Meta in the X-Hub-Signature-256 header.
    
    Args:
        payload: The raw request body as bytes.
        signature: The signature string (sha256=...).
        
    Returns:
        True if the signature is valid, False otherwise.
    """
    if not META_APP_SECRET:
        print("⚠️ META_APP_SECRET is not set. Skipping signature verification (UNSAFE!).")
        return True

    if not signature or not signature.startswith("sha256="):
        return False

    expected_signature = hmac.new(
        key=META_APP_SECRET.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected_signature}", signature)

async def get_api_key(api_key: str = Security(api_key_header)):
    """
    Dependency to validate the internal API key.
    """
    if api_key == INTERNAL_API_KEY:
        return api_key
    raise HTTPException(
        status_code=403,
        detail="Could not validate credentials"
    )

def sanitize_prompt_input(text: str) -> str:
    """
    Sanitizes user input for LLM prompts by adding delimiters.
    """
    if not text:
        return ""
    # Remove any existing delimiter-like markers to prevent bypass
    sanitized = text.replace("###", "---")
    return f"### USER MESSAGE START ###\n{sanitized}\n### USER MESSAGE END ###"
