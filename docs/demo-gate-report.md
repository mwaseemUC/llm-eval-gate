# Gate report from demo PR #1

Copied from the step summary of the failing `eval-gate` run on
[PR #1](https://github.com/mwaseemUC/llm-eval-gate/pull/1)
([run](https://github.com/mwaseemUC/llm-eval-gate/actions/runs/38823874260)).

## Eval gate: FAILED

- **Accuracy dropped 10.0 pts (100.0% -> 90.0%), limit is 3.0 pts**

| Metric | Baseline | Latest | Delta |
|---|---:|---:|---:|
| Accuracy | 100.0% | 90.0% | -10.0 pts |
| Macro-F1 | 1.000 | 0.898 | -0.102 |
| Tickets (errors) | 50 (0) | 50 (0) |  |
| Cost total | $0.0038 | $0.0040 | +0.0002 |
| Cost / ticket | $0.00008 | $0.00008 | +0.00000 |
| Latency p50 | 1.14s | 1.23s | +0.09s |
| Latency p95 | 1.66s | 1.82s | +0.16s |
| Tokens in / out | 120118 / 4269 | 58368 / 4004 |  |

### Per-class F1

| Class | Baseline F1 | Latest F1 | Delta | Support |
|---|---:|---:|---:|---:|
| billing | 1.000 | 0.842 | -0.158 | 9 |
| bug_report | 1.000 | 0.889 | -0.111 | 9 |
| feature_request | 1.000 | 0.941 | -0.059 | 8 |
| account_access | 1.000 | 1.000 | +0.000 | 8 |
| how_to | 1.000 | 0.714 | -0.286 | 8 |
| cancellation | 1.000 | 1.000 | +0.000 | 8 |

### Right -> wrong (5)

- `t007` (label `billing`): `billing` -> `how_to`
- `t009` (label `how_to`): `how_to` -> `billing`
- `t015` (label `how_to`): `how_to` -> `feature_request`
- `t025` (label `bug_report`): `bug_report` -> `billing`
- `t038` (label `how_to`): `how_to` -> `bug_report`

### Wrong -> right (0)

_none_

### Runs

- Baseline: model `claude-haiku-5-5`, prompt `v2`, sha `ffdd26761995`, 2026-10-15T23:23:28+00:00
- Latest: model `claude-haiku-5-5`, prompt `v3-trimmed`, sha `39ac89a8ecef`, 2026-10-15T23:29:45+00:00
