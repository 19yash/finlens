"""
Phase 3: reconcile GLM-OCR SDK output with the PDF's own text.

For every page:
  1. Check the SDK tables: do their numbers also appear in the PDF's own text
     (agreement), and do "Total ..." rows add up / have values (totals check)?
  2. Pages that fail are re-OCR'd as WHOLE pages on Modal with two prompts
     ("Table Recognition:" and "Text Recognition:"). Results are cached.
  3. The best-scoring version of each page is kept. Pages where every version
     still fails go to review.csv for a human look.
  4. Clean-up: decorative images dropped, image links removed, "(₹ crore)"-style
     units attached to the tables on their page.

Usage (venv active, in the rag folder):
  python reconcile.py --pdf annual-report-2024-2025.pdf \
      --sdk sdk_out/annual-report-2024-2025/annual-report-2024-2025.json
  # only check, no Modal calls:
  python reconcile.py --pdf ... --sdk ... --no-reocr

Outputs (in --out/<pdf name>/):
  pages.json   one record per page: chosen source, blocks, checks  (input for chunking)
  <name>.md    cleaned document, with <!-- page N --> markers
  review.csv   pages that still fail every check
  wholepage/   cached whole-page OCR results (never paid for twice)
"""
import argparse
import collections
import csv
import html
import json
import re
from pathlib import Path

import pymupdf

AGREE_MIN = 0.98          # share of OCR table numbers that must also be in the PDF text
MIN_NUMBERS = 15          # pages with fewer table numbers are not agreement-checked
PROMPTS = {"wp_table": "Table Recognition:", "wp_text": "Text Recognition:"}

# Font-specific broken digit glyphs found in TCS 2024-25 (harmless for other PDFs)
FIX = str.maketrans({"Ϭ": "0", "ϰ": "4", "ϱ": "5", "ϲ": "6", "ϳ": "7", "ϴ": "8", "ϵ": "9"})
NUM_RE = r"\d{1,3}(?:,\d{2,3})+|\d{3,}"
UNITS_RE = re.compile(
    r"^\(?\s*(?:₹|`|Rs\.?|INR|US\$|\$|€|£)?\s*(?:in\s+)?"
    r"(?:crore|crores|lakh|lakhs|million|millions|billion|billions|mn|bn)\s*\)?$", re.I)
IMG_LINK_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)\s*")


# ---------------------------------------------------------------- PDF's own text
def page_lines(page, ytol=2.5):
    """Rebuild text lines from character positions (fixes fragments and spacing)."""
    chars, seen = [], set()
    for b in page.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                for c in s["chars"]:
                    k = (c["c"], round(c["bbox"][0]), round(c["bbox"][1]))
                    if c["c"].strip() and k not in seen:       # skip spaces + overprints
                        seen.add(k)
                        chars.append((c["bbox"], c["c"], s["size"]))
    chars.sort(key=lambda x: ((x[0][1] + x[0][3]) / 2, x[0][0]))
    lines = []
    for c in chars:
        yc = (c[0][1] + c[0][3]) / 2
        if lines and abs(lines[-1][0] - yc) < ytol:
            lines[-1][1].append(c)
        else:
            lines.append([yc, [c]])
    out = []
    for _, cs in lines:
        cs.sort(key=lambda x: x[0][0])
        s, prev = "", None
        for bb, ch, sz in cs:
            if prev is not None:
                gap = bb[0] - prev[2]
                s += "   " if gap > sz * 0.9 else (" " if gap > sz * 0.22 else "")
            s += ch
            prev = bb
        out.append(s)
    return out


def pdf_numbers(page):
    """Numbers in the PDF's own text, via two methods (each fails on different pages)."""
    g = "\n".join(page_lines(page)).translate(FIX)
    for _ in range(2):
        g = re.sub(r"(?<=[\d,]) (?=[\d,])", "", g)
    raw = page.get_text().translate(FIX)
    p = re.sub(r"(?<=\d), (?=\d{2,3}\b)", ",", raw)        # '8, 280' -> '8,280'
    q = re.sub(r"(?<=[\d,]) (?=[\d,])", "", raw)            # '2, 1 3 1' -> '2,131'
    count = lambda t: collections.Counter(x.replace(",", "") for x in re.findall(NUM_RE, t))
    # each method is right on different pages; a number counts if any method finds it
    return count(g) | count(p) | count(q)


