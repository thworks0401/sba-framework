# SBA コードベース インベントリ - クイックサマリー

最終更新: 2026年4月2日

---

## 📊 実装状況スコアカード

```
総ファイル数: 30 Python
総実装行数: ~8,150 lines

ステータス分布:
├─ ✅ COMPLETE (13):  43% - 即利用可能
├─ 🟡 PARTIAL (17):   57% - 骨組み済み
└─ 🔴 SKELETON (0):    0% - 存在しない

Phase別完了度:
├─ Phase 0 (依存チェック): ✅ 100%
├─ Phase 1 (基盤層):       ✅ 92%
├─ Phase 2 (学習ループ):   🟡 40%
└─ Phase 3-5 (高度な機能): 🔴 5%
```

---

## 🟢 すぐに使える (COMPLETE)

| モジュール | ファイル | 説明 |
|-----------|--------|------|
| **storage** | api_usage_db | API 使用量・停止状態管理（SQLite） |
| | experiment_db | 実験ログ保存（SQLite） |
| | timeline_db | 学習タイムライン（SQLite） |
| | vector_store | Qdrant ベクトル検索・重複検出 |
| **inference** | tier1 | Phi-4:14B (Ollama local) |
| | tier2 | Gemini 2.5 Flash (Google API) |
| **scheduler** | scheduler | APScheduler タスク管理 |
| **subskill** | classifier | SubSkill 自動分類 |
| **cost** | rate_limiter | API レート制限 |
| **utils** | embedder | BAAI/bge-m3 シングルトン |
| | chunker | テキスト分割 (50トークンオーバーラップ) |
| | vram_guard | VRAM 排他制御 |

---

## 🟡 部分実装 (PARTIAL) - 要完成

### Storage 層

| ファイル | 完成度 | 未実装 |
|--------|-------|------|
| **graph_store.py** | 30% | Cypher ノード・エッジ操作、パス検索 |
| **knowledge_store.py** | 40% | query_hybrid、mark_deprecated、cleanup |

### Learning パイプライン

| ファイル | 完成度 | 未実装 |
|--------|-------|------|
| **learning_loop.py** | 30% | ループ制御、タイムアウト、リトライ、Step間連携 |
| **resource_finder.py** | 40% | ソース優先度切り替え、重複除外、信頼スコア |
| **knowledge_integrator.py** | 30% | 矛盾度計算、deprecated管理、human review判定 |
| **self_evaluator.py** | 40% | Lv昇進管理、スコア更新、連続通過カウント |

### Sources (リソース取得)

| ファイル | 完成度 | 未実装 |
|--------|-------|------|
| **web_fetcher.py** | 20% | DuckDuckGo検索、Jina実行、テキスト無効化 |
| **pdf_fetcher.py** | 20% | arXiv API実行、PDFMiner抽出、Tier2要約 |
| **code_fetcher.py** | 20% | GitHub API呼び出し、StackOverflow解析 |
| **video_fetcher.py** | 20% | yt-dlp字幕取得、Whisper統合 |
| **whisper_transcriber.py** | 20% | faster-whisper初期化、バッチ処理 |

### Experiment (自己実験)

| ファイル | 完成度 | 未実装 |
|--------|-------|------|
| **experiment_engine.py** | 30% | 仮説生成、実験種別選択、手順生成 |
| **experiment_runner.py** | 30% | A/B/D実行ロジック、採点、スコア更新 |
| **sandbox_exec.py** | 20% | コード実行、セキュリティチェック |

### Inference 層

| ファイル | 完成度 | 未実装 |
|--------|-------|------|
| **engine_router.py** | 50% | フォールバック、再試行、動的タイムアウト |
| **tier3.py** | 60% | コード品質評価、複数言語テンプレート |

### Utils

| ファイル | 完成度 | 未実装 |
|--------|-------|------|
| **notifier.py** | 30% | Desktop通知、ログローテーション、JSON Lines出力 |

---

## 🔴 Critical Gaps (実装必須)

### Tier 1: 今週中に完成させないと Phase 2 が始まらない

| Gap | ファイル | 影響 | 推定 |
|-----|--------|------|------|
| **graph_store Cypher 操作** | graph_store.py | Kuzu が死んでいる | 100行 |
| **knowledge_store 統合検索** | knowledge_store.py | ハイブリッド検索不可 | 150行 |
| **engine_router フォールバック** | engine_router.py | Tier1失敗時コスト増加 | 100行 |

### Tier 2: Phase 2 開始前（4月中旬）に完成

| Gap | ファイル | 影響 | 推定 |
|-----|--------|------|------|
| **Sources 全体 API実装** | web/pdf/code/video_fetcher | Step2 非機能 | 1,200行 |
| **experiment_runner 実装** | experiment_*.py | Step4 非機能 | 400行 |
| **learning_loop 制御** | learning_loop.py | ループ不安定 | 200行 |

