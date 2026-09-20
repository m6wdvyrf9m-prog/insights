from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


COLOURS = ("Blue", "Green", "Yellow", "Red")
PDF_TITLE_MARKERS = {
    "colour": ("insights discovery", "colour dynamics"),
    "wheel": ("insights discovery", "72 type wheel"),
    "strengths": ("key strengths", "strengths"),
    "weaknesses": ("key strengths", "possible weaknesses"),
    "value": ("value to the team",),
    "effective": ("communication", "effective communications"),
    "barriers": ("communication", "barriers to effective communication"),
    "blind_spots": ("possible blind spots",),
    "opposite": ("opposite type",),
    "development": ("suggestions for development",),
}


@dataclass
class ExtractedPage:
    number: int
    text: str


def parse_insights_pdf(path: str | Path) -> dict:
    pages = extract_pages(path)
    issues: list[str] = []
    confidence: dict[str, float] = {}

    full_name = extract_name(pages)
    confidence["name"] = 0.95 if full_name else 0.0
    if not full_name:
        issues.append("Participant name could not be confidently extracted from the first pages.")

    colour_page = find_page(pages, "colour")
    colour_dynamics = extract_colour_dynamics(colour_page.text if colour_page else "", issues, confidence)
    if colour_page:
        confidence["colour_page"] = 0.95
    else:
        confidence["colour_page"] = 0.0
        issues.append("The Colour Dynamics page could not be found.")

    wheel_page = find_page(pages, "wheel")
    wheel = extract_wheel_position(wheel_page.text if wheel_page else "", issues, confidence)
    if wheel_page:
        confidence["wheel_page"] = 0.95
    else:
        confidence["wheel_page"] = 0.0
        issues.append("The 72 Type Wheel page could not be found.")

    strengths = extract_bullets_for_page(pages, "strengths", ["key strengths:"])
    development_areas = extract_bullets_for_page(pages, "weaknesses", ["possible weaknesses:"])
    value_to_team = extract_bullets_for_page(pages, "value", ["as a team member"])
    effective = extract_bullets_for_page(pages, "effective", ["strategies for communicating"])
    barriers = extract_bullets_for_page(pages, "barriers", ["do not:"])
    suggestions = extract_bullets_for_page(pages, "development", ["may benefit from:"])

    for key, values in {
        "strengths": strengths,
        "development_areas": development_areas,
        "value_to_team": value_to_team,
        "effective_communications": effective,
        "communication_barriers": barriers,
        "suggestions_for_development": suggestions,
    }.items():
        confidence[key] = min(1.0, len(values) / 5) if values else 0.0
        if len(values) < 5:
            issues.append(f"Only {len(values)} items extracted for {key.replace('_', ' ')}.")

    blind_page = find_page(pages, "blind_spots")
    opposite_page = find_page(pages, "opposite", predicate=lambda text: "communication with" not in normalise(text))
    blind_excerpt = extract_body_after_marker(blind_page.text if blind_page else "", ["possible blind spots:"])
    opposite_excerpt = extract_body_after_marker(opposite_page.text if opposite_page else "", ["recognising your opposite type:"])
    blind_summary = summarize(blind_excerpt)
    opposite_summary = summarize(opposite_excerpt)
    confidence["possible_blind_spots_summary"] = 0.85 if blind_summary else 0.0
    confidence["opposite_type_summary"] = 0.85 if opposite_summary else 0.0

    raw_excerpts = {
        "colour_dynamics_page": colour_page.text if colour_page else "",
        "wheel_page": wheel_page.text if wheel_page else "",
        "strengths_page": get_page_text(pages, "strengths"),
        "possible_weaknesses_page": get_page_text(pages, "weaknesses"),
        "value_to_team_page": get_page_text(pages, "value"),
        "effective_communications_page": get_page_text(pages, "effective"),
        "barriers_page": get_page_text(pages, "barriers"),
        "possible_blind_spots_excerpt": blind_excerpt,
        "opposite_type_excerpt": opposite_excerpt,
        "suggestions_for_development_page": get_page_text(pages, "development"),
    }

    required = [confidence.get("name", 0), confidence.get("colour_scores", 0), confidence.get("wheel_position", 0)]
    confidence["overall"] = round(sum(confidence.values()) / max(1, len(confidence)), 3)
    if any(score <= 0 for score in required):
        issues.append("One or more required fields are missing; review the source PDF before sharing.")

    return {
        "full_name": full_name or "Unknown participant",
        "colour_dynamics": colour_dynamics,
        "wheel": wheel,
        "sections": {
            "strengths": strengths[:5],
            "development_areas": development_areas[:5],
            "value_to_team": value_to_team[:5],
            "effective_communications": effective[:5],
            "communication_barriers": barriers[:5],
            "suggestions_for_development": suggestions[:5],
            "possible_blind_spots_summary": blind_summary,
            "opposite_type_summary": opposite_summary,
        },
        "raw_excerpts": raw_excerpts,
        "confidence": confidence,
        "issues": dedupe(issues),
    }


