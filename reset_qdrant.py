from qdrant_client import QdrantClient
from qdrant_client.http import models

try:
    print("Recreating Qdrant Collection with 512 dimensions...")
    client = QdrantClient(url="http://localhost:6333")
    client.recreate_collection(
        collection_name="ladyspecial_products",
        vectors_config=models.VectorParams(size=512, distance=models.Distance.COSINE),
    )
    print("Collection 'ladyspecial_products' created successfully with dim 512.")
except Exception as e:
    print(f"Error: {e}")
