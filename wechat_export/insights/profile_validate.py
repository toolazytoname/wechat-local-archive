"""Reject unverifiable or disallowed profile statements."""

from __future__ import annotations

import re
from typing import Any

from wechat_export.insights.store import InsightsError


SENSITIVE = re.compile(
    r"(MBTI|大五|出轨|忠诚分|爱意评分|谁更爱|精神病|抑郁症诊断|性取向|宗教归属|资产总额)",
    re.I,
)
DENIAL_OR_REPORT = re.compile(
    r"(不打算|不想|不是我|并不是|并没有|没有打算|只是转述|开玩笑|难道我|他说|她说|他们说|有人说|但这不是)",
    re.I,
)
QUOTED_SPAN = re.compile(r'[“"「]([^”"」]{2,80})[”"」]')
PLAN_PREFIX = re.compile(r"^(你|我|他|她)?(计划|打算|准备|希望|想要|想|要)")
NEGATION = re.compile(r"(从来不|从不|并不|不是|不要|不会|没有|没|未|不)")
TRANSFER = re.compile(r"(.{0,16}?)(借钱给|借给|还给|送给|转给)(.{0,16})")
PAST_MARK = re.compile(r"(去年|已经|昨天|曾经|过了)")
FUTURE_MARK = re.compile(r"(明年|将来|以后|希望|打算)")
SECOND_PERSON = re.compile(r"^(你|您)")
FIRST_PERSON = re.compile(r"(^|[^他她])我")
THIN_TEXT = re.compile(
    r"^(好的?|嗯+|哈哈+|呵呵+|在吗|吃了吗|到了|收到|ok|okay|thanks?|谢谢|早安|晚安|早|夜安|[.。!?！？…～~\s]+)$",
    re.I,
)
SUPPORT_EXCERPT = "excerpt"
SUPPORT_GROUNDED = "grounded_summary"
SUPPORT_UNSUPPORTED = "unsupported"
SUPPORT_CONTRADICTED = "contradicted"


def core_phrase(text: str) -> str:
    value = (text or "").strip()
    value = PLAN_PREFIX.sub("", value)
    return re.sub(r"[。.!?,，、\s\"“”‘’「」]", "", value)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。.!?\n]", text or "") if part.strip()]


def _unsigned(text: str) -> str:
    value = NEGATION.sub("", core_phrase(text))
    return re.sub(r"^(你|我|他|她)", "", value)


def _transfer_args(text: str) -> tuple[str, str, str] | None:
    match = TRANSFER.search(re.sub(r"[。.!?\s]", "", text or ""))
    if match is None:
        return None
    left = core_phrase(match.group(1)) or match.group(1)
    right = core_phrase(match.group(3)) or match.group(3)
    return left, match.group(2), right


def _role_reversed(statement: str, source_text: str) -> bool:
    left = _transfer_args(statement)
    right = _transfer_args(source_text)
    if not left or not right or left[1] != right[1]:
        return False
    return bool(left[0] and right[0] and left[0] == right[2] and left[2] == right[0])


def _topic_tokens(text: str) -> set[str]:
    return set(re.findall(r"(整理|计划|笔记|希望|打算|熬夜|旅游|借钱|阅读|核对|观察|归档)", text or ""))


