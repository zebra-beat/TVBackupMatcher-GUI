import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .metadata import EpisodeInfo
from .ocr import normalize_for_matching

STOPWORDS = {"THE", "AND", "FOR", "OF", "A", "AN", "TO", "IN", "ON", "AT", "IS"}
MIN_OCR_WORD_LEN = 3        # OCR words shorter than this are treated as noise for fuzzy matching
FUZZY_MIN_TOKEN_LEN = 4     # only title words this long may match approximately
FUZZY_MIN_RATIO = 0.8       # SequenceMatcher ratio needed for an approximate word match
FUZZY_CREDIT = 0.9          # an approximate match is worth ratio * this
PHRASE_SCORE = 0.95         # whole title found as a phrase
_PART_RE = re.compile(r"\bPART (\d)\b")

Candidate = Tuple[EpisodeInfo, float, float, str, Optional[str]]  # (episode, score, time, ocr text, image)


@lru_cache(maxsize=4096)
def _title_tokens(norm_title: str) -> Tuple[str, ...]:
    words = norm_title.split()
    significant = [w for w in words if len(w) > 2 and w not in STOPWORDS]
    # Titles like "I Am the Night" leave one significant word; matching on that alone is
    # too permissive, so fall back to all words of 2+ letters.
    if len(significant) < 2 and len(words) > len(significant):
        significant = [w for w in words if len(w) >= 2] or words
    return tuple(significant)


@lru_cache(maxsize=4096)
def _prepare_text(text: str):
    norm = normalize_for_matching(text)
    words = norm.split()
    fuzzy_words = tuple(sorted({w for w in words if len(w) >= MIN_OCR_WORD_LEN}))
    return norm, f" {norm} ", frozenset(words), fuzzy_words, frozenset(_PART_RE.findall(norm))


@lru_cache(maxsize=65536)
def _best_fuzzy_ratio(token: str, fuzzy_words: Tuple[str, ...]) -> float:
    best = 0.0
    max_len_diff = max(2, len(token) // 3)
    for w in fuzzy_words:
        if abs(len(w) - len(token)) > max_len_diff:
            continue
        sm = SequenceMatcher(None, token, w)
        if sm.real_quick_ratio() < FUZZY_MIN_RATIO or sm.quick_ratio() < FUZZY_MIN_RATIO:
            continue
        best = max(best, sm.ratio())
    return best


def token_similarity(title: str, text: str) -> float:
    norm_title = normalize_for_matching(title)
    if not norm_title or not text:
        return 0.0
    norm_text, padded_text, text_words, fuzzy_words, text_parts = _prepare_text(text)
    if not norm_text:
        return 0.0

    if f" {norm_title} " in padded_text:          # whole-word phrase match
        score = PHRASE_SCORE
    else:
        title_tokens = _title_tokens(norm_title)
        matched = 0.0
        for tok in title_tokens:
            if tok in text_words:                   # exact whole-word match
                matched += 1.0
            elif len(tok) >= FUZZY_MIN_TOKEN_LEN:   # tolerate OCR typos (KRYPT0N, BATMAM)
                ratio = _best_fuzzy_ratio(tok, fuzzy_words)
                if ratio >= FUZZY_MIN_RATIO:
                    matched += ratio * FUZZY_CREDIT
        score = matched / max(len(title_tokens), 1)

    title_parts = set(_PART_RE.findall(norm_title))
    if title_parts:
        if title_parts & text_parts:
            score += 0.15
        elif text_parts:  # the card names a different part
            score = min(score - 0.6, 0.25)

    return max(0.0, min(1.0, score))


def rank_episodes_for_file(frame_samples: List[Tuple[float, str, str]],
                           episodes: List[EpisodeInfo], min_score: float = 0.0) -> List[Candidate]:
    \"\"\"Best-scoring frame for every episode, filtered by min_score, sorted best first.\"\"\"
    best: Dict[str, Candidate] = {}
    for t, txt, img_path in frame_samples:
        if not txt:
            continue
        for ep in episodes:
            score = token_similarity(ep.title, txt)
            if score >= min_score and score > 0 and (ep.code not in best or score > best[ep.code][1]):
                best[ep.code] = (ep, score, t, txt, img_path)
    return sorted(best.values(), key=lambda c: (-c[1], c[0].code))


def find_best_episode_for_file(
    frame_samples: List[Tuple[float, str, str]],  # (timestamp, ocr_text, image_path)
    available_episodes: List[EpisodeInfo]
) -> Tuple[Optional[EpisodeInfo], float, float, str, Optional[str]]:
    ranked = rank_episodes_for_file(frame_samples, available_episodes)
    if not ranked:
        return None, 0.0, 0.0, "", None
    return ranked[0]


def is_confident_unique(candidates: List[Candidate], threshold: float = PHRASE_SCORE, margin: float = 0.3) -> bool:
    \"\"\"True when the top candidate is a near-certain match clearly ahead of the runner-up.\"\"\"
    if not candidates or candidates[0][1] < threshold:
        return False
    return len(candidates) == 1 or candidates[0][1] - candidates[1][1] >= margin


def resolve_global_assignments(
    file_candidates: Dict[str, List[Tuple[EpisodeInfo, float, float, str, str]]]
) -> Dict[str, Tuple[EpisodeInfo, float, float, str, str]]:
    \"\"\"Greedy one-to-one assignment: highest-scoring (file, episode) pairs win first.\"\"\"
    final_mapping = {}
    assigned_episodes = set()

    all_pairs = []
    for fp, candidates in file_candidates.items():
        for ep, score, t, txt, img in candidates:
            all_pairs.append((score, fp, ep, t, txt, img))

    all_pairs.sort(key=lambda x: (-x[0], x[1], x[2].code))

    for score, fp, ep, t, txt, img in all_pairs:
        if fp not in final_mapping and ep.code not in assigned_episodes:
            final_mapping[fp] = (ep, score, t, txt, img)
            assigned_episodes.add(ep.code)

    return final_mapping
