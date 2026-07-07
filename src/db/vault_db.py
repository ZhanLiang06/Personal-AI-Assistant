import os
from pathlib import Path

from dotenv import load_dotenv
import chromadb
from sentence_transformers import SentenceTransformer

load_dotenv()

VAULT_PATH = Path(os.environ["VAULT_PATH"]).resolve()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma_db"
MANIFEST_PATH = DATA_DIR / "sync_manifest.json"
COLLECTION_NAME = "obsidian_vault_atlas"

EMBEDDING_MODEL_NAME = "BAAI/bge-base-en-v1.5"

_embedder = None
_chroma_client = None
_collection = None

def get_project_root() -> Path:
    """Returns the root directory of the project."""
    return PROJECT_ROOT


def get_embedder():
    """Returns the shared SentenceTransformer instance, loading it once on first call."""
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def get_collection():
    """Returns the shared ChromaDB collection, connecting once on first call."""
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _chroma_client.get_or_create_collection(COLLECTION_NAME)
    return _collection