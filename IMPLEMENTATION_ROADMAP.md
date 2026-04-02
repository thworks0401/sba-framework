# SBA Implementation Dependency Graph & Roadmap

**作成日**: 2026年4月2日

---

## 実装依存グラフ

```
┌──────────────────── PHASE 1: BASE LAYER (✅ COMPLETE) ──────────────────────┐
│                                                                                    │
│  API Usage DB ✅    Experiment DB ✅    Timeline DB ✅    Vector Store ✅          │
│  ├─ Daily/monthly counters                  ├─ Experiment logging            │
│  ├─ Rate limit thresholds                   └─ Score tracking                 │
│  └─ Stop state management                                                     │
│                                                                                │
│  Tier1 (Phi-4) ✅    Tier2 (Gemini) ✅      Scheduler ✅                      │
│  ├─ Semaphore(1)       ├─ Google API        ├─ APScheduler                    │
│  ├─ Chat/infer async   ├─ Token tracking    ├─ SQLite JobStore               │
│  └─ Latency tracking   └─ Fallback logic    └─ Job registration              │
│                                                                                │
│  Embedder ✅          Chunker ✅             VRAMGuard ✅                     │
│  ├─ BAAI/bge-m3       ├─ 400-600 tokens    ├─ Ollama model swap             │
│  ├─ Singleton pattern  ├─ 50-token overlap  ├─ Async lock/unlock            │
│  └─ CPU-only mode     └─ Boundary splitting └─ Tier1 ↔ Tier3 exclusion      │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
                                    ↓↓↓ 依存
┌──────────── PHASE 2: LEARNING PIPELINE (🟡 PARTIAL - 40% DONE) ──────────────┐
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐      │
│  │ Step 0: Inference Routing (需要完成)                               │      │
│  │                                                                     │      │
│  │  engine_router (50% done - needs fallback)                         │      │
│  │  ├─ Route task → Tier1/2/3 (基本ロジックあり)                    │      │
│  │  ├─ 🔴 Missing: Retry logic on Tier1 timeout                     │      │
│  │  ├─ 🔴 Missing: Dynamic timeout adjustment                       │      │
│  │  └─ 🔴 Missing: Graceful degradation                             │      │
│  │                                                                     │      │
│  │  Tier3 (Qwen2.5-Coder) (60% done - needs quality check)           │      │
│  │  ├─ Code generation template                                      │      │
│  │  └─ 🔴 Missing: Code quality validation                           │      │
│  └─────────────────────────────────────────────────────────────────────┘      │
│         ↓ Blocks all downstream                                              │
│                                                                               │
│  ┌──────────────── Storage: Graph Operations (Need ASAP) ────────────┐      │
│  │                                                                    │      │
│  │  graph_store.py (30% done)                                        │      │
│  │  ├─ ✅ Schema creation (_create_schema)                          │      │
│  │  ├─ 🔴 Missing: add_node(type, id, attrs)                        │      │
│  │  ├─ 🔴 Missing: add_edge(type, from_id, to_id)                   │      │
│  │  ├─ 🔴 Missing: query_path(start, end, max_hops)                 │      │
│  │  ├─ 🔴 Missing: delete_node(id)                                  │      │
│  │  ├─ 🔴 Missing: add_contradicts(id_a, id_b)                      │      │
│  │  └─ Impact: Blocks knowledge_store integration                   │      │
│  │                                                                    │      │
│  │  knowledge_store.py (40% done)                                    │      │
│  │  ├─ ✅ store_chunk() (atomic write to 3 stores)                  │      │
│  │  ├─ 🔴 Missing: query_hybrid(text, subskill) - CRITICAL          │      │
│  │  │   └─ Needs vector search + graph expansion + reranking         │      │
│  │  ├─ 🔴 Missing: mark_deprecated(chunk_id)                        │      │
│  │  ├─ 🔴 Missing: cleanup_outdated(threshold_date)                 │      │
│  │  └─ Impact: Blocks Step 1-6 knowledge retrieval                  │      │
│  └────────────────────────────────────────────────────────────────────┘      │
│         ↓ Foundation for learning                                           │
│                                                                               │
│  ┌────────── Step 1: Gap Detection ✅ (100% COMPLETE) ────────────┐         │
│  │                                                                  │         │
│  │  gap_detector.py (COMPLETE)                                     │         │
│  │  ├─ ✅ load_self_evaluation()                                   │         │
│  │  ├─ ✅ detect_gap() → KnowledgeGapResult                        │         │
│  │  ├─ ✅ get_priority_queue()                                     │         │
│  │  ├─ ✅ _calculate_gap_severity()                                │         │
│  │  ├─ ✅ _is_in_cooldown()                                        │         │
│  │  └─ Ready to feed into Step 2                                   │         │
│  └──────────────────────────────────────────────────────────────────┘         │
│         ↓ Output: target_subskill + suggested_query                         │
│                                                                               │
│  ┌────────── Step 2: Resource Discovery (Need to Complete) ─────────┐       │
│  │                                                                   │       │
│  │  resource_finder.py (40% done)                                   │       │
│  │  ├─ ✅ SourceType enum, ResourceCandidate dataclass             │       │
│  │  ├─ ? _check_api_quota() (needs api_usage_db integration)       │       │
│  │  ├─ 🔴 Missing: subskill_source_priority() mapping              │       │
│  │  │   └─ "Python Dev" → [GitHub, arXiv] vs "Finance" → [Web, PDF]│       │
│  │  ├─ 🔴 Missing: deduplicate_urls() (timeline check)             │       │
│  │  ├─ 🔴 Missing: assign_trust_scores(candidates)                 │       │
│  │  └─ Blocking: Needs ALL sources to be functional                │       │
│  └───────────────────────────────────────────────────────────────────┘       │
│         ↓ Depends on all Sources working                                    │
│                                                                               │
│  ┌─────── Step 3: Fetch & Chunk (Sources Implementation Block) ───┐         │
│  │                                                                 │         │
│  │  🔴 CRITICAL: All 5 sources have API integration missing       │         │
│  │                                                                 │         │
│  │  ┌─ web_fetcher.py (20% done) ─────────────────────────┐      │         │
│  │  │ ├─ ✅ WebCleaner (patterns defined)                 │      │         │
│  │  │ ├─ ✅ WebPageContent dataclass                      │      │         │
│  │  │ ├─ 🟡 _search_duckduckgo() (partial - stub only)   │      │         │
│  │  │ ├─ 🔴 _fetch_with_jina() (NOT implemented)          │      │         │
│  │  │ ├─ 🔴 _fetch_with_playwright() (NOT implemented)    │      │         │
│  │  │ └─ 🔴 Impact: Cannot fetch web content              │      │         │
│  │  └───────────────────────────────────────────────────────┘      │         │
│  │                                                                 │         │
│  │  ┌─ pdf_fetcher.py (20% done) ──────────────────────────┐      │         │
│  │  │ ├─ ✅ PDFContent dataclass                           │      │         │
│  │  │ ├─ ✅ ArXivSearcher.BASE_URL set                     │      │         │
│  │  │ ├─ 🔴 search_arxiv() NOT implemented                 │      │         │
│  │  │ ├─ 🔴 extract_pdf() NOT implemented                  │      │         │
│  │  │ └─ 🔴 Impact: Cannot fetch arXiv papers              │      │         │
│  │  └───────────────────────────────────────────────────────┘      │         │
│  │                                                                 │         │
│  │  ┌─ code_fetcher.py (20% done) ──────────────────────────┐     │         │
│  │  │ ├─ ✅ GitHubResult, StackOverflowResult dataclass     │     │         │
│  │  │ ├─ 🔴 search_repositories() NOT implemented          │     │         │
│  │  │ ├─ 🔴 get_stackoverflow_qa() NOT implemented         │     │         │
│  │  │ └─ 🔴 Impact: Cannot fetch code examples             │     │         │
│  │  └───────────────────────────────────────────────────────┘     │         │
│  │                                                                 │         │
│  │  ┌─ video_fetcher.py (20% done) ──────────────────────────┐    │         │
│  │  │ ├─ ✅ VideoSegment, VideoContent dataclass            │    │         │
│  │  │ ├─ ✅ yt_dlp import ready                             │    │         │
│  │  │ ├─ 🔴 extract_subtitles() NOT implemented            │    │         │
│  │  │ ├─ 🔴 transcribe_with_whisper() NOT implemented      │    │         │
│  │  │ └─ 🔴 segment_by_timestamps() NOT implemented        │    │         │
│  │  └───────────────────────────────────────────────────────┘    │         │
│  │                                                                 │         │
│  │  ┌─ whisper_transcriber.py (20% done) ──────────────────┐     │         │
│  │  │ ├─ ✅ TranscriptionResult dataclass                   │     │         │
│  │  │ ├─ ✅ WhisperModel import ready                       │     │         │
│  │  │ ├─ 🔴 __init__() NOT implemented                      │     │         │
│  │  │ ├─ 🔴 transcribe() NOT implemented                    │     │         │
│  │  │ └─ 🔴 Impact: Cannot transcribe audio                 │     │         │
│  │  └───────────────────────────────────────────────────────┘     │         │
│  │                                                                 │         │
│  │  ✅ TextChunker (ready - Phase 1)                              │         │
│  │  └─ Splits content into 400-600 token chunks                   │         │
│  │                                                                 │         │
│  │  ✅ SubSkillClassifier (ready - Phase 1)                       │         │
│  │  └─ Assigns primary_subskill + secondary_subskills             │         │
│  └─────────────────────────────────────────────────────────────────┘         │
│         ↓ All inputs must be ready                                          │
│                                                                               │
│  ┌────────── Step 4: Experimentation (🔴 NOT READY) ──────────────┐         │
│  │                                                                 │         │
│  │  experiment_engine.py (30% done)                               │         │
│  │  ├─ ✅ ExperimentType enum (A/B/C/D)                           │         │
│  │  ├─ ✅ ExperimentPlan dataclass                               │         │
│  │  ├─ 🔴 generate_hypothesis() NOT implemented                   │         │
│  │  ├─ 🔴 select_experiment_type() NOT implemented                │         │
│  │  └─ 🔴 generate_experiment_procedure() NOT implemented         │         │
│  │                                                                 │         │
│  │  experiment_runner.py (30% done)                               │         │
│  │  ├─ ✅ ExperimentResult enum                                   │         │
│  │  ├─ ✅ ExperimentRunResult dataclass                          │         │
│  │  ├─ 🔴 ExperimentRunnerA.run() NOT implemented (Type A)        │         │
│  │  ├─ 🔴 ExperimentRunnerB.run() NOT implemented (Type B)        │         │
│  │  ├─ 🔴 ExperimentRunnerD.run() NOT implemented (Type D)        │         │
│  │  ├─ 🔴 Missing: scoring logic                                  │         │
│  │  └─ 🔴 Missing: KB update on success                           │         │
│  │                                                                 │         │
│  │  sandbox_exec.py (20% done - Type C)                           │         │
│  │  ├─ ✅ VRAMGuard integration prepared                          │         │
│  │  ├─ 🔴 _execute_code() NOT implemented                         │         │
│  │  ├─ 🔴 _validate_output() NOT implemented                      │         │
│  │  └─ 🔴 Missing: Security sandboxing                            │         │
│  └─────────────────────────────────────────────────────────────────┘         │
│         ↓ Depends on Tier1/Tier3 working                                    │
│                                                                               │
│  ┌────────── Step 5: Knowledge Integration ────────────────────────┐        │
│  │                                                                  │        │
│  │  knowledge_integrator.py (30% done)                             │        │
│  │  ├─ ✅ ContradictionResult dataclass                            │        │
│  │  ├─ 🔴 detect_contradiction() NOT implemented                   │        │
│  │  ├─ 🔴 compute_contradiction_score() NOT implemented            │        │
│  │  ├─ 🔴 decide_primary() NOT implemented                         │        │
│  │  └─ 🔴 mark_deprecated() NOT implemented                        │        │
│  └──────────────────────────────────────────────────────────────────┘        │
│         ↓ Depends on knowledge_store being ready                           │
│                                                                               │
│  ┌────────── Step 6: Self-Evaluation ──────────────────────────────┐        │
│  │                                                                  │        │
│  │  self_evaluator.py (40% done)                                   │        │
│  │  ├─ ✅ BrainLevel enum (Lv.1/2/3)                               │        │
│  │  ├─ ✅ SubSkillEvaluation dataclass                             │        │
│  │  ├─ 🟡 evaluate_subskill() (partial)                            │        │
│  │  ├─ 🔴 _generate_questions() NOT implemented                    │        │
│  │  ├─ 🔴 _auto_score() NOT implemented                            │        │
│  │  ├─ 🔴 _check_promotion() NOT implemented (3-pass logic)        │        │
│  │  └─ 🔴 _update_self_eval_json() NOT implemented                 │        │
│  └──────────────────────────────────────────────────────────────────┘        │
│         ↓ Depends on Tier1 for question generation                         │
│                                                                               │
│  ┌────────── Loop Control (CRITICAL - 30% done) ────────────────────┐       │
│  │                                                                   │       │
│  │  learning_loop.py (30% done)                                     │       │
│  │  ├─ ✅ LearningCycleResult dataclass                             │       │
│  │  ├─ ✅ LearningLoop.__init__() with all dependencies             │       │
│  │  ├─ 🔴 run_cycle() NOT fully implemented                         │       │
│  │  │   ├─ Missing: Step 3→4 transition (resource→experiment)      │       │
│  │  │   ├─ Missing: Error handling & retry                         │       │
│  │  │   ├─ Missing: Timeout management (1800s default)             │       │
│  │  │   └─ Missing: Data pipeline normalization                    │       │
│  │  ├─ 🔴 auto_schedule_loop() NOT implemented                      │       │
│  │  ├─ 🔴 _cooldown_check() NOT fully implemented                   │       │
│  │  └─ Impact: Entire learning loop may fail or hang                │       │
│  └────────────────────────────────────────────────────────────────────┘       │
│         ↓ Blocks end-to-end testing                                         │
│                                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
                          ↓↓↓ Phase 3+
┌──────────────────── PHASE 3: ADVANCED (🔴 NOT STARTED) ──────────────────────┐
│                                                                               │
│  Self-Evaluator Lv Management (requires Step 6 working)                      │
│  Contradiction Resolution (requires Step 5 + graph_store working)            │
│  Notifier Desktop Integration (can start parallel)                           │
│  Human Review Dashboard (after Phases 1-2)                                   │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 実装優先度リスト（依存順）

### 🔴 NOW (This Week) - Without these, Phase 2 is blocked

| # | Module | Task | Est. | Blocker For |
|---|--------|------|------|------------|
| 1 | graph_store | Implement node/edge CRUD + query_path | 100 💪 | knowledge_store |
| 2 | knowledge_store | Implement query_hybrid | 150 💪 | Steps 1-6 |
| 3 | engine_router | Add fallback & retry logic | 100 💪 | experiment runner |

### 🟠 NEXT WEEK - Infrastructure for sources

| # | Module | Task | Est. | Blocker For |
|---|--------|------|------|------------|
| 4 | engine_router | Complete tier selection | 50 💪 | system stability |
| 5 | resource_finder | Add source prioritization logic | 80 💪 | Step 2 |
| 6 | rate_limiter | Integration test with Tier2 | 30 💪 | production readiness |

### 🟠 APRIL 15-30 - Sources Implementation Sprint

| # | Module | Task | Est. | Blocker For |
|---|--------|------|------|------------|
| 7 | web_fetcher | Implement DuckDuckGo + Jina | 250 💪 | Step 3 |
| 8 | pdf_fetcher | Implement arXiv + PDFMiner | 250 💪 | Step 3 |
| 9 | code_fetcher | Implement GitHub + StackOverflow | 250 💪 | Step 3 |
| 10 | video_fetcher | Implement yt-dlp + Whisper | 300 💪 | Step 3 |

### 🟠 MAY - Experiment & Learning Loop

| # | Module | Task | Est. | Blocker For |
|---|--------|------|------|------------|
| 11 | experiment_engine | Hypothesis generation + type selection | 150 💪 | Step 4 |
| 12 | experiment_runner | A/B/D execution + scoring | 250 💪 | Step 4 |
| 13 | sandbox_exec | Code execution sandboxing | 100 💪 | Step 4 (Type C) |
| 14 | learning_loop | Full orchestration | 200 💪 | Phase 2 completion |
| 15 | self_evaluator | Lv management + score update | 100 💪 | Step 6 |
| 16 | knowledge_integrator | Contradiction detection | 80 💪 | Step 5 |

### 🟡 LATER - Polish & UI

| # | Module | Task | Est. | For |
|---|--------|------|------|-----|
| 17 | notifier | Desktop notifications | 50 💪 | UX |
| 18 | Phase 2 tests | Integration test suite | 100 💪 | QA |

---

## 実装パス (Dependency-Ordered)

```
Week 1 (Apr 1-5):
├─ graph_store CRUD operations       [100 lines]
├─ knowledge_store.query_hybrid()    [150 lines]
└─ engine_router fallback logic      [100 lines]
    ↓ CHECKPOINT: storage + inference ready

