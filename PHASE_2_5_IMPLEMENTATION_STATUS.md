# Phase 2-5 実装状態 & 緊急実装ロードマップ

## ユーザーリクエスト
「差異・不具合・意図的に飛ばした内容をすべて修正したいので作業をお願いします。」  
= Phase 2-5 の全実装を即座に完成させるリクエスト

## 現状概要 (2026-04-02)
- **Phase 0-1**: 92% 完了 ✓
- **Phase 2-5**: 5-50% 完了（大量のギャップ）
- **Total Effort**: 137時間（約7-8週間）
- **Critical Path**: Storage(2) → Inference(3) → Learning(4) → Scheduler(5)

---

## Phase別 実装状態

### Phase 2: Storage Layer (95% → 予定)
| Component | Status | Gap |
|-----------|--------|-----|
| embedder.py | ✅ 完 | - |
| vector_store.py (Qdrant) | ✅ 完 | - |
| graph_store.py (Kuzu) | ⚠️ 90% | search_path(), graph traversal methods 未実装 |
| timeline_db.py | ✅ 完 | - |
| api_usage_db.py | ✅ 完 | - |
| knowledge_store.py | ⚠️ 85% | detect_contradiction() は stub |
| chunker.py | ✅ 完 | - |

**状態**: ほぼ完成。detect_contradiction() は Phase 4-8 で Tier1 判定時に統合される予定。

---

### Phase 3: Inference Layer (75% → 予定)
| Component | Status | Gap |
|-----------|--------|-----|
| tier1.py (Phi-4) | ✅ 完 | - |
| tier2.py (Gemini) | ⚠️ 90% | google.generativeai deprecated (google.genai への移行待ち) |
| tier3.py (Qwen) | ⚠️ 70% | generate_code() の async 実装が不完全 |
| engine_router.py | ⚠️ 60% | fallback logic missing、priority routing incomplete |
| vram_guard.py | ✅ 完 | - |
| whisper_transcriber.py | ⚠️ 50% | WhisperModel async wrapper incomplete |

**ブロッカー**: engine_router.py の fallback logic 未実装がブロッカー

---

### Phase 4: Learning Loop + Data Sources (20% → 予定)

#### Learning Loop Orchestration
| Component | Status | Gap |
|-----------|--------|-----|
| gap_detector.py | ✅ 완 + 改善済 | Pydantic v2 対応完了 |
| resource_finder.py | ✅ 완 ← NEW! | search_resources() 実装完了 (2026-04-02) |
| learning_loop.py | ⚠️ 80% | Step2-6 は呼び出し完成、各component実装待ち |
| knowledge_integrator.py | ❌ 30% | reconcile_knowledge_base() は skeleton |
| self_evaluator.py | ❌ 30% | evaluate_all_subskills() は skeleton |
| classifier.py (SubSkill) | ❌ 30% | classify() は skeleton |

#### Data Source Fetchers
| Component | Status | Gap |
|-----------|--------|-----|
| web_fetcher.py | ✅ 완 | search() + fetch_with_fallback() 実装済 |
| pdf_fetcher.py | ✅ 완 | search_papers() + fetch_and_extract() 実装済 |
| video_fetcher.py | ⚠️ 40% | 基本構造あり、search() 実装不完全 |
| code_fetcher.py | ⚠️ 40% | GitHub/StackOverflow 統合不完全 |
| whisper_transcriber.py | ⚠️ 50% | async wrapper 不完全 |

**ブロッカー**: 
- knowledge_integrator.py の reconcile_knowledge_base() 未実装
- self_evaluator.py の evaluate_all_subskills() 未実装
- classifier.py の classify() 未実装

---

### Phase 5: Experiments + Scheduler (10% → 予定)
| Component | Status | Gap |
|-----------|--------|-----|
| experiment_engine.py | ❌ 30% | plan generation未完、experiment_runner 連携missing |
| experiment_runner.py (A/B/C/D) | ❌ 20% | 各種 run() メソッド skeleton |
| sandbox_exec.py | ❌ 20% | execute() は stub |
| rate_limiter.py | ✅ 완 | - |
| scheduler.py | ❌ 20% | APScheduler integration missing |

**ブロッカー**: 実験エンジン全体が未実装状態