---

## 📈 実装ロードマップ

```
現在 (4月初旬)
  ↓
┌─ Week 1-2: Storage 層完成 (graph_store + knowledge_store)
│  推定: 250行
│  テスト対象: test_graph_store.py, test_knowledge_store.py
│
├─ Week 1: engine_router 完璧化
│  推定: 100行
│  テスト対象: test_engine_router.py
│
├─ Week 3-4: Sources 実装開始
│  優先順: web_fetcher → pdf_fetcher → code_fetcher → video_fetcher
│  推定: 1,200行
│
├─ Week 5-6: experiment 実装
│  推定: 400行
│
└─ Week 7: learning_loop 統合
   推定: 200行
   テスト: test_phase2_full_cycle.py
   
→ Phase 2 完了 (6月初旬)
```

---

## 💾 依存グラフ

```
Phase 1 基盤 (COMPLETE ✅)
├─ storage: api_usage_db ✅, timeline_db ✅, experiment_db ✅
├─ inference: tier1 ✅, tier2 ✅
├─ utils: embedder ✅, chunker ✅, vram_guard ✅
└─ scheduler: scheduler ✅

        ↓ (依存)

Phase 2 パイプライン (PARTIAL 🟡)
├─ storage: graph_store 🔴, knowledge_store 🟡
│   └── 必須: Cypher操作, query_hybrid
├─ learning:
│   ├─ gap_detector ✅
│   ├─ learning_loop 🟡 (ループ制御待ち)
│   └─ resource_finder 🟡 (sources待ち)
├─ sources: 5 fetcher 🔴 (API実装待ち)
├─ experiment: A/B/D runner 🔴 (実行ロジック待ち)
└─ inference: engine_router 🟡 (フォールバック待ち)

        ↓ (依存)

Phase 3-5 高度機能 (🟡〜🔴)
├─ sandbox_exec (Type C コード実験)
├─ knowledge_integrator (矛盾検出)
├─ self_evaluator (Lv管理)
└─ notifier (Desktop通知)
```

---

## ✅ テスト状況

| テストファイル | 対象 | 状況 |
|------------|------|------|
| test_brain_package.py | Brain Package | ✅ Pass |
| test_single_cycle.py | 単一サイクル | 🟡 Partial |
| test_brain_manager.py | HotSwap | ✅ Pass |
| test_phase_1_final.py | Phase 1 統合 | ✅ Pass |
| test_phase2_storage.py | Storage層 | 🟡 graph_store失敗 |
| test_phase4.py | 実験実行 | 🔴 Sources未実装のため失敗 |

---

## 🎯 今すぐアクション

### 今週 (4月1-5日)

- [ ] `graph_store.py` に add_node/edge/query_path 追加
  - Kuzu Cypher API 確認
  - ノード・エッジ CRUD 実装
  - テストケース作成

- [ ] `knowledge_store.py` に query_hybrid() 実装
  - Vector + Graph 統合検索
  - リランキング ロジック

- [ ] `engine_router.py` にフォールバック実装
  - Tier2 再試行ロジック
  - 動的タイムアウト

### 来週 (4月8-12日)

- [ ] Web Fetcher 実装開始
  - DuckDuckGo + Jina Reader
  - テキスト無効化パイプライン

- [ ] Phase 1 統合テスト実行
  - `test_phase2_storage.py` クリア
  - エラー修正・最適化

---

## 📋 参考: 各モジュール責務

| モジュール | 責務 | 完成度 |
|-----------|------|-------|
| **storage** | KBの永続化 | ✅ 92% |
| **inference** | 推論エンジンルーティング | ✅ 85% |
| **learning** | 自律学習ループ制御 | 🟡 40% |
| **sources** | リソース集約 | 🔴 20% |
| **experiment** | 自己実験実行 | 🔴 20% |
| **scheduler** | タスク定期実行 | ✅ 100% |
| **subskill** | 知識分類 | ✅ 100% |
| **cost** | API配額管理 | ✅ 100% |
| **utils** | 基盤ユーティリティ | ✅ 95% |

---

## 📊 実装負荷予測

```
優先度1 (CRITICAL - 今週)    : 250行  (1w)
優先度2 (HIGH - 来週・再来週)  : 1,200行  (3w)
優先度3 (MEDIUM - 4月下旬)     : 600行   (2w)
優先度4 (LOW - 5月)            : 300行   (1w)
───────────────────────────
合計 Phase 2 実装予想:  2,350行  (7w)
```

**Phase 2 完了日**: 約 6月初旬

---

**次ステップ**: `CODEBASE_INVENTORY.md` で詳細を確認してください。

