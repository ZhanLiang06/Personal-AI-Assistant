import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "vault_folder_manifest.json"


class VaultManifestError(ValueError):
    """Raised when the vault manifest cannot be loaded or validated."""


class VaultManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    description: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = value.strip()
        if not path:
            raise ValueError("path cannot be empty")
        if "\\" in path:
            raise ValueError("path must use forward slashes")
        if Path(path).is_absolute():
            raise ValueError("path must be relative to the vault root")
        if path != "." and any(part in {"", ".", ".."} for part in path.split("/")):
            raise ValueError("path must be canonical and cannot contain empty, '.' or '..' segments")
        return path

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        description = value.strip()
        if not description:
            raise ValueError("description cannot be empty")
        return description


class VaultManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    folders: list[VaultManifestEntry] = Field(min_length=1)
    files: list[VaultManifestEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_entries(self) -> "VaultManifest":
        folder_paths = [entry.path for entry in self.folders]
        file_paths = [entry.path for entry in self.files]
        all_paths = folder_paths + file_paths

        duplicates = sorted({path for path in all_paths if all_paths.count(path) > 1})
        if duplicates:
            raise ValueError(f"duplicate manifest paths: {', '.join(duplicates)}")
        if "." not in folder_paths:
            raise ValueError("folders must contain a vault-root entry with path '.'")

        non_markdown_files = [path for path in file_paths if Path(path).suffix.lower() != ".md"]
        if non_markdown_files:
            raise ValueError(
                "file entries must reference Markdown files: " + ", ".join(non_markdown_files)
            )
        return self


def load_vault_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> VaultManifest:
    """Load the JSON manifest and validate its schema without touching the vault."""
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VaultManifestError(f"Vault manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise VaultManifestError(
            f"Vault manifest contains invalid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc

    try:
        return VaultManifest.model_validate(raw_manifest)
    except ValidationError as exc:
        raise VaultManifestError(f"Vault manifest schema is invalid:\n{exc}") from exc


def validate_manifest_paths(manifest: VaultManifest, vault_root: Path) -> None:
    """Verify that every declared folder and file exists with the expected type."""
    vault_root = vault_root.resolve()
    errors: list[str] = []

    for entry in manifest.folders:
        target = vault_root if entry.path == "." else vault_root.joinpath(*entry.path.split("/"))
        if not target.is_dir():
            errors.append(f"folder does not exist: {entry.path}")

    for entry in manifest.files:
        target = vault_root.joinpath(*entry.path.split("/"))
        if not target.is_file():
            errors.append(f"file does not exist: {entry.path}")

    if errors:
        raise VaultManifestError("Vault manifest paths are invalid:\n- " + "\n- ".join(errors))


def load_and_validate_vault_manifest(
    vault_root: Path,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> VaultManifest:
    """Load the manifest, then verify its entries against the real vault."""
    manifest = load_vault_manifest(manifest_path)
    validate_manifest_paths(manifest, vault_root)
    return manifest

def resolve_vault_scope(
    manifest: VaultManifest,
    scope_path: str,
) -> tuple[Literal["folder", "file"], list[str]]:
    """
    Resolve one canonical manifest path.

    Folder scopes include that folder and all declared descendants.
    File scopes contain only the exact file.
    """

    scope_path = scope_path.strip()

    if not scope_path:
        raise VaultManifestError("Vault scope path cannot be empty.")

    folder_paths = [entry.path for entry in manifest.folders]
    file_paths = [entry.path for entry in manifest.files]

    if scope_path in folder_paths:
        if scope_path == ".":
            return "folder", ["."]

        child_prefix = f"{scope_path}/"

        matching_folders = [
            path
            for path in folder_paths
            if path == scope_path or path.startswith(child_prefix)
        ]

        return "folder", matching_folders

    if scope_path in file_paths:
        return "file", [scope_path]

    raise VaultManifestError(
        f"Unknown vault scope: {scope_path}. "
        "Use list_vault_structure to obtain a canonical path."
    )