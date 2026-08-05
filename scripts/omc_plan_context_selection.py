#!/usr/bin/env python3
"""Prepare and verify blind baseline-context selection artifacts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from omc_plan_runtime_pilot import validate_confirmatory_candidate_selection


RETRIEVAL_POLICY = {
    "schema_version": 1,
    "ranking_algorithm_version": 4,
    "candidate_source": "baseline_tree_only",
    "followup_data_allowed": False,
    "manual_substitution_allowed": False,
    "maximum_selected_files_per_case": 12,
    "maximum_indexed_files_per_case": 20_000,
    "excluded_path_parts": [
        ".git",
        ".next",
        ".nx",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "vendor",
    ],
}
FROZEN_SELECTION_COMMIT = "337f00da0a89ca9dd74c84ca092838bbd2fb820b"
FROZEN_SELECTION_SHA256 = (
    "71c8c7ae186b0ecce09b3f4a442629c5ac72c18ab4fc7e4ddef53a8588216ea5"
)
FROZEN_DIAGNOSTIC_SELECTION_SHA256 = (
    "7d9305445938ed6f71361c4a99a63e6e1b6a6bedef5ef3404a33c0167864a65b"
)
FROZEN_RETRIEVAL_DEVELOPMENT_SHA256 = (
    "b67eac41f9b0aaa3f21f7be86ecb9c8cb53330a6e2520035f0993b9e33ff0154"
)
FROZEN_RETRIEVAL_DEVELOPMENT_V2_SHA256 = (
    "0bf7ef1546f4454b4e70f6365914332551340ec495aa50413cf05a4615fcf398"
)
FROZEN_RETRIEVAL_DEVELOPMENT_V3_SHA256 = (
    "6b02970a944ab0c09b82e0c23e0ac87b2da8924afd9b03cad4af2dc2a4831d89"
)
DEFAULT_EXECUTION_BUDGET = {
    "max_provider_calls": 10,
    "timeout_seconds": 1_800,
    "max_input_tokens": 250_000,
    "max_output_tokens": 40_000,
    "prompt_token_reserve": 10_000,
    "stop_on_budget_exceeded": True,
}
_SENSITIVE_TEXT_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b"
        r"\s*[:=]\s*[\"'][^\"']{8,}[\"']"
    ),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "local_user_path": re.compile(r"(?:/Users|/home)/[^\s\"']+"),
}
_RETRIEVAL_DEVELOPMENT_SURFACES = {
    "ui_state",
    "api_payload",
    "data_indexing",
    "backend_rules",
    "multi_file_legacy",
}
_RETRIEVAL_FORBIDDEN_PROVENANCE_FIELDS = {
    "repo_alias",
    "baseline_commit",
    "followup_commit",
    "context_candidate_paths",
    "selection_sha256",
    "provider_output",
}
_REQUEST_TERM_EXPANSIONS = {
    "문의": ("inquiry", "question", "qna"),
    "화면": ("screen", "view", "page"),
    "다이얼로그": ("dialog", "modal"),
    "선택": ("select", "selected", "selection", "selector"),
    "편집": ("edit", "editor", "form"),
    "저장": ("save", "saved", "unsaved", "persist", "store"),
    "변경": ("change", "changes", "dirty", "update"),
    "나가기": ("exit", "leave", "navigation", "route"),
    "이탈": ("exit", "leave", "navigation", "route"),
    "확인": ("confirm", "guard", "block"),
    "파일": ("file", "asset"),
    "전송": ("upload", "transfer"),
    "업로드": ("upload", "asset"),
    "서명": ("sign", "signed", "presigned"),
    "주소": ("url", "uri"),
    "만료": ("expiry", "expire", "expiration"),
    "일회성": ("presigned", "temporary"),
    "요청": ("request", "payload", "input"),
    "서버": ("server", "api", "client"),
    "판단": ("decision",),
    "의사결정": ("decision",),
    "영속": ("persist", "persistence", "repository", "store"),
    "식별자": ("id", "identifier", "key"),
    "조회": ("get", "read", "fetch", "find"),
    "재시도": ("retry",),
    "허용": ("allow", "budget", "remaining"),
    "횟수": ("count", "budget", "remaining"),
    "소진": ("exhausted", "budget", "remaining"),
    "사유": ("reason",),
    "실행": ("run", "execute", "job", "worker"),
    "기존": ("legacy",),
    "결제": ("checkout", "payment"),
    "선택값": ("selection", "selected"),
    "주문": ("order",),
    "반려": ("reject", "rejected", "rejection"),
    "포트폴리오": ("portfolio",),
    "캐시": ("cache", "query"),
    "무효화": ("invalidate", "invalidation"),
    "상승": ("bullish", "rise", "up"),
    "다음 날": ("next", "day"),
    "패턴": ("pattern", "scan"),
    "후보": ("candidate",),
    "리서치": ("research", "evidence"),
    "누락": ("missing", "gap", "omission"),
    "충돌": ("conflict",),
    "분석": ("analyze", "analysis"),
    "비대칭": ("asymmetric", "asymmetry"),
    "컨텍스트": ("context",),
    "신호": ("signal", "entry"),
    "워크포워드": ("walk", "forward", "walkforward"),
    "규칙": ("rule", "policy"),
    "승격": ("promote", "promotion"),
    "제출": ("submit", "submission"),
    "완료": ("complete", "completion", "success"),
    "이벤트": ("event", "tracking", "analytics", "gtm"),
    "수집": ("collect", "tracking"),
    "인증": ("auth", "authentication", "login"),
    "생성": ("create",),
    "연결": ("bridge", "map", "adapter"),
}


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_retrieval_development_corpus(
    corpus: dict[str, Any], *, confirmatory_selection: dict[str, Any]
) -> dict[str, Any]:
    """Validate the frozen synthetic retrieval corpus without holdout leakage."""
    if not isinstance(corpus, dict) or set(corpus) != {
        "schema_version", "status", "cases", "corpus_sha256"
    }:
        raise ValueError("retrieval development corpus fields are invalid")
    schema_version = corpus["schema_version"]
    if schema_version not in {1, 2, 3} or corpus["status"] != "preregistered":
        raise ValueError("retrieval development corpus status is invalid")
    if corpus["corpus_sha256"] != canonical_digest(
        _without_digest(corpus, "corpus_sha256")
    ):
        raise ValueError("retrieval development corpus hash mismatch")
    confirmatory_cases = (
        confirmatory_selection.get("cases")
        if isinstance(confirmatory_selection, dict)
        else None
    )
    if (
        not isinstance(confirmatory_cases, list)
        or confirmatory_selection.get("selection_sha256")
        != FROZEN_DIAGNOSTIC_SELECTION_SHA256
        or canonical_digest(confirmatory_cases)
        != FROZEN_DIAGNOSTIC_SELECTION_SHA256
    ):
        raise ValueError("retrieval development requires frozen Batch A selection")
    batch_case_ids = {
        case.get("case_id") for case in confirmatory_cases if isinstance(case, dict)
    }
    batch_paths = {
        path
        for case in confirmatory_cases
        if isinstance(case, dict)
        for path in case.get("context_candidate_paths", [])
        if isinstance(path, str)
    }

    cases = corpus["cases"]
    if not isinstance(cases, list) or len(cases) != 5:
        raise ValueError("retrieval development corpus requires five cases")
    case_ids: set[str] = set()
    surface_counts = {surface: 0 for surface in _RETRIEVAL_DEVELOPMENT_SURFACES}
    critical_path_count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("retrieval development case is invalid")
        if _RETRIEVAL_FORBIDDEN_PROVENANCE_FIELDS.intersection(case):
            raise ValueError("retrieval development case has forbidden provenance")
        if set(case) != {
            "case_id",
            "split",
            "source_type",
            "surface",
            "request",
            "context_files",
            "context_labels",
        }:
            raise ValueError("retrieval development case fields are invalid")
        case_id = case["case_id"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in case_ids
        ):
            raise ValueError("retrieval development case id is invalid")
        if case_id in batch_case_ids:
            raise ValueError("retrieval development corpus overlaps Batch A case")
        case_ids.add(case_id)
        if (
            case["split"] != "development"
            or case["source_type"] != "synthetic_anonymized"
            or not isinstance(case["request"], str)
            or not case["request"].strip()
        ):
            raise ValueError("retrieval development case metadata is invalid")
        surface = case["surface"]
        if surface not in surface_counts:
            raise ValueError("retrieval development surface is invalid")
        surface_counts[surface] += 1

        files = case["context_files"]
        if not isinstance(files, list) or not files:
            raise ValueError("retrieval development context files are invalid")
        file_paths: set[str] = set()
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "content_utf8"}:
                raise ValueError("retrieval development context file is invalid")
            path = item["path"]
            pure_path = PurePosixPath(path) if isinstance(path, str) else None
            if (
                pure_path is None
                or pure_path.is_absolute()
                or ".." in pure_path.parts
                or not path
                or path in file_paths
                or not isinstance(item["content_utf8"], str)
            ):
                raise ValueError("retrieval development context path is invalid")
            if path in batch_paths:
                raise ValueError("retrieval development corpus overlaps Batch A path")
            file_paths.add(path)

        labels = case["context_labels"]
        if not isinstance(labels, list) or not labels:
            raise ValueError("retrieval development context labels are invalid")
        label_paths: set[str] = set()
        case_critical_count = 0
        for label in labels:
            if not isinstance(label, dict) or set(label) != {
                "path", "weight", "critical"
            }:
                raise ValueError("retrieval development context label is invalid")
            path = label["path"]
            weight = label["weight"]
            critical = label["critical"]
            if (
                path not in file_paths
                or path in label_paths
                or not isinstance(weight, int)
                or isinstance(weight, bool)
                or weight <= 0
                or not isinstance(critical, bool)
            ):
                raise ValueError("retrieval development context label is invalid")
            label_paths.add(path)
            case_critical_count += int(critical)
        if case_critical_count != 1:
            raise ValueError("retrieval development case requires one critical path")
        critical_path_count += case_critical_count

    if any(count != 1 for count in surface_counts.values()):
        raise ValueError("retrieval development surface quota mismatch")
    frozen_digest = {
        1: FROZEN_RETRIEVAL_DEVELOPMENT_SHA256,
        2: FROZEN_RETRIEVAL_DEVELOPMENT_V2_SHA256,
        3: FROZEN_RETRIEVAL_DEVELOPMENT_V3_SHA256,
    }[schema_version]
    if corpus["corpus_sha256"] != frozen_digest:
        raise ValueError("retrieval development frozen corpus mismatch")
    return {
        "case_count": len(cases),
        "critical_path_count": critical_path_count,
        "surface_counts": dict(sorted(surface_counts.items())),
    }


def _lexical_terms(value: str) -> list[str]:
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", expanded)
    return re.findall(r"[0-9a-z]+|[가-힣]+", expanded.lower())


def _term_frequencies(value: str) -> tuple[Counter[str], int]:
    terms = _lexical_terms(value)
    return Counter(terms), len(terms)


def _request_terms(value: str) -> list[str]:
    terms = _lexical_terms(value)
    for source, targets in _REQUEST_TERM_EXPANSIONS.items():
        if source in value:
            for target in targets:
                terms.extend(_lexical_terms(target))
    return terms


def _request_concept_groups(value: str) -> list[set[str]]:
    groups = [
        set(term for target in targets for term in _lexical_terms(target))
        for source, targets in _REQUEST_TERM_EXPANSIONS.items()
        if source in value
    ]
    if not re.search(r"[가-힣]", value):
        groups.extend({term} for term in _lexical_terms(value))
    return [group for group in groups if group]


def _bm25_scores(
    query_terms: list[str],
    documents: list[tuple[Counter[str], int]],
    *,
    weight: float,
) -> list[float]:
    if not documents:
        return []
    document_frequencies = Counter(
        term for frequencies, _ in documents for term in frequencies
    )
    average_length = sum(length for _, length in documents) / len(documents)
    query_counts = Counter(query_terms)
    scores: list[float] = []
    for frequencies, length in documents:
        length_ratio = length / max(average_length, 1.0)
        score = 0.0
        for term, query_count in query_counts.items():
            frequency = frequencies[term]
            if not frequency:
                continue
            inverse_document_frequency = math.log(
                1 + (len(documents) - document_frequencies[term] + 0.5)
                / (document_frequencies[term] + 0.5)
            )
            saturation = frequency * 2.2 / (
                frequency + 1.2 * (0.25 + 0.75 * length_ratio)
            )
            score += inverse_document_frequency * saturation * min(query_count, 2)
        scores.append(score * weight)
    return scores


def _contains_sensitive_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_TEXT_PATTERNS.values())


def _is_test_path(path: str) -> bool:
    return bool(
        re.search(r"(?:^|[./_-])(?:test|tests|spec)(?:[./_-]|$)", path.lower())
    )


def _implementation_key(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    name = re.sub(r"(?:^|[._-])(?:test|spec)(?=[._-]|$)", "", name)
    return re.sub(r"[^0-9a-z]+", "", name)


def build_baseline_only_shortlist(
    case: dict[str, Any], *, maximum_selected_files: int
) -> dict[str, Any]:
    """Rank baseline files using only the request, path, and baseline content."""
    if (
        not isinstance(case, dict)
        or not isinstance(case.get("case_id"), str)
        or not isinstance(case.get("request"), str)
        or not isinstance(case.get("context_files"), list)
        or not isinstance(maximum_selected_files, int)
        or isinstance(maximum_selected_files, bool)
        or maximum_selected_files <= 0
        or maximum_selected_files
        > RETRIEVAL_POLICY["maximum_selected_files_per_case"]
    ):
        raise ValueError("baseline-only shortlist input is invalid")
    request_terms = _request_terms(case["request"])
    concept_groups = _request_concept_groups(case["request"])
    eligible_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    sensitive_file_count = 0
    for item in case["context_files"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "content_utf8"}
            or not isinstance(item["path"], str)
            or not isinstance(item["content_utf8"], str)
            or item["path"] in seen_paths
        ):
            raise ValueError("baseline-only shortlist context file is invalid")
        path = item["path"]
        seen_paths.add(path)
        if _contains_sensitive_text(item["content_utf8"]):
            sensitive_file_count += 1
            continue
        path_frequencies, path_term_count = _term_frequencies(path)
        content_frequencies, content_term_count = _term_frequencies(
            item["content_utf8"]
        )
        eligible_files.append({
            "path": path,
            "path_document": (path_frequencies, path_term_count),
            "content_document": (content_frequencies, content_term_count),
        })
    if not eligible_files:
        raise ValueError("baseline-only shortlist requires context files")

    path_scores = _bm25_scores(
        request_terms,
        [item["path_document"] for item in eligible_files],
        weight=4.0,
    )
    content_scores = _bm25_scores(
        request_terms,
        [item["content_document"] for item in eligible_files],
        weight=1.0,
    )
    ranked: list[dict[str, Any]] = []
    for item, path_score, content_score in zip(
        eligible_files, path_scores, content_scores
    ):
        path = item["path"]
        score = path_score + content_score
        if path.startswith("docs/") or PurePosixPath(path).name.lower().startswith(
            "readme"
        ):
            score -= 3.0
        if _is_test_path(path):
            score -= 10.0
        concept_hits = {
            index
            for index, group in enumerate(concept_groups)
            if any(
                term in item["path_document"][0]
                or term in item["content_document"][0]
                for term in group
            )
        }
        ranked.append({
            "score": score,
            "path": path,
            "concept_hits": concept_hits,
        })
    ranked.sort(key=lambda item: (-item["score"], item["path"]))

    first_item = ranked[0]
    if _is_test_path(first_item["path"]):
        implementation_key = _implementation_key(first_item["path"])
        first_item = next(
            (
                item
                for item in ranked
                if not _is_test_path(item["path"])
                and _implementation_key(item["path"]) == implementation_key
            ),
            first_item,
        )
    selected_paths = [first_item["path"]]
    covered_concepts = set(first_item["concept_hits"])
    if maximum_selected_files > 1 and selected_paths and not _is_test_path(
        selected_paths[0]
    ):
        implementation_key = _implementation_key(selected_paths[0])
        related_test = next(
            (
                item
                for item in ranked
                if _is_test_path(item["path"])
                and _implementation_key(item["path"]) == implementation_key
            ),
            None,
        )
        if related_test is not None:
            selected_paths.append(related_test["path"])
            covered_concepts.update(related_test["concept_hits"])
    while len(selected_paths) < maximum_selected_files:
        remaining = [
            item for item in ranked if item["path"] not in selected_paths
        ]
        if not remaining:
            break
        next_item = min(
            remaining,
            key=lambda item: (
                -(
                    item["score"]
                    + 2.5 * len(item["concept_hits"] - covered_concepts)
                ),
                item["path"],
            ),
        )
        selected_paths.append(next_item["path"])
        covered_concepts.update(next_item["concept_hits"])
    result = {
        "case_id": case["case_id"],
        "selected_paths": selected_paths,
    }
    if sensitive_file_count:
        result["sensitive_file_count"] = sensitive_file_count
    return result


def measure_retrieval_development_corpus(
    corpus: dict[str, Any],
    *,
    confirmatory_selection: dict[str, Any],
    maximum_selected_files: int,
) -> dict[str, Any]:
    """Measure deterministic retrieval quality without using labels for ranking."""
    validate_retrieval_development_corpus(
        corpus,
        confirmatory_selection=confirmatory_selection,
    )
    candidate_count = 0
    sensitive_file_count = 0
    selected_count = 0
    critical_total = 0
    critical_hits = 0
    weighted_total = 0
    weighted_hits = 0
    candidate_input_token_upper_bound = 0
    selected_input_token_upper_bound = 0
    for case in corpus["cases"]:
        shortlist = build_baseline_only_shortlist(
            case,
            maximum_selected_files=maximum_selected_files,
        )
        selected = set(shortlist["selected_paths"])
        eligible_context_files = [
            item
            for item in case["context_files"]
            if not _contains_sensitive_text(item["content_utf8"])
        ]
        candidate_count += len(case["context_files"])
        sensitive_file_count += shortlist.get("sensitive_file_count", 0)
        selected_count += len(selected)
        candidate_input_token_upper_bound += estimate_case_input_tokens({
            "case_id": case["case_id"],
            "request": case["request"],
            "files": [
                {
                    "relative_path": item["path"],
                    "content_utf8": item["content_utf8"],
                }
                for item in eligible_context_files
            ],
        })
        selected_input_token_upper_bound += estimate_case_input_tokens({
            "case_id": case["case_id"],
            "request": case["request"],
            "files": [
                {
                    "relative_path": item["path"],
                    "content_utf8": item["content_utf8"],
                }
                for item in eligible_context_files
                if item["path"] in selected
            ],
        })
        for label in case["context_labels"]:
            weight = label["weight"]
            weighted_total += weight
            weighted_hits += weight * int(label["path"] in selected)
            if label["critical"]:
                critical_total += 1
                critical_hits += int(label["path"] in selected)
    critical_recall = critical_hits / critical_total
    weighted_recall = weighted_hits / weighted_total
    report = {
        "case_count": len(corpus["cases"]),
        "candidate_file_count": candidate_count,
        "selected_file_count": selected_count,
        "critical_path_recall": critical_recall,
        "weighted_path_recall": weighted_recall,
        "file_count_reduction": (
            candidate_count - selected_count
        ) / candidate_count,
        "development_gate_passed": (
            critical_recall == 1.0
            and weighted_recall == 1.0
            and selected_count < candidate_count
        ),
    }
    if corpus["schema_version"] >= 2:
        report["eligible_file_count"] = candidate_count - sensitive_file_count
        report["sensitive_file_count"] = sensitive_file_count
        report = {
            "case_count": report.pop("case_count"),
            "candidate_file_count": report.pop("candidate_file_count"),
            "eligible_file_count": report.pop("eligible_file_count"),
            "sensitive_file_count": report.pop("sensitive_file_count"),
            **report,
        }
    if corpus["schema_version"] >= 3:
        token_reduction = (
            candidate_input_token_upper_bound - selected_input_token_upper_bound
        ) / candidate_input_token_upper_bound
        report["candidate_input_token_upper_bound"] = (
            candidate_input_token_upper_bound
        )
        report["selected_input_token_upper_bound"] = (
            selected_input_token_upper_bound
        )
        report["input_token_upper_bound_reduction"] = token_reduction
        report["development_gate_passed"] = (
            report["development_gate_passed"] and token_reduction > 0.0
        )
    return report


def _without_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    payload = deepcopy(value)
    payload.pop(field, None)
    return payload


def _validate_packet_digest(
    packet: dict[str, Any], *, trusted_selection_public_keys: set[str]
) -> None:
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version",
        "status",
        "batch_id",
        "selection_sha256",
        "selection_author_session_id",
        "selection_provenance",
        "selection_provenance_sha256",
        "retrieval_policy",
        "retrieval_policy_sha256",
        "cases",
        "packet_sha256",
    }:
        raise ValueError("context selection packet fields are invalid")
    if (
        packet["schema_version"] != 1
        or packet["status"] != "prepared"
        or packet["retrieval_policy"] != RETRIEVAL_POLICY
        or packet["retrieval_policy_sha256"] != canonical_digest(RETRIEVAL_POLICY)
    ):
        raise ValueError("context selection packet must use the frozen policy")
    if packet.get("packet_sha256") != canonical_digest(
        _without_digest(packet, "packet_sha256")
    ):
        raise ValueError("context selection packet hash mismatch")
    provenance = packet["selection_provenance"]
    author_session_id = _verify_selection_provenance(
        packet["selection_sha256"],
        provenance,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if (
        packet["selection_author_session_id"] != author_session_id
        or packet["selection_provenance_sha256"] != canonical_digest(provenance)
    ):
        raise ValueError("selection provenance contract is invalid")


def selection_provenance_payload(provenance: dict[str, Any]) -> bytes:
    payload = deepcopy(provenance)
    signoff = payload.get("signoff")
    if not isinstance(signoff, dict):
        raise ValueError("selection provenance signoff is required")
    signoff["signature"] = ""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_selection_provenance(
    selection_sha256: str,
    provenance: dict[str, Any],
    *,
    trusted_selection_public_keys: set[str],
) -> str:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    expected_fields = {
        "schema_version",
        "status",
        "selection_sha256",
        "selection_commit",
        "author_session_id",
        "signoff",
    }
    if (
        not isinstance(provenance, dict)
        or set(provenance) != expected_fields
        or provenance["schema_version"] != 1
        or provenance["status"] != "attested"
        or selection_sha256 != FROZEN_SELECTION_SHA256
        or provenance["selection_sha256"] != selection_sha256
        or provenance["selection_commit"] != FROZEN_SELECTION_COMMIT
        or not isinstance(provenance["author_session_id"], str)
        or not provenance["author_session_id"].strip()
    ):
        message = (
            "frozen selection digest mismatch"
            if selection_sha256 != FROZEN_SELECTION_SHA256
            else "selection provenance contract is invalid"
        )
        raise ValueError(message)
    signoff = provenance["signoff"]
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer", "signer_public_key", "signature"
    }:
        raise ValueError("selection provenance signoff fields are invalid")
    public_key = signoff["signer_public_key"]
    if (
        not isinstance(signoff["signer"], str)
        or not signoff["signer"].strip()
        or public_key not in trusted_selection_public_keys
    ):
        raise ValueError("selection provenance signer is not trusted")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(
            base64.b64decode(signoff["signature"]),
            selection_provenance_payload(provenance),
        )
    except (ValueError, InvalidSignature) as error:
        raise ValueError("selection provenance signature is invalid") from error
    return provenance["author_session_id"].strip()


def _run_git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() if error.stderr else "git command failed"
        raise ValueError(detail) from error


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("baseline tree path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("baseline tree path is unsafe or non-canonical")
    return path


def _safe_case_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("context selection case id is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or path.as_posix() != value
        or value in {".", ".."}
    ):
        raise ValueError("context selection case id is unsafe or non-canonical")
    return value


def _baseline_tree(repo: Path, commit: str) -> list[dict[str, str]]:
    excluded = set(RETRIEVAL_POLICY["excluded_path_parts"])
    entries: list[dict[str, str]] = []
    output = _run_git(repo, "ls-tree", "-rz", "--full-tree", commit)
    for line in output.split("\0"):
        if not line:
            continue
        metadata, separator, relative = line.partition("\t")
        if not separator:
            raise ValueError("baseline tree entry is malformed")
        parts = metadata.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        path = _safe_relative_path(relative)
        if excluded.intersection(path.parts):
            continue
        entries.append({"path": path.as_posix(), "blob_oid": parts[2]})
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise ValueError("baseline tree contains no selectable files")
    if len(entries) > RETRIEVAL_POLICY["maximum_indexed_files_per_case"]:
        raise ValueError("baseline tree exceeds indexed file limit")
    return entries


def _verified_packet_tree(
    case: dict[str, Any], *, repo_roots: dict[str, str | Path]
) -> list[dict[str, str]]:
    repo_alias = case.get("repo_alias")
    if not isinstance(repo_alias, str) or repo_alias not in repo_roots:
        raise ValueError("baseline context repo mapping is missing")
    tree = _baseline_tree(
        Path(repo_roots[repo_alias]).resolve(), case.get("baseline_commit")
    )
    if (
        case.get("baseline_file_count") != len(tree)
        or case.get("baseline_tree_sha256") != canonical_digest(tree)
    ):
        raise ValueError("baseline tree fingerprint mismatch")
    return tree


def prepare_context_selection_packet(
    selection: dict[str, Any],
    *,
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    selection_provenance: dict[str, Any],
    trusted_selection_public_keys: set[str],
) -> dict[str, Any]:
    """Build a selector packet without exposing follow-up or local path data."""
    validate_confirmatory_candidate_selection(
        selection,
        trusted_prior_commits=trusted_prior_commits,
    )
    selection_author_session_id = _verify_selection_provenance(
        selection["selection_sha256"],
        selection_provenance,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )

    packet_cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for case in selection["cases"]:
        case_id = _safe_case_id(case.get("case_id"))
        repo_alias = case.get("repo_alias")
        baseline = case.get("baseline_commit")
        followup = case.get("followup_commit")
        request = case.get("request")
        if (
            case_id in case_ids
            or not isinstance(repo_alias, str)
            or repo_alias not in repo_roots
            or not isinstance(baseline, str)
            or not isinstance(followup, str)
            or not isinstance(request, str)
            or not request.strip()
        ):
            raise ValueError("context selection case is invalid")
        case_ids.add(case_id)
        repo = Path(repo_roots[repo_alias]).resolve()
        parents = _run_git(repo, "show", "-s", "--format=%P", followup).split()
        if not parents or parents[0] != baseline:
            raise ValueError("follow-up first parent does not match baseline")
        baseline_tree = _baseline_tree(repo, baseline)
        packet_cases.append({
            "case_id": case_id,
            "repo_alias": repo_alias,
            "baseline_commit": baseline,
            "request": request,
            "baseline_file_count": len(baseline_tree),
            "baseline_tree_sha256": canonical_digest(baseline_tree),
        })

    packet = {
        "schema_version": 1,
        "status": "prepared",
        "batch_id": selection.get("batch_id"),
        "selection_sha256": selection["selection_sha256"],
        "selection_author_session_id": selection_author_session_id,
        "selection_provenance": deepcopy(selection_provenance),
        "selection_provenance_sha256": canonical_digest(selection_provenance),
        "retrieval_policy": deepcopy(RETRIEVAL_POLICY),
        "retrieval_policy_sha256": canonical_digest(RETRIEVAL_POLICY),
        "cases": packet_cases,
    }
    packet["packet_sha256"] = canonical_digest(packet)
    return packet


def _validate_packet_against_selection(
    packet: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selection_public_keys: set[str],
) -> None:
    _validate_packet_digest(
        packet,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    expected = prepare_context_selection_packet(
        selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        selection_provenance=packet["selection_provenance"],
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if packet != expected:
        raise ValueError("context selection packet projection mismatch")


def materialize_baseline_workspaces(
    packet: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    output_root: str | Path,
    trusted_selection_public_keys: set[str],
) -> dict[str, Path]:
    """Create exact baseline-only workspaces without serializing private paths."""
    _validate_packet_against_selection(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    root = Path(output_root).resolve()
    if root.exists():
        raise ValueError("baseline workspace root already exists")
    verified = [
        (case, _verified_packet_tree(case, repo_roots=repo_roots))
        for case in packet.get("cases", [])
    ]
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        for case, tree in verified:
            case_id = _safe_case_id(case["case_id"])
            destination = staging / case_id
            destination.mkdir()
            repo = Path(repo_roots[case["repo_alias"]]).resolve()
            archive = subprocess.check_output(
                [
                    "git", "-C", str(repo), "archive", "--format=tar",
                    case["baseline_commit"],
                ],
                stderr=subprocess.PIPE,
            )
            allowed = {item["path"] for item in tree}
            extracted: set[str] = set()
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
                for member in bundle.getmembers():
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("baseline archive contains unsafe path")
                    if path.as_posix() not in allowed:
                        continue
                    bundle.extract(member, destination, filter="data")
                    extracted.add(path.as_posix())
            if extracted != allowed:
                raise ValueError("baseline archive does not match indexed tree")
        os.replace(staging, root)
    except subprocess.CalledProcessError as error:
        shutil.rmtree(staging, ignore_errors=True)
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or "baseline archive failed") from error
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {case["case_id"]: root / case["case_id"] for case, _ in verified}


def build_baseline_workspace_shortlists(
    packet: dict[str, Any],
    *,
    workspace_root: str | Path,
    maximum_selected_files: int,
    trusted_selection_public_keys: set[str],
) -> dict[str, Any]:
    """Build an unsigned shortlist draft from materialized baseline workspaces."""
    _validate_packet_digest(
        packet,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        raise ValueError("baseline workspace root is invalid")
    excluded = set(RETRIEVAL_POLICY["excluded_path_parts"])
    cases: list[dict[str, Any]] = []
    for case in packet["cases"]:
        case_id = _safe_case_id(case["case_id"])
        case_root = root / case_id
        if not case_root.is_dir():
            raise ValueError("baseline case workspace is missing")
        context_files: list[dict[str, str]] = []
        for path in sorted(case_root.rglob("*")):
            relative = path.relative_to(case_root)
            if any(part in excluded for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if (
                len(context_files)
                >= RETRIEVAL_POLICY["maximum_indexed_files_per_case"]
            ):
                raise ValueError("baseline workspace exceeds indexed file limit")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            except OSError as error:
                raise ValueError("baseline workspace file read failed") from error
            context_files.append({
                "path": relative.as_posix(),
                "content_utf8": content,
            })
        cases.append(
            build_baseline_only_shortlist(
                {
                    "case_id": case_id,
                    "request": case["request"],
                    "context_files": context_files,
                },
                maximum_selected_files=maximum_selected_files,
            )
        )
    draft = {
        "schema_version": 1,
        "status": "draft",
        "packet_sha256": packet["packet_sha256"],
        "retrieval_policy_sha256": packet["retrieval_policy_sha256"],
        "maximum_selected_files": maximum_selected_files,
        "cases": cases,
    }
    draft["shortlist_sha256"] = canonical_digest(draft)
    return draft


def build_execution_contract(
    execution_budget: dict[str, Any],
    *,
    attestation_type: str = "operator_attested",
) -> dict[str, Any]:
    """Freeze the pre-execution budget without claiming provider provenance."""
    if execution_budget != DEFAULT_EXECUTION_BUDGET:
        raise ValueError("execution budget must match the frozen local policy")
    if attestation_type == "provider_verified":
        raise ValueError(
            "provider_verified attestation requires a native provider receipt verifier"
        )
    if attestation_type != "operator_attested":
        raise ValueError("execution attestation type is invalid")
    contract = {
        "schema_version": 1,
        "status": "frozen",
        **deepcopy(execution_budget),
        "attestation_type": attestation_type,
        "replacement_claim_eligible": False,
    }
    contract["contract_sha256"] = canonical_digest(contract)
    return contract


def validate_transfer_budget(
    transfer_manifest: dict[str, Any], execution_contract: dict[str, Any]
) -> None:
    if execution_contract.get("contract_sha256") != canonical_digest(
        _without_digest(execution_contract, "contract_sha256")
    ):
        raise ValueError("execution contract hash mismatch")
    cases = transfer_manifest.get("cases")
    if (
        not isinstance(cases, list)
        or transfer_manifest.get("case_count") != len(cases)
        or len(cases) > execution_contract["max_provider_calls"]
    ):
        raise ValueError("transfer bundle exceeds provider call budget")
    for case in cases:
        estimate = case.get("estimated_input_tokens")
        if (
            not isinstance(estimate, int)
            or estimate < 0
            or estimate > execution_contract["max_input_tokens"]
        ):
            raise ValueError("transfer case exceeds input token budget")


def estimate_case_input_tokens(case_payload: dict[str, Any]) -> int:
    """Return a conservative UTF-8 byte upper bound plus prompt reserve."""
    serialized = json.dumps(
        case_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(serialized) + DEFAULT_EXECUTION_BUDGET["prompt_token_reserve"]


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_sensitive_output_path(
    output_path: str | Path, *, repo_roots: dict[str, str | Path]
) -> Path:
    destination = Path(output_path).resolve()
    protected_roots = {
        Path(__file__).resolve().parent.parent,
        *(Path(root).resolve() for root in repo_roots.values()),
    }
    if any(_path_is_within(destination, root) for root in protected_roots):
        raise ValueError("sensitive readiness output must be outside repositories")
    return destination


def _privacy_findings(text: str, *, subject: str) -> list[dict[str, str]]:
    return [
        {"subject": subject, "code": code}
        for code, pattern in _SENSITIVE_TEXT_PATTERNS.items()
        if pattern.search(text)
    ]


def _decode_transfer_text(content: bytes) -> tuple[str | None, str | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, "invalid_utf8"
    allowed_controls = {"\t", "\n", "\r", "\f"}
    if any(
        (ord(character) < 32 and character not in allowed_controls)
        or ord(character) == 127
        for character in text
    ):
        return None, "control_character"
    return text, None


def _baseline_archive_files(
    case: dict[str, Any], *, repo_roots: dict[str, str | Path]
) -> list[tuple[str, str, bytes]]:
    tree = _verified_packet_tree(case, repo_roots=repo_roots)
    expected = {item["path"]: item["blob_oid"] for item in tree}
    repo = Path(repo_roots[case["repo_alias"]]).resolve()
    try:
        archive = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "archive",
                "--format=tar",
                case["baseline_commit"],
            ],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or "baseline archive failed") from error

    files: list[tuple[str, str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("baseline archive contains unsafe path")
            relative = path.as_posix()
            if relative not in expected:
                continue
            if not member.isfile():
                raise ValueError("transfer bundle accepts regular files only")
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError("baseline archive file cannot be read")
            files.append((relative, expected[relative], stream.read()))
    if {path for path, _, _ in files} != set(expected):
        raise ValueError("baseline archive does not match indexed tree")
    files.sort(key=lambda item: item[0])
    return files


def _build_local_transfer_artifacts(
    packet: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selection_public_keys: set[str],
    baseline_context_manifest: dict[str, Any] | None = None,
    trusted_selector_public_keys: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _validate_packet_against_selection(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    selected_paths_by_case: dict[str, set[str]] | None = None
    baseline_context_manifest_sha256: str | None = None
    if baseline_context_manifest is not None:
        if not trusted_selector_public_keys:
            raise ValueError(
                "baseline context manifest requires trusted selector keys"
            )
        validate_baseline_context_manifest(
            baseline_context_manifest,
            packet=packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=trusted_prior_commits,
            trusted_selector_public_keys=trusted_selector_public_keys,
            trusted_selection_public_keys=trusted_selection_public_keys,
        )
        selected_paths_by_case = {
            case["case_id"]: {
                item["path"] for item in case["selected_context"]
            }
            for case in baseline_context_manifest["cases"]
        }
        baseline_context_manifest_sha256 = baseline_context_manifest[
            "manifest_sha256"
        ]
    bundle_cases: list[dict[str, Any]] = []
    manifest_cases: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    source_byte_size = 0
    transfer_byte_size = 0
    transferred_file_count = 0
    omitted_file_count = 0
    omitted_binary_count = 0
    omitted_sensitive_count = 0
    for case in packet["cases"]:
        case_id = case["case_id"]
        request = case["request"]
        request_findings = _privacy_findings(
            request, subject=f"{case_id}:request"
        )
        if request_findings:
            raise ValueError("transfer request contains sensitive content")
        bundle_files: list[dict[str, Any]] = []
        manifest_files: list[dict[str, Any]] = []
        case_source_byte_size = 0
        case_transfer_byte_size = len(request.encode("utf-8"))
        transfer_byte_size += case_transfer_byte_size
        for path, blob_oid, content in _baseline_archive_files(
            case, repo_roots=repo_roots
        ):
            if (
                selected_paths_by_case is not None
                and path not in selected_paths_by_case[case_id]
            ):
                continue
            content_sha256 = hashlib.sha256(content).hexdigest()
            source_byte_size += len(content)
            case_source_byte_size += len(content)
            text, binary_reason = _decode_transfer_text(content)
            if text is None:
                omitted_file_count += 1
                omitted_binary_count += 1
                manifest_files.append({
                    "relative_path": path,
                    "source_blob_oid": blob_oid,
                    "blob_sha256": content_sha256,
                    "byte_size": len(content),
                    "transfer_disposition": "omitted_binary",
                    "binary_reason": binary_reason,
                    "privacy_classification": "not_scanned_binary",
                    "privacy_checks": ["regular_file", binary_reason],
                })
                continue
            file_findings = _privacy_findings(text, subject=f"{case_id}:{path}")
            if file_findings:
                findings.extend(file_findings)
                omitted_file_count += 1
                omitted_sensitive_count += 1
                manifest_files.append({
                    "relative_path": path,
                    "source_blob_oid": blob_oid,
                    "blob_sha256": content_sha256,
                    "byte_size": len(content),
                    "transfer_disposition": "omitted_sensitive",
                    "sensitive_codes": sorted({
                        finding["code"] for finding in file_findings
                    }),
                    "privacy_classification": "omitted_sensitive",
                    "privacy_checks": [
                        "regular_file",
                        "utf8",
                        "sensitive_content_omitted",
                    ],
                })
                continue
            transfer_byte_size += len(content)
            case_transfer_byte_size += len(content)
            transferred_file_count += 1
            bundle_files.append({
                "relative_path": path,
                "content_utf8": text,
            })
            manifest_files.append({
                "relative_path": path,
                "source_blob_oid": blob_oid,
                "blob_sha256": content_sha256,
                "byte_size": len(content),
                "transfer_disposition": "included_text",
                "privacy_classification": (
                    "blocked" if file_findings else "pattern_clear"
                ),
                "privacy_checks": [
                    "regular_file",
                    "utf8",
                    "credential_patterns",
                    "local_user_paths",
                ],
            })
        bundle_case = {
            "case_id": case_id,
            "request": request,
            "files": bundle_files,
        }
        bundle_cases.append(bundle_case)
        manifest_cases.append({
            "case_id": case_id,
            "request_sha256": hashlib.sha256(request.encode("utf-8")).hexdigest(),
            "source_byte_size": case_source_byte_size,
            "transfer_byte_size": case_transfer_byte_size,
            "estimated_input_tokens": estimate_case_input_tokens(bundle_case),
            "files": manifest_files,
        })

    transfer_bundle = {
        "schema_version": 1,
        "status": "local_only",
        "batch_id": packet["batch_id"],
        "selection_sha256": packet["selection_sha256"],
        "cases": bundle_cases,
    }
    if baseline_context_manifest_sha256 is not None:
        transfer_bundle["baseline_context_manifest_sha256"] = (
            baseline_context_manifest_sha256
        )
    transfer_bundle["bundle_sha256"] = canonical_digest(transfer_bundle)
    transfer_manifest = {
        "schema_version": 1,
        "status": "frozen",
        "batch_id": packet["batch_id"],
        "selection_sha256": packet["selection_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "transfer_bundle_sha256": transfer_bundle["bundle_sha256"],
        "case_count": len(manifest_cases),
        "source_byte_size": source_byte_size,
        "transfer_byte_size": transfer_byte_size,
        "transferred_file_count": transferred_file_count,
        "omitted_file_count": omitted_file_count,
        "omitted_binary_count": omitted_binary_count,
        "omitted_sensitive_count": omitted_sensitive_count,
        "cases": manifest_cases,
    }
    if baseline_context_manifest_sha256 is not None:
        transfer_manifest["baseline_context_manifest_sha256"] = (
            baseline_context_manifest_sha256
        )
    transfer_manifest["manifest_sha256"] = canonical_digest(transfer_manifest)
    privacy_audit = {
        "schema_version": 1,
        "status": "sanitized" if findings else "pattern_clear",
        "scanner_version": 1,
        "transfer_manifest_sha256": transfer_manifest["manifest_sha256"],
        "finding_count": len(findings),
        "omitted_binary_count": omitted_binary_count,
        "omitted_sensitive_count": omitted_sensitive_count,
        "findings": findings,
    }
    privacy_audit["audit_sha256"] = canonical_digest(privacy_audit)
    return transfer_bundle, transfer_manifest, privacy_audit


def prepare_local_transfer_readiness(
    packet: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selection_public_keys: set[str],
    execution_budget: dict[str, Any],
    baseline_context_manifest: dict[str, Any] | None = None,
    trusted_selector_public_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Prepare exact local artifacts; external transfer remains disallowed."""
    transfer_bundle, transfer_manifest, privacy_audit = (
        _build_local_transfer_artifacts(
            packet,
            selection=selection,
            repo_roots=repo_roots,
            trusted_prior_commits=trusted_prior_commits,
            trusted_selection_public_keys=trusted_selection_public_keys,
            baseline_context_manifest=baseline_context_manifest,
            trusted_selector_public_keys=trusted_selector_public_keys,
        )
    )
    execution_contract = build_execution_contract(execution_budget)
    validate_transfer_budget(transfer_manifest, execution_contract)
    readiness = {
        "schema_version": 1,
        "status": "approval_required",
        "transfer_bundle": transfer_bundle,
        "transfer_manifest": transfer_manifest,
        "privacy_audit": privacy_audit,
        "execution_contract": execution_contract,
        "external_transfer_approved": False,
        "provider_execution_allowed": False,
        "replacement_claim_eligible": False,
    }
    if baseline_context_manifest is not None:
        readiness["baseline_context_manifest_sha256"] = (
            baseline_context_manifest["manifest_sha256"]
        )
    readiness["readiness_sha256"] = canonical_digest(readiness)
    return readiness


