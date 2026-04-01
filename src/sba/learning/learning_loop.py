"""
Step1～6 オーケストレーター: Learning Loop メインエンジン

設計根拠（自律学習ループ設定書 §1.2, §9）:
  - Step1～6を1サイクルとして順序実行
  - エラーハンドリング・ループインターバル管理
  - APScheduler からの呼び出し口となるエントリポイント
  - 各Step実行前にクールダウン・リソース確認

【修正履歴】
  - LearningCycleResult.logs のデフォルト値を None → field(default_factory=list) に変更
    （dataclass でデフォルト値なしフィールドの後に mutable default が来るエラーを回避）
  - typing から List を正しくインポートするよう修正
  - Step3 のスタブに TODO コメントを整備（Phase 5 前に Fetcher 統合が必要な箇所を明示）
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .gap_detector import GapDetector
from .knowledge_integrator import KnowledgeIntegrator
from .resource_finder import ResourceFinder
from .self_evaluator import SelfEvaluator
from ..subskill.classifier import SubSkillClassifier


logger = logging.getLogger(__name__)


@dataclass
class LearningCycleResult:
    """1サイクルの実行結果"""
    cycle_id:              str
    brain_id:              str
    start_time:            str
    end_time:              str
    step1_gap:             Optional[Dict] = None   # ギャップ検出結果
    step2_resources:       int            = 0      # 取得リソース数
    step3_chunks_stored:   int            = 0      # 格納チャンク数
    step5_contradictions:  int            = 0      # 検出矛盾数
    step6_overall_score:   float          = 0.0    # 最終スコア
    step6_level:           str            = ""     # 最終Lv
    error:                 Optional[str]  = None
    # 修正: mutable default は field(default_factory=...) を使う
    logs: List[str] = field(default_factory=list)


class LearningLoop:
    """
    自律学習ループのメインオーケストレーター。

    Step1 ギャップ検出
    ↓
    Step2 リソース探索
    ↓
    Step3 データ取得・整理（各 Fetcher 呼び出し ← Phase 5 前に実装）
    ↓
    Step4 自己実験（Self-Experimentation Engine へ委譲）
    ↓
    Step5 知識統合
    ↓
    Step6 自己評価・優先度決定
    """

    DEFAULT_LOOP_INTERVAL_SECONDS = 3600   # 1時間
    DEFAULT_CYCLE_TIMEOUT_SECONDS = 1800   # 30分

    def __init__(
        self,
        brain_id:           str,
        brain_name:         str,
        active_brain_path:  Path,
        gap_detector:       Optional[GapDetector]        = None,
        resource_finder:    Optional[ResourceFinder]     = None,
        classifier:         Optional[SubSkillClassifier] = None,
        integrator:         Optional[KnowledgeIntegrator] = None,
        evaluator:          Optional[SelfEvaluator]      = None,
    ) -> None:
        """
        Initialize LearningLoop.

        Args:
            brain_id:          Brain ID
            brain_name:        Brain名
            active_brain_path: Brain ディレクトリパス
            各 Component インスタンス（None の場合は後から設定）
        """
        self.brain_id          = brain_id
        self.brain_name        = brain_name
        self.active_brain_path = Path(active_brain_path)

        # Components
        self.gap_detector   = gap_detector
        self.resource_finder = resource_finder
        self.classifier     = classifier
        self.integrator     = integrator
        self.evaluator      = evaluator

        # Configuration
        self.loop_interval = self.DEFAULT_LOOP_INTERVAL_SECONDS
        self.cycle_timeout = self.DEFAULT_CYCLE_TIMEOUT_SECONDS

        # State
        self.is_running = False
        self.last_cycle_result: Optional[LearningCycleResult] = None

        # Paths
        self.self_eval_path          = self.active_brain_path / "self_eval.json"
        self.subskill_manifest_path  = self.active_brain_path / "subskill_manifest.json"
        self.learning_log_path       = self.active_brain_path / "learning_log.jsonl"

    # ======================================================================
    # 1サイクル実行
    # ======================================================================

    async def run_single_cycle(self) -> LearningCycleResult:
        """
        1サイクルを実行（Step1～6）。

        Returns:
            LearningCycleResult
        """
        cycle_id = f"{self.brain_id}_{datetime.now().isoformat()}"
        result = LearningCycleResult(
            cycle_id   = cycle_id,
            brain_id   = self.brain_id,
            start_time = datetime.now().isoformat(),
            end_time   = "",
        )

        try:
            logger.info(f"Learning cycle started: {cycle_id}")

            # SubSkill定義を読み込み
            with open(self.subskill_manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # ------------------------------------------------------------------
            # Step1: ギャップ検出
            # ------------------------------------------------------------------
            if self.gap_detector:
                try:
                    gap_result = await self.gap_detector.detect_gap(
                        self.self_eval_path, manifest
                    )
                    result.step1_gap = asdict(gap_result)
                    result.logs.append(f"Step1: gap={gap_result.target_subskill}, score={gap_result.current_score:.2f}")
                    logger.info(f"Gap detected: {gap_result.target_subskill}")
                except Exception as e:
                    logger.error(f"Step1 failed: {str(e)}")
                    result.error = f"Step1 error: {str(e)}"
                    result.end_time = datetime.now().isoformat()
                    return result

            # ------------------------------------------------------------------
            # Step2: リソース探索
            # ------------------------------------------------------------------
            if self.resource_finder and result.step1_gap:
                try:
                    resources = await self.resource_finder.search_resources(
                        result.step1_gap["target_subskill"],
                        result.step1_gap["suggested_query"],
                    )
                    result.step2_resources = len(resources)
                    result.logs.append(f"Step2: resources={len(resources)}")
                    logger.info(f"Resources found: {len(resources)}")
                except Exception as e:
                    logger.error(f"Step2 failed: {str(e)}")
                    result.logs.append(f"Step2 error: {str(e)}")

            # ------------------------------------------------------------------
            # Step3: データ取得・整理
            # TODO (Phase 5 着手前に実装):
            #   resource_finder から返った ResourceCandidate を Fetcher に渡して
            #   実際にコンテンツを取得→チャンク化→KnowledgeStore に格納する。
            #   具体的には以下を実装する:
            #     - SourceType.WEB         → WebFetcher.fetch()
            #     - SourceType.ARXIV / PDF → PDFFetcher.fetch()
            #     - SourceType.YOUTUBE     → VideoFetcher.fetch()
            #     - SourceType.GITHUB      → CodeFetcher.fetch_github()
            #     - SourceType.STACKOVERFLOW → CodeFetcher.fetch_stackoverflow()
            #   各 Fetcher が返したテキストを Chunker で分割し、
            #   SubSkillClassifier で仕分けてから KnowledgeStore.store_chunk() する。
            #   設計書: 学習ソース・収集方針設定書 §3～§6
            # ------------------------------------------------------------------
            result.step3_chunks_stored = 0
            result.logs.append("Step3: stub (Fetcher integration pending)")

            # ------------------------------------------------------------------
            # Step4: 自己実験
            # 省略: Self-Experimentation Engine（Phase 5）へ委譲
            # ------------------------------------------------------------------

            # ------------------------------------------------------------------
            # Step5: 知識統合・矛盾検出
            # ------------------------------------------------------------------
            if self.integrator:
                try:
                    reconcile_result = await self.integrator.reconcile_knowledge_base(
                        new_chunks=[],  # Step3 が実装されたら実際のチャンクを渡す
                        brain_id=self.brain_id,
                    )
                    result.step5_contradictions = reconcile_result["contradictions_found"]
                    result.logs.append(f"Step5: contradictions={result.step5_contradictions}")
                    logger.info(f"Contradictions resolved: {result.step5_contradictions}")
                except Exception as e:
                    logger.error(f"Step5 failed: {str(e)}")
                    result.logs.append(f"Step5 error: {str(e)}")

            # ------------------------------------------------------------------
            # Step6: 自己評価・Lv更新
            # ------------------------------------------------------------------
            if self.evaluator:
                try:
                    eval_result = await self.evaluator.evaluate_all_subskills(manifest)
                    result.step6_overall_score = eval_result.overall_score
                    result.step6_level         = eval_result.level.value

                    # self_eval.json 更新
                    await self.evaluator.update_self_evaluation_file(
                        self.self_eval_path, eval_result
                    )
                    result.logs.append(
                        f"Step6: score={eval_result.overall_score:.2f}, level={eval_result.level.value}"
                    )
                    logger.info(f"Evaluation complete: {eval_result.level.value}")
                except Exception as e:
                    logger.error(f"Step6 failed: {str(e)}")
                    result.logs.append(f"Step6 error: {str(e)}")

            result.end_time = datetime.now().isoformat()
            self.last_cycle_result = result
            logger.info(f"Learning cycle completed: {cycle_id}")
            return result

        except Exception as e:
            logger.error(f"Cycle failed: {str(e)}")
            result.error    = str(e)
            result.end_time = datetime.now().isoformat()
            return result

    # ======================================================================
    # 連続実行
    # ======================================================================

    async def run_continuous(
        self,
        max_cycles:        Optional[int]                            = None,
        on_cycle_complete: Optional[Callable[[LearningCycleResult], None]] = None,
    ) -> None:
        """
        連続実行ループ（APScheduler から呼び出し用）。

        Args:
            max_cycles:        最大サイクル数（None=無制限）
            on_cycle_complete: 各サイクル完了時コールバック
        """
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

                    self._log_cycle_result(result)

                except asyncio.TimeoutError:
                    logger.error(f"Cycle timeout after {self.cycle_timeout}s")

                await asyncio.sleep(self.loop_interval)

        except Exception as e:
            logger.error(f"Continuous loop failed: {str(e)}")
        finally:
            self.is_running = False

    def stop(self) -> None:
        """ループを停止"""
        self.is_running = False

    def _log_cycle_result(self, result: LearningCycleResult) -> None:
        """サイクル結果をログに記録（JSONL形式）"""
        try:
            with open(self.learning_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to log cycle result: {str(e)}")

    async def get_status(self) -> Dict:
        """現在の学習ループ状態を取得"""
        return {
            "brain_id":              self.brain_id,
            "brain_name":            self.brain_name,
            "is_running":            self.is_running,
            "last_cycle_result":     asdict(self.last_cycle_result) if self.last_cycle_result else None,
            "loop_interval_seconds": self.loop_interval,
        }
