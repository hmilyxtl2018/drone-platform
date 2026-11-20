"""Run the complete first-demo intelligence governance chain.

This script uses a temporary SQLite database by default, so it is safe to run
repeatedly. Pass --db to retain the evidence ledger for inspection.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

try:
    from intelligence_agent import IntelligenceAgent
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_agent import IntelligenceAgent
    from .intelligence_store import IntelligenceStore


def review(agent: IntelligenceAgent, candidate: dict, action: str, reason: str, **extra) -> dict:
    payload = {
        "action": action,
        "reviewer": "operator.demo",
        "reason": reason,
        "expectedRevision": candidate["revision"],
        **extra,
    }
    return agent.review(candidate["candidateId"], payload)


def run(db: Path) -> dict:
    agent = IntelligenceAgent(IntelligenceStore(db))
    first = agent.ingest({
        "title": "知识库运行规范更新",
        "sourceUri": "https://example.test/runbook",
        "sourceVersion": "2026.09",
        "accessBasis": "public",
        "sourceReliability": "primary",
        "body": "文档归档 | 保留期限 | 30 天\n运行事件 | 审计方式 | append-only\n人工审核 | 发布条件 | 逐条确认",
    })
    ordered = sorted(first["candidates"], key=lambda item: item["evidence"]["lineStart"])
    old_retention, audit, rejected = ordered
    review(agent, audit, "edit", "补充术语后保留原文证据", editedStatement="运行事件的审计方式为 append-only。")
    audit = agent.store.get_candidate(audit["candidateId"])
    review(agent, audit, "accept", "人工确认修改后的声明")
    review(agent, old_retention, "accept", "初步确认，等待后续来源比较")
    review(agent, rejected, "reject", "证据范围不足，暂不纳入知识版本")

    newer = agent.ingest({
        "title": "归档规范修订",
        "sourceUri": "https://example.test/runbook",
        "sourceVersion": "2026.10",
        "accessBasis": "public",
        "sourceReliability": "primary",
        "body": "文档归档 | 保留期限 | 60 天",
    })
    new_retention = newer["candidates"][0]
    review(agent, new_retention, "accept", "新版本来源明确，替代旧值", resolveConflict="replace-existing")
    version = agent.publish({
        "reviewer": "operator.demo",
        "candidateIds": [audit["candidateId"], new_retention["candidateId"]],
        "title": "情报采集 Agent 首个知识版本",
    })
    state = agent.state()
    return {
        "documentIds": [first["document"]["documentId"], newer["document"]["documentId"]],
        "candidateStatuses": {c["candidateId"]: c["status"] for c in state["candidates"]},
        "publishedVersion": version["version"],
        "publishedHash": version["hash"],
        "eventTypes": [event["eventType"] for event in state["events"]],
        "auditEventCount": len(state["events"]),
        "summary": state["summary"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the intelligence Agent evidence-governance demo")
    parser.add_argument("--db", type=Path, help="SQLite path to retain the demo ledger")
    args = parser.parse_args()
    if args.db:
        print(json.dumps(run(args.db), ensure_ascii=False, indent=2))
        return
    with tempfile.TemporaryDirectory(prefix="crk-intelligence-demo-") as directory:
        print(json.dumps(run(Path(directory) / "intelligence.sqlite"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
