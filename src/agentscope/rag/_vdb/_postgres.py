# -*- coding: utf-8 -*-
"""PostgreSQL/pgvector implementation of the vector store backend."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal, Self

from ._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)
from .._document import Chunk


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(value: str, *, label: str) -> str:
    """Validate a generated SQL identifier fragment."""
    if not _IDENT_RE.fullmatch(value):
        raise ValueError(f"{label} must be a valid SQL identifier.")
    return value


class PostgresVectorStore(VectorStoreBase):
    """Vector store backed by PostgreSQL with the ``pgvector`` extension.

    Each AgentScope knowledge base maps to one logical collection row.
    Vector records live in one shared table with ``collection_name`` as
    the partitioning key, which keeps the app-level collection model the
    same as the existing Qdrant backend.
    """

    _DISTANCE_SQL: dict[str, tuple[str, str]] = {
        "Cosine": ("<=>", "1 - ({distance})"),
        "Dot": ("<#>", "-({distance})"),
        "Euclid": ("<->", "-({distance})"),
        "Manhattan": ("<+>", "-({distance})"),
    }

    def __init__(
        self,
        dsn: str | None = None,
        *,
        host: str = "localhost",
        port: int = 5432,
        database: str = "agentscope",
        user: str | None = None,
        password: str | None = None,
        pool: Any | None = None,
        table_prefix: str = "agentscope_rag",
        distance: Literal[
            "Cosine",
            "Dot",
            "Euclid",
            "Manhattan",
        ] = "Cosine",
        ensure_schema: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize the PostgreSQL vector store.

        Args:
            dsn (`str | None`, optional):
                PostgreSQL DSN. When omitted, discrete connection
                parameters are forwarded to ``asyncpg.create_pool``.
            host (`str`, defaults to ``"localhost"``):
                PostgreSQL host when ``dsn`` is omitted.
            port (`int`, defaults to ``5432``):
                PostgreSQL port when ``dsn`` is omitted.
            database (`str`, defaults to ``"agentscope"``):
                PostgreSQL database when ``dsn`` is omitted.
            user (`str | None`, optional):
                PostgreSQL user when ``dsn`` is omitted.
            password (`str | None`, optional):
                PostgreSQL password when ``dsn`` is omitted.
            pool (`Any | None`, optional):
                Externally managed ``asyncpg`` pool. The caller owns
                its lifecycle.
            table_prefix (`str`, defaults to ``"agentscope_rag"``):
                Prefix for generated pgvector tables.
            distance (`Literal[...]`, defaults to ``"Cosine"``):
                Distance metric used for newly created collections.
            ensure_schema (`bool`, defaults to ``True``):
                Create ``vector`` extension and tables on enter.
            **kwargs (`Any`):
                Extra keyword arguments forwarded to
                ``asyncpg.create_pool``.
        """
        self._dsn = dsn
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password
        self._external_pool = pool
        self._kwargs = kwargs
        self._distance = distance
        self._ensure_schema = ensure_schema

        prefix = _validate_ident(table_prefix, label="table_prefix")
        self._collections_table = f"{prefix}_collections"
        self._records_table = f"{prefix}_records"

        self._pool: Any | None = pool
        self._owned_pool: Any | None = None

    async def __aenter__(self) -> Self:
        """Create the asyncpg pool and initialize pgvector schema."""
        if self._external_pool is not None:
            self._pool = self._external_pool
        else:
            try:
                import asyncpg
            except ImportError as e:
                raise ImportError(
                    "The 'asyncpg' package is required for "
                    "PostgresVectorStore. Install it with: "
                    'pip install "agentscope[postgres]"',
                ) from e

            connect_kwargs: dict[str, Any]
            if self._dsn is not None:
                connect_kwargs = {"dsn": self._dsn}
            else:
                connect_kwargs = {
                    "host": self._host,
                    "port": self._port,
                    "database": self._database,
                    "user": self._user,
                    "password": self._password,
                }
            self._owned_pool = await asyncpg.create_pool(
                **connect_kwargs,
                **self._kwargs,
            )
            self._pool = self._owned_pool

        if self._ensure_schema:
            await self._create_schema()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Close the owned asyncpg pool, if any."""
        if self._owned_pool is not None:
            await self._owned_pool.close()
            self._owned_pool = None
        self._pool = self._external_pool

    async def _create_schema(self) -> None:
        """Create pgvector extension and backing tables."""
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._collections_table} (
                    name TEXT PRIMARY KEY,
                    dimensions INTEGER NOT NULL,
                    distance TEXT NOT NULL
                )
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._records_table} (
                    id UUID PRIMARY KEY,
                    collection_name TEXT NOT NULL
                        REFERENCES {self._collections_table}(name)
                        ON DELETE CASCADE,
                    document_id TEXT NOT NULL,
                    embedding vector NOT NULL,
                    chunk JSONB NOT NULL
                )
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._records_table}_doc_idx
                ON {self._records_table} (collection_name, document_id)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._records_table}_chunk_gin_idx
                ON {self._records_table} USING GIN (chunk)
                """,
            )

    async def create_collection(self, name: str, dimensions: int) -> None:
        """Create a logical collection if missing."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._collections_table}
                    (name, dimensions, distance)
                VALUES ($1, $2, $3)
                ON CONFLICT (name) DO NOTHING
                """,
                name,
                dimensions,
                self._distance,
            )

    async def delete_collection(self, name: str) -> None:
        """Delete a collection and all vector records it owns."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"DELETE FROM {self._collections_table} WHERE name = $1",
                name,
            )

    async def has_collection(self, name: str) -> bool:
        """Return whether the logical collection exists."""
        async with self._pool.acquire() as conn:
            exists = await conn.fetchval(
                f"""
                SELECT EXISTS(
                    SELECT 1
                    FROM {self._collections_table}
                    WHERE name = $1
                )
                """,
                name,
            )
        return bool(exists)

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Insert records into a collection."""
        if not records:
            return

        async with self._pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {self._records_table}
                    (id, collection_name, document_id, embedding, chunk)
                VALUES ($1, $2, $3, $4::vector, $5::jsonb)
                """,
                [
                    (
                        uuid.uuid4(),
                        collection,
                        record.document_id,
                        self._vector_literal(record.vector),
                        record.chunk.model_dump_json(),
                    )
                    for record in records
                ],
            )

    async def delete(self, collection: str, document_id: str) -> None:
        """Delete all vector records for one source document."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                DELETE FROM {self._records_table}
                WHERE collection_name = $1 AND document_id = $2
                """,
                collection,
                document_id,
            )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Find nearest vectors in a collection."""
        collection_info = await self._get_collection(collection)
        if collection_info is None:
            return []

        operator, score_template = self._DISTANCE_SQL[
            collection_info["distance"]
        ]
        where, params = self._build_where(collection, metadata_filter)
        params.append(self._vector_literal(query_vector))
        vector_ref = f"${len(params)}::vector"
        distance_expr = f"embedding {operator} {vector_ref}"
        score_expr = score_template.format(distance=distance_expr)
        params.append(top_k)
        limit_ref = f"${len(params)}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    document_id,
                    chunk,
                    {score_expr} AS score
                FROM {self._records_table}
                WHERE {where}
                ORDER BY {distance_expr} ASC
                LIMIT {limit_ref}
                """,
                *params,
            )

        return [
            VectorSearchResult(
                score=float(row["score"]),
                document_id=row["document_id"],
                chunk=Chunk.model_validate(self._json_obj(row["chunk"])),
            )
            for row in rows
        ]

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """List source documents indexed in a collection."""
        where, params = self._build_where(collection, metadata_filter)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    document_id,
                    count(*)::int AS chunk_count,
                    (array_agg(chunk->>'source' ORDER BY id))[1] AS source,
                    (array_agg(chunk->'metadata' ORDER BY id))[1] AS metadata
                FROM {self._records_table}
                WHERE {where}
                GROUP BY document_id
                """,
                *params,
            )

        return [
            DocumentSummary(
                document_id=row["document_id"],
                source=row["source"] or "",
                chunk_count=int(row["chunk_count"]),
                metadata=self._json_obj(row["metadata"]) or {},
            )
            for row in rows
        ]

    async def _get_collection(self, name: str) -> Any | None:
        """Fetch collection metadata."""
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                f"""
                SELECT name, dimensions, distance
                FROM {self._collections_table}
                WHERE name = $1
                """,
                name,
            )

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        """Format a Python vector as a pgvector literal."""
        return "[" + ",".join(str(float(value)) for value in vector) + "]"

    @staticmethod
    def _json_obj(value: Any) -> Any:
        """Decode asyncpg JSON/JSONB values when returned as strings."""
        if isinstance(value, str):
            return json.loads(value)
        return value

    @staticmethod
    def _build_where(
        collection: str,
        metadata_filter: dict[str, Any] | None,
    ) -> tuple[str, list[Any]]:
        """Build WHERE SQL and params for collection + metadata filters."""
        params: list[Any] = [collection]
        clauses = ["collection_name = $1"]
        if metadata_filter:
            params.append(json.dumps(metadata_filter, ensure_ascii=False))
            clauses.append(f"chunk->'metadata' @> ${len(params)}::jsonb")
        return " AND ".join(clauses), params
