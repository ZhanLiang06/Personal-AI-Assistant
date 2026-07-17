import os
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional
from langchain_core.tools import tool
import re
from src.retrieval.search_notes import search_notes as _search_notes_impl
from src.retrieval.vault_manifest import (
    VaultManifestError,
    load_and_validate_vault_manifest,
    resolve_vault_scope,
)

load_dotenv()
VAULT_ROOT = Path(os.environ.get("VAULT_PATH"))
SEARCH_RESULT_LIMIT = 5

# List Vault Manifest Tool
@tool
def list_vault_structure() -> str:
    """
    List the canonical folders and standalone root files in the user's
    Obsidian vault, together with their descriptions.

    Use this before search_notes when the user names a personal domain
    likely represented by a folder or file, but its canonical path is
    not already available. Also use it for questions about vault
    organization or where information is stored.

    Call this tool at most once per user request. Reuse its result.
    Do not use its descriptions as evidence about note content.
    """

    try:
        manifest = load_and_validate_vault_manifest(VAULT_ROOT)
    except VaultManifestError as exc:
        return f"Vault structure is unavailable: {exc}"

    lines = ["Vault folders:"]

    for folder in manifest.folders:
        display_path = "vault root (.)" if folder.path == "." else folder.path
        lines.append(f"- {display_path}: {folder.description}")

    if manifest.files:
        lines.append("")
        lines.append("Standalone root files:")

        for file in manifest.files:
            lines.append(f"- {file.path}: {file.description}")

    return "\n".join(lines)

def build_scope_filter(scope_path: str) -> dict:
    """
    Convert a canonical manifest scope into a Chroma metadata filter.
    """

    manifest = load_and_validate_vault_manifest(VAULT_ROOT)

    scope_type, resolved_paths = resolve_vault_scope(
        manifest=manifest,
        scope_path=scope_path,
    )

    # Ingestion uses pathlib-generated Windows paths in Chroma metadata,
    # while the JSON manifest uses portable forward slashes.
    metadata_paths = [
        "." if path == "." else str(Path(*path.split("/")))
        for path in resolved_paths
    ]

    metadata_field = (
        "folder"
        if scope_type == "folder"
        else "source_path"
    )

    if len(metadata_paths) == 1:
        return {
            metadata_field: metadata_paths[0]
        }

    return {
        metadata_field: {
            "$in": metadata_paths
        }
    }


# Search Notes Tool
class SearchNotesInput(BaseModel):
    query: str = Field(
        description="A clean, focused search query extracted from the user's question — not the raw user message verbatim."
    )
    scope_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional exact folder or file path returned by "
            "list_vault_structure. If omitted, search the entire vault."
        ),
    )

@tool(args_schema=SearchNotesInput)
def search_notes(query: str, scope_path: Optional[str] = None) -> str:
    """
    Search relevant content in the user's personal Obsidian notes.

    Optionally restrict the search to an exact folder or file scope
    returned by list_vault_structure.
    """
    where = None
    if scope_path is not None:
        try:
            where = build_scope_filter(scope_path)
        except VaultManifestError as exc:
            return f"Cannot search the requested vault scope: {exc}"
        
    results = _search_notes_impl(
        query=query,
        k = SEARCH_RESULT_LIMIT,
        where=where
    )

    if not results:
        if scope_path is not None:
            return (
                "No matching notes found within vault scope "
                f"'{scope_path}'."
            )
        return "No matching notes found."

    formatted = []
    for r in results:
        metadata = r["metadata"]
        title = metadata.get("title", "unknown")
        header_title = metadata.get("header_title")
        source_path = metadata.get("source_path", "unknown")
        heading = (
            f"{title} > {header_title}"
            if header_title
            else title
        )
        heading = f"{title} > {header_title}" if header_title else title
        formatted.append(
            f"[{heading}]\n"
            f"Source: {source_path}\n"
            f"{r['text']}"
        )

    return "\n---\n".join(formatted)