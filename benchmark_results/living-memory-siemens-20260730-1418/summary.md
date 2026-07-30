# Living-memory benchmark summary

| Track | Backend | Model | Weighted score | Cases | Failed |
|---|---|---|---:|---:|---:|
| pure_model | siemens | Mistral-Small-24B-Instruct-2501-FP8-dynamic | 51.95 | 14 | 11 |
| pure_model | siemens | deepseek-v4-flash | 64.34 | 14 | 10 |
| pure_model | siemens | gpt-oss-120b | 70.75 | 14 | 9 |
| pure_model | siemens | ministral-3-14b-instruct-2512 | 73.64 | 14 | 7 |
| pure_model | siemens | qwen-3.6-27b | 73.56 | 14 | 8 |

Scores weight core/common/edge cases 3/2/1. Compare pure-model and tool-agent tracks separately: only the latter measures filesystem retrieval.
