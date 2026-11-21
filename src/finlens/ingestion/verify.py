"""Check a downloaded PDF is the report we asked for, before anything is spent on it.

Fetching the wrong company's (or year's) report gives a confident wrong answer, so
the fetcher refuses a file whose own pages name another company or year.

These functions are sync and CPU-bound (PyMuPDF): call them with asyncio.to_thread.
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

PAGES_TO_SCAN = 12  # cover, contents and the first section carry the name and year
MIN_TEXT_CHARS = 200  # less than this over those pages: no usable text layer (scanned)
MAX_ZIP_BYTES = 400 * 1024 * 1024  # uncompressed; anything bigger isn't an annual report

# Font-specific broken digit glyphs seen in TCS 2024-25 (same map as reconcile.py)
_GLYPH_DIGITS = str.maketrans({"Ϭ": "0", "ϰ": "4", "ϱ": "5", "ϲ": "6", "ϳ": "7", "ϴ": "8",
                               "ϵ": "9"})
_NAME_SUFFIXES = re.compile(r"(limited|ltd)$")
# Any fiscal-year label once squashed: 2024-25, 2024–2025, fy2025, fy25
_ANY_FY = re.compile(r"(20\d\d)[-–](\d{2}(?:\d{2})?)(?!\d)|fy(20\d\d|\d{2})(?!\d)")


class VerificationError(Exception):
    pass


@dataclass(frozen=True)
class Verification:
    page_count: int
    has_text: bool  # False: scanned PDF, name and year could not be checked

    @property
    def needs_review(self) -> bool:
        return not self.has_text


def squash(text: str) -> str:
    """Lowercase, fix glyph digits, drop everything but letters, digits and dashes.

    Undoes letter-spacing in the text layer: "T ata C onsultancy" -> "tataconsultancy".
    """
    text = text.translate(_GLYPH_DIGITS).lower()
    return re.sub(r"[^a-z0-9\-–]", "", text)


def _fy_end_years(squashed: str) -> list[int]:
    years = []
    for m in _ANY_FY.finditer(squashed):
        start, end, fy = m.groups()
        if fy:
            years.append(int(fy) if len(fy) == 4 else 2000 + int(fy))
        elif len(end) == 2 and int(end) == (int(start) + 1) % 100:
            years.append(int(start) + 1)
        elif len(end) == 4 and int(end) == int(start) + 1:
            years.append(int(end))
    return years


def verify_report_pdf(path: Path, *, company_name: str, isin: str, fiscal_year: int) -> Verification:
    with path.open("rb") as f:
        if not f.read(1024).lstrip().startswith(b"%PDF"):
            # e.g. an HTML "Access Denied" page saved as .pdf; PyMuPDF would open it
            raise VerificationError("not a readable PDF: no %PDF header")
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as e:
        raise VerificationError(f"not a readable PDF: {e}") from e
    with doc:
        if doc.page_count == 0:
            raise VerificationError("PDF has no pages")
        first = " ".join(doc[i].get_text() for i in range(min(PAGES_TO_SCAN, doc.page_count)))
        title = doc.metadata.get("title") or ""
        if len(first.strip()) < MIN_TEXT_CHARS:
            # Scanned report: OCR will read it, but nothing can be checked here
            return Verification(page_count=doc.page_count, has_text=False)

        text = squash(f"{title} {first}")
        name = _NAME_SUFFIXES.sub("", squash(company_name.replace("&", "")))
        if name not in text.replace("&", "") and isin.lower() not in text:
            # The ISIN is printed in the shareholder section, usually past the first pages
            full = squash(" ".join(page.get_text() for page in doc))
            if isin.lower() not in full:
                raise VerificationError(f"neither {company_name!r} nor {isin} found in the PDF")

        years = _fy_end_years(text)
        if fiscal_year not in years:
            raise VerificationError(f"FY{fiscal_year} not mentioned in the first pages")
        # Reports cite the prior year a lot (HDFC Bank FY25: 2024 and 2025 seven times
        # each), so a tie with an earlier year is fine. A year cited more often, or a
        # later year cited as often, means this is another year's report.
        own = years.count(fiscal_year)
        for year in set(years) - {fiscal_year}:
            if years.count(year) > own or (year > fiscal_year and years.count(year) == own):
                raise VerificationError(f"looks like the FY{year} report, not FY{fiscal_year}")
        return Verification(page_count=doc.page_count, has_text=True)


def pdf_from_zip(zip_path: Path, dest: Path) -> int:
    """Write the PDF(s) inside a ZIP filing to `dest`. Returns the number of PDFs merged.

    Older NSE filings are ZIPs; some hold the report in several parts, which are
    merged in file-name order. Member names are never used as paths.
    """
    with zipfile.ZipFile(zip_path) as zf:
        members = sorted(
            (m for m in zf.infolist() if m.filename.lower().endswith(".pdf") and not m.is_dir()),
            key=lambda m: m.filename,
        )
        if not members:
            raise VerificationError("ZIP contains no PDF")
        if sum(m.file_size for m in members) > MAX_ZIP_BYTES:
            raise VerificationError("ZIP contents too large")
        merged = pymupdf.open()
        for m in members:
            with pymupdf.open(stream=zf.read(m), filetype="pdf") as part:
                merged.insert_pdf(part)
        merged.save(dest, garbage=3, deflate=True)
        merged.close()
    return len(members)
