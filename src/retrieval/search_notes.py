from typing import Optional
from src.db.vault_db import get_embedder, get_collection

def search_notes(
        query: str,
        k: int = 5,
        max_distance: Optional[float] = None,
        where: Optional[dict] = None,
) -> list[dict]:
    """
    Search the Obsidian vault's embedded chunks for the most relevant matches.

    Args:
        query: Natural language search query.
        k: Max number of results to return.
        max_distance: If set, drop any result with distance above this value.
        where: Optional ChromaDB metadata filter, e.g. {"folder": "Projects"}
               or {"chunk_type": "note"}. If None, searches the whole vault.

    Returns:
        A list of dicts, each shaped like:
        {
            "text": str,
            "distance": float,
            "metadata": dict,
        }
        Ordered by distance ascending (most relevant first).
    """

    embedder = get_embedder()
    collection = get_collection()

    query_embedding = embedder.encode(query).tolist()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        where=where,
    )

    documents = results["documents"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]

    matches = [
        {"text": doc, "distance": dist, "metadata": meta}
        for doc, dist, meta in zip(documents, distances, metadatas)
    ]

    if max_distance is not None:
        matches = [m for m in matches if m["distance"] <= max_distance]

    return matches

if __name__ == "__main__":
    # Example usage
    results = search_notes("what did I learn about LangGraph state management", k=5)
    for r in results:
        print(f"distance: {r['distance']:.4f}")
        print(f"folder: {r['metadata'].get('folder')}")
        print(f"title: {r['metadata'].get('title')}")
        print(f"text preview: {r['text'][:150]}...")
        print("-" * 40)