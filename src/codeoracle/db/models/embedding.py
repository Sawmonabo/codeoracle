"""SQLAlchemy model for embedding chunks."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from codeoracle.db.base import Base

EMBEDDING_DIM = 1536


class EmbeddingRecord(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    snapshot_sha: Mapped[str] = mapped_column(
        String(80), nullable=False, index=True
    )
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    chunk_start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=False
    )
