# SBA Framework - テストスイート実行ガイド

## テストフェーズ概要

テストフェーズ: 2026/6/1 〜 6/12 (35時間)
マイルストーン: 全統合テスト通過・24時間連続稼働確認・本稼働準備完了

## テストタスク一覧

| ID  | テスト内容                              | 対象コンポーネント | 時間 | 実行コマンド                                       |
|-----|----------------------------------------|----------------|------|--------------------------------------------------|
| T-1 | ユニットテスト: ストレージ層            | KnowledgeStore | 4h   | `pytest tests/unit/test_storage.py -v`           |
| T-2 | ユニットテスト: 推論エンジン + VRAM制御 | Tier1/2/3      | 3h   | `pytest tests/unit/test_inference.py -v`         |
| T-3 | ユニットテスト: 学習ループ各Step       | LearningLoop   | 4h   | `pytest tests/unit/test_learning_loop.py -v`     |
| T-4 | ユニットテスト: 自己実験エンジン       | Experiment     | 3h   | `pytest tests/unit/test_experiment_engine.py -v` |
| T-5 | 統合テスト: Brain Hot-Swap 完全フロー  | Brain Bank     | 4h   | `pytest tests/integration/test_hot_swap.py -v`   |
| T-6 | 統合テスト: 1サイクル学習フロー        | LearningLoop   | 5h   | `pytest tests/integration/test_learning_cycle.py -v` |
| T-7 | 統合テスト: API レート制限・自動停止   | RateLimiter    | 3h   | `pytest tests/integration/test_rate_limiter.py -v`   |
| T-8 | 24時間連続稼働テスト + 安定性確認      | Scheduler      | 4h   | `pytest tests/integration/test_stability.py -v`  |
| T-9 | バグ修正・ポリッシュ                   | 全体           | 5h   | (バグ修正後再実行) |

## セットアップ

### 1. 依存パッケージのインストール

```bash
cd C:/TH_Works/SBA

# pytest 及び関連ツール
pip install pytest pytest-asyncio pytest-cov pytest-mock

# または、requirements-dev.txt から
pip install -r requirements-dev.txt
```

### 2. テストディレクトリ構成

```
tests/
├── __init__.py
├── conftest.py                  # 共通フィクスチャ・設定
├── unit/                        # ユニットテスト
│   ├── __init__.py
│   ├── test_learning_loop.py    # T-3
│   ├── test_experiment_engine.py # T-4
│   ├── test_storage.py          # T-1
│   └── test_inference.py        # T-2
├── integration/                 # 統合テスト
│   ├── __init__.py
│   ├── test_learning_cycle.py   # T-6
│   ├── test_rate_limiter.py     # T-7
│   ├── test_hot_swap.py         # T-5
│   └── test_stability.py        # T-8
└── fixtures/                    # テストデータ
```

## テスト実行

### 全テストを実行

```bash
pytest tests/ -v
```

### 特定のテストタスクのみ実行

```bash
# T-3: 学習ループテスト
pytest tests/unit/test_learning_loop.py -v

# T-6: 1サイクル学習フロー統合テスト
pytest tests/integration/test_learning_cycle.py -v

# T-7: APIレート制限テスト
pytest tests/integration/test_rate_limiter.py -v
```

### ユニットテストのみ実行

```bash
pytest tests/unit/ -v
```

### 統合テストのみ実行

```bash
pytest tests/integration/ -v
```

### カバレッジレポート付き実行

```bash
pytest tests/ --cov=src/sba --cov-report=html
```

（生成されたレポート: `htmlcov/index.html`）

## テスト実行結果の確認

### 成功基準

各テストタスクが以下の条件を満たすことを確認：

1. **T-1**: ストレージ層の CRUD・検索・フィルタ が全て PASSED
2. **T-2**: EngineRouter 分岐条件・VRAM 排他制御が PASSED
3. **T-3**: Step1～6 各コンポーネント モック テストが PASSED
4. **T-4**: 4種実験タイプ・サンドボックス実行・タイムアウト が PASSED
5. **T-5**: Brain Hot-Swap 完全フロー・バージョン管理・export が PASSED
6. **T-6**: Python開発Brain 1サイクル学習フロー エンドツーエンド実行 PASSED
7. **T-7**: Gemini/YouTube/GitHub API レート制限・自動停止 PASSED
8. **T-8**: NSSMサービス・APScheduler・メモリリーク監視 PASSED
9. **T-9**: バグ修正・エラーメッセージ改善・ポリッシュ PASSED

### ログ確認

テスト実行ログ: `tests/test_results.log`

```bash
# テスト実行結果をログに保存
pytest tests/ -v --tb=short > tests/test_results.log 2>&1
```

## トラブルシューティング

### ImportError が発生する場合

```bash
# src を Python パスに追加
export PYTHONPATH=C:/TH_Works/SBA:$PYTHONPATH
pytest tests/
```

### asyncio テストが失敗する場合

```bash
# pytest-asyncio モードを設定
export PYTEST_ASYNCIO_TIMEOUT=30
pytest tests/ -v
```

### テストが時間切れする場合

```bash
# タイムアウト値を増加
pytest tests/ --timeout=300 -v
```

## 継続的実行

### 開発中のテスト実行（ファイル変更監視）

```bash
pip install pytest-watch
ptw -- tests/ -v
```

### CI/CD パイプライン用

```bash
# テスト実行 + カバレッジ確認 + ベンチマーク
pytest tests/ \
  --cov=src/sba \
  --cov-report=xml \
  --benchmark \
  -v \
  --tb=short \
  > test_results.xml
```

## 本稼働前チェックリスト

- [ ] T-1 ～ T-7 全テスト PASSED
- [ ] T-8 24時間連続稼働テスト PASSED
- [ ] T-9 バグ修正・ポリッシュ 完了
- [ ] コードカバレッジ >= 80%
- [ ] メモリリーク検出なし
- [ ] VRAM OOM 検出なし

## 参考資料

- テストフェーズ設計: docs/spec/16.SBA_タスクスケジュール_v1.0.md
- テストデータ例: tests/fixtures/
- モック定義: tests/conftest.py
