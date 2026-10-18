"""Deterministic local CRM transitions, independent of model text."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum


class OpportunityStage(StrEnum):
    NEW = "New"
    CONTACTED = "Contacted"
    FOLLOW_UP = "Follow-up"
    WON = "Won"
    LOST = "Lost"


class TaskStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


ALLOWED_STAGE_TRANSITIONS = {
    OpportunityStage.NEW: {OpportunityStage.CONTACTED},
    OpportunityStage.CONTACTED: {OpportunityStage.FOLLOW_UP, OpportunityStage.WON, OpportunityStage.LOST},
    OpportunityStage.FOLLOW_UP: {OpportunityStage.CONTACTED, OpportunityStage.WON, OpportunityStage.LOST},
    OpportunityStage.WON: set(),
    OpportunityStage.LOST: set(),
}

ALLOWED_TASK_TRANSITIONS = {
    TaskStatus.PENDING_APPROVAL: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.COMPLETED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELLED: set(),
}


def can_transition(prior: OpportunityStage, target: OpportunityStage) -> bool:
    return target in ALLOWED_STAGE_TRANSITIONS[prior]


def normalize_identifier(kind: str, value: str) -> str:
    if kind == "email":
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Invalid email identifier")
        return normalized
    if kind == "phone":
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            raise ValueError("Invalid phone identifier")
        return digits
    raise ValueError("Unsupported identifier kind")


def logical_followup_key(segment_ids: list[str], action: str) -> str:
    material = "|".join(sorted(segment_ids)) + "|" + " ".join(action.lower().split())
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def commitment_key(call_id: str, kind: str, action: str) -> str:
    """Logical follow-up ID stable across re-analysis: segment UUIDs change per transcript version, so they are excluded."""
    normalized = " ".join(re.sub(r"[^\w\s]", " ", action.lower()).split())
    material = f"commitment|{call_id}|{kind}|{normalized}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def readable_action(action: str) -> str:
    """Models sometimes return the task kind ("send_info") as the action; show it as words."""
    text = " ".join(action.split())
    return text.replace("_", " ").capitalize() if re.fullmatch(r"[a-z_]+", text) else text
