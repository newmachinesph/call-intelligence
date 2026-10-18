"""Versioned transcript chunking and verified Upstash Vector publication."""

from __future__ import annotations

import hashlib
import time
from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from upstash_vector import Index

from call_intelligence_api import ai
from call_intelligence_api.auth import domain_engine
from call_intelligence_api.call_models import (
    CallIndexChunk,
    CallRecord,
    IndexManifest,
    TranscriptSegment,
)
from call_intelligence_api.processing import (
    StaleGeneration,
    _claim,
    _fail,
    _locked_current,
    failure_code,
)


class VectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    upstash_vector_rest_url: str
    upstash_vector_rest_token: SecretStr


@lru_cache
def vector_client() -> Index:
    settings = VectorSettings()
    return Index(url=settings.upstash_vector_rest_url, token=settings.upstash_vector_rest_token.get_secret_value())


def chunk_segments(segments: list[TranscriptSegment]) -> list[dict]:
    """Keep adjacent turns together, with one previous turn for reading context."""
    output = []
    current: list[TranscriptSegment] = []
    estimated = 0
    for segment in segments:
        cost = max(1, len(segment.text) // 4)
        if current and estimated + cost > 380:
            output.append(current)
            current = current[-1:]
            estimated = sum(max(1, len(item.text) // 4) for item in current)
        current.append(segment)
        estimated += cost
    if current and (not output or current != output[-1]):
        output.append(current)
    return [
        {
            "ordinal": index,
            "text": "\n".join(f"{segment.speaker_label or 'Speaker ?'}: {segment.text}" for segment in group),
            "segment_ids": [str(segment.id) for segment in group],
            "start_seconds": min(segment.start_seconds for segment in group),
            "end_seconds": max(segment.end_seconds for segment in group),
        }
        for index, group in enumerate(output)
    ]


def indexing_stage(call_id: UUID, generation: int) -> UUID | None:
    claim = _claim(call_id, generation, "indexing")
    if claim is None:
        return None
    fence, owner = claim
    try:
        with Session(domain_engine()) as session:
            existing_call = session.get(CallRecord, call_id)
            active = session.get(IndexManifest, existing_call.active_manifest_id) if existing_call.active_manifest_id else None
            if (
                active
                and active.transcript_id == existing_call.active_transcript_id
                and active.state == "ready"
                and active.embedding_model == ai.settings().openai_embedding_model
                and active.dimensions == ai.settings().openai_embedding_dimensions
            ):
                with Session(domain_engine()) as publish, publish.begin():
                    current, step = _locked_current(publish, call_id, generation, "indexing", fence, owner)
                    current.index_status = "ready"
                    step.result_id = active.id
                    step.state = "complete"
                    step.lease_owner = None
                    step.lease_until = None
                return active.id
        with Session(domain_engine()) as session:
            call = session.get(CallRecord, call_id)
            if not call.active_transcript_id:
                raise ValueError("Transcript is not ready")
            transcript_id = call.active_transcript_id
            organization_id = call.organization_id
            segments = session.scalars(
                select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript_id).order_by(TranscriptSegment.ordinal)
            ).all()
        prepared = chunk_segments(segments)
        if not prepared:
            raise ValueError("No canonical transcript chunks")
        with Session(domain_engine()) as session, session.begin():
            call, _ = _locked_current(session, call_id, generation, "indexing", fence, owner)
            if call.active_transcript_id != transcript_id:
                raise StaleGeneration()
            manifest = session.scalar(
                select(IndexManifest).where(
                    IndexManifest.call_id == call_id,
                    IndexManifest.generation == generation,
                    IndexManifest.transcript_id == transcript_id,
                )
            )
            if manifest is None:
                manifest = IndexManifest(
                    organization_id=organization_id,
                    call_id=call_id,
                    transcript_id=transcript_id,
                    generation=generation,
                    embedding_model=ai.settings().openai_embedding_model,
                    dimensions=ai.settings().openai_embedding_dimensions,
                    expected_count=len(prepared),
                )
                session.add(manifest)
                session.flush()
                for item in prepared:
                    digest = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
                    session.add(
                        CallIndexChunk(
                            organization_id=organization_id,
                            call_id=call_id,
                            manifest_id=manifest.id,
                            transcript_id=transcript_id,
                            ordinal=item["ordinal"],
                            vector_id=f"{call_id}:{transcript_id}:{item['ordinal']}",
                            text=item["text"],
                            text_sha256=digest,
                            segment_ids=item["segment_ids"],
                            start_seconds=item["start_seconds"],
                            end_seconds=item["end_seconds"],
                        )
                    )
            manifest_id = manifest.id

        with Session(domain_engine()) as session:
            chunks = session.scalars(
                select(CallIndexChunk).where(CallIndexChunk.manifest_id == manifest_id).order_by(CallIndexChunk.ordinal)
            ).all()
            chunk_data = [
                (chunk.vector_id, chunk.text, chunk.text_sha256, chunk.start_seconds, chunk.end_seconds, chunk.segment_ids)
                for chunk in chunks
            ]
        embedding = ai.client().embeddings.create(
            model=ai.settings().openai_embedding_model,
            dimensions=ai.settings().openai_embedding_dimensions,
            input=[item[1] for item in chunk_data],
            timeout=90,
        )
        if len(embedding.data) != len(chunk_data):
            raise ValueError("Embedding count differs from chunk count")
        namespace = str(organization_id)
        vectors = []
        for item, result in zip(chunk_data, embedding.data):
            vector_id, _, digest, start, end, segment_ids = item
            vectors.append(
                (
                    vector_id,
                    result.embedding,
                    {
                        "organization_id": namespace,
                        "call_id": str(call_id),
                        "transcript_id": str(transcript_id),
                        "manifest_id": str(manifest_id),
                        "segment_ids": segment_ids,
                        "start_seconds": start,
                        "end_seconds": end,
                        "text_sha256": digest,
                    },
                )
            )
        vector_client().upsert(vectors=vectors, namespace=namespace)
        ids = [item[0] for item in chunk_data]
        readable = 0
        for delay in (0.0, 0.5, 1.5, 3.0):
            if delay:
                time.sleep(delay)
            fetched = vector_client().fetch(ids=ids, namespace=namespace, include_metadata=False)
            readable = sum(item is not None for item in fetched)
            if readable == len(ids):
                break
        if readable != len(ids):
            raise RuntimeError("Vector manifest is not fully readable")
        with Session(domain_engine()) as session, session.begin():
            call, step = _locked_current(session, call_id, generation, "indexing", fence, owner)
            if call.active_transcript_id != transcript_id:
                raise StaleGeneration()
            manifest = session.get(IndexManifest, manifest_id)
            manifest.readable_count = readable
            manifest.state = "ready"
            manifest.usage = embedding.usage.model_dump(mode="json") if embedding.usage else {}
            call.active_manifest_id = manifest_id
            call.index_status = "ready"
            step.result_id = manifest_id
            step.state = "complete"
            step.lease_owner = None
            step.lease_until = None
            return manifest_id
    except Exception as exc:
        _fail(call_id, generation, "indexing", fence, owner, failure_code(exc))
        raise
