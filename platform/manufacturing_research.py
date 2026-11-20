"""Targeted public-source collection and evidence-gated manufacturing extraction."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound
    from manufacturing_contract import assert_valid
    from manufacturing_store import ManufacturingStore
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound
    from .manufacturing_contract import assert_valid
    from .manufacturing_store import ManufacturingStore


class DeepSeekManufacturingExtractor:
    """Small adapter around the shared DeepSeek transport."""

    def __init__(self, ontology_agent):
        trend_provider = getattr(ontology_agent, "trend_provider", None)
        self.provider = getattr(trend_provider, "claim_provider", None)

    @property
    def configured(self) -> bool:
        return bool(self.provider is not None and getattr(self.provider, "configured", False))

    def extract(self, entity: dict[str, Any], sources: list[dict[str, Any]]) -> Any:
        if not self.configured:
            raise RuntimeError("DeepSeek is not configured")
        method = getattr(self.provider, "extract_manufacturing", None)
        if not callable(method):
            raise RuntimeError("DeepSeek manufacturing extraction is unavailable")
        return method(entity, sources)


class ManufacturingResearchWorkflow:
    def __init__(self, ontology_agent, extractor=None):
        self.agent = ontology_agent
        self.store = ontology_agent.store
        self.queue = ontology_agent.queue
        self.manufacturing = ManufacturingStore(ontology_agent)
        self.extractor = extractor if extractor is not None else DeepSeekManufacturingExtractor(ontology_agent)

    def _current_industrial_job(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        jobs = self.queue.list(entity_id=entity["entityId"])
        with self.store._connect() as con:
            row = con.execute(
                "SELECT entity_revision_id FROM ontology_entity_revision WHERE entity_id=? AND revision=?",
                (entity["entityId"], entity["currentRevision"]),
            ).fetchone()
        revision_id = row[0] if row else None
        return next((job for job in jobs if job.get("targetType") == "industrial" and job.get("entityRevisionId") == revision_id), None)

    def _snapshot_count(self, verification_id: str | None) -> int:
        if not verification_id:
            return 0
        with self.store._connect() as con:
            return int(con.execute(
                "SELECT COUNT(*) FROM ontology_collected_source_snapshot WHERE verification_id=?",
                (verification_id,),
            ).fetchone()[0])

    @staticmethod
    def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if not job:
            return None
        result = job.get("result") or {}
        return {
            "verificationId": job.get("verificationId"),
            "entityRevisionId": job.get("entityRevisionId"),
            "targetType": job.get("targetType"),
            "status": job.get("status"),
            "query": job.get("query"),
            "sourceScopeIds": job.get("sourceScopeIds") or [],
            "retainedItems": len(result.get("items") or []),
            "snapshotIds": [item.get("snapshotId") for item in (result.get("items") or []) if item.get("snapshotId")],
            "error": job.get("error"),
            "completedAt": job.get("completedAt"),
        }

    def status(self, entity_id: str) -> dict[str, Any]:
        entity, _ = self.agent._entity_document(entity_id)
        job = self._current_industrial_job(entity)
        return {
            "entity": entity,
            "verification": self._job_summary(job),
            "snapshotCount": self._snapshot_count(job.get("verificationId") if job else None),
            "collector": {
                "provider": "firecrawl-search",
                "configured": bool(getattr(self.queue.provider, "configured", False)),
            },
            "extractor": {
                "provider": "deepseek",
                "model": getattr(getattr(self.extractor, "provider", None), "model", None),
                "configured": bool(getattr(self.extractor, "configured", False)),
            },
        }

    def collect(self, entity_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        force = payload.get("force", False)
        if type(force) is not bool:
            raise ValueError("force must be a boolean")
        entity, _ = self.agent._entity_document(entity_id)
        self.queue.plan(entity["entityId"], force=force, target_types={"industrial"})
        job = self._current_industrial_job(entity)
        if job is None:
            raise IntelligenceConflict("industrial verification job could not be planned")
        cached = job.get("status") in {"needs-review", "completed"} and self._snapshot_count(job["verificationId"]) > 0
        if job.get("status") == "running":
            raise IntelligenceConflict("industrial verification job is already running")
        result = job if cached and not force else self.queue.execute(job["verificationId"])
        items = (result.get("result") or {}).get("items") or []
        return {
            "ok": True,
            "status": result.get("status"),
            "entity": entity,
            "verification": self._job_summary(result),
            "retainedItems": len(items),
            "snapshotCount": self._snapshot_count(result.get("verificationId")),
            "cached": bool(cached and not force),
        }

    @staticmethod
    def _identity_key(value: Any) -> str:
        # Fold case and common Latin diacritics so aliases such as
        # “Akıncı” and “AKINCI” bind to the same equipment entity while
        # retaining non-Latin names used by the ontology.
        text = str(value or "").replace("ı", "i").replace("İ", "I").replace("ß", "ss")
        text = unicodedata.normalize("NFKD", text).casefold()
        text = "".join(char for char in text if not unicodedata.combining(char))
        return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()

    def _entity_names(self, entity: dict[str, Any]) -> list[str]:
        names = [entity.get("preferredName"), entity.get("englishName")]
        with self.store._connect() as con:
            names.extend(row[0] for row in con.execute(
                "SELECT alias FROM ontology_entity_alias WHERE entity_id=? ORDER BY confidence DESC,alias",
                (entity["entityId"],),
            ).fetchall())
        return list(dict.fromkeys(str(name).strip() for name in names if str(name or "").strip()))

    def _other_model_names(self, entity: dict[str, Any], target_names: list[str]) -> list[str]:
        with self.store._connect() as con:
            rows = con.execute(
                "SELECT entity_id,preferred_name,english_name FROM ontology_entity "
                "WHERE entity_type='EquipmentModel' AND entity_id!=?",
                (entity["entityId"],),
            ).fetchall()
            names = []
            for row in rows:
                names.extend((row["preferred_name"], row["english_name"]))
                names.extend(alias[0] for alias in con.execute(
                    "SELECT alias FROM ontology_entity_alias WHERE entity_id=?",
                    (row["entity_id"],),
                ).fetchall())
        target_keys = {self._identity_key(name) for name in target_names}
        return list(dict.fromkeys(
            str(name).strip() for name in names
            if str(name or "").strip() and len(self._identity_key(name)) >= 4
            and self._identity_key(name) not in target_keys
        ))

    def _contains_name(self, text: str, name: str) -> bool:
        text_key = f" {self._identity_key(text)} "
        name_key = self._identity_key(name)
        return bool(name_key and f" {name_key} " in text_key)

    def _saved_sources(self, entity: dict[str, Any], job: dict[str, Any]) -> list[dict[str, Any]]:
        result_items = (job.get("result") or {}).get("items") or []
        snapshot_ids = [str(item.get("snapshotId") or "") for item in result_items if item.get("snapshotId")]
        if not snapshot_ids:
            return []
        with self.store._connect() as con:
            placeholders = ",".join("?" for _ in snapshot_ids)
            rows = con.execute(
                "SELECT snapshot_id,content_hash,item_json FROM ontology_collected_source_snapshot "
                f"WHERE verification_id=? AND entity_revision_id=? AND snapshot_id IN ({placeholders})",
                (job["verificationId"], job["entityRevisionId"], *snapshot_ids),
            ).fetchall()
        by_id = {row["snapshot_id"]: row for row in rows}
        sources = []
        for snapshot_id in snapshot_ids[:16]:
            row = by_id.get(snapshot_id)
            if row is None:
                continue
            item = json.loads(row["item_json"])
            body = str(item.get("body") or "")
            source_uri = str(item.get("sourceUri") or item.get("itemUri") or "").strip()
            if not body.strip() or not source_uri:
                continue
            sources.append({
                "snapshotId": row["snapshot_id"],
                "contentHash": row["content_hash"],
                "sourceUri": source_uri,
                "itemUri": str(item.get("itemUri") or source_uri),
                "title": str(item.get("title") or ""),
                "publishedAt": item.get("publishedAt"),
                "sourceVersion": item.get("sourceVersion"),
                "body": body,
                "item": item,
            })
        return sources

    def _bind_and_verify_evidence(self, document: dict[str, Any], entity: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise ValueError("extraction result must be a JSON object")
        # Validate the root/entity envelope before isolating observations. A
        # malformed root cannot be safely salvaged, while a single malformed
        # observation can be rejected without discarding valid siblings.
        root = dict(document)
        root["observations"] = []
        assert_valid(root)
        extracted_entity = document["entity"]
        entity_names = self._entity_names(entity)
        accepted_names = {self._identity_key(name) for name in entity_names}
        other_model_names = self._other_model_names(entity, entity_names)
        if extracted_entity["entityId"] != entity["entityId"]:
            raise ValueError("extraction entityId does not match requested entity")
        if self._identity_key(extracted_entity["name"]) not in accepted_names:
            raise ValueError("extraction entity name does not match requested entity")

        bound = json.loads(json.dumps(document, ensure_ascii=False))
        valid_observations = []
        rejected = []
        for index, observation in enumerate(bound.get("observations") or []):
            try:
                if not isinstance(observation, dict):
                    raise ValueError("observation must be an object")
                evidence = observation.get("evidence")
                if not isinstance(evidence, dict):
                    raise ValueError("observation evidence is missing")
                quote = str(evidence.get("quote") or "")
                requested_snapshot = str(evidence.get("snapshotId") or "").strip()
                if not requested_snapshot:
                    raise ValueError("observation must provide snapshotId")
                source_uri = str(evidence.get("sourceUri") or "").strip()
                candidates = [source for source in sources
                              if source["snapshotId"] == requested_snapshot
                              and quote and quote in source["body"]]
                if source_uri:
                    candidates = [source for source in candidates
                                  if source_uri in {source["sourceUri"], source["itemUri"]}]
                if evidence.get("contentHash"):
                    candidates = [source for source in candidates if source["contentHash"] == evidence["contentHash"]]
                if len(candidates) != 1:
                    raise ValueError("observation source snapshot or verbatim quote did not match saved evidence")
                source = candidates[0]
                source_subject = str(evidence.get("sourceSubject") or "").strip()
                if source_subject and self._identity_key(source_subject) not in accepted_names:
                    raise ValueError("observation source subject does not match requested entity")
                if any(self._contains_name(quote, name) for name in other_model_names):
                    raise ValueError("observation quote explicitly names a different equipment model")
                if not any(self._contains_name(quote, name) or self._contains_name(source["title"], name) for name in entity_names):
                    raise ValueError("observation quote and source title do not identify the requested model")
                evidence.update({
                    "sourceUri": source["sourceUri"],
                    "sourceTitle": source["title"] or None,
                    "publishedAt": source["publishedAt"],
                    "sourceVersion": source["sourceVersion"],
                    "sourceSubject": entity["preferredName"],
                    "snapshotId": source["snapshotId"],
                    "contentHash": source["contentHash"],
                })
                # Validate each observation independently after evidence has
                # been bound and backend fields have been filled.
                assert_valid({
                    "schemaVersion": bound["schemaVersion"],
                    "entity": bound["entity"],
                    "observations": [observation],
                    "gaps": [],
                })
                valid_observations.append(observation)
            except (KeyError, TypeError, ValueError) as exc:
                rejected.append(f"Observation {index + 1} rejected: {exc}")
        bound["observations"] = valid_observations
        gaps = [str(item) for item in (bound.get("gaps") or []) if isinstance(item, str) and item.strip()]
        bound["gaps"] = gaps + rejected
        return bound

    def extract(self, entity_id: str) -> dict[str, Any]:
        entity, _ = self.agent._entity_document(entity_id)
        job = self._current_industrial_job(entity)
        if not job or job.get("status") not in {"needs-review", "completed"}:
            return {"ok": True, "status": "no-evidence", "entity": entity, "candidateCount": 0, "candidates": []}
        sources = self._saved_sources(entity, job)
        if not sources:
            return {"ok": True, "status": "no-evidence", "entity": entity, "candidateCount": 0, "candidates": []}
        if not bool(getattr(self.extractor, "configured", False)):
            return {"ok": True, "status": "model-unavailable", "entity": entity, "candidateCount": 0, "candidates": [], "snapshotCount": len(sources)}
        # Keep model context focused on snapshots that explicitly identify the
        # requested equipment.  Broad search results often contain comparison
        # rows for other UAVs; sending those rows to the model increases false
        # bindings while adding no usable evidence for this entity.
        target_names = self._entity_names(entity)
        focused_sources = [
            source for source in sources
            if any(self._contains_name(source.get("title", ""), name) for name in target_names)
        ]
        # A few official pages use a generic title but identify the model in
        # the first paragraph. Keep those only when no title-qualified source
        # exists, avoiding a mixed comparison corpus.
        if not focused_sources:
            focused_sources = [
                source for source in sources
                if any(self._contains_name(source.get("body", ""), name) for name in target_names)
            ]
        model_sources = [{key: source[key] for key in ("snapshotId", "contentHash", "sourceUri", "title", "publishedAt", "sourceVersion", "body")} for source in focused_sources]
        try:
            document = self.extractor.extract(entity, model_sources)
        except Exception:
            return {"ok": True, "status": "extraction-failed", "errorCode": "model-call-failed", "entity": entity, "candidateCount": 0, "candidates": [], "snapshotCount": len(sources)}
        bound = self._bind_and_verify_evidence(document, entity, sources)
        result = self.manufacturing.ingest_extraction(bound, expected_entity_id=entity["entityId"])
        factor_graph = None
        if result["candidateCount"]:
            try:
                from factor_graph import FactorGraphStore
            except ModuleNotFoundError:  # pragma: no cover
                from .factor_graph import FactorGraphStore
            factor_graph = FactorGraphStore(self.store).build_from_candidates(
                entity,
                result["candidates"],
                entity_revision_id=job.get("entityRevisionId"),
            )
        return {
            "ok": True,
            "status": "awaiting-human-review" if result["candidateCount"] else "no-candidates",
            "entity": entity,
            "snapshotCount": len(sources),
            "factorGraph": {
                "graphRunId": factor_graph["graphRunId"],
                "status": factor_graph["inference"]["status"],
                "summary": factor_graph["inference"].get("summary", {}),
                "conflicts": factor_graph["inference"].get("conflicts", []),
                "gaps": factor_graph["inference"].get("gaps", []),
            } if factor_graph else None,
            **result,
        }


__all__ = ["DeepSeekManufacturingExtractor", "ManufacturingResearchWorkflow"]
