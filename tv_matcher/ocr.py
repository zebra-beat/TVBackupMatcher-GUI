import os
import re
import subprocess
from functools import lru_cache
from typing import Iterable, Optional

from .config import TESSERACT_BIN

# Tesseract spawns one OpenMP thread per core by default; we run several OCR jobs in
# parallel, so pin each process to one thread to avoid oversubscription.
_TESSERACT_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


def _tesseract(image_path: str, psm: str) -> str:
    cmd = [TESSERACT_BIN, image_path, "stdout", "--psm", psm]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=_TESSERACT_ENV, timeout=60)
        return proc.stdout.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def run_ocr(image_path: str, variants: Optional[Iterable[str]] = None) -> str:
    """
    OCRs a frame. With `variants` (e.g. greyscale and inverted pre-processed images) each is
    read with sparse-text mode (--psm 11, best for title cards). Without variants, falls back
    to the original behaviour of two page-segmentation modes on the single image.
    """
    if not os.path.exists(image_path):
        return ""

    images = [p for p in (variants or []) if p and os.path.exists(p)]
    if images:
        jobs = [(p, "11") for p in images]
    else:
        jobs = [(image_path, "11"), (image_path, "3")]

    combined_text = [txt for txt in (_tesseract(p, psm) for p, psm in jobs) if txt]
    raw = " ".join(combined_text)
    return re.sub(r"\s+", " ", raw).strip()


_WORD_NUMBERS = {"ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4"}


@lru_cache(maxsize=8192)
def normalize_for_matching(text: str) -> str:
    t = text.upper()
    t = re.sub(r"\bPART\s+III\b", "PART 3", t)
    t = re.sub(r"\bPART\s+II\b", "PART 2", t)
    t = re.sub(r"\bPART\s+I\b", "PART 1", t)
    t = re.sub(r"\bPART\s+IV\b", "PART 4", t)
    t = re.sub(r"\bPART\s+(ONE|TWO|THREE|FOUR)\b", lambda m: f"PART {_WORD_NUMBERS[m.group(1)]}", t)
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


CREDIT_PHRASES = (
    "DIRECTED BY",
    "WRITTEN BY",
    "STORY BY",
    "TELEPLAY BY",
    "PRODUCED BY",
    "EXECUTIVE PRODUCER",
    "SUPERVISING PRODUCER",
    "ASSOCIATE PRODUCER",
    "MUSIC BY",
    "ANIMATION BY",
    "ANIMATION DIRECTOR",
    "CREATED BY",
    "STARRING",
    "GUEST STARRING",
    "DEVELOPED BY",
    "ART DIRECTOR",
    "CHARACTER DESIGN",
    "PRODUCTION DESIGNER",
    "CASTING BY",
    "EDITED BY",
)


def has_credit_keywords(text: str) -> bool:
    """True if text appears to be director, writer, or staff credits rather than a pure title."""
    if not text:
        return False
    norm = normalize_for_matching(text)
    return any(phrase in norm for phrase in CREDIT_PHRASES)
