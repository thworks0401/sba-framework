"""
Step1～6 オーケストレーター: Learning Loop メインエンジン

設計根拠（自律学習ループ設定書 §1.2, §9）:
  - Step1～6を1サイクルとして順序実行
  - エラーハンドリング・ループインターバル管理
  - APScheduler からの呼び出し口となるエントリポイント
  - 各Step実行前にクールダウン・リソース確認
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .gap_detector import GapDetector
from .knowledge_integrator import KnowledgeIntegrator
from .resource_finder import ResourceCandidate, ResourceFinder, SourceType
from .self_evaluator import SelfEvaluator
from ..cost.rate_limiter import APIRateLimiter
from ..experiment.experiment_engine import ExperimentEngine, ExperimentPlan, ExperimentType
from ..experiment.experiment_runner import (
    ExperimentResult,
    ExperimentRunResult,
    ExperimentRunnerA,
    ExperimentRunnerB,
    ExperimentRunnerD,
)
from ..experiment.sandbox_exec import SandboxExecutor
from ..inference.tier3 import Tier3Engine
from ..sources.code_fetcher import CodeFetcher
from ..sources.pdf_fetcher import PDFFetcher
from ..sources.video_fetcher import VideoFetcher
from ..sources.web_fetcher import WebFetcher
from ..storage.knowledge_store import KnowledgeStore
from ..subskill.classifier import SubSkillClassifier
from ..utils.chunker import TextChunker
from ..utils.notifier import HumanReviewItem, SBANotifier
from ..utils.vram_guard import VRAMGuard, get_global_vram_guard


logger = logging.getLogger(__name__)


@dataclass
class LearningCycleResult:
    """1サイクルの実行結果"""

    cycle_id: str
    brain_id: str
    start_time: str
    end_time: str
    step1_gap: Optional[Dict] = None
    step2_resources: int = 0
    step3_chunks_stored: int = 0
    step4_experiment_type: str = ""
    step4_result: str = ""
    step4_score_change: float = 0.0
    step5_contradictions: int = 0
    step5_human_review_items: int = 0
    step6_overall_score: float = 0.0
    step6_level: str = ""
    error: Optional[str] = None
    logs: List[str] = field(default_factory=list)


class LearningLoop:
    """
    自律学習ループのメインオーケストレーター。
    """

    DEFAULT_LOOP_INTERVAL_SECONDS = 3600
    DEFAULT_CYCLE_TIMEOUT_SECONDS = 1800

    def __init__(
        self,
        brain_id: str,
        brain_name: str,
        active_brain_path: Path,
        gap_detector: Optional[GapDetector] = None,
        resource_finder: Optional[ResourceFinder] = None,
        classifier: Optional[SubSkillClassifier] = None,
        integrator: Optional[KnowledgeIntegrator] = None,
        knowledge_integrator: Optional[KnowledgeIntegrator] = None,
        evaluator: Optional[SelfEvaluator] = None,
        experiment_engine: Optional[ExperimentEngine] = None,
        knowledge_store: Optional[KnowledgeStore] = None,
        chunker: Optional[TextChunker] = None,
        web_fetcher: Optional[WebFetcher] = None,
        pdf_fetcher: Optional[PDFFetcher] = None,
        video_fetcher: Optional[VideoFetcher] = None,
        code_fetcher: Optional[CodeFetcher] = None,
        notifier: Optional[SBANotifier] = None,
        rate_limiter: Optional[APIRateLimiter] = None,
        tier3_engine: Optional[Tier3Engine] = None,
        vram_guard: Optional[VRAMGuard] = None,
    ) -> None:
        self.brain_id = brain_id
        self.brain_name = brain_name
        self.active_brain_path = Path(active_brain_path)

        self.gap_detector = gap_detector
        self.resource_finder = resource_finder
        self.classifier = classifier
        self.integrator = integrator or knowledge_integrator
        self.evaluator = evaluator
        self.experiment_engine = experiment_engine
        self.knowledge_store = (
            knowledge_store
            or getattr(self.integrator, "knowledge_store", None)
            or getattr(self.resource_finder, "knowledge_store", None)
        )

        self.chunker = chunker or TextChunker()
        self.web_fetcher = web_fetcher
        self.pdf_fetcher = pdf_fetcher
        self.video_fetcher = video_fetcher
        self.code_fetcher = code_fetcher
        self.notifier = notifier
        self.rate_limiter = rate_limiter
        self.tier3_engine = tier3_engine
        self.vram_guard = vram_guard or get_global_vram_guard()

        self.loop_interval = self.DEFAULT_LOOP_INTERVAL_SECONDS
        self.cycle_timeout = self.DEFAULT_CYCLE_TIMEOUT_SECONDS

        self.is_running = False
        self.last_cycle_result: Optional[LearningCycleResult] = None

        self.self_eval_path = self.active_brain_path / "self_eval.json"
        self.subskill_manifest_path = self.active_brain_path / "subskill_manifest.json"
        self.learning_log_path = self.active_brain_path / "learning_log.jsonl"

    async def run_single_cycle(self) -> LearningCycleResult:
        """1サイクルを実行（Step1～6）。"""
        cycle_id = f"{self.brain_id}_{datetime.now().isoformat()}"
        result = LearningCycleResult(
            cycle_id=cycle_id,
            brain_id=self.brain_id,
            start_time=datetime.now().isoformat(),
            end_time="",
        )

        resources: List[ResourceCandidate] = []
        stored_chunks: List[Dict[str, Any]] = []

        try:
            logger.info("Learning cycle started: %s", cycle_id)
            manifest = self._load_manifest()
            self._ensure_subskill_nodes(manifest)

            if self.gap_detector:
                try:
                    gap_result = await self.gap_detector.detect_gap(
                        self.self_eval_path,
                        manifest,
                    )
                    result.step1_gap = asdict(gap_result)
                    result.logs.append(
                        f"Step1: gap={gap_result.target_subskill}, score={gap_result.current_score:.2f}"
                    )
                except Exception as e:
                    logger.error("Step1 failed: %s", e)
                    result.error = f"Step1 error: {e}"
                    result.end_time = datetime.now().isoformat()
                    return result

            if self.resource_finder and result.step1_gap:
                try:
                    # Add timeout to prevent hanging on network requests
                    resources = await asyncio.wait_for(
                        self.resource_finder.search_resources(
                            result.step1_gap["target_subskill"],
                            result.step1_gap["suggested_query"],
                            is_tech_brain=self._is_tech_brain(manifest),
                        ),
                        timeout=5.0,  # 5 second timeout for resource search
                    )
                    ranker = getattr(self.resource_finder, "rank_candidates", None)
                    if callable(ranker):
                        ranked_resources = ranker(resources)
                        if isinstance(ranked_resources, list):
                            resources = ranked_resources
                    result.step2_resources = len(resources)
                    result.logs.append(f"Step2: resources={len(resources)}")
                except asyncio.TimeoutError:
                    # Resource search timeout - continue with no resources
                    logger.warning("Step2 resource search timeout after 5s - continuing")
                    result.logs.append("Step2: resource search timeout (5s)")
                except Exception as e:
                    logger.error("Step2 failed: %s", e)
                    result.logs.append(f"Step2 error: {e}")

            try:
                stored_chunks = await self._execute_step3(
                    resources=resources,
                    manifest=manifest,
                    preferred_subskill=(result.step1_gap or {}).get("target_subskill"),
                )
                result.step3_chunks_stored = len(stored_chunks)
                result.logs.append(f"Step3: chunks_stored={len(stored_chunks)}")
            except Exception as e:
                logger.error("Step3 failed: %s", e)
                result.logs.append(f"Step3 error: {e}")

            if self.experiment_engine and result.step1_gap:
                try:
                    plan, experiment_result = await self._run_experiment_for_gap(
                        result.step1_gap
                    )
                    if plan:
                        result.step4_experiment_type = self._get_plan_type_value(plan)
                        result.logs.append(
                            f"Step4: experiment designed ({result.step4_experiment_type or 'unknown'})"
                        )
                    else:
                        result.logs.append("Step4: experiment design skipped")

                    if experiment_result:
                        result.step4_result = experiment_result.result.value
                        result.step4_score_change = experiment_result.score_change
                        result.logs.append(
                            f"Step4: result={experiment_result.result.value}, "
                            f"delta={experiment_result.score_change:+.2f}"
                        )
                except Exception as e:
                    logger.error("Step4 failed: %s", e)
                    result.logs.append(f"Step4 error: {e}")
            else:
                result.logs.append("Step4: experiment engine not available")

            if self.integrator:
                try:
                    reconcile_result = await self.integrator.reconcile_knowledge_base(
                        new_chunks=stored_chunks,
                        brain_id=self.brain_id,
                    )
                    result.step5_contradictions = reconcile_result.get("contradictions_found", 0)
                    result.step5_human_review_items = reconcile_result.get("human_review_items", 0)
                    result.logs.append(
                        f"Step5: contradictions={result.step5_contradictions}, "
                        f"human_review={result.step5_human_review_items}"
                    )
                    self._log_human_review_items(reconcile_result)
                except Exception as e:
                    logger.error("Step5 failed: %s", e)
                    result.logs.append(f"Step5 error: {e}")

            if self.evaluator:
                try:
                    eval_result = await self.evaluator.evaluate_all_subskills(manifest)
                    result.step6_overall_score = eval_result.overall_score
                    result.step6_level = eval_result.level.value
                    await self.evaluator.update_self_evaluation_file(
                        self.self_eval_path,
                        eval_result,
                    )
                    result.logs.append(
                        f"Step6: score={eval_result.overall_score:.2f}, "
                        f"level={eval_result.level.value}"
                    )
                except Exception as e:
                    logger.error("Step6 failed: %s", e)
                    result.logs.append(f"Step6 error: {e}")

            result.end_time = datetime.now().isoformat()
            self.last_cycle_result = result
            self._log_cycle_result(result)
            logger.info("Learning cycle completed: %s", cycle_id)
            return result

        except Exception as e:
            logger.error("Cycle failed: %s", e)
            result.error = str(e)
            result.end_time = datetime.now().isoformat()
            self.last_cycle_result = result
            return result

    async def run_targeted_experiment(
        self,
        preferred_type: Optional[ExperimentType] = None,
    ) -> Optional[ExperimentRunResult]:
        """弱点SubSkillに対して実験のみを単独実行する。"""
        if not self.experiment_engine or not self.gap_detector:
            return None

        manifest = self._load_manifest()
        gap_result = await self.gap_detector.detect_gap(self.self_eval_path, manifest)
        _, experiment_result = await self._run_experiment_for_gap(
            asdict(gap_result),
            preferred_type=preferred_type,
        )
        return experiment_result

    # ======================================================================
    # Step3 helpers
    # ======================================================================

    async def _execute_step3(
        self,
        resources: List[ResourceCandidate],
        manifest: Dict[str, Any],
        preferred_subskill: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self._resolve_knowledge_store()
        if not resources or not store:
            return []

        known_subskills = {
            skill.get("id")
            for skill in manifest.get("subskills", [])
            if skill.get("id")
        }
        stored_chunks: List[Dict[str, Any]] = []

        for resource in resources:
            payloads = await self._fetch_resource_payloads(resource)
            for payload in payloads:
                text_chunks = self._chunk_payload(resource.source_type, payload)
                for chunk_text in text_chunks:
                    chunk_text = chunk_text.strip()
                    if not chunk_text:
                        continue

                    primary_subskill = preferred_subskill or ""
                    secondary_subskills: List[str] = []

                    if self.classifier:
                        try:
                            classification = await self.classifier.classify(chunk_text)
                            if (
                                classification.primary_subskill in known_subskills
                                and not self.classifier.is_unclassified(
                                    classification.primary_subskill
                                )
                            ):
                                primary_subskill = classification.primary_subskill
                            secondary_subskills = [
                                sid
                                for sid in (classification.secondary_subskills or [])
                                if sid in known_subskills and sid != primary_subskill
                            ]
                        except Exception as e:
                            logger.warning("Step3 classification fallback: %s", e)

                    if not primary_subskill:
                        primary_subskill = preferred_subskill or "__unclassified__"
                        if primary_subskill == "__unclassified__":
                            store.ensure_subskill_node("__unclassified__", "__unclassified__")

                    trust_score = float(
                        payload.get(
                            "trust_score",
                            resource.trust_score or resource.initial_trust_score,
                        )
                    )
                    stored = store.store_chunk(
                        text=chunk_text,
                        primary_subskill=primary_subskill,
                        source_type=self._normalize_source_type(resource.source_type),
                        source_url=payload.get("source_url", resource.url),
                        trust_score=trust_score,
                        summary=payload.get("summary", "")[:500],
                        secondary_subskills=secondary_subskills,
                    )

                    if stored.get("duplicate_detected"):
                        continue

                    stored_chunks.append(
                        {
                            "id": stored.get("chunk_id"),
                            "text": chunk_text,
                            "subskill": primary_subskill,
                            "secondary_subskills": secondary_subskills,
                            "source_type": self._normalize_source_type(resource.source_type),
                            "source_url": payload.get("source_url", resource.url),
                            "trust_score": trust_score,
                            "summary": payload.get("summary", "")[:500],
                        }
                    )

        return stored_chunks

    async def _fetch_resource_payloads(
        self,
        resource: ResourceCandidate,
    ) -> List[Dict[str, Any]]:
        source_type = resource.source_type

        if source_type in (SourceType.WEB, SourceType.WIKIPEDIA):
            content = await self._ensure_web_fetcher().fetch_with_fallback(resource.url)
            if content.error or not content.content:
                return []
            return [
                {
                    "text": content.content,
                    "summary": content.title or resource.title,
                    "source_url": resource.url,
                    "trust_score": resource.trust_score or resource.initial_trust_score,
                }
            ]

        if source_type in (SourceType.ARXIV, SourceType.PDF):
            arxiv_id = self._extract_arxiv_id(resource.url)
            pdf = await self._ensure_pdf_fetcher().fetch_and_extract(
                arxiv_id=arxiv_id,
                pdf_url=resource.url,
                title=resource.title,
                summarize=(source_type == SourceType.PDF),
            )
            if pdf.error:
                return []

            full_text = pdf.full_text or pdf.abstract
            if not full_text and pdf.sections:
                full_text = "\n\n".join(pdf.sections.values())
            if not full_text:
                return []

            return [
                {
                    "text": full_text,
                    "summary": pdf.abstract or resource.title,
                    "source_url": pdf.source_url or resource.url,
                    "trust_score": resource.trust_score or resource.initial_trust_score,
                }
            ]

        if source_type == SourceType.YOUTUBE:
            video = await self._ensure_video_fetcher().fetch_video_content(resource.url)
            if video.error:
                return []
            return [
                {
                    "text": segment.text,
                    "summary": video.title or resource.title,
                    "source_url": resource.url,
                    "trust_score": resource.trust_score or resource.initial_trust_score,
                    "prechunked": True,
                }
                for segment in (video.segments or [])
                if segment.text.strip()
            ]

        if source_type == SourceType.GITHUB:
            repo_name = self._extract_github_repo_name(resource.url)
            if not repo_name:
                return []
            repo = await self._ensure_code_fetcher().fetch_repository_full_content(repo_name)
            if repo.error:
                return []

            payloads: List[Dict[str, Any]] = []
            if repo.readme_content:
                payloads.append(
                    {
                        "text": repo.readme_content,
                        "summary": repo.repo_name,
                        "source_url": repo.url,
                        "trust_score": resource.trust_score or resource.initial_trust_score,
                        "content_kind": "text",
                    }
                )
            for snippet in repo.code_snippets or []:
                payloads.append(
                    {
                        "text": snippet,
                        "summary": repo.repo_name,
                        "source_url": repo.url,
                        "trust_score": resource.trust_score or resource.initial_trust_score,
                        "content_kind": "code",
                    }
                )
            for issue in repo.issues or []:
                text = f"{issue.get('title', '')}\n\n{issue.get('body', '')}".strip()
                if text:
                    payloads.append(
                        {
                            "text": text,
                            "summary": issue.get("title", repo.repo_name),
                            "source_url": repo.url,
                            "trust_score": resource.trust_score or resource.initial_trust_score,
                            "content_kind": "text",
                        }
                    )
            return payloads

        if source_type == SourceType.STACKOVERFLOW:
            detail = await self._ensure_code_fetcher().stackoverflow.fetch_question_detail(
                resource.url
            )
            if not detail:
                return []

            text = (
                f"{detail.title}\n\nQuestion:\n{detail.question_body}\n\n"
                f"Accepted Answer:\n{detail.answer_body}"
            ).strip()
            if not text:
                return []

            return [
                {
                    "text": text,
                    "summary": detail.title,
                    "source_url": detail.url or resource.url,
                    "trust_score": resource.trust_score or resource.initial_trust_score,
                }
            ]

        return []

    def _chunk_payload(
        self,
        source_type: SourceType,
        payload: Dict[str, Any],
    ) -> List[str]:
        text = str(payload.get("text", "") or "").strip()
        if not text:
            return []

        if payload.get("prechunked"):
            return [text]

        if source_type == SourceType.GITHUB and payload.get("content_kind") == "code":
            chunks = self.chunker.chunk_code(text)
        else:
            chunks = self.chunker.chunk_text(text)

        return chunks or [text]

    # ======================================================================
    # Step4 helpers
    # ======================================================================

    async def _run_experiment_for_gap(
        self,
        gap_info: Dict[str, Any],
        preferred_type: Optional[ExperimentType] = None,
    ) -> Tuple[Optional[ExperimentPlan], Optional[ExperimentRunResult]]:
        if not self.experiment_engine:
            return None, None

        weak_subskill = gap_info.get("target_subskill", "")
        gap_desc = gap_info.get("suggested_query") or gap_info.get("gap_description") or weak_subskill
        current_score = float(gap_info.get("current_score", 0.0))

        plan = await self.experiment_engine.design_experiment(
            weak_subskill,
            gap_desc,
            current_score,
        )
        if not plan:
            return None, None

        if preferred_type and preferred_type != plan.experiment_type:
            procedure = await self.experiment_engine.generate_experiment_procedure(
                plan.hypothesis,
                preferred_type,
            )
            if procedure:
                plan = replace(
                    plan,
                    experiment_type=preferred_type,
                    procedure_prompt=procedure.get("procedure_prompt", plan.procedure_prompt),
                    expected_outcome=procedure.get("expected_outcome", plan.expected_outcome),
                    success_criteria=procedure.get("success_criteria", plan.success_criteria),
                )

        experiment_result = await self._execute_experiment_plan(plan)
        return plan, experiment_result

    async def _execute_experiment_plan(
        self,
        plan: ExperimentPlan,
    ) -> Optional[ExperimentRunResult]:
        engine = self.experiment_engine
        tier1 = getattr(engine, "tier1", None) if engine else None
        exp_repo = getattr(engine, "exp_repo", None) if engine else None
        if not engine or not tier1 or not exp_repo:
            return None

        if plan.experiment_type == ExperimentType.A:
            runner = ExperimentRunnerA(
                brain_id=self.brain_id,
                tier1=tier1,
                exp_repo=exp_repo,
                knowledge_store=self._resolve_knowledge_store(),
            )
            result = await runner.run(plan)
        elif plan.experiment_type == ExperimentType.B:
            runner = ExperimentRunnerB(
                brain_id=self.brain_id,
                tier1=tier1,
                exp_repo=exp_repo,
            )
            result = await runner.run(plan)
        elif plan.experiment_type == ExperimentType.C:
            runner = SandboxExecutor(
                brain_id=self.brain_id,
                tier3=self._ensure_tier3_engine(),
                exp_repo=exp_repo,
                vram_guard=self.vram_guard,
            )
            result = await runner.run(plan)
        else:
            runner = ExperimentRunnerD(
                brain_id=self.brain_id,
                tier1=tier1,
                exp_repo=exp_repo,
            )
            result = await runner.run(plan)

        self._record_experiment(plan, result)
        self._apply_experiment_score_change(plan.subskill, result.score_change)
        self._store_experiment_knowledge(plan, result)
        self._notify_experiment(plan, result)
        return result

    def _record_experiment(self, plan: ExperimentPlan, result: ExperimentRunResult) -> None:
        engine = self.experiment_engine
        if not engine or not engine.exp_repo:
            return

        try:
            engine.exp_repo.insert_experiment(
                exp_id=plan.experiment_id,
                brain_id=self.brain_id,
                subskill=plan.subskill,
                exp_type=plan.experiment_type.name,
                hypothesis=plan.hypothesis.text,
                plan=plan.procedure_prompt,
                input_data=plan.hypothesis.gap_description,
                output_data=result.output_text,
                result=result.result.name.upper(),
                analysis=result.analysis_text,
                delta_score=result.score_change,
                exec_ms=int(result.execution_time_seconds * 1000),
            )
        except Exception as e:
            logger.warning("Failed to persist experiment log: %s", e)

    def _apply_experiment_score_change(self, subskill_id: str, delta: float) -> None:
        if not subskill_id or delta == 0.0:
            return

        try:
            if self.self_eval_path.exists():
                data = json.loads(self.self_eval_path.read_text(encoding="utf-8"))
            else:
                data = {"scores": {}, "history": []}

            scores = data.get("scores")
            if not isinstance(scores, dict):
                scores = {
                    key: value
                    for key, value in data.items()
                    if isinstance(value, (int, float))
                }

            current = float(scores.get(subskill_id, 0.0))
            scores[subskill_id] = max(0.0, min(1.0, current + delta))
            data["scores"] = scores
            data.setdefault("history", []).append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "subskill": subskill_id,
                    "delta": delta,
                    "source": "experiment",
                }
            )
            self.self_eval_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to apply experiment score delta: %s", e)

    def _store_experiment_knowledge(
        self,
        plan: ExperimentPlan,
        result: ExperimentRunResult,
    ) -> None:
        store = self._resolve_knowledge_store()
        if not store or result.result == ExperimentResult.FAILURE:
            return

        text = (
            f"Experiment {plan.experiment_type.name} on {plan.subskill}\n"
            f"Hypothesis: {plan.hypothesis.text}\n"
            f"Expected: {plan.expected_outcome}\n"
            f"Output: {result.output_text[:2000]}\n"
            f"Analysis: {result.analysis_text[:1000]}"
        )

        try:
            stored = store.store_chunk(
                text=text,
                primary_subskill=plan.subskill,
                source_type="Experiment",
                source_url=plan.experiment_id,
                trust_score=0.75 if result.result == ExperimentResult.SUCCESS else 0.65,
                summary=plan.hypothesis.text[:300],
            )
            if stored.get("chunk_id"):
                result.related_knowledge_ids.append(stored["chunk_id"])
        except Exception as e:
            logger.warning("Failed to store experiment knowledge: %s", e)

    def _notify_experiment(
        self,
        plan: ExperimentPlan,
        result: ExperimentRunResult,
    ) -> None:
        if not self.notifier:
            return

        try:
            self.notifier.log_experiment_result(
                plan.experiment_id,
                plan.subskill,
                result.result.value,
                result.score_change,
                details={"type": plan.experiment_type.value},
            )
        except Exception:
            pass

    @staticmethod
    def _get_plan_type_value(plan: Any) -> str:
        experiment_type = getattr(plan, "experiment_type", None)
        if isinstance(experiment_type, ExperimentType):
            return experiment_type.value

        value = getattr(experiment_type, "value", "")
        return value if isinstance(value, str) else ""

    # ======================================================================
    # Runtime helpers
    # ======================================================================

    def _load_manifest(self) -> Dict[str, Any]:
        with open(self.subskill_manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ensure_subskill_nodes(self, manifest: Dict[str, Any]) -> None:
        store = self._resolve_knowledge_store()
        if not store:
            return

        for subskill in manifest.get("subskills", []):
            skill_id = subskill.get("id")
            if not skill_id:
                continue
            store.ensure_subskill_node(skill_id, subskill.get("display_name", skill_id))

    def _resolve_knowledge_store(self) -> Optional[KnowledgeStore]:
        if self.knowledge_store:
            return self.knowledge_store
        if self.integrator and getattr(self.integrator, "knowledge_store", None):
            self.knowledge_store = self.integrator.knowledge_store
        elif self.resource_finder and getattr(self.resource_finder, "knowledge_store", None):
            self.knowledge_store = self.resource_finder.knowledge_store
        return self.knowledge_store

    def _ensure_web_fetcher(self) -> WebFetcher:
        if self.web_fetcher is None:
            self.web_fetcher = getattr(self.resource_finder, "web_fetcher", None) or WebFetcher()
        return self.web_fetcher

    def _ensure_pdf_fetcher(self) -> PDFFetcher:
        if self.pdf_fetcher is None:
            self.pdf_fetcher = getattr(self.resource_finder, "pdf_fetcher", None) or PDFFetcher()
        return self.pdf_fetcher

    def _ensure_video_fetcher(self) -> VideoFetcher:
        if self.video_fetcher is None:
            self.video_fetcher = getattr(self.resource_finder, "video_fetcher", None) or VideoFetcher()
        return self.video_fetcher

    def _ensure_code_fetcher(self) -> CodeFetcher:
        if self.code_fetcher is None:
            self.code_fetcher = getattr(self.resource_finder, "code_fetcher", None) or CodeFetcher()
        return self.code_fetcher

    def _ensure_tier3_engine(self) -> Tier3Engine:
        if self.tier3_engine is None:
            self.tier3_engine = Tier3Engine()
        return self.tier3_engine

    def _log_human_review_items(self, reconcile_result: Dict[str, Any]) -> None:
        if not self.notifier:
            return

        items = reconcile_result.get("details", {}).get("human_review_items", [])
        for item in items:
            try:
                review_item = HumanReviewItem(
                    item_type="contradiction",
                    severity="high",
                    message=(
                        f"Knowledge contradiction requires review: "
                        f"{item.get('existing_id')} vs {item.get('new_id')}"
                    ),
                    context=item,
                )
                self.notifier.log_human_review_item(review_item)
            except Exception:
                pass

    @staticmethod
    def _normalize_source_type(source_type: SourceType | str) -> str:
        if isinstance(source_type, SourceType):
            return source_type.value
        return str(source_type)

    @staticmethod
    def _extract_arxiv_id(url: str) -> str:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url or "")
        if not match:
            return (url or "").rstrip("/").split("/")[-1].replace(".pdf", "")
        return match.group(1).replace(".pdf", "")

    @staticmethod
    def _extract_github_repo_name(url: str) -> Optional[str]:
        match = re.search(r"github\.com/([^/]+/[^/#?]+)", url or "")
        return match.group(1) if match else None

    @staticmethod
    def _is_tech_brain(manifest: Dict[str, Any]) -> bool:
        domain = str(manifest.get("domain", "")).lower()
        return any(keyword in domain for keyword in ("python", "tech", "code", "開発", "program"))

    # ======================================================================
    # 連続実行
    # ======================================================================

    async def run_continuous(
        self,
        max_cycles: Optional[int] = None,
        on_cycle_complete: Optional[Callable[[LearningCycleResult], None]] = None,
    ) -> None:
        """連続実行ループ（APScheduler から呼び出し用）。"""
        self.is_running = True
        cycle_count = 0

        try:
            while self.is_running and (max_cycles is None or cycle_count < max_cycles):
                try:
                    result = await asyncio.wait_for(
                        self.run_single_cycle(),
                        timeout=self.cycle_timeout,
                    )
                    cycle_count += 1

                    if on_cycle_complete:
                        on_cycle_complete(result)

                except asyncio.TimeoutError:
                    logger.error("Cycle timeout after %ss", self.cycle_timeout)

                await asyncio.sleep(self.loop_interval)

        except Exception as e:
            logger.error("Continuous loop failed: %s", e)
        finally:
            self.is_running = False

    def stop(self) -> None:
        """ループを停止"""
        self.is_running = False

    def _log_cycle_result(self, result: LearningCycleResult) -> None:
        """サイクル結果をログに記録（JSONL形式）"""
        try:
            with open(self.learning_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(result), ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to log cycle result: %s", e)

    async def get_status(self) -> Dict[str, Any]:
        """現在の学習ループ状態を取得"""
        return {
            "brain_id": self.brain_id,
            "brain_name": self.brain_name,
            "is_running": self.is_running,
            "last_cycle_result": asdict(self.last_cycle_result) if self.last_cycle_result else None,
            "loop_interval_seconds": self.loop_interval,
        }