def _shared_content(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    if len(a) < 2 or len(b) < 2:
        return False
    grams_a = {a[i : i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i : i + 2] for i in range(len(b) - 1)}
    if not grams_a:
        return False
    return (len(grams_a & grams_b) / len(grams_a)) >= 0.34


def _polarity_flipped(statement: str, source_text: str) -> bool:
    if bool(NEGATION.search(statement)) == bool(NEGATION.search(source_text)):
        return False
    return _shared_content(_unsigned(statement), _unsigned(source_text)) or bool(
        _topic_tokens(statement) & _topic_tokens(source_text)
    )


def _time_aspect_conflict(statement: str, source_text: str) -> bool:
    statement_past = bool(PAST_MARK.search(statement))
    statement_future = bool(FUTURE_MARK.search(statement))
    source_past = bool(PAST_MARK.search(source_text))
    source_future = bool(FUTURE_MARK.search(source_text))
    if not ((statement_past and source_future) or (statement_future and source_past)):
        return False
    return _shared_content(_unsigned(statement), _unsigned(source_text)) or bool(
        _topic_tokens(statement) & _topic_tokens(source_text)
    )


def _address_frame_conflict(statement: str, source_text: str) -> bool:
    """Reject second-person restatements of first-person source speech."""
    statement = (statement or "").strip()
    source_text = source_text or ""
    if not statement or not source_text:
        return False
    if not SECOND_PERSON.search(statement):
        return False
    if not FIRST_PERSON.search(source_text):
        return False
    return _shared_content(_unsigned(statement), _unsigned(source_text)) or bool(
        _topic_tokens(statement) & _topic_tokens(source_text)
    )


def is_thin_text(text: str) -> bool:
    value = re.sub(r"\s+", "", (text or "").strip())
    if not value:
        return True
    if len(value) <= 2:
        return True
    return bool(THIN_TEXT.match(value))


def sentence_units(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。.!?！？\n]+", text or "") if part.strip()]


def classify_statement_support(statement: str, source_text: str) -> str:
    """Conservative support: excerpt | grounded_summary | unsupported | contradicted.

    Exact sentence excerpts remain the only auto-verified layer. Grounded
    summaries keep exact quotes as evidence but mark meaning as unverified.
    Role/polarity/time/address-frame reversals stay contradicted.
    """
    statement = (statement or "").strip()
    source_text = source_text or ""
    if not statement or not source_text:
        return SUPPORT_UNSUPPORTED
    candidate = statement.rstrip("。.!?！？").strip()
    if candidate in sentence_units(source_text):
        return SUPPORT_EXCERPT
    if (
        _role_reversed(statement, source_text)
        or _polarity_flipped(statement, source_text)
        or _time_aspect_conflict(statement, source_text)
        or _address_frame_conflict(statement, source_text)
    ):
        return SUPPORT_CONTRADICTED
    return SUPPORT_UNSUPPORTED


def classify_observation_support(
    statement: str,
    source_texts: list[str],
    *,
    quotes: list[str] | None = None,
) -> str:
    """Classify a multi-evidence observation. Empty/thin statements stay unsupported."""
    statement = (statement or "").strip()
    sources = [text for text in source_texts if (text or "").strip()]
    if not statement or not sources or is_thin_text(statement):
        return SUPPORT_UNSUPPORTED
    if any(classify_statement_support(statement, text) == SUPPORT_CONTRADICTED for text in sources):
        return SUPPORT_CONTRADICTED
    if any(classify_statement_support(statement, text) == SUPPORT_EXCERPT for text in sources):
        return SUPPORT_EXCERPT
    verified_quotes = [
        quote
        for quote in (quotes or [])
        if quote and any(quote in text for text in sources)
    ]
    if verified_quotes:
        return SUPPORT_GROUNDED
    if any(_shared_content(_unsigned(statement), _unsigned(text)) for text in sources):
        return SUPPORT_GROUNDED
    if any(_topic_tokens(statement) & _topic_tokens(text) for text in sources):
        return SUPPORT_GROUNDED
    return SUPPORT_UNSUPPORTED


def validate_observation(
    obs: dict[str, Any],
    *,
    allowed_uids: set[str],
    self_ids: set[str],
    subject: str,
    records: dict[str, dict[str, Any]] | None = None,
) -> None:
    statement = str(obs.get("statement") or "")
    if SENSITIVE.search(statement):
        raise InsightsError("sensitive inference is not allowed", "sensitive_rejected")
    evidence = obs.get("evidence") or []
    ids = obs.get("evidence_ids") or []
    if not evidence and not ids:
        raise InsightsError("observation needs evidence", "missing_evidence")
    uids: list[str] = []
    for item in evidence:
        uid = item if isinstance(item, str) else item.get("record_uid")
        if not uid:
            raise InsightsError("evidence is outside the frozen scope", "evidence_out_of_scope")
        uids.append(str(uid))
    for uid in ids:
        if str(uid) not in uids:
            uids.append(str(uid))
    if not uids:
        raise InsightsError("observation needs evidence", "missing_evidence")
    for uid in uids:
        if uid not in allowed_uids:
            raise InsightsError("evidence is outside the frozen scope", "evidence_out_of_scope")
        if records is not None and uid not in records:
            raise InsightsError("evidence is outside the frozen scope", "evidence_out_of_scope")
    rebuilt = obs.get("evidence") or []
    if records is not None:
        for item in rebuilt:
            if not isinstance(item, dict):
                raise InsightsError("evidence is outside the frozen scope", "evidence_out_of_scope")
            rec = records[item["record_uid"]]
            if item.get("conversation_id") and item["conversation_id"] != rec.get("conversation_id"):
                raise InsightsError("evidence conversation does not match the frozen record", "wrong_conversation")
            if item.get("sender_id") and item["sender_id"] != rec.get("sender_id"):
                raise InsightsError("self profile cannot cite another speaker as self", "wrong_speaker")
            quote = str(item.get("quote") or obs.get("quote") or "")
            source = rec.get("text") or ""
            if quote and quote not in source:
                raise InsightsError("quote is not in the source text", "quote_mismatch")
    if subject == "self":
        for item in rebuilt:
            if isinstance(item, dict) and item.get("sender_id") and item["sender_id"] not in self_ids:
                raise InsightsError("self profile cannot cite another speaker as self", "wrong_speaker")
        if records is not None:
            for uid in uids:
                sender = records[uid].get("sender_id")
                if sender and sender not in self_ids:
                    raise InsightsError("self profile cannot cite another speaker as self", "wrong_speaker")
    quote = obs.get("quote") or ""
    source_text = obs.get("source_text") or ""
    if records is not None and uids:
        source_text = records[uids[0]].get("text") or ""
    if quote and quote not in source_text:
        raise InsightsError("quote is not in the source text", "quote_mismatch")


def filter_valid(observations: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    kept = []
    for obs in observations:
        try:
            validate_observation(obs, **kwargs)
        except InsightsError:
            continue
        kept.append(obs)
    return kept