# ---------------------------------------------------------------- OCR text helpers
def ocr_numbers(text):
    s = html.unescape(re.sub(r"<.*?>", " ", text))
    s = re.sub(r"(\d),(20\d\d)\b", r"\1, \2", s)       # 'March 31,2025' is a date
    return collections.Counter(x.replace(",", "") for x in re.findall(NUM_RE, s))


def to_value(cell):
    """'1,59,629' -> 159629.0, '(46)' -> -46.0, '-' / '' -> 0.0, text -> None."""
    s = cell.replace(",", "").replace("₹", "").strip()
    s = re.sub(r"(?<=[\d)\-–—])[*#^†]+$", "", s)       # footnote marks: '-*', '1,234*'
    if s in ("-", "–", "—", ""):
        return 0.0
    m = re.fullmatch(r"\((\d+(?:\.\d+)?)\)", s)
    if m:
        return -float(m.group(1))
    return float(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else None


def rows_from_html(text):
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        cells = [html.unescape(re.sub(r"<.*?>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if cells:
            out.append(cells)
    return out


def rows_from_lines(text):
    """Whole-page text format: a label line, then a line of note + numbers."""
    out, label = [], None
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        if line.startswith("|"):                       # Markdown table row
            if label is not None:
                out.append([label])
                label = None
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):  # skip |---|
                out.append(cells)
            continue
        toks = line.split()
        if all(to_value(t) is not None or re.fullmatch(r"\d+\([a-z]\)", t) for t in toks):
            out.append([label or ""] + toks)
            label = None
        else:
            if label is not None:
                out.append([label])
            label = line
    if label is not None:
        out.append([label])
    return out


def table_rows(text):
    return rows_from_html(text) if "<tr>" in text else rows_from_lines(text)


def run_matching(section, pair):
    """Length k of the run of rows directly above that explains `pair`, per year column:
    either their sum, or the first minus the rest ('Opening ... Less: Closing',
    'balance ... Less: Appropriations'). None if no run explains both columns."""
    for k in range(1, len(section) + 1):
        run = section[-k:]
        ok = True
        for col in (0, 1):
            vals = [x[col] for x in run]
            total, first_minus_rest = sum(vals), vals[0] - sum(vals[1:])
            if not (abs(total - pair[col]) < 0.5 or abs(first_minus_rest - pair[col]) < 0.5):
                ok = False
                break
        if ok:
            return k
    return None


def totals_check(rows):
    """Check 'Total ...' / 'TOTAL ...' rows in the last two (year) columns.

    A 'Total' row passes if it equals the sum of some run of rows directly above it
    (handles nested subtotals and P&L lines). 'TOTAL ...' rows must equal the sum
    of the 'Total' rows since the previous 'TOTAL'. Returns a list of problems.
    """
    problems, section, section_totals = [], [], []
    for r in rows:
        label = r[0].strip()
        vals = [to_value(c) for c in r[1:]]
        pair = vals[-2:] if len(vals) >= 2 and None not in vals[-2:] else None
        has_values = len(r) >= 3 and any(to_value(c) not in (None, 0.0) for c in r[-2:])
        if label.endswith(":"):              # 'Total ... attributable to:' is a heading
            if has_values:                   # ...so numbers on it mean rows slid up
                problems.append(f"values on heading row: {label[:50]}")
            continue
        is_grand = label.startswith("TOTAL ")
        # an unlabelled row with numbers is a subtotal in notes tables (fine if it
        # adds up) or a value that slid off its label (caught when it doesn't add up)
        is_total = (label.lower().startswith("total ") and not is_grand) or \
                   (not label and has_values)
        if is_total or is_grand:
            if pair is None or pair == [0.0, 0.0]:
                problems.append(f"empty total: {label[:50]}")
                section = []
            elif is_grand:
                if section_totals and [sum(x[0] for x in section_totals),
                                       sum(x[1] for x in section_totals)] != pair:
                    problems.append(f"grand total mismatch: {label[:50]}")
                section_totals, section = [], []
            else:
                k = run_matching(section, pair)
                # unlabelled rows may total rows on an earlier page, so only a
                # labelled 'Total' that doesn't add up counts as a problem
                if section and k is None and label:
                    problems.append(f"total mismatch: {label[:50]}")
                section_totals.append(pair)
                # the subtotal replaces the rows it sums, so a later total can add it up
                section = (section[:-k] if k else []) + [pair]
            continue
        elif pair is not None and len(r) >= 3:
            section.append(pair)
    return problems


STATEMENT_HINT = re.compile(r"as at|year ended|march 31|crore", re.I)


def is_statement(rows, page_text):
    """Financial-statement-style table: label + (note) + two period columns.

    Only these get the totals check. ESG/BRSR tables ('Total employees',
    'Total Scope 1 emissions') and multi-year tables use 'Total' for things
    that are not sums of the rows above.
    """
    numeric = [r for r in rows if len(r) >= 3 and to_value(r[-1]) is not None
               and to_value(r[-2]) is not None]
    if len(numeric) < 6:
        return False
    narrow = sum(len(r) <= 4 for r in numeric) / len(numeric) >= 0.8
    # 2nd column of a statement is a note ref ('8(a)', '14') or blank, not a unit ('tCO2e')
    four = [r for r in numeric if len(r) == 4]
    notes = (not four) or sum(bool(re.fullmatch(r"(\d+(\([a-z]+\))?)?", r[1].strip()))
                              for r in four) / len(four) >= 0.8
    return narrow and notes and bool(STATEMENT_HINT.search(page_text))


def score(text, pdf_nums, table_only_text=None, page_text=""):
    """Lower is better: (number of total problems, -agreement)."""
    nums_src = table_only_text if table_only_text is not None else text
    o = ocr_numbers(nums_src)
    n = sum(o.values())
    agree = (sum((o & pdf_nums).values()) / n) if n >= MIN_NUMBERS else 1.0
    if not text.strip():
        problems = ["empty"]
    else:
        # judge each HTML table on its own (a page can hold a statement and an ESG
        # table); line/Markdown text from whole-page OCR is one block
        parts = (re.findall(r"<table.*?</table>", text, re.S)                 # HTML tables
                 or re.findall(r"(?:^\|.*\n?){2,}", text, re.M)            # Markdown tables
                 or [text])                                                # plain lines
        problems = []
        for part in parts:
            rows = table_rows(part)
            if is_statement(rows, part):           # hint must be in the table itself
                problems += totals_check(rows)
    return {"agreement": round(agree, 3), "numbers": n, "problems": problems,
            "passed": not problems and agree >= AGREE_MIN,
            "key": (len(problems), -agree)}


# ---------------------------------------------------------------- SDK page cleanup
def sdk_blocks(regions):
    """SDK regions -> clean blocks; units text attached to the page's tables."""
    blocks, units = [], None
    for r in regions:
        content = IMG_LINK_RE.sub("", r.get("content") or "").strip()
        if r["label"] == "image" or not content:
            continue                                   # decorative images, empty
        if r["label"] != "table" and UNITS_RE.match(content):
            units = content.strip("() ")
            continue
        kind = "table" if r["label"] == "table" else r.get("native_label") or r["label"]
        blocks.append({"type": kind, "content": content, "bbox": r.get("bbox_2d")})
    if units:
        for b in blocks:
            if b["type"] == "table":
                b["units"] = units
    return blocks


def blocks_to_md(blocks):
    parts = []
    for b in blocks:
        if b["type"] == "table" and b.get("units"):
            parts.append(f"_Units: {b['units']}_")
        parts.append(b["content"])
    return "\n\n".join(parts)


def choose(checks):
    """Pick the version to keep. The SDK version stays unless a whole-page version
    (a) passes every check, or (b) has fewer total problems without losing
    agreement. Either way it must contain at least 80% as many numbers, so an
    empty, cut-off or wrong page can never win just by having no 'Total' rows."""
    sdk = checks["sdk"]
    alts = {k: c for k, c in checks.items() if k != "sdk"
            and c["numbers"] >= 0.8 * sdk["numbers"]}
    passing = [k for k, c in alts.items() if c["passed"]]
    if passing and not sdk["passed"]:
        return max(passing, key=lambda k: alts[k]["agreement"])
    better = [k for k, c in alts.items()
              if len(c["problems"]) < len(sdk["problems"])
              and c["agreement"] >= sdk["agreement"] - 0.02]
    if better:
        return min(better, key=lambda k: (len(alts[k]["problems"]), -alts[k]["agreement"]))
    # nothing passes: take a version with no more problems and clearly higher agreement
    clearer = [k for k, c in alts.items()
               if len(c["problems"]) <= len(sdk["problems"])
               and c["agreement"] >= sdk["agreement"] + 0.02]
    if clearer:
        return max(clearer, key=lambda k: alts[k]["agreement"])
    return "sdk"


# ---------------------------------------------------------------- whole-page re-OCR
def reocr(pdf_path, pages, cache_dir):
    """Whole-page OCR of `pages` with both prompts on Modal; cached on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in pages
            if not all((cache_dir / f"p{p:03d}_{k}.md").exists() for k in PROMPTS)]
    if todo:
        import modal
        from modal_glm_ocr_sdk import Parser, app

        src, small = pymupdf.open(pdf_path), pymupdf.open()
        for p in todo:                                  # upload only the needed pages
            small.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        print(f"Re-OCR of {len(todo)} pages on Modal (whole page, 2 prompts each) ...")
        with modal.enable_output(), app.run():
            results = Parser().ocr_pages.remote(small.tobytes(), list(PROMPTS.values()))
        names = {v: k for k, v in PROMPTS.items()}
        for r in results:
            page = todo[r["index"]]
            text = r["content"] + ("\n<!-- truncated -->" if r["truncated"] else "")
            (cache_dir / f"p{page:03d}_{names[r['prompt']]}.md").write_text(text)
    return {p: {k: (cache_dir / f"p{p:03d}_{k}.md").read_text() for k in PROMPTS}
            for p in pages}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--sdk", required=True, help="SDK JSON, e.g. sdk_out/X/X.json")
    ap.add_argument("--out", default="reconciled")
    ap.add_argument("--no-reocr", action="store_true", help="check only, no Modal calls")
    args = ap.parse_args()

    pdf = pymupdf.open(args.pdf)
    sdk = json.load(open(args.sdk, encoding="utf-8"))
    assert len(sdk) == len(pdf), f"SDK has {len(sdk)} pages, PDF has {len(pdf)}"
    name = Path(args.pdf).stem
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)

    # 1. check the SDK version of every page
    records, flagged = [], []
    for i, regions in enumerate(sdk):
        blocks = sdk_blocks(regions)
        md = blocks_to_md(blocks)
        tables = "\n".join(b["content"] for b in blocks if b["type"] == "table")
        nums = pdf_numbers(pdf[i])
        chk = score(tables, nums, tables, page_text=md) if tables else {
            "agreement": 1.0, "numbers": 0, "problems": [], "passed": True, "key": (0, -1.0)}
        records.append({"page": i + 1, "source": "sdk", "blocks": blocks, "md": md,
                        "checks": {"sdk": chk}, "pdf_nums": nums})
        if not chk["passed"]:
            flagged.append(i + 1)
    print(f"{len(pdf)} pages, {len(flagged)} flagged by checks: {flagged}")

    # 2. re-OCR flagged pages as whole pages, keep the best version
    if flagged and not args.no_reocr:
        alt = reocr(args.pdf, flagged, out / "wholepage")
        for p in flagged:
            rec = records[p - 1]
            for k, text in alt[p].items():
                truncated = "<!-- truncated -->" in text
                text = IMG_LINK_RE.sub("", text.replace("<!-- truncated -->", "")).strip()
                rec["checks"][k] = score(text, rec["pdf_nums"], page_text=text)
                if truncated:
                    rec["checks"][k]["problems"].append("output truncated")
                    rec["checks"][k]["passed"] = False
                rec.setdefault("alt", {})[k] = text
            best = choose(rec["checks"])
            if best != "sdk":
                rec["source"] = best
                rec["blocks"] = [{"type": "page", "content": rec["alt"][best]}]
                rec["md"] = rec["alt"][best]

    # 3. write outputs
    review = []
    for rec in records:
        chosen = rec["checks"][rec["source"]]
        if not chosen["passed"]:
            review.append(rec)
    with open(out / "pages.json", "w", encoding="utf-8") as f:
        json.dump([{"page": r["page"], "source": r["source"], "blocks": r["blocks"],
                    "checks": {k: {kk: vv for kk, vv in v.items() if kk != "key"}
                               for k, v in r["checks"].items()},
                    "needs_review": r in review} for r in records],
                  f, ensure_ascii=False, indent=1)
    (out / f"{name}.md").write_text(
        "\n\n".join(f"<!-- page {r['page']} -->\n\n{r['md']}" for r in records),
        encoding="utf-8")
    with open(out / "review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["page", "chosen", "agreement", "problems", "other_versions"])
        for r in review:
            c = r["checks"][r["source"]]
            others = "; ".join(f"{k}: {v['agreement']} / {len(v['problems'])} problems"
                               for k, v in r["checks"].items() if k != r["source"])
            w.writerow([r["page"], r["source"], c["agreement"], " | ".join(c["problems"]),
                        others])

    src_count = collections.Counter(r["source"] for r in records)
    print(f"Sources used: {dict(src_count)}")
    print(f"Still failing (see review.csv): {[r['page'] for r in review]}")
    print(f"Written to {out}/: pages.json, {name}.md, review.csv")


if __name__ == "__main__":
    main()
