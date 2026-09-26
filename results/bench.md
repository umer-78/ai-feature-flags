## Upgrades

100 replays each, 80,000 requests, candidate on 1/5/25/50% of users for 20,000 requests per stage. Extra wrong answers: served by the candidate beyond what control would have got wrong (median over replays); a direct switch is every request on the candidate. Tolerance 2 points.

| Upgrade | Overall | Guardrails | Rolled back | Median rollback | Candidate requests (median) | Extra wrong answers | Direct switch |
|---|---|---|---|---|---|---|---|
| gpt-4o-2024-05-13 → gpt-4o-2024-08-06 | 77.3% → 77.5% | overall | 0/100 | — | 16,133 | -22 | -123 |
| gpt-4o-2024-05-13 → gpt-4o-2024-08-06 | 77.3% → 77.5% | segments | 1/100 (openbookqa) | request 22,000 (5% stage) | 16,132 | -22 | -123 |
| gemini-1.5-pro-001 → gemini-1.5-pro-002 | 77.8% → 78.6% | overall | 0/100 | — | 16,133 | -141 | -686 |
| gemini-1.5-pro-001 → gemini-1.5-pro-002 | 77.8% → 78.6% | segments | 0/100 | — | 16,133 | -141 | -686 |
| gemini-1.5-flash-001 → gemini-1.5-flash-002 | 70.8% → 62.6% | overall | 100/100 (overall) | request 21,025 (5% stage) | 266 | 26 | 6,574 |
| gemini-1.5-flash-001 → gemini-1.5-flash-002 | 70.8% → 62.6% | segments | 100/100 (gsm8k, overall) | request 11,350 (1% stage) | 120 | 10 | 6,574 |
| gemini-1.0-pro-001 → gemini-1.0-pro-002 | 60.8% → 57.5% | overall | 69/100 (overall) | request 57,700 (25% stage) | 8,466 | 309 | 2,672 |
| gemini-1.0-pro-001 → gemini-1.0-pro-002 | 60.8% → 57.5% | segments | 100/100 (legalbench, mmlu, openbookqa, overall) | request 39,625 (5% stage) | 1,228 | 42 | 2,672 |
| mistral-large-2402 → mistral-large-2407 | 62.8% → 68.7% | overall | 0/100 | — | 16,133 | -954 | -4,761 |
| mistral-large-2402 → mistral-large-2407 | 62.8% → 68.7% | segments | 91/100 (math) | request 55,000 (25% stage) | 5,214 | -288 | -4,761 |
| llama-3-70b → llama-3.1-70b-instruct | 74.7% → 74.0% | overall | 1/100 (overall) | request 25,600 (5% stage) | 16,132 | 129 | 563 |
| llama-3-70b → llama-3.1-70b-instruct | 74.7% → 74.0% | segments | 100/100 (legalbench) | request 27,450 (5% stage) | 602 | 5 | 563 |

## Identical candidate (A/A): false rollbacks

Each of the 12 versions against itself, 100 replays each; every rollback is a false alarm. Tolerance 0, so the candidate sits exactly on the line the test guards, the hardest case. Target: under 5.0%.

| Test | Guardrails | False rollbacks | 95% interval |
|---|---|---|---|
| always-valid (this project) | overall | 45/1200 (3.8%) | 2.8%–5.0% |
| always-valid (this project) | segments | 51/1200 (4.2%) | 3.2%–5.5% |
| fixed-sample test re-run every 50 requests | overall | 551/1200 (45.9%) | 43.1%–48.7% |
| fixed-sample test re-run every 50 requests | segments | 612/1200 (51.0%) | 48.2%–53.8% |