def extract_pages(path: str | Path) -> list[ExtractedPage]:
    path = Path(path)
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return [
                ExtractedPage(i + 1, page.extract_text(x_tolerance=1, y_tolerance=3) or "")
                for i, page in enumerate(pdf.pages)
            ]
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [ExtractedPage(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]


def normalise(text: str) -> str:
    text = text.replace("®", "").replace("\u2022", "●")
    return re.sub(r"\s+", " ", text).strip().lower()


def clean_line(line: str) -> str:
    line = line.replace("\u2022", "●").replace("\uf0b7", "●")
    return re.sub(r"\s+", " ", line).strip()


def find_page(
    pages: list[ExtractedPage],
    marker_key: str,
    *,
    predicate: Callable[[str], bool] | None = None,
) -> ExtractedPage | None:
    markers = PDF_TITLE_MARKERS[marker_key]
    for page in pages:
        first_lines = [clean_line(line).lower() for line in page.text.splitlines()[:5]]
        if "contents" in first_lines:
            continue
        text = normalise(page.text)
        if all(marker in text for marker in markers) and (predicate is None or predicate(page.text)):
            if marker_key == "strengths" and "possible weaknesses" in text:
                continue
            return page
    return None


def get_page_text(pages: list[ExtractedPage], marker_key: str) -> str:
    page = find_page(pages, marker_key)
    return page.text if page else ""


def extract_name(pages: list[ExtractedPage]) -> str | None:
    candidate_lines: list[str] = []
    for page in pages[:3]:
        for raw_line in page.text.splitlines():
            line = clean_line(raw_line)
            if not line:
                continue
            lower = line.lower()
            if lower.startswith(("date ", "date completed", "date printed", "telephone", "referral", "contents")):
                continue
            if "@" in line or "©" in line or "insights discovery" in lower:
                continue
            if line in {"Foundation Chapter", "Personal Details"}:
                continue
            if re.search(r"\d", line):
                continue
            if re.fullmatch(r"[A-Z][A-Za-z' -]+ [A-Z][A-Za-z' -]+", line):
                candidate_lines.append(line)
    if candidate_lines:
        return candidate_lines[0]
    joined = "\n".join(page.text for page in pages[:5])
    match = re.search(r"based on ([A-Z][A-Za-z' -]+?)'s responses", joined)
    return clean_line(match.group(1)) if match else None


def extract_colour_dynamics(text: str, issues: list[str], confidence: dict[str, float]) -> dict:
    result = {
        colour: {"score": None, "percentage": None}
        for colour in COLOURS
    }
    if not text:
        confidence["colour_scores"] = 0.0
        confidence["colour_percentages"] = 0.0
        return result

    decimals = [float(match.group(1)) for match in re.finditer(r"(?<!\d)(\d+\.\d{1,2})(?!\d)", text)]
    scores = decimals[:4]
    if len(scores) < 4:
        issues.append("Could not extract all four conscious colour scores.")
        confidence["colour_scores"] = len(scores) / 4 if scores else 0.0
    else:
        confidence["colour_scores"] = 0.95
        for colour, score in zip(COLOURS, scores):
            result[colour]["score"] = score

    percentages = [float(match.group(1)) for match in re.finditer(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)%", text)]
    selected_percentages = choose_percentage_window(scores, percentages)
    if len(selected_percentages) == 4:
        confidence["colour_percentages"] = 0.95
        for colour, percentage in zip(COLOURS, selected_percentages):
            result[colour]["percentage"] = int(round(percentage))
    elif len(scores) == 4:
        confidence["colour_percentages"] = 0.75
        for colour, score in zip(COLOURS, scores):
            result[colour]["percentage"] = int(round((score / 6.0) * 100))
        issues.append("Colour percentages were derived from scores because the PDF percentage line was ambiguous.")
    else:
        confidence["colour_percentages"] = 0.0
        issues.append("Could not extract conscious colour percentages.")

    return result


def choose_percentage_window(scores: list[float], percentages: list[float]) -> list[float]:
    if len(percentages) < 4:
        return []
    if len(scores) < 4:
        return percentages[:4]
    expected = [(score / 6.0) * 100 for score in scores[:4]]
    best: tuple[float, list[float]] | None = None
    for index in range(0, len(percentages) - 3):
        window = percentages[index : index + 4]
        if any(value > 100 for value in window):
            continue
        error = sum(abs(a - b) for a, b in zip(expected, window))
        if best is None or error < best[0]:
            best = (error, window)
    if best and best[0] <= 18:
        return best[1]
    return percentages[:4]


def extract_wheel_position(text: str, issues: list[str], confidence: dict[str, float]) -> dict:
    if not text:
        confidence["wheel_position"] = 0.0
        return {"conscious_position": None, "conscious_label": "", "conscious_text": ""}
    match = re.search(
        r"Conscious Wheel Position\s+(\d{1,2})\s*:\s*([^\n]+?)(?=\s+Less Conscious|\n|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        issues.append("Conscious Wheel Position was not found.")
        confidence["wheel_position"] = 0.0
        return {"conscious_position": None, "conscious_label": "", "conscious_text": ""}
    position = int(match.group(1))
    label = clean_line(match.group(2))
    confidence["wheel_position"] = 0.95 if 1 <= position <= 72 else 0.6
    return {
        "conscious_position": position,
        "conscious_label": label,
        "conscious_text": f"{position}: {label}",
    }


def extract_bullets_for_page(pages: list[ExtractedPage], marker_key: str, intro_markers: list[str]) -> list[str]:
    page = find_page(pages, marker_key)
    if not page:
        return []
    return extract_bullets(page.text, intro_markers)


def extract_bullets(text: str, intro_markers: list[str]) -> list[str]:
    lines = [clean_line(line) for line in text.splitlines()]
    start = 0
    lower_lines = [line.lower() for line in lines]
    for marker in intro_markers:
        for index, line in enumerate(lower_lines):
            if marker in line:
                start = index + 1
                break
        if start:
            break
    bullets: list[str] = []
    current: list[str] = []
    for line in lines[start:]:
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(("personal notes", "© the insights", "insights discovery and insights learning")):
            break
        match = re.match(r"^[●•*-]\s*(.+)$", line)
        if match:
            if current:
                bullets.append(clean_line(" ".join(current)))
            current = [match.group(1)]
            continue
        if current and not looks_like_heading(line):
            current.append(line)
    if current:
        bullets.append(clean_line(" ".join(current)))
    return [bullet.rstrip(".") + "." for bullet in bullets if bullet]


def looks_like_heading(line: str) -> bool:
    if len(line) > 80:
        return False
    if line.endswith(":"):
        return True
    words = line.split()
    if not words:
        return False
    titleish = sum(1 for word in words if word[:1].isupper())
    return len(words) <= 5 and titleish >= max(1, len(words) - 1)


def extract_body_after_marker(text: str, markers: list[str]) -> str:
    if not text:
        return ""
    lines = [clean_line(line) for line in text.splitlines()]
    start = 0
    lower_lines = [line.lower() for line in lines]
    for marker in markers:
        for index, line in enumerate(lower_lines):
            if marker in line:
                start = index + 1
                break
        if start:
            break
    body: list[str] = []
    for line in lines[start:]:
        lower = line.lower()
        if not line:
            continue
        if lower.startswith(("personal notes", "© the insights", "insights discovery and insights learning")):
            break
        if line in {"Possible Blind Spots", "Opposite Type", "Communication"}:
            continue
        body.append(line)
    return clean_line(" ".join(body))


def summarize(text: str, *, max_sentences: int = 3, max_chars: int = 620) -> str:
    text = clean_line(text)
    if not text:
        return ""
    sentences = split_sentences(text)
    if len(sentences) <= max_sentences:
        return trim_chars(" ".join(sentences), max_chars)

    words = [word.lower() for word in re.findall(r"[A-Za-z]{4,}", text)]
    stop = {
        "that",
        "with",
        "they",
        "their",
        "them",
        "this",
        "from",
        "have",
        "when",
        "will",
        "often",
        "people",
        "stephen",
        "should",
        "would",
        "could",
    }
    freqs: dict[str, int] = {}
    for word in words:
        if word not in stop:
            freqs[word] = freqs.get(word, 0) + 1
    scored: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(sentences):
        sentence_words = re.findall(r"[A-Za-z]{4,}", sentence.lower())
        if not sentence_words:
            continue
        score = sum(freqs.get(word, 0) for word in sentence_words) / math.sqrt(len(sentence_words))
        score += max(0, 3 - index) * 0.35
        scored.append((score, index, sentence))
    chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_sentences]
    chosen = sorted(chosen, key=lambda item: item[1])
    return trim_chars(" ".join(sentence for _, _, sentence in chosen), max_chars)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [clean_line(part) for part in parts if clean_line(part)]


def trim_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    trimmed = text[: max_chars - 1].rsplit(" ", 1)[0]
    return trimmed.rstrip(".,;:") + "."


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output
