import io
import zipfile
from pathlib import Path

import pymupdf
import pytest

from finlens.ingestion.verify import (
    VerificationError,
    pdf_from_zip,
    squash,
    verify_report_pdf,
)
from tests.conftest import FILLER, make_pdf

TCS = {"company_name": "Tata Consultancy Services Limited", "isin": "INE467B01029"}
RAG = Path(__file__).resolve().parents[2] / "rag"  # local research copies, not in git


def write(tmp_path, lines, pages=3, name="r.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(make_pdf(lines + FILLER, pages))
    return path


def test_squash_undoes_letter_spacing_and_glyph_digits():
    assert squash("T ata C onsultancy Serv ices") == "tataconsultancyservices"
    assert squash("2 0 2 ϰ- 2 ϱ") == "2024-25"


def test_accepts_matching_report(tmp_path):
    path = write(tmp_path, ["Tata Consultancy Services Limited", "Integrated Annual Report 2024-25"])
    check = verify_report_pdf(path, fiscal_year=2025, **TCS)
    assert check.page_count == 3 and not check.needs_review


def test_rejects_other_company(tmp_path):
    path = write(tmp_path, ["Infosys Limited", "Annual Report 2024-25"])
    with pytest.raises(VerificationError, match="found in the PDF"):
        verify_report_pdf(path, fiscal_year=2025, **TCS)


def test_isin_anywhere_identifies_company(tmp_path):
    path = write(tmp_path, ["TCS Annual Report 2024-25"])
    doc = pymupdf.open(path)
    for _ in range(15):
        doc.new_page()
    doc[-1].insert_text((72, 72), "ISIN: INE467B01029")
    doc.saveIncr()
    doc.close()
    assert verify_report_pdf(path, fiscal_year=2025, **TCS).page_count == 18


@pytest.mark.parametrize("label", ["2024-25", "2024-2025", "FY 2025", "FY25"])
def test_year_label_formats(tmp_path, label):
    path = write(tmp_path, ["Tata Consultancy Services", f"Annual Report {label}"])
    verify_report_pdf(path, fiscal_year=2025, **TCS)


def test_rejects_wrong_year(tmp_path):
    path = write(tmp_path, ["Tata Consultancy Services", "Annual Report 2025-26",
                            "compared with FY 2025"])
    with pytest.raises(VerificationError, match="FY2026"):
        verify_report_pdf(path, fiscal_year=2025, **TCS)


def test_tie_with_prior_year_is_accepted(tmp_path):
    """Real case: HDFC Bank FY25 cites 2024 and 2025 equally often."""
    path = write(tmp_path, ["Tata Consultancy Services", "Annual Report 2024-25",
                            "compared with FY 2024"])
    verify_report_pdf(path, fiscal_year=2025, **TCS)


def test_scanned_pdf_accepted_but_flagged(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_pdf([], pages=2))
    check = verify_report_pdf(path, fiscal_year=2025, **TCS)
    assert check.needs_review


def test_not_a_pdf(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_text("<html>Access Denied</html>")
    with pytest.raises(VerificationError, match="not a readable PDF"):
        verify_report_pdf(path, fiscal_year=2025, **TCS)


def test_zip_parts_merged_in_name_order(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AR/part2.pdf", make_pdf(["second"], pages=2))
        zf.writestr("AR/part1.pdf", make_pdf(["first"], pages=1))
        zf.writestr("../../evil.txt", "ignored")
    (tmp_path / "f.zip").write_bytes(buf.getvalue())
    assert pdf_from_zip(tmp_path / "f.zip", tmp_path / "out.pdf") == 2
    doc = pymupdf.open(tmp_path / "out.pdf")
    assert doc.page_count == 3 and "first" in doc[0].get_text()
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_zip_without_pdf(tmp_path):
    with zipfile.ZipFile(tmp_path / "f.zip", "w") as zf:
        zf.writestr("readme.txt", "no report here")
    with pytest.raises(VerificationError, match="no PDF"):
        pdf_from_zip(tmp_path / "f.zip", tmp_path / "out.pdf")


@pytest.mark.skipif(not (RAG / "annual-report-2024-2025.pdf").exists(),
                    reason="real TCS reports not present")
@pytest.mark.parametrize("file, year, ok", [
    ("annual-report-2024-2025.pdf", 2025, True),
    ("annual-report-2025-2026.pdf", 2026, True),
    ("annual-report-2024-2025.pdf", 2026, False),
    ("annual-report-2025-2026.pdf", 2025, False),
])
def test_real_tcs_reports(file, year, ok):
    """Real text layer: letter-spaced and with broken digit glyphs."""
    if ok:
        verify_report_pdf(RAG / file, fiscal_year=year, **TCS)
    else:
        with pytest.raises(VerificationError):
            verify_report_pdf(RAG / file, fiscal_year=year, **TCS)
