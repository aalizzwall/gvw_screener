# Issue #11 reference projects

Issue #11 proposes an AI stock-analysis assistant before users make trading
decisions and links Shioaji Pro and TradingAgents as examples. The projects
are useful in different ways; neither is a direct drop-in implementation.

## Shioaji Pro

[Shioaji Pro](https://sinotrade.github.io/shioaji-pro-app/) is a desktop
trading application built around Sinopac's Shioaji brokerage API, not a
standalone stock-selection model. Its site describes agent-assisted research,
strategy writing/backtesting, and brokerage workflows. It also states that
the user supplies/configures the AI service, that research/backtests simulate
trades, and that the agent does not recommend tickers in its portfolio
allocation workflow; the user supplies target allocations and rules.

The key implementation limitation is stated by the project itself: the
[public repository](https://github.com/Sinotrade/shioaji-pro-app) publishes
the interface and Shioaji HTTP/SSE integration examples, while the AI agent
and backtesting engine are private desktop modules. Thus it is a useful
product/UX reference (visible permissions, simulation vs. live trading,
explicit approval), but its AI implementation cannot be reused from that
repository. Its brokerage execution features are outside Issue #11's stated
pre-trade analysis scope.

## TradingAgents

[TradingAgents](https://github.com/TauricResearch/TradingAgents) is an
open-source, LLM-based multi-agent financial-trading research framework. Its
README describes specialist fundamental, sentiment, news, and technical
analysts; bullish/bearish researcher debate; and trader, risk-management, and
portfolio-manager roles. The project explicitly says it is for research and
is not financial, investment, or trading advice.

The source layout provides a reference for separating analysts and workflow
orchestration ([agents](https://github.com/TauricResearch/TradingAgents/tree/main/tradingagents/agents),
[graph](https://github.com/TauricResearch/TradingAgents/tree/main/tradingagents/graph),
[dataflows](https://github.com/TauricResearch/TradingAgents/tree/main/tradingagents/dataflows)).
Its package metadata requires Python 3.11+ and includes LangChain/LangGraph,
provider integrations, and other dependencies
([pyproject.toml](https://github.com/TauricResearch/TradingAgents/blob/main/pyproject.toml)).
The vendor tree includes Yahoo and Alpha Vantage integrations, but no TWSE or
FinMind adapter; it therefore does not directly plug into this project's
existing Taiwan-market provider factory.

## Recommendation for this project

Use Shioaji Pro as a reference for safe interaction design, not as an AI code
source. Use TradingAgents as a source of ideas for separating analysis roles,
but do not transplant its full multi-agent stack into the current Telegram
bot.

A smaller first release should use this project's existing ranking, price,
and financial snapshots as the only analysis context, then make one optional
OpenAI request to produce a structured explanation: positive evidence,
counter-evidence/risks, data freshness or gaps, and a clearly non-authoritative
conclusion. Keep ranking deterministic, clearly label AI output as reference
only, and do not connect it to order execution. This minimizes added
dependencies, API cost, and unsupported claims while addressing the request
for an AI analyst before a trade.
