# Bench baselines (`python -m blemees.bench`)

Empirical numbers captured against the real Claude / Codex CLIs. Not
gates — these are baselines so future regressions are visible. Spec
§11.4 lists the *targets*; this document records the actuals.

Methodology:

* Daemon is a clean `python -m blemees` started immediately before the
  bench (no warmup outside what the bench itself does).
* `--prompt "Reply with just the word OK."` (default).
* Latency is measured from the `agent.user` send to the first
  `agent.delta` / `agent.message` / `agent.tool_use` / `agent.result`
  frame. Bookkeeping frames (`agent.system_init`, `agent.notice`) are
  deliberately excluded — they fire before the upstream model has
  produced anything, so counting them would understate latency.

## Claude

```
$ python -m blemees.bench --backend claude --iters 3
```

Captured 2026-04-28, dev laptop (M-class, warm network). Three iterations:

| Metric | iter 1 | iter 2 | iter 3 | average | spec §11.4 target |
|---|---|---|---|---|---|
| `cold_first_event` | 1.44 s | 1.29 s | 1.36 s | **1.36 s** | ≤ 1.5 s |
| `warm_first_event` | 0.98 s | 1.29 s | 0.90 s | **1.06 s** | ≤ 0.5 s |
| `resume_first_event` | 1.12 s | 1.16 s | 1.26 s | **1.18 s** | ≤ 1.5 s |

The warm-user target of 0.5 s is missed by ~0.5 s on every iteration.
Most of that is round-trip to the model edge for haiku-class models
on a warm session — not daemon overhead. The daemon's pipe-to-pipe
forwarding is sub-millisecond.

## Codex

`python -m blemees.bench --backend codex` skips the
`resume_first_event` step: codex 0.125.x's `tools/call codex-reply`
does not reliably rehydrate state when called from a *fresh*
`codex mcp-server` process with a `threadId` from a prior process —
it returns an empty success result without resuming the thread, which
would hang the bench's first-event timer. The same-process resume
path (single backend child, codex-reply on the second turn) *does*
work and is exercised by the daemon-mock suite
(`test_codex_session_resume_uses_codex_reply`).

```
$ python -m blemees.bench --backend codex --iters 3
```

Captured 2026-04-28 on the same laptop:

| Metric | iter 1 | iter 2 | iter 3 | average | spec §11.4 target |
|---|---|---|---|---|---|
| `cold_first_event` | 5.47 s | 2.78 s | 8.60 s | **5.62 s** | empirical only |
| `cold_open_plus_first` | 5.49 s | 2.83 s | 8.65 s | **5.66 s** | empirical only |
| `warm_first_event` | 1.40 s | 1.69 s | 1.21 s | **1.43 s** | ≤ 1.0 s |

Cold-open variance is high (2.8–8.6 s) — codex's `initialize` +
`tools/list` handshake plus first model RTT swing widely with network
state. The warm-delta target of 1.0 s is missed by ~0.4 s on these
runs.

## Reproducing

```sh
# Pre-reqs:
#   * `claude auth login` for claude bench,
#   * `codex login` for codex bench.

SOCK=/tmp/blemees-bench.sock
python -m blemees --socket "$SOCK" &
DAEMON_PID=$!

python -m blemees.bench --backend claude --iters 3
python -m blemees.bench --backend codex  --iters 3

kill -TERM $DAEMON_PID
```