def validate_local_transfer_readiness(
    readiness: dict[str, Any],
    *,
    packet: dict[str, Any],
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selection_public_keys: set[str],
    baseline_context_manifest: dict[str, Any] | None = None,
    trusted_selector_public_keys: set[str] | None = None,
) -> None:
    if readiness.get("readiness_sha256") != canonical_digest(
        _without_digest(readiness, "readiness_sha256")
    ):
        raise ValueError("local transfer readiness hash mismatch")
    expected = prepare_local_transfer_readiness(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selection_public_keys=trusted_selection_public_keys,
        execution_budget=DEFAULT_EXECUTION_BUDGET,
        baseline_context_manifest=baseline_context_manifest,
        trusted_selector_public_keys=trusted_selector_public_keys,
    )
    if readiness != expected:
        raise ValueError("local transfer readiness contract mismatch")


def selector_response_payload(response: dict[str, Any]) -> bytes:
    payload = deepcopy(response)
    signoff = payload.get("signoff")
    if not isinstance(signoff, dict):
        raise ValueError("selector response signoff is required")
    signoff["signature"] = ""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_response_signature(
    response: dict[str, Any], *, trusted_selector_public_keys: set[str]
) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signoff = response.get("signoff")
    if not isinstance(signoff, dict) or set(signoff) != {
        "signer", "signer_public_key", "signature"
    }:
        raise ValueError("selector response signoff fields are invalid")
    public_key = signoff["signer_public_key"]
    if (
        not isinstance(signoff["signer"], str)
        or not signoff["signer"].strip()
        or public_key not in trusted_selector_public_keys
    ):
        raise ValueError("selector response signer is not trusted")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(
            base64.b64decode(signoff["signature"]),
            selector_response_payload(response),
        )
    except (ValueError, InvalidSignature) as error:
        raise ValueError("selector response signature is invalid") from error


