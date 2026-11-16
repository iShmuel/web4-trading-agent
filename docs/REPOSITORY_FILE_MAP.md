# Web4 Trading Agent: Repository File Map & Component Directory

This document provides a granular, file-by-file directory for every single file in the **Web4 Autonomous Trading Agent** repository. For each file, it specifies:
- **WHAT**: Its exact role and functional responsibility.
- **USED BY**: Which caller, engine, daemon, or test suite invokes it.
- **WHY NEEDED**: The architectural purpose it serves and what would break if it was omitted.

---

## Complete Annotated Repository Tree

```text
web4-trading-agent/
│
├── live_paper_trader.py                  # WHAT: Live daemon heartbeat loop ticking every 60s
│                                         # USED BY: Master background process launched via CLI; ticks coordinator.py
│                                         # WHY NEEDED: Heartbeat of the bot; without it, no live market scans or trades happen
│
├── dashboard.py                          # WHAT: Real-time ANSI curses-style terminal monitor
│                                         # USED BY: Terminal monitor run by the user; reads live_status.json & portfolio
│                                         # WHY NEEDED: Visualizes P&L, open positions & pairs gauges with zero risk of database locks
│
├── AGENTS.md                             # WHAT: Constitutional safety rulebook for autonomous AI agents
│                                         # USED BY: Read by AI coding agents (Antigravity/Claude/Gemini) before running any tasks
│                                         # WHY NEEDED: Enforces immutable laws: paper-only trading, 1%-5% sizing, and data preservation
│
├── FUTURE_DEVELOPMENT_NOTES.md           # WHAT: Architectural roadmap and backlog for future upgrades
│                                         # USED BY: Developers & AI agents when planning new institutional features
│                                         # WHY NEEDED: Retains design ideas across restarts so long-term engineering memory is never lost
│
├── PROJECT_CURRENT_STATE_AUDIT.md        # WHAT: Health audit report verifying tests, daemon status & execution pipelines
│                                         # USED BY: Developers and auditors evaluating system stability
│                                         # WHY NEEDED: Provides a timestamped baseline of system integrity and test coverage
│
├── PROJECT_OPTIMIZATION_REPORT.md        # WHAT: Empirical report documenting Sharpe, win-rate & drawdown improvements
│                                         # USED BY: Quantitative researchers evaluating strategy parameter upgrades
│                                         # WHY NEEDED: Verifies that parameter tuning generated real statistical performance gains
│
├── README.md                             # WHAT: Repository overview, quickstart instructions & architecture summary
│                                         # USED BY: Anyone cloning or onboarding to the repository
│                                         # WHY NEEDED: First entry point explaining installation, startup flags, and safety limits
│
├── requirements.txt                      # WHAT: Python dependencies (CCXT, pandas, numpy, scipy, statsmodels, tabulate)
│                                         # USED BY: pip install when provisioning environments or Docker containers
│                                         # WHY NEEDED: Ensures deterministic environment setups without dependency mismatch
│
├── pytest.ini                            # WHAT: Pytest configuration, rootdir specification, and test discovery options
│                                         # USED BY: pytest command runner during CI/CD and automated testing
│                                         # WHY NEEDED: Prevents test collection warnings and ensures test discoverability
│
├── web4_trading_agent_presentation.pdf   # WHAT: 8-slide executive presentation deck on Web4 architecture & live results
│                                         # USED BY: Institutional stakeholders, investors, and technical evaluators
│                                         # WHY NEEDED: Visual presentation of autonomous levels, risk fortress & live performance
│
├── web4_trading_agent_presentation.pptx  # WHAT: Editable PowerPoint source presentation deck
│                                         # USED BY: Presenters customizing slides for institutional pitches
│                                         # WHY NEEDED: Allows modifications to diagrams, figures, and strategy slides
│
├── config/
│   ├── active_config.json                # WHAT: Live parameters, active symbols, strategy thresholds & risk toggles
│   │                                     # USED BY: coordinator.py, risk_engine.py, strategies, and agent/governor.py
│   │                                     # WHY NEEDED: Decouples logic from settings; allows tuning parameters without altering code
│   │
│   ├── pending_proposals.json            # WHAT: Staging sandbox holding candidate parameter upgrades found by AI
│   │                                     # USED BY: Written by optimizer.py; evaluated by validator.py & governor.py
│   │                                     # WHY NEEDED: Quarantines AI experiments so unverified parameters can never reach production
│   │
│   ├── active_config.json.bak            # WHAT: Immediate rollback backup of active_config.json
│   │                                     # USED BY: governor.py during emergency rollbacks
│   │                                     # WHY NEEDED: Restores valid configuration instantly if an invalid config is ever written
│   │
│   └── active_config.pre_a15_backup.json # WHAT: Historical checkpoint configuration before major parameter optimizations
│                                         # USED BY: baseline_reconstruction.py during comparative backtests
│                                         # WHY NEEDED: Enables side-by-side performance benchmarking against legacy baseline settings
│
├── core/
│   ├── coordinator.py                    # WHAT: Multi-strategy conflict arbiter & execution orchestrator
│   │                                     # USED BY: Called by live_paper_trader.py on every tick, and by backtest_engine.py
│   │                                     # WHY NEEDED: Prevents conflicting orders, resolves multi-strategy collisions & routes fills
│   │
│   ├── risk_engine.py                    # WHAT: 1%-5% ATR equity sizing, portfolio heat caps & circuit breakers
│   │                                     # USED BY: Called by coordinator.py before any buy or short order is placed
│   │                                     # WHY NEEDED: Prevents account ruin by capping sizing and locking trading on 4% daily drawdowns
│   │
│   ├── paper_account.py                  # WHAT: Simulated ledger tracking balance, fills, fees (0.06%) & slippage (0.05%)
│   │                                     # USED BY: coordinator.py; reads and writes persistent state to paper_portfolio.json
│   │                                     # WHY NEEDED: Replicates real-world exchange friction so paper results match live market reality
│   │
│   ├── preflight.py                      # WHAT: 5 execution safety guards & LLM devil's advocate screener
│   │                                     # USED BY: Executed by coordinator.py at the exact moment a trade signal fires
│   │                                     # WHY NEEDED: Blocks bad fills during wide spreads, illiquid books, stale feeds, or panic spikes
│   │
│   ├── journal.py                        # WHAT: Decision telemetry logging & 24h settlement engine
│   │                                     # USED BY: coordinator.py on every tick; appends to decision_journal.jsonl
│   │                                     # WHY NEEDED: Full transparency; records exactly why every trade was executed or rejected
│   │
│   ├── backtest_engine.py                # WHAT: Event-driven backtesting engine with realistic fees & slippage
│   │                                     # USED BY: Used by optimizer.py, validator.py, and all research test suites
│   │                                     # WHY NEEDED: Accurately tests strategies over historical data without lookahead bias
│   │
│   ├── basis_yield.py                    # WHAT: Delta-neutral funding rate carry harvest engine (spot long + perp short)
│   │                                     # USED BY: coordinator.py during market scans; streams telemetry to live_status.json
│   │                                     # WHY NEEDED: Harvests 8-hour perp funding yields during quiet or sideways market regimes
│   │
│   ├── data_loader.py                    # WHAT: Multi-source market candle fetcher (CCXT crypto + Yahoo equities/ETFs)
│   │                                     # USED BY: coordinator.py, backtest_engine.py, and asset_selector.py
│   │                                     # WHY NEEDED: Pulls, validates, and locally caches OHLCV data to prevent API rate-limit bans
│   │
│   ├── fred_radar.py                     # WHAT: St. Louis Fed / VIX Macro Crash Defense Radar
│   │                                     # USED BY: coordinator.py before evaluating trade signals
│   │                                     # WHY NEEDED: Cuts position sizing by 50% or vetos long entries when the VIX spikes above 30
│   │
│   ├── polymarket_radar.py               # WHAT: Polymarket decentralized prediction market odds radar
│   │                                     # USED BY: coordinator.py and preflight.py
│   │                                     # WHY NEEDED: Injects crowd probability to pause aggressive trading before volatile macro events
│   │
│   ├── institutional_stress_tester.py    # WHAT: DSR, PSR, Ulcer Index & 1x-5x friction stress sweeps
│   │                                     # USED BY: validator.py and research stress suites
│   │                                     # WHY NEEDED: Mathematically proves a strategy isn't lucky and won't fail if trading fees rise
│   │
│   ├── skill_intelligence_gate.py        # WHAT: 13F whale radar, Pelosi tracker, drift rebalancer & SEC 8-K veto
│   │                                     # USED BY: coordinator.py and preflight.py
│   │                                     # WHY NEEDED: Vetos trades on adverse SEC filings and optimizes tax efficiency (wash-sale rules)
│   │
│   ├── process_lock.py                   # WHAT: Linux OS-level file locking via `fcntl.flock` PID tracking
│   │                                     # USED BY: live_paper_trader.py
│   │                                     # WHY NEEDED: Prevents duplicate concurrent trading loops
│   │
│   ├── cpi_benchmark.py                  # WHAT: 4-tier Automated Central Performance Index (CPI) Benchmark Harness
│   │                                     # USED BY: live_paper_trader.py --cpi-benchmark & tests/test_roadmap_items_1_to_4.py
│   │                                     # WHY NEEDED: Empirically scores Web4 Coordinated & Dynamic Router vs. Single-Agent & Uncoordinated baselines
│   │
│   ├── async_agent_runner.py             # WHAT: Non-blocking ThreadPoolExecutor worker pool for heavy Agent 9/10/11 tasks
│   │                                     # USED BY: live_paper_trader.py --loop
│   │                                     # WHY NEEDED: Prevents 20s-35s LLM/Optimizer jobs from stalling the 60s stop-loss loop
│   │                                     # USED BY: Acquired by live_paper_trader.py; checked by dashboard.py
│   │                                     # WHY NEEDED: Prevents duplicate bot instances from running simultaneously and doubling risk
│   │
│   ├── strategy.py                       # WHAT: Base abstract class defining the trading strategy interface (`BaseStrategy`)
│   │                                     # USED BY: Inherited by all strategy bots in strategies/
│   │                                     # WHY NEEDED: Enforces uniform methods across strategies so the coordinator can run any bot
│   │
│   └── signals.py                        # WHAT: Type-safe dataclass schemas for signals, trade directions & metadata
│                                         # USED BY: Shared across all strategies, coordinator.py, and paper_account.py
│                                         # WHY NEEDED: Eliminates runtime bugs by standardizing how trade instructions pass between modules
│
├── strategies/
│   ├── momentum.py                       # WHAT: Donchian channel breakout trend bot with ADX and EMA trend filters
│   │                                     # USED BY: coordinator.py and backtest_engine.py
│   │                                     # WHY NEEDED: Captures major multi-day bull/bear trend waves using dynamic ATR trailing stops
│   │
│   ├── mean_reversion.py                 # WHAT: Bollinger Bands + RSI swing bot with 50% partial take-profit
│   │                                     # USED BY: coordinator.py and backtest_engine.py
│   │                                     # WHY NEEDED: Generates steady returns during ranging markets where trend strategies suffer chop
│   │
│   └── stat_arb_pairs.py                 # WHAT: Cointegrated statistical arbitrage pairs bot (e.g. GLD:SLV spread)
│                                         # USED BY: coordinator.py to execute hedged dual-leg pair trades
│                                         # WHY NEEDED: Provides pure market-neutral alpha by trading mean-reverting valuation spreads
│
├── agent/
│   ├── governor.py                       # WHAT: 4-Gate auto-approval constitutional gatekeeper
│   │                                     # USED BY: Invoked after validator.py tests proposed parameter mutations
│   │                                     # WHY NEEDED: Enables safe self-improving autonomy by strictly blocking underperforming changes
│   │
│   ├── optimizer.py                      # WHAT: In-sample parameter exploration engine searching for optimal settings
│   │                                     # USED BY: Research suites & background tuning jobs; writes pending_proposals.json
│   │                                     # WHY NEEDED: Adapts strategy parameters to shifting market volatility instead of static numbers
│   │
│   ├── validator.py                      # WHAT: Out-of-sample walk-forward verification engine
│   │                                     # USED BY: Called automatically between the optimizer and the governor
│   │                                     # WHY NEEDED: Protects against curve-fitting by verifying candidates on unseen market data
│   │
│   ├── asset_selector.py                 # WHAT: Dynamic universe discovery & toxic drag pruning engine
│   │                                     # USED BY: Run periodically to rank and update the active watchlist
│   │                                     # WHY NEEDED: Prevents capital allocation into illiquid, unvolatlie, or persistently losing assets
│   │
│   ├── trade_analyst.py                  # WHAT: Post-trade forensic diagnosis & root-cause analyst
│   │                                     # USED BY: Triggered when trades close; creates trade_forensic_report.json
│   │                                     # WHY NEEDED: Diagnoses why trades failed so the AI optimizer knows what weaknesses to target
│   │
│   └── llm_client.py                     # WHAT: Zero-dependency local Ollama (phi4-mini) LLM client
│                                         # USED BY: trade_analyst.py and preflight.py Devil's Advocate
│                                         # WHY NEEDED: Delivers qualitative reasoning completely offline with zero cloud API costs
│
├── data/
│   ├── paper_portfolio.json              # WHAT: Persistent virtual ledger: cash, equity, open positions & closed fills
│   │                                     # USED BY: Written by paper_account.py; read by dashboard.py & coordinator.py
│   │                                     # WHY NEEDED: Preserves account state across restarts so positions and stops are never wiped
│   │
│   ├── decision_journal.jsonl            # WHAT: Immutable audit log recording every single tick decision and rejection
│   │                                     # USED BY: Written by journal.py; read during trade forensics and audits
│   │                                     # WHY NEEDED: Provides full proof and debugging records of why trades were accepted or vetoed
│   │
│   ├── live_status.json                  # WHAT: Real-time IPC telemetry state file (PID, timestamp, pairs & scan feed)
│   │                                     # USED BY: Written by live_paper_trader.py; read continuously by dashboard.py
│   │                                     # WHY NEEDED: Powers the live terminal dashboard with instant zero-lag updates without db locks
│   │
│   ├── trade_forensic_report.json        # WHAT: AI forensic report diagnosing recent closed trades
│   │                                     # USED BY: Written by trade_analyst.py; read by optimizer.py and the trader
│   │                                     # WHY NEEDED: Identifies recurring pattern mistakes to prevent repeating the same losses
│   │
│   ├── asset_selection_report.json       # WHAT: JSON output report from dynamic asset universe screening
│   │                                     # USED BY: asset_selector.py and coordinator.py
│   │                                     # WHY NEEDED: Records the liquidity score, spread, and momentum rank for screened assets
│   │
│   ├── benchmark_results.json            # WHAT: Archive of benchmark comparison runs against SPY, QQQ, and BTC
│   │                                     # USED BY: benchmark.py and executive reporting
│   │                                     # WHY NEEDED: Quantifies agent outperformance relative to passive buy-and-hold benchmarks
│   │
│   ├── candidate_upgrades_backtest.json  # WHAT: Backtest metrics generated during parameter mutation experiments
│   │                                     # USED BY: optimizer.py and validator.py
│   │                                     # WHY NEEDED: Provides quantitative proof before candidate parameter sets move to the Governor
│   │
│   ├── agent_governance_audit.log        # WHAT: Immutable text log of all Governor approvals and rejections
│   │                                     # USED BY: governor.py to append auto-approval telemetry
│   │                                     # WHY NEEDED: Compliance audit trail recording exact metrics that caused promotions/rejections
│   │
│   └── *.csv                             # WHAT: Cached historical 1h OHLCV candle datasets for crypto and equities
│                                         # USED BY: data_loader.py, backtest_engine.py, and optimizer.py
│                                         # WHY NEEDED: Enables fast, repeatable backtests and model evaluations without hitting live APIs
│
├── reports/
│   ├── oos_validation_report.json        # WHAT: Empirical 90-day out-of-sample backtest report across all bots
│   │                                     # USED BY: Governor, developers, and users to confirm strategy profitability
│   │                                     # WHY NEEDED: Provides verifiable mathematical proof that strategies beat transaction friction
│   │
│   ├── optimization_experiments.json     # WHAT: Historical archive of parameter search experiments and trial scores
│   │                                     # USED BY: optimization_experiments.py and quant research analysis
│   │                                     # WHY NEEDED: Prevents testing duplicate parameter grids and tracks parameter convergence
│   │
│   ├── basis_yield_research.json         # WHAT: Quantitative research dataset analyzing perpetual funding APYs across tokens
│   │                                     # USED BY: basis_yield_research.py and coordinator.py
│   │                                     # WHY NEEDED: Validates the historical stability and profitability of delta-neutral funding carry
│   │
│   ├── baseline_reconstruction.json      # WHAT: Performance benchmark metrics of baseline code before upgrades
│   │                                     # USED BY: baseline_reconstruction.py
│   │                                     # WHY NEEDED: Serves as the control group baseline to prove new code improves profitability
│   │
│   ├── llm_governance_benchmark_20.json  # WHAT: Benchmark dataset recording latency and accuracy of phi4-mini decisions
│   │                                     # USED BY: evaluate_llm_governance_benchmark.py
│   │                                     # WHY NEEDED: Verifies that local LLM inference stays within 200ms latency without hallucinations
│   │
│   ├── INDEPENDENT_OPTIMIZATION_VALIDATION.md # WHAT: Independent verification report validating strategy parameter gains
│   │                                     # USED BY: Quant reviewers and compliance officers
│   │                                     # WHY NEEDED: Documented evidence that Sharpe improvements were verified on unseen data
│   │
│   ├── git_baseline_comparison/
│   │   ├── WORKING_TREE_VS_GIT_REPORT.md # WHAT: Markdown audit comparing working tree optimizations against git commit base
│   │   │                                 # USED BY: Engineering leads reviewing PRs and releases
│   │   │                                 # WHY NEEDED: Explains every functional enhancement made across files against the git baseline
│   │   │
│   │   ├── comparison_worker.py          # WHAT: Parallel worker script that runs isolated backtests on git revisions
│   │   │                                 # USED BY: run_comparison_suite.py
│   │   │                                 # WHY NEEDED: Isolates backtest memory to avoid process pollution across test iterations
│   │   │
│   │   ├── run_comparison_suite.py       # WHAT: Orchestrator running side-by-side backtests of working tree vs git commit
│   │   │                                 # USED BY: Developers running regression benchmarks
│   │   │                                 # WHY NEEDED: Computes exact delta in Sharpe ratio, net profit, and max drawdown
│   │   │
│   │   ├── comparison_results.json       # WHAT: Raw output data from comparison backtests
│   │   │                                 # USED BY: WORKING_TREE_VS_GIT_REPORT.md
│   │   │                                 # WHY NEEDED: Quantitative backing for git comparison conclusions
│   │   │
│   │   ├── strategy_comparison.json      # WHAT: Per-strategy breakdown of baseline vs optimized performance
│   │   │                                 # USED BY: run_comparison_suite.py
│   │   │                                 # WHY NEEDED: Identifies which specific strategies contributed to overall portfolio gains
│   │   │
│   │   └── git_state_before_after.txt    # WHAT: Git diff summary recording files modified between benchmarks
│   │                                     # USED BY: Engineering audit workflows
│   │                                     # WHY NEEDED: Fast text reference for changed lines and commit hashes
│   │
│   ├── independent_validation/
│   │   └── stress_and_dsr_results.json   # WHAT: Deflated Sharpe Ratio (DSR) and stress test results on production models
│   │                                     # USED BY: institutional_stress_tester.py
│   │                                     # WHY NEEDED: Proves statistically that the strategy Sharpe ratio is not a false discovery
│   │
│   └── targeted_profitability_optimization/
│       ├── DEPLOYMENT_APPROVAL_REPORT.md # WHAT: Executive approval document certifying production deployment readiness
│       │                                 # USED BY: Risk committees and automated deployment gates
│       │                                 # WHY NEEDED: Formal signoff proving all 4 constitutional gates passed successfully
│       │
│       ├── FINAL_EXECUTIVE_SUMMARY.md    # WHAT: High-level overview of portfolio returns, risk ratios, and alpha drivers
│       │                                 # USED BY: Stakeholders and portfolio managers
│       │                                 # WHY NEEDED: Condenses complex mathematical backtests into executive-level KPIs
│       │
│       ├── IMPLEMENTATION_CHANGELOG.md   # WHAT: Chronological changelog detailing exact code and parameter modifications
│       │                                 # USED BY: Developers tracing system changes over time
│       │                                 # WHY NEEDED: Provides transparency into when and why specific constants were tuned
│       │
│       ├── OPTIMIZATION_RESULTS.md       # WHAT: Comprehensive results table for all tested parameter combinations
│       │                                 # USED BY: Quant researchers
│       │                                 # WHY NEEDED: Documents tested parameter surfaces to avoid repeating sub-optimal tests
│       │
│       ├── OPTIMIZATION_STRATEGY.md      # WHAT: Research thesis outlining the mathematical methodology used for tuning
│       │                                 # USED BY: Quants designing optimization sweeps
│       │                                 # WHY NEEDED: Defines bounds, objective functions, and constraints for the optimizer
│       │
│       ├── run_optimization_research.py  # WHAT: Script orchestrating targeted parameter tuning across assets
│       │                                 # USED BY: Quant researchers via CLI
│       │                                 # WHY NEEDED: Automates multi-asset grid searches and records output metrics
│       │
│       ├── strategy_analysis.json        # WHAT: Granular metrics per strategy under various parameter combinations
│       │                                 # USED BY: run_optimization_research.py
│       │                                 # WHY NEEDED: Feeds performance data into OPTIMIZATION_RESULTS.md
│       │
│       ├── test_statarb_reconciliation.py# WHAT: Unit test verifying statistical arbitrage spread math and hedge ratios
│       │                                 # USED BY: pytest and research runners
│       │                                 # WHY NEEDED: Guarantees that pair trade math perfectly reconciles with broker balances
│       │
│       └── validation_results.json       # WHAT: Walk-forward validation results for targeted optimization candidates
│                                         # USED BY: DEPLOYMENT_APPROVAL_REPORT.md
│                                         # WHY NEEDED: Validates candidate performance across out-of-sample data slices
│
├── research/
│   ├── run_shifted_oos_validation.py     # WHAT: 90-day shifted out-of-sample multi-strategy validation suite
│   │                                     # USED BY: Quant researchers before committing major parameter upgrades
│   │                                     # WHY NEEDED: Verifies whether strategy performance holds up across non-overlapping market periods
│   │
│   ├── run_institutional_stress_suite.py # WHAT: Historical crisis simulation runner (2020 COVID, 2022 rate hikes)
│   │                                     # USED BY: Risk auditors and developers testing worst-case resilience
│   │                                     # WHY NEEDED: Verifies that circuit breakers and drawdown locks survive market flash crashes
│   │
│   ├── basis_yield_research.py           # WHAT: Historical funding rate analysis and carry yield backtester
│   │                                     # USED BY: Quant researchers investigating market-neutral income
│   │                                     # WHY NEEDED: Determines threshold funding rates (e.g. 8% APY) for delta-neutral trades
│   │
│   ├── monte_carlo_and_sensitivity.py    # WHAT: Monte Carlo trade resampling and parameter sensitivity heatmaps
│   │                                     # USED BY: Risk analysts testing curve-fitting fragility
│   │                                     # WHY NEEDED: Measures probability of maximum drawdown under randomized trade orders
│   │
│   ├── optimization_experiments.py       # WHAT: Autonomous parameter exploration runner across strategies
│   │                                     # USED BY: AI swarm and researcher exploration workflows
│   │                                     # WHY NEEDED: Searches multi-dimensional parameter spaces to locate robust parameter plateaus
│   │
│   ├── evaluate_llm_governance_benchmark.py # WHAT: Latency and accuracy benchmarking suite for local phi4-mini
│   │                                     # USED BY: Developers validating LLM preflight veto reliability
│   │                                     # WHY NEEDED: Ensures LLM responses finish well within the 60-second live trading cycle
│   │
│   ├── backtest_live_replay.py           # WHAT: Simulates live tick-by-tick market conditions from historical logs
│   │                                     # USED BY: Developers validating coordinator and daemon behavior
│   │                                     # WHY NEEDED: Tests real-time order routing and trailing stop triggering without waiting in real time
│   │
│   ├── backtest_compare.py               # WHAT: Side-by-side comparison script for evaluating two strategy configs
│   │                                     # USED BY: Researchers comparing competing parameter proposals
│   │                                     # WHY NEEDED: Shows immediate delta in equity curves, Sharpe, and trade counts
│   │
│   ├── backtest_overall.py               # WHAT: Comprehensive multi-asset, multi-strategy portfolio backtest runner
│   │                                     # USED BY: Developers evaluating total portfolio performance
│   │                                     # WHY NEEDED: Measures portfolio-wide correlation, diversification benefits, and total return
│   │
│   ├── backtest_upgrade_candidates.py    # WHAT: Verification script testing pending proposals against deployment criteria
│   │                                     # USED BY: Governor verification pipelines
│   │                                     # WHY NEEDED: Confirms that candidate parameters satisfy all 4 auto-approval gates
│   │
│   ├── baseline_reconstruction.py        # WHAT: Reconstructs performance of previous baseline git commits
│   │                                     # USED BY: Developers verifying that code changes did not cause regressions
│   │                                     # WHY NEEDED: Ensures code refactorings never degrade historical alpha
│   │
│   ├── benchmark.py                      # WHAT: Standardized benchmarking script comparing agent against SPY and BTC
│   │                                     # USED BY: Executive reporting and investor presentations
│   │                                     # WHY NEEDED: Proves whether the agent generates positive alpha over passive investing
│   │
│   └── independent_audit_suite.py        # WHAT: Mathematical validation script checking execution drag and risk formulas
│                                         # USED BY: Independent code reviewers and compliance officers
│                                         # WHY NEEDED: Verifies zero lookahead bias, fee deduction accuracy, and risk limits
│
├── docs/
│   ├── SYSTEM_WALKTHROUGH.md             # WHAT: Master 540+ line documentation of all formulas, rules, and architecture
│   │                                     # USED BY: Engineers, quants, and AI agents onboarding to the repository
│   │                                     # WHY NEEDED: The single source of truth for the system's quantitative and software design
│   │
│   ├── REPOSITORY_FILE_MAP.md            # WHAT: Granular file-by-file dependency and purpose directory (THIS FILE)
│   │                                     # USED BY: Developers, code reviewers, and AI agents looking up component interactions
│   │                                     # WHY NEEDED: Provides instant reference for caller-callee relationships and failure impacts
│   │
│   ├── web4-autonomous-levels.md         # WHAT: Specification of autonomous agent levels (Level 1 to Level 5)
│   │                                     # USED BY: Project planners and architectural governance
│   │                                     # WHY NEEDED: Establishes the clear milestone roadmap for full self-improving algorithmic trading
│   │
│   ├── presentation_assets/
│   │   ├── build_presentation.py         # WHAT: Python automation script generating PowerPoint and PDF presentation decks
│   │   │                                 # USED BY: Developers rendering the presentation via CLI
│   │   │                                 # WHY NEEDED: Converts slide templates and chart renders into professional PDF/PPTX formats
│   │   │
│   │   ├── pdf_render_template.html      # WHAT: HTML/CSS template used by headless browser engines to print PDF slides
│   │   │                                 # USED BY: build_presentation.py
│   │   │                                 # WHY NEEDED: Defines institutional typography, color palettes, and slide layouts
│   │   │
│   │   ├── slide3_architecture.png       # WHAT: High-resolution architecture diagram render for Slide 3
│   │   │                                 # USED BY: Embedded into presentation slides
│   │   │                                 # WHY NEEDED: Visual illustration of the 4-tier trading agent architecture
│   │   │
│   │   ├── slide6_performance_charts.png # WHAT: Chart rendering comparing agent equity vs Buy & Hold for Slide 6
│   │   │                                 # USED BY: Embedded into presentation slides
│   │   │                                 # WHY NEEDED: Visual proof of risk-adjusted alpha generation
│   │   │
│   │   ├── slide7_defense_proof.png      # WHAT: Chart rendering showing drawdown defense during market crashes for Slide 7
│   │   │                                 # USED BY: Embedded into presentation slides
│   │   │                                 # WHY NEEDED: Visual evidence of VIX circuit breakers preventing large portfolio drops
│   │   │
│   │   └── slide_preview-*.png           # WHAT: Preview images of individual generated presentation slides (Slides 1-8)
│   │                                     # USED BY: Markdown documentation and slide deck previewers
│   │                                     # WHY NEEDED: Allows instant visual verification of slide decks without opening PDF files
│   │
│   └── system_audit/
│       ├── ARCHITECTURE_DEEP_DIVE.md     # WHAT: Comprehensive audit paper decomposing system tiers and class relationships
│       │                                 # USED BY: System architects and senior software engineers
│       │                                 # WHY NEEDED: Documents decoupling patterns, data flow contracts, and concurrency models
│       │
│       ├── AUDIT_FINDINGS.md             # WHAT: Findings log from institutional code audits with applied remediation steps
│       │                                 # USED BY: Security auditors and compliance teams
│       │                                 # WHY NEEDED: Documents resolved edge cases, race conditions, and precision safeguards
│       │
│       ├── COMPLETE_TRADING_LIFECYCLE.md # WHAT: Granular walkthrough of a trade from candle fetch to 24h settlement
│       │                                 # USED BY: New engineers tracing trade lifecycle state transitions
│       │                                 # WHY NEEDED: Explains every intermediate state (SCAN -> SIGNAL -> PREFLIGHT -> FILL -> STOP)
│       │
│       ├── DATA_AND_EXECUTION_PIPELINE.md# WHAT: Specification of candle fetching, caching, pre-flight safety & order routing
│       │                                 # USED BY: Quant developers maintaining market data connectors
│       │                                 # WHY NEEDED: Documents timestamp alignment rules, gap detection, and fill modeling
│       │
│       ├── FINAL_SYSTEM_AUDIT_REPORT.md  # WHAT: Formal certification of system readiness, test coverage, and safety compliance
│       │                                 # USED BY: Lead architects and external auditors
│       │                                 # WHY NEEDED: Official record certifying that the codebase meets institutional standards
│       │
│       ├── PERFORMANCE_AND_SCALABILITY.md# WHAT: Profiling study on tick latency, RAM consumption, and scaling capacity
│       │                                 # USED BY: DevOps and infrastructure engineers
│       │                                 # WHY NEEDED: Confirms memory stability and ensures tick execution completes in < 5 seconds
│       │
│       ├── QUANTITATIVE_FORMULAS.md      # WHAT: Mathematical derivations for ATR, Donchian, Bollinger, RSI, Z-Score, DSR & PSR
│       │                                 # USED BY: Quantitative researchers validating indicator calculations
│       │                                 # WHY NEEDED: Serves as the mathematical specification to prevent calculation drift
│       │
│       ├── REQUIREMENTS_TRACEABILITY.md  # WHAT: Traceability matrix linking business requirements to exact file line numbers
│       │                                 # USED BY: Compliance auditors and project managers
│       │                                 # WHY NEEDED: Proves that every institutional requirement is implemented in code
│       │
│       ├── RISK_AND_GOVERNANCE.md        # WHAT: Whitepaper specifying 1%-5% ATR sizing, circuit breakers, and 4-gate governance
│       │                                 # USED BY: Risk officers and portfolio managers
│       │                                 # WHY NEEDED: The quantitative risk standard preventing capital catastrophic failure
│       │
│       ├── STRATEGY_EXPLANATION.md       # WHAT: Conceptual thesis, mathematical triggers, and regime suitability for all bots
│       │                                 # USED BY: Traders and strategists reviewing alpha engines
│       │                                 # WHY NEEDED: Explains why and when each strategy is expected to make or lose money
│       │
│       ├── SYSTEM_OVERVIEW.md            # WHAT: High-level architectural summary of the Web4 autonomous trading agent
│       │                                 # USED BY: General technical readers and new team members
│       │                                 # WHY NEEDED: Easy-to-read introductory document covering the whole project
│       │
│       └── USER_GUIDE.md                 # WHAT: Practical manual with CLI commands, flags, and troubleshooting steps
│                                         # USED BY: End users and live operators running the daemon and dashboard
│                                         # WHY NEEDED: Step-by-step instructions for operating, resetting, and monitoring the system
│
└── tests/
    ├── test_asset_profiles.py            # WHAT: Tests asset-specific parameter overrides (forex, equity, crypto)
    │                                     # USED BY: pytest runner during automated test runs
    │                                     # WHY NEEDED: Ensures BTC, ETH, and equities receive appropriate volatility parameters
    │
    ├── test_asset_selector.py            # WHAT: Tests dynamic asset universe screening and liquidity scoring logic
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Guarantees that illiquid or unvolatlie assets are correctly flagged and pruned
    │
    ├── test_conditional_auto_approval.py # WHAT: Tests Governor 4-Gate auto-approval criteria and constitutional boundaries
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Prevents parameter changes with low Sharpe or high drawdown from being approved
    │
    ├── test_coordinator.py               # WHAT: Tests multi-strategy conflict resolution, position limits & order routing
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Verifies that opposing strategy signals on the same symbol are safely resolved
    │
    ├── test_data_quality.py              # WHAT: Validates data loader candle completeness, timestamp alignment & gap handling
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Prevents corrupt, missing, or misaligned candles from feeding into indicators
    │
    ├── test_dynamic_pairs_and_basis_live.py # WHAT: Tests statistical arbitrage pairs trading and funding carry execution logic
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Verifies that dual-leg hedged pairs enter and exit cleanly at target Z-Scores
    │
    ├── test_governor.py                  # WHAT: Tests Governor safety rules, automatic rollback, and audit logging
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Confirms that failed parameter proposals trigger an instant config rollback
    │
    ├── test_institutional_alpha_suite.py # WHAT: Tests institutional alpha indicators, trend filters, and regime detection
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Validates mathematical correctness of ADX, EMA, and Donchian calculations
    │
    ├── test_institutional_income_upgrades.py # WHAT: Verifies basis carry harvesting, funding payments, and fee drag calculations
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Confirms that 8h funding yields are credited and fee friction is deducted properly
    │
    ├── test_institutional_stress_suite.py# WHAT: Tests DSR, PSR, Ulcer Index, and break-even friction stress test calculations
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Guarantees that statistical risk metrics produce exact mathematical figures
    │
    ├── test_institutional_upgrades.py    # WHAT: Verifies trailing stop mechanics, partial take-profits, and breakeven stops
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Prevents execution logic bugs when moving stops to lock in unrealized profits
    │
    ├── test_journal_and_backtest.py      # WHAT: Tests decision journal logging accuracy and backtest engine execution fidelity
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Ensures that simulated trades record exact execution timestamps and reasons
    │
    ├── test_live_trader.py               # WHAT: Tests live trader daemon startup, tick execution cycle, and graceful shutdown
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Verifies that SIGINT/SIGTERM handlers cleanly release locks and save portfolio state
    │
    ├── test_optimizer.py                 # WHAT: Tests in-sample parameter exploration and metric scoring algorithms
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Confirms that the optimizer correctly navigates parameter spaces without crashes
    │
    ├── test_paper_account_advanced.py    # WHAT: Validates virtual balance tracking, cash reservation, fees, and margin accounting
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Guarantees that cash is accurately deducted and open positions never exceed balance
    │
    ├── test_performance_upgrades.py      # WHAT: Verifies caching layers, speed optimizations, and memory efficiency
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Ensures candle caching reduces execution latency without serving stale data
    │
    ├── test_pre_deployment_safety.py     # WHAT: Ensures all invariant safety constraints (AGENTS.md) cannot be breached
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Guarantees that real-money trading flags can never be activated accidentally
    │
    ├── test_process_lock.py              # WHAT: Tests file locking concurrency to ensure multiple daemons cannot run simultaneously
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Proves that launching a second bot instance is immediately blocked by flock
    │
    ├── test_profitability_optimizations.py # WHAT: Validates that optimized parameters maintain superior risk-adjusted returns
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Confirms that new parameter defaults improve Sharpe without increasing drawdown
    │
    ├── test_remediation_suite.py         # WHAT: Tests automated remediation mechanisms triggered by post-trade forensics
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Verifies that repeated stop-outs trigger automatic cooldowns on toxic assets
    │
    ├── test_risk_and_strategy.py         # WHAT: Validates that all strategy signals conform strictly to 1%-5% sizing limits
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Proves that position sizing can never exceed 5% of equity regardless of volatility
    │
    ├── test_risk_circuit_breakers.py     # WHAT: Tests daily 4% equity drawdown lock and 12% peak-to-trough circuit breakers
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Ensures trading is immediately halted when portfolio drawdown limits are breached
    │
    ├── test_signals.py                   # WHAT: Tests technical indicator math (Donchian, Bollinger, RSI, ATR, Z-Score)
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Guarantees zero division errors, correct moving average smoothing, and bounds
    │
    ├── test_skill_upgrades_suite.py      # WHAT: Tests 13F whale parsing, Pelosi trade monitoring, and NYSE market clock rules
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Validates alternative data parsing and prevents trading outside NYSE hours
    │
    ├── test_strategies.py                # WHAT: Comprehensive unit tests for momentum, mean reversion, and pairs strategies
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Tests signal generation under simulated bull, bear, and ranging market candles
    │
    ├── test_tier1_institutional_features.py # WHAT: Tests Tier-1 institutional safeguards, execution guards, and order validation
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Verifies that pre-flight safety checks catch extreme spreads and flash crashes
    │
    ├── test_trade_analyst.py             # WHAT: Tests forensic trade analysis and LLM prompt construction logic
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Ensures trade telemetry is correctly structured for the local LLM prompt
    │
    ├── test_audit_fixes.py               # WHAT: Regression suite for all 11 audit fixes (rollback, log rotation, live-price exits, weekend staleness)
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Guarantees every critical bug fix and rollback safety net stays intact
    │
    ├── test_roadmap_items_1_to_4.py      # WHAT: Unit & integration tests for CPI Benchmark, 72h Probationary Reentry, Maker Re-Quoting, & Async Runner
    │                                     # USED BY: pytest runner
    │                                     # WHY NEEDED: Verifies all 4 deployed roadmap enhancements and 1%-5% risk bounds
    │
    └── test_web4_self_improvement.py     # WHAT: Tests the end-to-end self-improvement loop (optimizer -> validator -> governor)
                                          # USED BY: pytest runner
                                          # WHY NEEDED: Proves that the complete autonomous self-tuning cycle operates safely without errors
```