Week 2 (Apr 8-12):
├─ resource_finder source priority   [80 lines]
└─ web_fetcher (DuckDuckGo + Jina)   [250 lines]
    ↓ CHECKPOINT: Step 2-3 framework ready

Week 3-4 (Apr 15-28):
├─ pdf_fetcher (arXiv + PDFMiner)    [250 lines]
├─ code_fetcher (GitHub + SO)        [250 lines]
└─ video_fetcher (yt-dlp + Whisper)  [300 lines]
    ↓ CHECKPOINT: All sources functional

Week 5 (Apr 29-May 5):
├─ experiment_engine (hypothesis)    [150 lines]
└─ experiment_runner (A/B/D run)     [250 lines]
    ↓ CHECKPOINT: Step 4 ready

Week 6 (May 6-12):
├─ sandbox_exec (code execution)     [100 lines]
├─ self_evaluator (scoring)          [100 lines]
└─ knowledge_integrator (conflict)   [80 lines]
    ↓ CHECKPOINT: Step 5-6 ready

Week 7 (May 13-19):
└─ learning_loop (full orchestration) [200 lines]
    ↓ CHECKPOINT: Phase 2 complete ✅

Week 8 (May 20-26):
├─ Phase 2 integration testing
├─ Bug fixes
└─ Documentation
    ↓ PHASE 2 READY FOR PRODUCTION
```

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| graph_store Cypher complexity | 🟠 Medium | CRITICAL | Use Kuzu examples, start with simple ops |
| API rate limits during testing | 🟠 Medium | HIGH | Use test API keys, mock responses |
| VRAM contention issues | 🟡 Low | MEDIUM | Pre-test Tier1+Tier3 interaction |
| Network failures in sources | 🟠 Medium | MEDIUM | Implement retry + fallback sources |
| Duplicate content handling | 🟠 Medium | LOW | Use timestamp + content hash checks |

---

## Success Criteria for Phase 2

- [ ] All 30 files >= 75% implementation
- [ ] test_phase2_full_cycle.py passes ✅
- [ ] Single learning loop completes without timeout
- [ ] Knowledge base accumulates 1000+ chunks
- [ ] Brain self-evaluation score stabilizes

