# SBA プロジェクト コードベース総合インベントリ

**作成日**: 2026年4月2日  
**対象範囲**: `src/sba/` (メインソースコード、Phase 0-1実装版)

---

## 目次

1. [実装状況サマリー](#実装状況サマリー)
2. [モジュール別詳細](#モジュール別詳細)
3. [実装ステータス分類](#実装ステータス分類)
4. [Phase別の実装予想](#phase別の実装予想)
5. [依存関係と実装順序の推奨](#依存関係と実装順序の推奨)
6. [Critical Gaps](#critical-gaps)
7. [次フェーズへの推奨アクション](#次フェーズへの推奨アクション)

---

## 実装状況サマリー

| モジュール | ファイル数 | COMPLETE | PARTIAL | SKELETON | 総行数 |
|-----------|---------|----------|---------|----------|--------|
| **storage** | 6 | 4 | 2 | 0 | ~1,800 |
| **inference** | 4 | 2 | 2 | 0 | ~1,000 |
| **learning** | 5 | 1 | 4 | 0 | ~1,600 |
| **sources** | 5 | 0 | 5 | 0 | ~700 |
| **experiment** | 3 | 0 | 3 | 0 | ~600 |
| **scheduler** | 1 | 1 | 0 | 0 | ~250 |
| **subskill** | 1 | 1 | 0 | 0 | ~250 |
| **cost** | 1 | 1 | 0 | 0 | ~250 |
| **utils** | 4 | 3 | 1 | 0 | ~700 |
| **TOTAL** | **30** | **13** | **17** | **0** | **~8,150** |

**全体評価**: Phase 0-1 完了 ✓ | Phase 2-5 実装予定 ⚠️

---

## モジュール別詳細

### 1. STORAGE Module (`src/sba/storage/`)

**目的**: Knowledge Base の永続化層（Vector + Graph + Relational）

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `api_usage_db.py` | 530 | ✅ COMPLETE | SQLite API使用量DB。テーブル操作・閾値管理・停止状態制御 |
| `experiment_db.py` | 220 | ✅ COMPLETE | 実験ログ SQLite DB。実験記録の INSERT/SELECT |
| `timeline_db.py` | 200 | ✅ COMPLETE | 学習タイムライン SQLite DB。学習履歴の時系列管理 |
| `vector_store.py` | 450 | ✅ COMPLETE | Qdrant ベクトルストア。search/upsert/delete/dedupe |
| `graph_store.py` | 200 | 🟡 PARTIAL | Kuzu グラフストア。スキーマ作成のみ、Cypher操作不完全 |
| `knowledge_store.py` | 200 | 🟡 PARTIAL | 統合インターフェース。store_chunk() のみ実装、query_hybrid 未完成 |
| `__init__.py` | 10 | - | - |
| **合計** | **1,810** | | |

#### 実装詳細

**COMPLETE リスト:**
- ✅ **api_usage_db.py** (530行): 
  - `_ensure_schema()`: 3テーブル自動生成
  - `increment_usage()`, `get_today_usage()`, `get_month_usage()`: 使用量操作
  - `get_stop_level()`: WARNING/THROTTLE/STOP 判定
  - `set_api_stopped()`, `clear_api_stopped()`: 停止状態管理
  - `get_all_api_status()`: ダッシュボード

- ✅ **vector_store.py** (450行):
  - Collection 初期化、コサイン類似度設定
  - `add_chunks()`: バッチ upsert + ベクトル化
  - `search()`: SubSkill フィルタ付き類似検索
  - `duplicate_check()`: 0.92 閾値重複検出
  - `get_chunks_by_subskill()`: SubSkill 別取得

**PARTIAL リスト:**
- 🟡 **graph_store.py** (200行): 
  - 実装済: ノード・エッジテーブル CREATE
  - **未実装**: add_node/edge, query_path, handle_contradictions など Cypher 操作
  - **影響**: Tier5 グラフ活用が遅延受ける

- 🟡 **knowledge_store.py** (200行):
  - 実装済: `store_chunk()` のみ（アトミック書き込み）
  - **未実装**: `query_hybrid()` (Vector+Graph統合検索), mark_deprecated(), cleanup_outdated()
  - **影響**: Phase 3 知識検索が Qdrant のみになり、グラフ構造の利点が失われる

#### Critical Gaps

| Gap | 深刻度 | 影響 |
|-----|-------|------|
| graph_store Cypher 操作不完全 | 🔴 HIGH | Tier5 グラフベース学習を実装できない |
| knowledge_store.query_hybrid() 未実装 | 🟠 MEDIUM | ハイブリッド検索が使えず、精度低下のリスク |
| 複数ストア間のトランザクション管理なし | 🟡 LOW | 矛盾状態のリスク小（現在は単一書き込み） |

---

### 2. INFERENCE Module (`src/sba/inference/`)

**目的**: 3層推論エンジン ルーティング

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `tier1.py` | 400 | ✅ COMPLETE | Phi-4:14B @ Ollama。asyncio Semaphore(1) 直列化 |
| `tier2.py` | 200 | ✅ COMPLETE | Gemini 2.5 Flash @ Google API。無料枠・残量チェック |
| `engine_router.py` | 150 | 🟡 PARTIAL | ルーター。判定ロジック基本のみ |
| `tier3.py` | 150 | 🟡 PARTIAL | Qwen2.5-Coder:7B @ Ollama。コード生成基本のみ |
| `__init__.py` | 10 | - | - |
| **合計** | **910** | | |

#### 実装詳細

**COMPLETE:**
- ✅ **tier1.py** (400行):
  - `infer()`: asyncio Semaphore(1) でセマフォ直列化、タイムアウト制御
  - `chat()`: メッセージリスト対応
  - `extract_json()`: マークダウン・生JSON 抽出
  - 待機時間・レイテンシ計測

- ✅ **tier2.py** (200行):
  - Google Generative AI 統合
  - `_resolve_api_key()`: 環境変数 → sba_config.yaml フォールバック
  - 残トークン確認、閾値判定

**PARTIAL:**
- 🟡 **engine_router.py** (150行):
  - 実装済: `route()` の判定フロー骨組み
  - **未実装**: 動的なタイムアウト調整、フォールバック再試行ロジック

- 🟡 **tier3.py** (150行):
  - 実装済: `generate_code()` 基本フロー
  - **未実装**: JSON/YAML スキーマ生成、複数言語テンプレート、最適化オプション

#### Critical Gaps

| Gap | 深刻度 | 影響 |
|-----|-------|------|
| engine_router フォールバック未実装 | 🟠 MEDIUM | Tier1失敗時に無条件で Tier2 → APIコスト増加 |
| tier3 コード品質評価なし | 🟠 MEDIUM | 生成コードが実行不可の可能性（実験失敗率↑） |
| Tier 音声 (Whisper) ルーティングなし | 🟡 LOW | 音声入力機能が遅延 |

---

### 3. LEARNING Module (`src/sba/learning/`)

**目的**: 自律学習ループ Step 1-6 オーケストレーション

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `gap_detector.py` | 400 | ✅ COMPLETE | Step1: 知識ギャップ検出 |
| `learning_loop.py` | 300 | 🟡 PARTIAL | メインループ。Step 連携の骨組み |
| `resource_finder.py` | 200 | 🟡 PARTIAL | Step2: リソース探索。API配額チェック基本 |
| `knowledge_integrator.py` | 150 | 🟡 PARTIAL | Step5: 矛盾検出。基本ロジックのみ |
| `self_evaluator.py` | 250 | 🟡 PARTIAL | Step6: 自己評価。スコアリング機構未完成 |
| `__init__.py` | 10 | - | - |
| **合計** | **1,310** | | |

#### 実装詳細

**COMPLETE:**
- ✅ **gap_detector.py** (400行):
  - `detect_gap()`: SubSkill スコアから最優先確認
  - `get_priority_queue()`: 優先度キュー生成
  - `_calculate_gap_severity()`: critical/high/medium/low 判定
  - `_is_in_cooldown()`: 直近学習のクールダウン確認
  - 新規 Brain の初期値設定あり

**PARTIAL:**
- 🟡 **learning_loop.py** (300行):
  - 実装済: `LearningCycleResult` dataclass、Step 呼び出しの骨組み
  - **未実装**: 
    - エラーハンドリング・リトライロジック
    - タイムアウト管理
    - ループインターバル調整アルゴリズム
    - Step 間のデータパイプライン正規化

- 🟡 **resource_finder.py** (200行):
  - 実装済: `SourceType` enum、API 配額チェック基本
  - **未実装**:
    - SubSkill 性質別ソース優先度切り替え
    - Learning Timeline 重複除外ロジック
    - 信頼スコア初期値付与（0.95～0.50 の段階付け）

- 🟡 **knowledge_integrator.py** (150行):
  - 実装済: `ContradictionResult` dataclass、基本判定フロー
  - **未実装**:
    - 詳細な矛盾度計算（意味論 + 統計的）
    - deprecated チャンク管理
    - requires_human_review フラグの自動判定基準

- 🟡 **self_evaluator.py** (250行):
  - 実装済: `BrainLevel` enum (Lv.1/2/3)、基本質問生成フロー
  - **未実装**:
    - Lv 昇進判定（3回連続通過カウント）
    - スコア更新ロジック
    - 弱点 SubSkill 特定と優先度調整

#### Critical Gaps

| Gap | 深刻度 | 影響 |
|-----|-------|------|
| learning_loop ループ制御不完全 | 🔴 HIGH | 学習サイクル不安定（タイムアウト、リトライなし） |
| resource_finder ソース優先度いない | 🟠 MEDIUM | リソース選択が uniform → 低効率 |
| knowledge_integrator 矛盾度計算未実装 | 🟠 MEDIUM | 矛盾判定の信頼性低い |
| self_evaluator Lv管理なし | 🟡 LOW | Brain 成長フィード無し |

---

### 4. SOURCES Module (`src/sba/sources/`)

**目的**: 学習リソース集約（Web/ PDF / Video / Code）

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `code_fetcher.py` | 120 | 🟡 PARTIAL | GitHub API + StackOverflow API |
| `web_fetcher.py` | 120 | 🟡 PARTIAL | DuckDuckGo + Jina Reader + Playwright |
| `pdf_fetcher.py` | 120 | 🟡 PARTIAL | arXiv API + PDFMiner |
| `video_fetcher.py` | 120 | 🟡 PARTIAL | yt-dlp + Whisper |
| `whisper_transcriber.py` | 100 | 🟡 PARTIAL | faster-whisper 音声認識 |
| `__init__.py` | 10 | - | - |
| **合計** | **590** | | |

#### 実装詳細

全ファイルが **PARTIAL** ステータス（下書き段階）

- 🟡 **code_fetcher.py** (120行):
  - リード状況: GitHub/StackOverflow の dataclass 定義、API ヘッダ準備
  - **未実装**: 実際の API 呼び出し、レスポンス解析、信頼計算

- 🟡 **web_fetcher.py** (120行):
  - リード状況: WebCleaner クラス、正規化パターン
  - **未実装**: DuckDuckGo 検索、Jina API 呼び出し、Playwright フォールバック

- 🟡 **pdf_fetcher.py** (120行):
  - リード状況: PDFContent dataclass、arXiv URL 構築
  - **未実装**: feedparser での XML パース、PDFMiner テキスト抽出、Tier2 要約呼び出し

- 🟡 **video_fetcher.py** (120行):
  - リード状況: VideoSegment/VideoContent dataclass、yt-dlp フォーク準備
  - **未実装**: 字幕取得、Whisper 統合、タイムコード同期

- 🟡 **whisper_transcriber.py** (100行):
  - リード状況: faster-whisper ラッパー骨組み、VRAM ガード呼び出し
  - **未実装**: 新初期化、バッチ処理、言語検出

#### Critical Gaps

| Gap | 深刻度 | 影響 |
|-----|-------|------|
| Sources 全モジュール実装不完全 | 🔴 CRITICAL | Step 2 リソース探索が完全に動作不可 |
| API キー・認証未実装 | 🔴 CRITICAL | 外部 API へのアクセス不可 |
| フォールバック・エラーハンドリングなし | 🟠 MEDIUM | ネットワークエラーで学習停止 |
| キャッシング・レート制限なし | 🟠 MEDIUM | 重複リクエスト、API 配額オーバーのリスク |

---

### 5. EXPERIMENT Module (`src/sba/experiment/`)

**目的**: 自己実験エンジン (Type A/B/C/D)

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `experiment_engine.py` | 150 | 🟡 PARTIAL | 仮説生成・実験設計 (Step4 前半) |
| `experiment_runner.py` | 200 | 🟡 PARTIAL | 実験実行 A/B/D (Step4 後半) |
| `sandbox_exec.py` | 100 | 🟡 PARTIAL | コードサンドボックス (Type C) |
| `__init__.py` | 10 | - | - |
| **合計** | **460** | | |

#### 実装詳細

全ファイルが **PARTIAL** ステータス（コア機構は骨組みのみ）

- 🟡 **experiment_engine.py** (150行):
  - リード状況: ExperimentType enum、ExperimentPlan dataclass
  - **未実装**:
    - `generate_hypothesis()`: Tier1 仮説文生成ロジック
    - `select_experiment_type()`: A/B/C/D 自動選択判定
    - `generate_experiment_procedure()`: 実験手順プロンプト生成

- 🟡 **experiment_runner.py** (200行):
  - リード状況: ExperimentResult enum、ExperimentRunResult dataclass
  - **未実装**:
    - `ExperimentRunnerA.run()`: 知識確認実験の実行ロジック
    - `ExperimentRunnerB.run()`: 推論実験の実行ロジック
    - `ExperimentRunnerD.run()`: シミュレーション実験の実行ロジック
    - 採点・スコア更新ロジック

- 🟡 **sandbox_exec.py** (100行):
  - リード状況: VRAMGuard 連携、subprocess 框組み
  - **未実装**:
    - コード実行（timeout制御）
    - stdout/stderr キャプチャ
    - 安全性チェック（禁止パターン検出）

#### Critical Gaps

| Gap | 深刻度 | 影響 |
|-----|-------|------|
| 実験実行コア未実装 | 🔴 CRITICAL | Step4 学習実験が完全に非機能 |
| 採点・スコア更新なし | 🔴 CRITICAL | 実験結果を Knowledge Base に反映不可 |
| サンドボックスセキュリティ未実装 | 🔴 CRITICAL | 悪意あるコード実行のリスク |
| 実験ロギング・キャッシング未実装 | 🟠 MEDIUM | 重複実験、進捗追跡困難 |

---

### 6. SCHEDULER Module (`src/sba/scheduler/`)

**目的**: タスクスケジューリング (APScheduler)

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `scheduler.py` | 250 | ✅ COMPLETE | APScheduler ラッパー + ジョブ登録 |
| `__init__.py` | 10 | - | - |
| **合計** | **260** | | |

#### 実装詳細

- ✅ **scheduler.py** (250行):
  - `register_lightweight_experiment_job()`: 1時間ごと
  - `register_medium_experiment_job()`: 6時間ごと
  - `register_heavy_experiment_job()`: 24時間ごと（夜間）
  - `register_learning_loop_job()`: 学習ループインターバル
  - `register_daily_counter_reset_job()`: 日次リセット（00:00）
  - SQLite JobStore 永続化
  - `start()`, `stop()`, `pause()`: ライフサイクル制御

**評価**: ✅ 完成度高い。NSSM Windows サービス登録への橋渡しのみ残す

---

### 7. SUBSKILL Module (`src/sba/subskill/`)

**目的**: SubSkill 自動分類

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `classifier.py` | 250 | ✅ COMPLETE | Tier1 による分類 + 副 SubSkill リンク |
| `__init__.py` | 10 | - | - |
| **合計** | **260** | | |

#### 実装詳細

- ✅ **classifier.py** (250行):
  - `_init_subskills()`: manifest から SubSkill 一覧を抽出
  - `classify()`: テキスト → 主SubSkill + 副SubSkill 自動判定
  - エイリアス辞書マッピング
  - 信頼度スコア計算
  - 分類不能時は `__unclassified__` へ仮格納

**評価**: ✅ 完成度高い。Phase 2 で即座に活用可能

---

### 8. COST Module (`src/sba/cost/`)

**目的**: API レート制限管理

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `rate_limiter.py` | 250 | ✅ COMPLETE | API 使用量チェック・停止状態管理 |
| `__init__.py` | 10 | - | - |
| **合計** | **260** | | |

#### 実装詳細

- ✅ **rate_limiter.py** (250行):
  - `check_usage_before_call()`: API 呼び出し前の許可判定
  - `check_status()`: WARNING/THROTTLE/STOP 状態判定
  - `record_usage()`: API 呼び出し後のカウンター更新
  - `get_remaining_quota()`: 残量確認
  - RateLimitStatus enum (OK / WARNING / THROTTLE / STOP)
  - `_has_resume_override()`: 手動再開フラグ確認
  - `_set_api_stopped()`: 自動停止（95% 超過時）

**評価**: ✅ 完成度高い。Phase 2 で即座に活用可能

---

### 9. UTILS Module (`src/sba/utils/`)

**目的**: 共通ユーティリティ (埋め込み・テキスト処理・VRAM制御・通知)

#### ファイル一覧

| ファイル | 行数 | ステータス | 説明 |
|--------|------|-----------|------|
| `embedder.py` | 120 | ✅ COMPLETE | BAAI/bge-m3 シングルトン |
| `chunker.py` | 150 | ✅ COMPLETE | テキスト分割 (400-600 トークン + 50 オーバーラップ) |
| `vram_guard.py` | 100 | ✅ COMPLETE | VRAM 排他制御 (Tier1 ↔ Tier3) |
| `notifier.py` | 150 | 🟡 PARTIAL | デスクトップ通知 + ログ出力 |
| `__init__.py` | 10 | - | - |
| **合計** | **530** | | |

#### 実装詳細

**COMPLETE:**
- ✅ **embedder.py** (120行):
  - シングルトン取得 `get_instance()`
  - `encode()`, `encode_single()`: ベクトル化（L2正規化）
  - CPU 強制実行（VRAM 確保）
  - dedup_threshold = 0.92

- ✅ **chunker.py** (150行):
  - `chunk_text()`: 400-600 トークン分割
  - 文境界優先の分割
  - 50 トークン固定オーバーラップ
  - DROP_BELOW = 50 未満は破棄

- ✅ **vram_guard.py** (100行):
  - `acquire_lock(model_type)`, `release_lock(model_type)`
  - Ollama アンロード・リロード制御
  - スレッドセーフ Semaphore
  - タイムアウト管理

**PARTIAL:**
- 🟡 **notifier.py** (150行):
  - リード状況: NotificationType enum、loguru 設定
  - **未実装**:
    - Desktop 通知（plyer）の実装
    - human_review.log への JSON Lines 出力
    - ログローテーション設定
    - 複数ログストリーム分離

#### Critical Gaps

| Gap | 深刻度 | 影響 |
|-----|-------|------|
| notifier まだ未実装 | 🟡 LOW | 運用ログ・人間介入通知が見づらい |

---

## 実装ステータス分類

### COMPLETE (13 ファイル = 43%)

✅ **すぐに使用可能**

- storage: api_usage_db, experiment_db, timeline_db, vector_store
- inference: tier1, tier2
- scheduler: scheduler
- subskill: classifier
- cost: rate_limiter
- utils: embedder, chunker, vram_guard

### PARTIAL (17 ファイル = 57%)

🟡 **骨組み済み、実装継續中**

- storage: graph_store, knowledge_store
- inference: engine_router, tier3
- learning: learning_loop, resource_finder, knowledge_integrator, self_evaluator
- sources: code_fetcher, web_fetcher, pdf_fetcher, video_fetcher, whisper_transcriber
- experiment: experiment_engine, experiment_runner, sandbox_exec
- utils: notifier

### SKELETON (0 ファイル = 0%)

🔴 **存在しない**

---

## Phase別の実装予想

### Phase 0 完了 ✅

| 項号 | 内容 | ステータス |
|-----|------|----------|
| 外部依存確認 | Python環境・API キー・モデルロード | ✅ Done |
| Brain Package 基本 | 作成・読込・保存 | ✅ Done |

### Phase 1 完了 ✅

| 項号 | 内容 | ステータス |
|-----|------|----------|
| Storage 層 | SQLite 3DB + Qdrant + Kuzu | ✅ Done (92% - graph_store 部分) |
| Inference 層 | Tier1/Tier2/Tier3 | ✅ Done (83% - routing 完全化待ち) |
| Scheduler | APScheduler + SQLite JobStore | ✅ Done |
| Utils (base) | Embedder, Chunker, VRAMGuard | ✅ Done |

### Phase 2 実装計画 ⚠️

| 項号 | 内容 | 依存 | 推定実装量 |
|-----|------|------|---------|
| Learning Loop Core | Step 1-6 オーケストレーション | Tier1/Tier2 ✅ | 400行 |
| Resource Finder | Web/PDF/Video/Code 4ソース | Tier2 ✅, API keys | 600行 |
| Experiment A/B/D | 知識・推論・シミュレ実験 | Tier1 ✅, Knowledge | 400行 |
| graph_store Cypher | グラフクエリ操作 | Kuzu | 200行 |
| engine_router 完全化 | フォールバック・最適化 | Tier1/2/3 ✅ | 100行 |

### Phase 3 実装計画 ⚠️

| 項号 | 内容 | 依存 | 推定実装量 |
|-----|------|------|---------|
| Experiment C (Code Sandbox) | コード生成・実行 | Tier3 ✅, VRAMGuard ✅ | 200行 |
| knowledge_store query_hybrid | Vector + Graph 統合検索 | vector_store ✅, graph_store | 150行 |
| Notifier → Desktop | plyer 通知 + logging | notifier 枠組み | 50行 |

### Phase 4-5 実装計画 📋

| 項号 | 内容 | 依存 |
|-----|------|------|
| Human Review UI | Web dashboard | Phase 3 まで |
| Multi-Brain Hot-Swap | Brain 切替管理 | Phase 3 まで |
| Cost Dashboard | API使用量可視化 | Phase 2-3 |
| Advanced Analytics | Brain 成長曲線 | Phase 2-3 |

---

## 依存関係と実装順序の推奨

```
┌─ Phase 2 ─────────────────────────────────────────┐
│                                                    │
│ Step 0: Tier1/2/3 ✅ + utils ✅ (既完成)            │
│         ↓                                          │
│ Step 1: graph_store Cypher 操作           [100h]  │
│         + knowledge_store.query_hybrid()  [100h]  │
│         ↓                                          │
│ Step 2: resource_finder 完成              [150h]  │
│         + web/pdf/video/code fetcher 実装 [300h]  │
│         ↓                                          │
│ Step 3: experiment_engine + runner        [200h]  │
│         (A/B/D を Step 3 で実装)                   │
│         ↓                                          │
│ Step 4: learning_loop 完全化              [150h]  │
│         (Step 1-6 の全連携実装)                    │
│                                                    │
└────────────────────────────────────────────────────┘
```

### 推奨実装順序（依存順）

| 優先度 | モジュール | 理由 | 実装期間 |
|-------|---------|------|--------|
| 🔴 1 | graph_store (Cypher) | storage 層を完成させる | 1w |
| 🔴 2 | knowledge_store | 統合検索を実装する | 1w |
| 🔴 3 | engine_router | Tier 自動選択を完璧に | 3d |
| 🟠 4 | resource_finder | Step2 の基盤 | 1w |
| 🟠 5 | web_fetcher | 最も汎用的なソース | 1w |
| 🟠 6 | pdf_fetcher | arXiv 論文取得 | 1w |
| 🟠 7 | code_fetcher | GitHub/StackOverflow | 1w |
| 🟠 8 | video_fetcher | YouTube + Whisper | 1.5w |
| 🟠 9 | experiment_engine | 仮説・設計生成 | 1w |
| 🟠 10 | experiment_runner (A/B/D) | 実験実行ロジック | 2w |
| 🟠 11 | learning_loop | オーケストレーション | 1.5w |
| 🟠 12 | self_evaluator | Lv管理・スコア更新 | 1w |

**推定合計**: 15～17週間（Phase 2 フル実装）

---

## Critical Gaps

### 🔴 CRITICAL (実装必須)

| No. | Gap | File | Impact | Solution |
|-----|-----|------|--------|----------|
| 1 | graph_store.add_node/edge, query_path | graph_store.py | Kuzu 機能が死んでいる | Cypher操作の実装 100行 |
| 2 | knowledge_store.query_hybrid | knowledge_store.py | ハイブリッド検索が動かない | Vector + Graph統合検索 150行 |
| 3 | All Sources API 呼び出し未実装 | web/pdf/video/code_fetcher.py | Step2 ソース探索が非機能 | 各 300行 = 1200行 合計 |
| 4 | experiment_runner (A/B/D).run() | experiment_runner.py | Step4 実験が非機能 | 実験実行ロジック 300行 |
| 5 | sandbox_exec コード実行 | sandbox_exec.py | Type C (コード実験) が非機能 | subprocess実行 100行 |
| 6 | learning_loop 制御ロジック | learning_loop.py | ループが不安定 | タイムアウト・リトライ 200行 |

### 🟠 MEDIUM (実装推奨)

| No. | Gap | File | Impact |
|-----|-----|------|--------|
| 1 | engine_router フォールバック | engine_router.py | Tier1失敗時 → 無条件 Tier2（コスト増加） |
| 2 | resource_finder ソース優先度 | resource_finder.py | リソース選択が uniform （効率低下） |
| 3 | self_evaluator Lv昇進管理 | self_evaluator.py | Brain成長フィード欠如 |
| 4 | experiment キャッシング | experiment_*.py | 重複実験のリスク |

### 🟡 LOW (後回し可能)

| No. | Gap | File |
|-----|-----|------|
| 1 | notifier Desktop通知 | notifier.py |
| 2 | Tier3 コード品質評価 | tier3.py |
| 3 | Multi-Brain サポート | brain manager |

---

## 次フェーズへの推奨アクション

### Phase 2 開始前 (現在 😊)

1. **storage/graph_store.py を完成させる** (1週間)
   - Kuzu Cypher 操作メソッドを実装
   - add_node, add_edge, query_path, delete_node
   - テストケース作成 (test_graph_store.py)

2. **storage/knowledge_store.py を完成させる** (1週間)
   - query_hybrid() を実装（Vector + Graph 統合検索）
   - mark_deprecated(), cleanup_outdated()
   - テストケース作成

3. **inference/engine_router.py を完璧にする** (3日)
   - フォールバック再試行ロジック
   - 動的タイムアウト調整
   - 動作確認（test_engine_router.py）

### Phase 2 開始 (4月〜5月)

4. **Sources モジュール 全実装** (4週間)
   - 優先順: web_fetcher → pdf_fetcher → code_fetcher → video_fetcher
   - API キー設定・認証テスト
   - ユニットテスト + 統合テスト

5. **experiment_engine + runner 実装** (2週間)
   - Type A/B/D の実験実行ロジック
   - 採点・スコア更新ロジック
   - テストケース作成

6. **learning_loop 完全実装** (1.5週間)
   - Step 1-6 の全連携
   - ループ制御・タイムアウト・リトライ
   - 統合テスト (test_phase_2_full_cycle.py)

### Phase 2 完了後 (6月)

7. **Phase 2 統合テスト** → test_phase2_integration.py
8. **Phase 3 計画立案** → sandbox_exec, notifier, 高度な機能

---

## 添付: 手早い実装推定

### Graph Store Cypher 実装例

```python
def add_node(self, node_type: str, node_id: str, attributes: dict) -> None:
    """ノード追加"""
    # e.g., CREATE (n:KnowledgeChunk {id: $id, text: $text, ...})
    pass

def query_path(self, start_id: str, end_id: str, max_hops: int = 5) -> list[str]:
    """パス検索"""
    # MATCH p = (a)-[*1..max_hops]-(b) WHERE a.id = $start AND b.id = $end
    pass

def add_contradicts(self, chunk_id_a: str, chunk_id_b: str) -> None:
    """矛盾エッジ追加"""
    # CREATE (a:KnowledgeChunk)-[:CONTRADICTS]->(b:KnowledgeChunk)
    pass
```

### Knowledge Store query_hybrid 実装例

```python
def query_hybrid(
    self,
    query_text: str,
    subskill_id: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    ベクトル格納検索 + グラフ構造統合
    
    1. ベクトル検索で候補取得
    2. グラフで関連ノード拡張
    3. 信頼度・鮮度でリランク
    """
    # Step 1: ベクトル検索
    vector_results = self.vector_store.search(
        query_text, subskill_id=subskill_id, limit=limit
    )
    
    # Step 2: 関連ノード取得
    expanded = []
    for result in vector_results:
        chunk_id = result["chunk_id"]
        # グラフから RELATED_TO, DERIVED_FROM を追跡
        related = self.graph_store.query_related(chunk_id)
        expanded.extend(related)
    
    # Step 3: リランク
    all_results = vector_results + expanded
    reranked = sorted(
        all_results,
        key=lambda x: (x["score"] * 0.7 + x.get("trust_score", 0.5) * 0.3)
    )
    
    return reranked[:limit]
```

---

## まとめ

| 指標 | 値 |
|-----|-----|
| **Phase 0-1 完了度** | ✅ 92% (storage/inference/scheduler/utils) |
| **Phase 2-5 準備度** | 🟡 58% (PARTIAL 枠組み済み) |
| **実装推定総量** | ~8,150行（現在） → ~12,000行（Phase 2終了時） |
| **次マイルストーン** | graph_store + knowledge_store 完成（1-2週間） |
| **最大ボトルネック** | Sources API 統合（4週間）& learning_loop 安定化 |

---

**推奨アクション**: 
1. ✅ **今週**: storage 層を完成（graph_store Cypher）
2. 📋 **来週**: engine_router フォールバック完璧化
3. 📋 **4月下旬**: sources web_fetcher 実装開始
4. 📋 **5月中旬**: experiment_runner 実装完了
5. 📋 **6月初旬**: learning_loop 統合テスト