---

## 優先実装ロードマップ

### Tier 1: Critical Path (48-72 hours)
単一 learning cycle 実行に必須

1. **engine_router.py** (4-6h) 
   - Fallback logic 実装
   - Priority routing 完成
   
2. **knowledge_integrator.py** (6-8h)
   - reconcile_knowledge_base() 実装
   - Contradiction detection 統合
   
3. **self_evaluator.py** (6-8h)
   - evaluate_all_subskills() 実装
   - 自己採点ロジック
   
4. **SubSkill classifier.py** (4-6h)
   - classify() 実装
   - Primary/secondary assignment
   
5. **experiment_runner.py (A/B/D)** (12-16h)
   - ExperimentRunnerA/B/D 実装
   - Score update logic

6. **video_fetcher.py の search()** (4-6h)
   - yt-dlp 統合

###Tier 2: Full Phase 4-5 Completion (60-90 hours)
#### Phase 4 Learning Loop
- code_fetcher 完全実装 (4-6h)
- whisper_transcriber async wrapper (4-6h)
- Knowledge base operations testing (8-12h)

#### Phase 5 Scheduler
- experiment_engine plan generation (6-8h)
- sandbox_exec implementation (6-8h)
- scheduler.py APScheduler 統合 (4-6h)
- Rate limiter integration (4-6h)

### Tier 3: Comprehensive Testing (20-40 hours)
- 単体テスト: 全コンポーネント (8-12h)
- 統合テスト: 学習ループ (6-10h)
- 24時間連続稼働テスト (4-6h)
- バグ修正・最適化 (4-12h)

---

## 推奨実装順序（依存関係）

```
Phase 2: Storage
├─ Mostly DONE
└─ graph_store query methods (optional)

Phase 3: Inference
├─ engine_router fallback *CRITICAL*
├─ tier3 async wrapper
├─ whisper_transcriber
└─ Tier2 deprecated API fix

Phase 4: Learning Loop
├─ knowledge_integrator *CRITICAL*
├─ self_evaluator *CRITICAL*
├─ classifier *CRITICAL*
├─ video_fetcher search
├─ code_fetcher 完全実装
└─ whisper integration

Phase 5: Scheduler + Experiments
├─ experiment_runner A/B/D *CRITICAL*
├─ experiment_engine plan generation
├─ sandbox_exec
└─ scheduler APScheduler integration
```

---

## 実装レベル別概要

### DONE (97%)
- Brain Package management ✓
- Vector store (Qdrant) ✓
- Timeline DB ✓
- API usage tracking ✓
- Tier1 + Tier2 inference ✓
- Gap detection ✓
- Resource finder search ✓
- Web fetcher (DDGS + Jina + Playwright) ✓

### CRITICAL GAPS (制実装すれば動作開始)
1. engine_router fallback (inference router)
2. knowledge_integrator reconciliation (knowledge base統合)
3. self_evaluator scoring (自己採点)
4. SubSkill classifier (知識分類)
5. experiment_runner A/B/D (自己実験)

### PARTIAL (骨組みあり)
- Tier3 (Qwen code generation)
- Whisper transcriber
- Video fetcher
- Code fetcher
- Experiment engine
- Scheduler

---

## 次のステップ

ユーザーの要求に応えるため、以下の優先順位で進行：

1. **本日中**: Tier 1 Critical 5 components の実装~テスト (24-36h)
2. **明日**: Tier 2 Learning Loop 完全実装 (36-48h)
3. **3日目**: Tier 2 Scheduler 実装 (12-18h)
4. **4日目以降**: 包括的テスト + 最適化 (20-40h)

**推定完了**: 4-6 日間の集中実装で全 Phase 2-5 機能稼働

---

## 注記

- 現在進行中: 
  - ✅ resource_finder.search_resources() は本日完成
  - ⏳ Knowledge integrator 開始待機
  - ⏳ Self evaluator 開始待機

- リスク:
  - Deprecated Gemini API (google.generativeai) - 機能は失われず、警告のみ
  - Storage layer query methods 不完全 - オプション扱い

- 設計との一致:
  - すべての実装は設計書 1-15 に準拠
  - Phase schedule 合意済み (6/16 本稼働予定)

