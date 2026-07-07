"""
ingest_vault.py

Ingests an Obsidian vault into a local, persistent ChromaDB vector store.

Design (locked after discussion):
- Adaptive chunking: split by markdown headers first, then fixed-size (sentence/line
  -boundary aware, budgeted against the embedder's real 512-token limit, ~50 token
  overlap, ~100 token minimum merge) within any section still too long.
- Markdown preservation
- Images: resolved as `<note_folder>/Attachments/<filename>` (fixed convention, no
  fallback search). OCR'd via Tesseract only (no vision-model captioning fallback).
  OCR text that passes a quality gate becomes its own `image_ocr` chunk, positioned
  within its true section/document order (not appended at the end), and carries the
  same section-header context as neighboring text chunks. Every chunk (text or OCR)
  also carries a folder/title context prefix in its embedded text for better
  fuzzy/semantic recall, plus `images` metadata pointing at resolved file path(s)
  so an agent can surface them even when their content isn't a semantic match.
- Token budgeting: chunk size is checked against the embedder's actual 512-token
    limit using its real tokenizer (not a word-count heuristic) on the fully
    assembled embedded string, so the text that gets embedded is what is measured.
- Parent linking: via existing `source_path` metadata (no separate mechanism needed
  -- an agent can re-read the full note from disk once it knows the path).
- Sequential linking: every chunk carries `chunk_index` / `total_chunks` so an
  agent can fetch neighboring chunks for extra context without pulling the whole note.
- Incremental sync: a JSON manifest keyed by note path, storing a combined hash of
  the note's raw text *and* the bytes of every image it references, so an
  image-only edit (e.g. replacing a screenshot without touching the caption)
  still triggers reprocessing of that note.
- Notes whose filename (stem) ends with "(no embed)" are excluded from ingestion
  entirely (and their chunks removed if that marker is added to a previously
  ingested note).

Run: uv run ingest_vault.py
"""

import os
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from PIL import Image
import pytesseract
from src.db.vault_db import get_embedder, get_collection

load_dotenv()

embedder = get_embedder()
collection = get_collection()
print("Done Setting Up Clients")

VAULT_PATH = Path(os.environ["VAULT_PATH"]).resolve()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MANIFEST_PATH = DATA_DIR / "sync_manifest.json"

MAX_SEQUENCE_TOKENS = 512   # bge-base-en-v1.5's real hard limit
CHUNK_OVERLAP_TOKENS = 50
SOFT_MIN_CHUNK_TOKENS = 100

OCR_MIN_CHARS = 15
OCR_MIN_ALPHA_RATIO = 0.5

ATTACHMENTS_DIRNAME = "Attachments"
SKIP_DIR_NAMES = {".obsidian"}
NO_EMBED_SUFFIX = "(no embed)"


def count_tokens(text: str) -> int:
    """Real token count via the embedder's own tokenizer -- not a word-count guess."""
    # print("Counting tokens")
    # tokens_count = len(embedder.tokenizer.encode(text, add_special_tokens=True))
    # print("Tokens counted:", tokens_count)
    # return tokens_count
    return len(embedder.tokenizer.encode(text, add_special_tokens=True))


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"notes": {}}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

# Image Path Resolution
IMAGE_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]|!\[[^\]]*\]\(([^)]+)\)")


def resolve_image_path(raw_ref: str, note_path: Path) -> Path | None:
    filename = Path(raw_ref.strip()).name
    candidate = note_path.parent / ATTACHMENTS_DIRNAME / filename
    if candidate.exists():
        return candidate
    print(f"[WARN] Image not found at expected path '{candidate}' (referenced in {note_path})")
    return None


def find_image_refs(text: str) -> list[str]:
    return [m.group(1) or m.group(2) for m in IMAGE_EMBED_RE.finditer(text)]


# OCR (Tesseract only)

def ocr_image(image_path: Path) -> str:
    try:
        return pytesseract.image_to_string(Image.open(image_path)).strip()
    except Exception as e:
        print(f"[WARN] OCR failed for {image_path}: {e}")
        return ""


def is_usable_ocr(text: str) -> bool:
    if len(text) < OCR_MIN_CHARS:
        return False
    return True

def clean_ocr_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    # Drop isolated single-character symbols (stray UI-icon/border noise), but
    # never touch a character that's part of a larger token (e.g. "142.50").
    text = re.sub(r"(?<!\S)[^\sA-Za-z0-9](?!\S)", "", text)
    return text.strip()


