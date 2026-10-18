"""Content-free OpenAI token cost estimate for the one selected call."""

from __future__ import annotations

import argparse
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from call_intelligence_api.auth import domain_engine
from call_intelligence_api.call_models import AnalysisRun, ChatMessage, ChatThread, IndexManifest, TranscriptVersion


def price(input_tokens: int, output_tokens: int, input_per_million: str, output_per_million: str) -> Decimal:
    return (Decimal(input_tokens) * Decimal(input_per_million)
            + Decimal(output_tokens) * Decimal(output_per_million)) / 1_000_000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("call_id", type=UUID)
    call_id = parser.parse_args().call_id
    with Session(domain_engine()) as session:
        transcripts = session.scalars(select(TranscriptVersion).where(TranscriptVersion.call_id == call_id)).all()
        runs = session.scalars(select(AnalysisRun).where(AnalysisRun.call_id == call_id)).all()
        manifests = session.scalars(select(IndexManifest).where(IndexManifest.call_id == call_id)).all()
        chats = session.scalars(select(ChatMessage).join(ChatThread, ChatMessage.thread_id == ChatThread.id).where(
            ChatThread.call_id == call_id, ChatMessage.usage.is_not(None),
        )).all()
        audio_input = audio_output = 0
        for transcript in transcripts:
            for chunk in (transcript.raw_response or {}).get("chunks", []):
                usage = chunk.get("response", {}).get("usage", {})
                audio_input += usage.get("input_tokens", 0)
                audio_output += usage.get("output_tokens", 0)
        text_input = text_output = latest_input = latest_output = 0
        latest_generation = max((run.generation for run in runs), default=0)
        for run in runs:
            for usage in (run.usage or {}).values():
                text_input += usage.get("input_tokens", 0)
                text_output += usage.get("output_tokens", 0)
                if run.generation == latest_generation:
                    latest_input += usage.get("input_tokens", 0)
                    latest_output += usage.get("output_tokens", 0)
        embedding_tokens = sum((manifest.usage or {}).get("total_tokens", 0) for manifest in manifests)
        chat_input = sum((message.usage or {}).get("answer", {}).get("input_tokens", 0) for message in chats)
        chat_output = sum((message.usage or {}).get("answer", {}).get("output_tokens", 0) for message in chats)
        chat_embedding = sum((message.usage or {}).get("embedding", {}).get("total_tokens", 0) for message in chats)
    transcription_cost = price(audio_input, audio_output, "2.50", "10")
    analysis_cost = price(text_input, text_output, "0.40", "1.60")
    latest_analysis_cost = price(latest_input, latest_output, "0.40", "1.60")
    embedding_cost = price(embedding_tokens, 0, "0.02", "0")
    chat_cost = price(chat_input, chat_output, "0.40", "1.60") + price(chat_embedding, 0, "0.02", "0")
    print(f"audio_tokens={audio_input}/{audio_output} text_tokens={text_input}/{text_output} "
          f"embedding_tokens={embedding_tokens} chat_tokens={chat_input}/{chat_output}")
    print(f"successful_transcription_usd={transcription_cost:.4f} latest_analysis_usd={latest_analysis_cost:.4f} "
          f"normal_processing_usd={(transcription_cost + latest_analysis_cost + embedding_cost):.4f}")
    print(f"all_recorded_openai_usage_usd={(transcription_cost + analysis_cost + embedding_cost + chat_cost):.4f} "
          f"saved_chat_usd={chat_cost:.4f}")
    print("Rates: 2026-09-26 published OpenAI per-million token prices. A timed-out request has unknown billable usage; R2, Neon, Upstash and taxes are excluded.")


if __name__ == "__main__":
    main()
