"""Four tables: companies, reports, chunks (text for RAG) and facts (numbers for SQL).

Fiscal years are stored as the year they END in: FY25 = April 2024 to March 2025 = 2025.
"""

from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from finlens.config import settings


class Base(DeclarativeBase):
    pass


class Company(Base):
    """One row per NSE-listed company, loaded from NSE's EQUITY_L.csv."""

    __tablename__ = "companies"

    isin: Mapped[str] = mapped_column(String(12), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    series: Mapped[str] = mapped_column(String(4))  # EQ, BE, BZ
    listed_on: Mapped[date | None]
    official_domain: Mapped[str | None] = mapped_column(String(255))  # e.g. tcs.com
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    reports: Mapped[list["Report"]] = relationship(back_populates="company")


class Report(Base):
    """One annual report PDF; `status` drives the ingestion progress shown in the UI."""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("company_isin", "fiscal_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_isin: Mapped[str] = mapped_column(ForeignKey("companies.isin"), index=True)
    fiscal_year: Mapped[int]
    source: Mapped[str] = mapped_column(String(16))  # nse, search, ir_site, upload
    source_url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None]
    # queued -> downloading -> parsing -> embedding -> ready | failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    company: Mapped[Company] = relationship(back_populates="reports")


class Chunk(Base):
    """A page-aware piece of report text, searchable by vector and by full-text."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_company_year", "company_isin", "fiscal_year"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    # Copied from the report so every retrieval can filter without a join
    company_isin: Mapped[str] = mapped_column(String(12))
    fiscal_year: Mapped[int]
    page: Mapped[int]
    block_type: Mapped[str | None] = mapped_column(String(32))  # table, text, footnote ...
    section: Mapped[str | None] = mapped_column(String(255))  # from the PDF table of contents
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )


class Fact(Base):
    """One extracted figure, so comparisons are SQL plus Python arithmetic, not LLM maths."""

    __tablename__ = "facts"
    __table_args__ = (Index("ix_facts_lookup", "company_isin", "metric", "fiscal_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    company_isin: Mapped[str] = mapped_column(String(12))
    fiscal_year: Mapped[int]  # the period the value is for (reports also hold prior year)
    metric: Mapped[str] = mapped_column(String(64))  # revenue, net_profit, eps, headcount ...
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    unit: Mapped[str] = mapped_column(String(32))  # INR crore, INR, count, percent
    statement: Mapped[str | None] = mapped_column(String(16))  # consolidated, standalone
    page: Mapped[int]
    source_text: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(default=False)