#Clean markup files
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
HIGHLIGHT_RE = re.compile(r"==(.+?)==")
CHECKBOX_RE = re.compile(r"^[-*]\s*\[( |x|X)\]\s*(.+)$", re.MULTILINE)
BULLET_RE = re.compile(r"^[-*]\s+", re.MULTILINE)
BLOCK_REF_RE = re.compile(r"\^[a-zA-Z0-9]+\s*$", re.MULTILINE)
COMMENT_RE = re.compile(r"%%.+?%%", re.DOTALL)
CALLOUT_RE = re.compile(r"^>\s*\[!(\w+)\]\s*(.*)$", re.MULTILINE)
BLOCKQUOTE_MARKER_RE = re.compile(r"^>\s?", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TABLE_SEP_RE = re.compile(r"^\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+$", re.MULTILINE)
TABLE_PIPE_RE = re.compile(r"\|")


def strip_frontmatter(text: str) -> str:
    match = FRONTMATTER_RE.match(text)
    return text[match.end():] if match else text


def clean_markdown(text: str) -> tuple[str, list[str]]:
    wikilinks: list[str] = []

    def _wikilink_sub(m):
        name = m.group(1).strip()
        wikilinks.append(name)
        return m.group(0) #keep the original links
 
    text = WIKILINK_RE.sub(_wikilink_sub, text)
    # text = COMMENT_RE.sub("", text)
    # text = CALLOUT_RE.sub(lambda m: f"{m.group(1).title()}: {m.group(2)}".rstrip(": "), text)
    # text = BLOCKQUOTE_MARKER_RE.sub("", text)
    # text = CHECKBOX_RE.sub(lambda m: m.group(2), text)
    # text = TABLE_SEP_RE.sub("", text)          # strip separator rows FIRST
    # text = TABLE_PIPE_RE.sub(" ", text)        # then flatten remaining cell pipes
    # text = HIGHLIGHT_RE.sub(lambda m: m.group(1), text)
    # text = BOLD_ITALIC_RE.sub(lambda m: m.group(2), text)
    # text = BLOCK_REF_RE.sub("", text)
    # text = BULLET_RE.sub("", text)
    # text = re.sub(r"[ \t]+", " ", text)
    # text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip(), wikilinks


# ---------------------------------------------------------------------------
# Chunking: header-first, then token-budgeted fixed-size fallback.
# Sentence/line splitting treats BOTH sentence-final punctuation and bare
# newlines as boundaries, since short unpunctuated lines ("set stop loss")
# are common in this vault and shouldn't get glued to unrelated neighbors.
# ---------------------------------------------------------------------------

LINE_OR_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
HEADER_SPLIT_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

# test this function get each chunks tokens does it exceed the budget 
def fixed_size_split(text: str, budget: int, header_title: str = "", context_prefix: str = "", no_check_last: bool = False) -> list[str]:
    units = [u for u in LINE_OR_SENTENCE_SPLIT_RE.split(text) if u.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    prefix = f"{context_prefix}{header_title}: " if header_title else f"{context_prefix}"
    prefix_tokens = count_tokens(prefix)
    last_chunk_overlap_tokens = 0
    overlap_units = 0
    orphan_str = ""

    for unit in units:
        unit_tokens = count_tokens(unit)
        if current_tokens + unit_tokens + prefix_tokens > budget and current:
            chunks.append(' '.join(current))
            overlap, overlap_tokens, overlap_units = [], 0, 0
            for u in reversed(current):
                t = count_tokens(u)
                if overlap_tokens + t > CHUNK_OVERLAP_TOKENS:
                    break
                overlap.insert(0, u)
                overlap_tokens += t
                overlap_units += 1
            current, current_tokens = overlap, overlap_tokens
            last_chunk_overlap_tokens = overlap_tokens
        current.append(unit)
        current_tokens += unit_tokens

    if no_check_last:
        if current:
            chunks.append(' '.join(current))
        return chunks

    if current:
        temp_current = current[overlap_units:] if last_chunk_overlap_tokens > 0 else current
        orphan_str = ' '.join(temp_current)
    
    if len(chunks) >= 1 and orphan_str and count_tokens(orphan_str) < SOFT_MIN_CHUNK_TOKENS - last_chunk_overlap_tokens - prefix_tokens:
        # Pull the last chunk + orphan leftover, recombine, split evenly in two.
        print(f"[INFO] Last chunk + orphan ({count_tokens(orphan_str)} tokens) < {SOFT_MIN_CHUNK_TOKENS} tokens, redistributing...")
        previous_chunk = chunks.pop()
        combined_text = previous_chunk + " " + orphan_str
        last_two_chunks = split_into_two(combined_text, budget, prefix_tokens)
        chunks.extend(last_two_chunks)

    else:
        chunks.append(' '.join(current))

    final_chunks = [f"{prefix}{c}" for c in chunks]

    return final_chunks

def split_into_two(text: str, budget: int, prefix_tokens: int) -> list[str]:
    """Split text into exactly two overlap-respecting, budget-respecting halves.
    Returns raw (unprefixed) text -- prefix is added by the caller uniformly,
    same as every other chunk, to avoid double-prefixing."""
    units = [u for u in LINE_OR_SENTENCE_SPLIT_RE.split(text) if u.strip()]
    total = count_tokens(text)
    target = total // 2
    running = 0
    split_at = len(units)
    for i, u in enumerate(units):
        running += count_tokens(u)
        if running >= target:
            split_at = i + 1
            break

    # Carry trailing units of the first half into the second (same accumulation
    # logic fixed_size_split uses for overlap).
    overlap, overlap_tokens = [], 0
    for u in reversed(units[:split_at]):
        t = count_tokens(u)
        if overlap_tokens + t > CHUNK_OVERLAP_TOKENS:
            break
        overlap.insert(0, u)
        overlap_tokens += t

    first_half = ' '.join(units[:split_at])
    second_half = ' '.join(overlap + units[split_at:])
    result = [first_half, second_half]

    for c in result:
        assert count_tokens(c) + prefix_tokens <= budget, (
            f"split_into_two produced an oversized chunk: "
            f"{count_tokens(c) + prefix_tokens} > {budget}"
        )
    return result

# bucket_1, bucket_2 = [], []
        # bucket_1_tokens = 0
        
        # for unit in all_units:
        #     unit_tokens = count_tokens(unit)
        #     if bucket_1_tokens + unit_tokens <= half_tokens:
        #         bucket_1.append(unit)
        #         bucket_1_tokens += unit_tokens
        #         ## overlap
        #         overlap, overlap_tokens = [], 0
        #         for u in reversed(bucket_1):
        #             t = count_tokens(u)
        #             if overlap_tokens + t > CHUNK_OVERLAP_TOKENS:
        #                 break
        #             overlap.insert(0, u)
        #             overlap_tokens += t
        #         bucket_2 = overlap
        #     else:
        #         bucket_2.append(unit)
        # # 4. Push the beautifully balanced chunks back into your list
        # chunks.append(' '.join(bucket_1))
        # chunks.append(f"{prefix}{' '.join(bucket_2)}")


def split_by_headers(text: str) -> list[tuple[str, str]]:
    matches = list(HEADER_SPLIT_RE.finditer(text))
    if not matches:
        return [("", text)]

    sections = []
    intro = text[: matches[0].start()].strip()
    if intro:
        sections.append(("", intro))

    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((title, text[start:end].strip()))

    return sections


def chunk_section_text(cleaned_text: str, budget: int, header_title: str, context_prefix: str) -> list[str]:
    if not cleaned_text:
        return []
    exact_text = f"{context_prefix}{header_title}: {cleaned_text}" if header_title else f"{context_prefix}{cleaned_text}"
    if count_tokens(exact_text) <= budget:
        return [exact_text]
    return fixed_size_split(cleaned_text, budget, header_title, context_prefix)


# ---------------------------------------------------------------------------
# Context prefix
# ---------------------------------------------------------------------------

def build_context_prefix(note_path: Path, vault_path: Path) -> str:
    rel = note_path.relative_to(vault_path)
    folder = " > ".join(rel.parent.parts) if rel.parent.parts else "the vault root"
    return f"This note, titled '{note_path.stem}', is from the {folder} folder."


# ---------------------------------------------------------------------------
# Note processing.
#
# Header-splitting runs FIRST, so each section knows its own title. Within
# each section, text and image references are interleaved in true document
# order, so an image_ocr chunk carries the same section-header context as the
# text chunks around it, and lands at its real position for sequential linking.
# ---------------------------------------------------------------------------

def build_units(section_text: str) -> list[tuple[str, str | Path, str]]:
    """Split a section's text on image embeds. Returns ('text', str, '') or
    ('image', raw_ref, '') tuples in document order (path resolution happens
    by the caller, which has the note_path)."""
    units = []
    last_end = 0
    for m in IMAGE_EMBED_RE.finditer(section_text):
        segment = section_text[last_end:m.start()]
        if segment.strip():
            units.append(("text", segment, ""))
        units.append(("image", m.group(1) or m.group(2), ""))
        last_end = m.end()
    trailing = section_text[last_end:]
    if trailing.strip():
        units.append(("text", trailing, ""))
    return units


def process_note(note_path: Path, vault_path: Path) -> list[dict]:
    raw_text = note_path.read_text(encoding="utf-8", errors="replace")
    body = strip_frontmatter(raw_text)

    context_prefix = build_context_prefix(note_path, vault_path)
    budget = MAX_SEQUENCE_TOKENS

    rel_path = str(note_path.relative_to(vault_path))
    folder = str(note_path.relative_to(vault_path).parent)
    last_modified = datetime.fromtimestamp(note_path.stat().st_mtime, tz=timezone.utc).isoformat()

    ordered_texts: list[tuple[str, str, str]] = []  # (chunk_type, chunk_text_without_prefix, header_title)
    all_wikilinks: list[str] = []
    all_image_paths: list[str] = []

    for title, section_text in split_by_headers(body):
        for unit_type, payload, _ in build_units(section_text):
            if unit_type == "text":
                cleaned, wikilinks = clean_markdown(payload)
                all_wikilinks.extend(wikilinks)
                for chunk_text in chunk_section_text(cleaned, budget, title, context_prefix):
                    ordered_texts.append(("text", chunk_text, title))
            else:
                resolved = resolve_image_path(payload, note_path)
                if not resolved:
                    continue
                all_image_paths.append(str(resolved))
                ocr_raw = ocr_image(resolved)
                if not is_usable_ocr(ocr_raw):
                    continue
                ocr_text = clean_ocr_text(ocr_raw)
                label = f"{title}: " if title else ""
                for chunk_text in chunk_section_text(ocr_text, budget, f"{label}Image content (ocr)", context_prefix):
                    ordered_texts.append(("image_ocr", chunk_text, title))


    total = len(ordered_texts)
    docs = []
    for i, (chunk_type, chunk_text, header_title) in enumerate(ordered_texts):
        docs.append({
            "id": f"{rel_path}::chunk{i}",
            "text": f"{chunk_text}",
            "metadata": {
                "title": note_path.stem,
                "header_title": header_title,
                "chunk_type": chunk_type,
                "source_path": rel_path,
                "folder": folder,
                "last_modified": last_modified,
                # "images": all_image_paths,
                # "wikilinks": all_wikilinks,
                "images": ", ".join(all_image_paths), 
                "wikilinks": ", ".join(dict.fromkeys(all_wikilinks)),
                "chunk_index": i,
                "total_chunks": total,
                "total_tokens": count_tokens(chunk_text),
            },
        })
    return docs


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------

def compute_note_combined_hash(note_path: Path) -> str:
    raw_text = note_path.read_text(encoding="utf-8", errors="replace")
    h = hashlib.sha256()
    h.update(raw_text.encode("utf-8"))
    for raw_ref in find_image_refs(raw_text):
        img_path = resolve_image_path(raw_ref, note_path)
        if img_path:
            h.update(img_path.read_bytes())
    return h.hexdigest()


def is_excluded(note_path: Path) -> bool:
    return note_path.stem.strip().lower().endswith(NO_EMBED_SUFFIX)


def gather_notes(vault_path: Path) -> list[Path]:
    notes = []
    for path in vault_path.rglob("*.md"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if is_excluded(path):
            continue
        notes.append(path)
    return notes


def delete_chunks_for_source(rel_path: str) -> None:
    existing = collection.get(where={"source_path": rel_path})
    print(f"Initialized delete_chunks_for_source for {rel_path}, found {len(existing.get('ids', []))} existing chunks.")
    if existing and existing.get("ids"):
        print(f"Deleting {len(existing['ids'])} chunks for {rel_path}")
        collection.delete(ids=existing["ids"])


def upsert_docs(docs: list[dict]) -> None:
    if not docs:
        return
    embeddings = embedder.encode([d["text"] for d in docs], normalize_embeddings=True).tolist()
    print(f"Upserting {len(docs)} docs with embeddings into ChromaDB collection '{collection.name}'...")
    print(f"All docs ids : ")
    for d in docs:
        print(d["id"])
    collection.upsert(
        ids=[d["id"] for d in docs],
        documents=[d["text"] for d in docs],
        metadatas=[d["metadata"] for d in docs],
        embeddings=embeddings,
    )


def sync_vault() -> None:
    manifest = load_manifest()
    notes = gather_notes(VAULT_PATH)
    seen: set[str] = set()

    for note_path in notes:
        rel_path = str(note_path.relative_to(VAULT_PATH))
        seen.add(rel_path)

        current_hash = compute_note_combined_hash(note_path)
        prior_hash = manifest["notes"].get(rel_path)
        if current_hash == prior_hash:
            continue

        print(f"Processing: {rel_path}")
        if prior_hash is not None:
            delete_chunks_for_source(rel_path)

        docs = process_note(note_path, VAULT_PATH)
        upsert_docs(docs)
        manifest["notes"][rel_path] = current_hash

    # Notes removed from the vault OR newly marked "(no embed)" both fall out
    # of `seen` -- either way their existing chunks should be cleared.
    for rel_path in list(manifest["notes"].keys()):
        if rel_path not in seen:
            print(f"Removing: {rel_path}")
            delete_chunks_for_source(rel_path)
            del manifest["notes"][rel_path]

    save_manifest(manifest)
    print(f"Sync complete. {len(seen)} notes ingested.")


##test fucntion
def build_text_with_token_count(target_tokens: int) -> str:
    """Construct realistic text - short sentences and bare-newline lines mixed,
    matching the vault's actual style - whose tokenized length is exactly
    target_tokens. Built from whole units (not words) so LINE_OR_SENTENCE_SPLIT_RE
    has real split points to work with, same as it would against real notes."""
    unit_pool = [
        "The market moved sideways today.",
        "set stop loss",  # bare unpunctuated line, mirrors the vault's real style
        "Reliability testing continued through the quarter.",
        "check telemetry logs\n",
        "The agent retrieved the wrong section.",
        "fun",
    ]
    units: list[str] = []
    i = 0
    while count_tokens("\n".join(units)) < target_tokens:
        units.append(unit_pool[i % len(unit_pool)])
        i += 1
    text = "\n".join(units)

    # Trim by whole units (not words) until exact, so every unit stays intact
    # and the split regex keeps working on it exactly as it would on real text.
    while count_tokens(text) > target_tokens and units:
        units.pop()
        text = "\n".join(units)
    return text

def test_chunking_edge_cases() -> None:
    """Boundary tests for chunk_section_text: empty input, exactly-at-budget,
    and one-over-budget. Prints chunk count + per-chunk token count so you can
    eyeball whether the split logic behaves correctly at each edge."""
    budget = MAX_SEQUENCE_TOKENS  # 512
    header_title = "Test Section"
    context_prefix = "This note, titled 'Test', is from the vault root folder."
    prefix_tokens = count_tokens(f"{context_prefix}{header_title}: ")
    print(f"Prefix tokens count: {prefix_tokens}")
    cases = {
        # "empty": "",
        # "exactly_512": build_text_with_token_count(budget - prefix_tokens + 7),
        # "exactly_512": "Continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. "
        # "exceeds_by_1": build_text_with_token_count(budget - prefix_tokens + 8),
        "exceeds_by_1":  "The Town, a crumbling monument to a time when knowledge was preserved on paper rather than floating in the invisible ether of digital networks. As the heavy wooden doors groaned open, a swirl of dust danced in the singular shaft of golden sunlight that pierced through the fractured glass window high above. Inside, rows of towering mahogany shelves stretched toward the vaulted ceiling, casting long, solemn shadows across the floorboards. Every surface was blanketed in a fine layer of gray dust, a silent testament to the decades that had passed since human hands last turned these delicate pages. Stepping forward, the air felt thick and smelled intensely of aged parchment, leather bindings, and the faint, sweet scent of vanilla that emanated from decaying lignin. It was a sanctuary of lost thoughts, a physical manifestation of humanity’s collective memory, frozen in time and waiting for someone to awaken its dormant voices. Walking down the central aisle, my fingers lightly brushed against the spines of countless volumes, feeling the textures of embossed gold lettering and worn fabric. Each book represented a mind that had once burned with passion, curiosity, or a desperate desire to be understood across the vast chasm of generations. There were grand histories of empires that had long since turned to ash, intricate scientific treatises on laws of nature that had since been refined, and intimate collections of poetry capturing the fleeting heartbreaks of ordinary lives. In this quiet space, the frantic noise of the modern world completely dissolved, replaced by a profound, heavy silence that felt almost reverent. It was a stark reminder that despite our rapid technological advancements and the instantaneous nature of contemporary communication, there is something deeply sacred about the tangible remnants of our past. We are, after all, a species defined by our stories, constantly searching for meaning and looking for echoes of ourselves in the words left behind by those who walked the earth before us. To sit among these forgotten relics is to realize that our individual lives are merely brief sentences in a massive, ongoing narrative that spans millennia. As I carefully pulled a leather bound journal from its resting place, the spine cracked softly, sounding like a sudden whisper in the stillness, and I knew right. then and there that the. simple and deliberate act of reading. was a remarkably powerful. bridge. connecting. my. own. personal. present. moment. to. a. silent. ghost. from. a. completely. different. historical. era. Soccer. completely. different. historical. era. Soccer. completely. different. historical. era.",
    }

    for label, text in cases.items():
        print(f"\n--- Case: {label} (raw body tokens: {count_tokens(text) if text else 0}) ---")
        chunks = chunk_section_text(text, budget, header_title, context_prefix)
        if not chunks:
            print("  -> [] (no chunks produced)")
            continue
        for i, c in enumerate(chunks):
            t = count_tokens(c)
            status = "OK" if t <= budget else "!!! EXCEEDS BUDGET !!!"
            print(f"  chunk {i}: {t} tokens {status} text:{c} \n\n\n")

def inspect_note(rel_path: str) -> None:
    """Print all chunks for one note, in chunk_index order — verifies sequential
    linking (no gaps/dupes) and that image_ocr chunks landed in the right section."""
    result = collection.get(where={"source_path": rel_path})
    if not result["ids"]:
        print(f"No chunks found for {rel_path}")
        return
    rows = sorted(zip(result["metadatas"], result["documents"]), key=lambda r: r[0]["chunk_index"])
    print(f"\n--- {rel_path}: {len(rows)} chunks ---")
    for meta, doc in rows:
        print(f"[{meta['chunk_index']}/{meta['total_chunks']}] type={meta['chunk_type']} images={meta['images'] or '-'}")
        print(f"  {doc[:150]}{'...' if len(doc) > 150 else ''}\n")


def semantic_query(query: str, n_results: int = 3) -> None:
    """Run a test embedding query and print ranked hits with distance + metadata."""
    query_emb = embedder.encode([query], normalize_embeddings=True).tolist()
    results = collection.query(query_embeddings=query_emb, n_results=n_results)
    print(f"\n--- Query: '{query}' ---")
    for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
        print(f"[{dist:.4f}] {meta['source_path']} chunk {meta['chunk_index']}/{meta['total_chunks']} ({meta['chunk_type']})")
        print(f"  {doc[:150]}{'...' if len(doc) > 150 else ''}\n")

if __name__ == "__main__":
    # test_chunking_edge_cases()
    # print(count_tokens("This note, titled 'Test', is from the vault root folder.Test Section: Continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today. Reliability testing continued through the quarter. The market moved sideways today."))
    sync_vault()
    print(f"\nTotal chunks in collection: {collection.count()}")

    # inspect_note("Career/Goreal R&D Internships/<note filename>.md")
    semantic_query("stop loss")
    semantic_query("SMT reliability testing")

    import json

    # 1. Connect to your local persistent ChromaDB

    # 2. Define the collection you want to export
    collection = get_collection()

    # 3. Setup pagination variables
    limit = 100
    offset = 0
    all_records = []

    print(f"Starting export for collection: {collection.name}...")

    while True:
        # Explicitly pull only documents and metadatas (ids are always included)
        batch = collection.get(
            limit=limit,
            offset=offset,
            include=["documents", "metadatas"]
        )
        
        # Break the loop if no more records are found
        if not batch["ids"]:
            break
            
        # Reformat the columnar response into a list of JSON-friendly objects
        for i in range(len(batch["ids"])):
            record = {
                "id": batch["ids"][i],
                "document": batch["documents"][i] if batch["documents"] else None,
                "metadata": batch["metadatas"][i] if batch["metadatas"] else None
            }
            all_records.append(record)
            
        offset += limit

    # 4. Save the records to a JSON file
    output_file = f"{collection.name}_export.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=4)

    print(f"Successfully exported {len(all_records)} records to '{output_file}'!")


