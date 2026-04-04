# blank_template 構成定義
# brain_bank/blank_template/ はgit管理外のため、この仕様書で構造を記録する
# 最終更新: 2026-04-04

## 必須ファイル・ディレクトリ（9点）

| ファイル/ディレクトリ | 種別 | 説明 |
|---|---|---|
| knowledge_graph/  | dir  | Kuzu グラフDB格納ディレクトリ |
| vector_index/     | dir  | Qdrant ベクトルDB格納ディレクトリ |
| brain.db          | file | Brain メタDB (SQLite) |
| data.json         | file | Brain汎用データ |
| experiment_log.db | file | 実験ログ (SQLite, Phase 5) |
| learning_timeline.db | file | 学習タイムライン (SQLite) |
| metadata.json     | file | domain/version/level/last_saved_at |
| self_eval.json    | file | SubSkill評価スコア |
| subskill_manifest.json | file | SubSkill構造定義 |

## [active] 追加ファイル（swapで引き継がない）

| ファイル | 説明 |
|---|---|
| learning_log.jsonl | 学習ループが生成するログ（Phase 4以降） |

## 備考
- blank_template は読み取り専用マスター（直接編集禁止）
- brain swap時は blank_template をコピーして新Brainを生成
- [active] の learning_log.jsonl は blank_template に含めない
