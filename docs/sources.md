# Report sources (checked 20 Nov 2026)

## Decision

**NSE only for v1.** The company list and every annual report come from NSE.
BSE's API refuses automated requests, and the project never bypasses a block.

Fetch order for an annual report:

| Order | Source | Covers |
|---|---|---|
| 1 | NSE annual-report filings | Every NSE-listed company (SEBI LODR Reg. 34 makes filing mandatory) |
| 2 | Web search, official domain only | Fallback if NSE is down or a filing is missing |
| 3 | Manual upload | Last resort |

## Findings

| Host | robots.txt | Automated access |
|---|---|---|
| `www.nseindia.com` (redirects to `.in`) | `Allow: /`, only `/market-data-test` disallowed | Allowed |
| `nsearchives.nseindia.com` (files) | none | Works (company list and PDFs) |
| `www.bseindia.com` | none (URL returns the home page) | Site loads |
| `api.bseindia.com` | 403 Access Denied | **Blocked**: not used |

NSE silently drops requests (timeout, no error) whose user agent doesn't look like a
browser, e.g. `FinLensBot/0.1`. A `Mozilla/5.0 ... FinLens/0.1` agent works.

## Company list

`https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv`: 2,632 companies
(2,405 EQ, 193 BE, 34 BZ series). Columns: `SYMBOL, NAME OF COMPANY, SERIES,
DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE`.
The ISIN is the primary key of `companies`. No website column, so
`official_domain` is filled separately (by hand for pre-loaded companies).

## Annual reports

List of filings for one company (JSON, no session needed):

```
GET https://www.nseindia.com/api/annual-reports?index=equities&symbol=TCS
→ {"data": [{"companyName", "fromYr": "2024", "toYr": "2025",
             "fileName": "https://nsearchives.nseindia.in/annual_reports/AR_..._TCS_2024_2025_....pdf",
             "attFileSize": "16.62 MB", "broadcast_dttm", "submission_type"}, ...]}
```

- `toYr` is the fiscal year in FinLens terms (FY25 → `toYr` 2025).
- Recent years are `.pdf`; FY24 and earlier are often `.zip` (PDF inside).
- Files are downloadable directly; the `.in` file host timed out from here, so
  rewrite `nsearchives.nseindia.in` → `nsearchives.nseindia.com`.
- Checked: TCS (10 years, FY17–FY26) and the small-cap 20 Microns (10 years).

## Still open

- XBRL: NSE's "Annual Reports XBRL" and "Integrated Filing XBRL" pages could give
  structured figures without table parsing. Not checked yet.
- Rate limit to use (start at 1 request every 2 s).