def _validate_selector_response(
    packet: dict[str, Any],
    response: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selector_public_keys: set[str],
    trusted_selection_public_keys: set[str],
) -> list[dict[str, Any]]:
    _validate_packet_against_selection(
        packet,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if not isinstance(response, dict) or set(response) != {
        "schema_version", "status", "packet_sha256", "selector", "cases", "signoff"
    }:
        raise ValueError("selector response fields are invalid")
    if response["schema_version"] != 1 or response["status"] != "selected":
        raise ValueError("selector response status is invalid")
    if response["packet_sha256"] != packet["packet_sha256"]:
        raise ValueError("selector response packet hash mismatch")
    selector = response["selector"]
    if not isinstance(selector, dict) or set(selector) != {
        "session_id", "provider_outputs_available"
    }:
        raise ValueError("selector provenance fields are invalid")
    if (
        not isinstance(selector["session_id"], str)
        or not selector["session_id"].strip()
        or selector["session_id"] == packet["selection_author_session_id"]
    ):
        raise ValueError("selector requires an independent session")
    if selector["provider_outputs_available"] is not False:
        raise ValueError("selector provider outputs must be unavailable")
    _verify_response_signature(
        response, trusted_selector_public_keys=trusted_selector_public_keys
    )

    packet_ids = [case["case_id"] for case in packet["cases"]]
    packet_by_id = {case["case_id"]: case for case in packet["cases"]}
    response_cases = response["cases"]
    response_ids = (
        [case.get("case_id") for case in response_cases]
        if isinstance(response_cases, list)
        and all(isinstance(case, dict) for case in response_cases)
        else []
    )
    if (
        not isinstance(response_cases, list)
        or len(response_cases) != len(packet_by_id)
        or any(not isinstance(case, dict) for case in response_cases)
        or any(not isinstance(case_id, str) for case_id in response_ids)
        or len(response_ids) != len(set(response_ids))
        or set(response_ids) != set(packet_by_id)
    ):
        raise ValueError("selector response cases do not match packet")
    if response_ids != packet_ids:
        raise ValueError("selector response case order does not match packet")
    selected_cases: list[dict[str, Any]] = []
    for selected in response_cases:
        if not isinstance(selected, dict) or set(selected) != {
            "case_id", "selected_paths"
        }:
            raise ValueError("selector response case fields are invalid")
        paths = selected["selected_paths"]
        if (
            not isinstance(paths, list)
            or not paths
            or len(paths) > RETRIEVAL_POLICY["maximum_selected_files_per_case"]
            or any(not isinstance(path, str) or not path for path in paths)
            or len(paths) != len(set(paths))
        ):
            raise ValueError("selector selected paths are invalid")
        source = packet_by_id[selected["case_id"]]
        tree = {
            item["path"]: item["blob_oid"]
            for item in _verified_packet_tree(source, repo_roots=repo_roots)
        }
        if any(path not in tree for path in paths):
            raise ValueError("selector path is not present in baseline tree")
        selected_cases.append({
            "case_id": selected["case_id"],
            "repo_alias": source["repo_alias"],
            "baseline_commit": source["baseline_commit"],
            "selected_context": [
                {"path": path, "blob_oid": tree[path]} for path in paths
            ],
        })
    selected_cases.sort(key=lambda item: item["case_id"])
    return selected_cases


def apply_selector_response(
    packet: dict[str, Any],
    response: dict[str, Any],
    *,
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selector_public_keys: set[str],
    trusted_selection_public_keys: set[str],
) -> dict[str, Any]:
    selected_cases = _validate_selector_response(
        packet,
        response,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selector_public_keys=trusted_selector_public_keys,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    manifest = {
        "schema_version": 1,
        "status": "preregistered",
        "selection_sha256": packet["selection_sha256"],
        "retrieval_policy_sha256": packet["retrieval_policy_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "selector": deepcopy(response["selector"]),
        "selector_response_sha256": canonical_digest(response),
        "cases": selected_cases,
        "signoff": deepcopy(response["signoff"]),
    }
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest


def validate_baseline_context_manifest(
    manifest: dict[str, Any],
    *,
    packet: dict[str, Any],
    selection: dict[str, Any],
    repo_roots: dict[str, str | Path],
    trusted_prior_commits: list[str],
    trusted_selector_public_keys: set[str],
    trusted_selection_public_keys: set[str],
) -> None:
    if manifest.get("manifest_sha256") != canonical_digest(
        _without_digest(manifest, "manifest_sha256")
    ):
        raise ValueError("baseline context manifest hash mismatch")
    response = {
        "schema_version": 1,
        "status": "selected",
        "packet_sha256": manifest.get("packet_sha256"),
        "selector": deepcopy(manifest.get("selector")),
        "cases": [
            {
                "case_id": case.get("case_id"),
                "selected_paths": [
                    item.get("path") for item in case.get("selected_context", [])
                ],
            }
            for case in manifest.get("cases", [])
        ],
        "signoff": deepcopy(manifest.get("signoff")),
    }
    selected_cases = _validate_selector_response(
        packet,
        response,
        selection=selection,
        repo_roots=repo_roots,
        trusted_prior_commits=trusted_prior_commits,
        trusted_selector_public_keys=trusted_selector_public_keys,
        trusted_selection_public_keys=trusted_selection_public_keys,
    )
    if (
        manifest.get("schema_version") != 1
        or manifest.get("status") != "preregistered"
        or manifest.get("selection_sha256") != packet.get("selection_sha256")
        or manifest.get("retrieval_policy_sha256")
        != packet.get("retrieval_policy_sha256")
        or manifest.get("selector_response_sha256") != canonical_digest(response)
        or manifest.get("cases") != selected_cases
    ):
        raise ValueError("baseline context manifest contract mismatch")


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _load_commit_registry(path: str | Path) -> list[str]:
    registry = _load_json(path)
    if (
        set(registry) != {"schema_version", "commits"}
        or registry.get("schema_version") != 1
        or not isinstance(registry.get("commits"), list)
    ):
        raise ValueError("confirmatory prior commit registry is invalid")
    return registry["commits"]


def _write_json_atomic(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("selection")
    prepare.add_argument("repo_map")
    prepare.add_argument("prior_registry")
    prepare.add_argument("selection_provenance")
    prepare.add_argument("--trusted-selection-public-key", action="append", required=True)
    prepare.add_argument("--output", required=True)
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("packet")
    materialize.add_argument("selection")
    materialize.add_argument("repo_map")
    materialize.add_argument("prior_registry")
    materialize.add_argument("output_root")
    materialize.add_argument(
        "--trusted-selection-public-key", action="append", required=True
    )
    apply = subparsers.add_parser("apply")
    apply.add_argument("packet")
    apply.add_argument("response")
    apply.add_argument("selection")
    apply.add_argument("repo_map")
    apply.add_argument("prior_registry")
    apply.add_argument("--trusted-selector-public-key", action="append", required=True)
    apply.add_argument(
        "--trusted-selection-public-key", action="append", required=True
    )
    apply.add_argument("--output", required=True)
    readiness = subparsers.add_parser("readiness")
    readiness.add_argument("packet")
    readiness.add_argument("selection")
    readiness.add_argument("repo_map")
    readiness.add_argument("prior_registry")
    readiness.add_argument(
        "--trusted-selection-public-key", action="append", required=True
    )
    readiness.add_argument("--baseline-context-manifest")
    readiness.add_argument("--trusted-selector-public-key", action="append")
    readiness.add_argument("--output", required=True)
    development = subparsers.add_parser("measure-development")
    development.add_argument("corpus")
    development.add_argument("confirmatory_selection")
    development.add_argument(
        "--maximum-selected-files", type=int, default=2
    )
    development.add_argument("--output", required=True)
    shortlist = subparsers.add_parser("shortlist-workspaces")
    shortlist.add_argument("packet")
    shortlist.add_argument("workspace_root")
    shortlist.add_argument(
        "--maximum-selected-files", type=int, default=2
    )
    shortlist.add_argument(
        "--trusted-selection-public-key", action="append", required=True
    )
    shortlist.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_context_selection_packet(
            _load_json(args.selection),
            repo_roots=_load_json(args.repo_map),
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            selection_provenance=_load_json(args.selection_provenance),
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
        )
    elif args.command == "apply":
        result = apply_selector_response(
            _load_json(args.packet),
            _load_json(args.response),
            selection=_load_json(args.selection),
            repo_roots=_load_json(args.repo_map),
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            trusted_selector_public_keys=set(args.trusted_selector_public_key),
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
        )
    elif args.command == "readiness":
        repo_roots = _load_json(args.repo_map)
        baseline_context_manifest = (
            _load_json(args.baseline_context_manifest)
            if args.baseline_context_manifest
            else None
        )
        result = prepare_local_transfer_readiness(
            _load_json(args.packet),
            selection=_load_json(args.selection),
            repo_roots=repo_roots,
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
            execution_budget=DEFAULT_EXECUTION_BUDGET,
            baseline_context_manifest=baseline_context_manifest,
            trusted_selector_public_keys=set(
                args.trusted_selector_public_key or []
            ),
        )
        _write_json_atomic(
            validate_sensitive_output_path(args.output, repo_roots=repo_roots),
            result,
        )
        return 0
    elif args.command == "measure-development":
        result = measure_retrieval_development_corpus(
            _load_json(args.corpus),
            confirmatory_selection=_load_json(args.confirmatory_selection),
            maximum_selected_files=args.maximum_selected_files,
        )
    elif args.command == "shortlist-workspaces":
        result = build_baseline_workspace_shortlists(
            _load_json(args.packet),
            workspace_root=args.workspace_root,
            maximum_selected_files=args.maximum_selected_files,
            trusted_selection_public_keys=set(
                args.trusted_selection_public_key
            ),
        )
    else:
        materialize_baseline_workspaces(
            _load_json(args.packet),
            selection=_load_json(args.selection),
            repo_roots=_load_json(args.repo_map),
            trusted_prior_commits=_load_commit_registry(args.prior_registry),
            output_root=args.output_root,
            trusted_selection_public_keys=set(args.trusted_selection_public_key),
        )
        return 0
    _write_json_atomic(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
