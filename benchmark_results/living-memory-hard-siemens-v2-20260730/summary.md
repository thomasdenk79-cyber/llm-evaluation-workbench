# Hard-agent benchmark summary

| Model | Overall | State | Response | Cases | Reliability errors |
|---|---:|---:|---:|---:|---:|
| `siemens/qwen-3.6-27b` | 95.74 | 94.32 | 100.00 | 8/8 | 1 |
| `siemens/deepseek-v4-flash` | 94.80 | 93.07 | 100.00 | 8/8 | 0 |
| `siemens/ministral-3-14b-instruct-2512` | 78.41 | 75.24 | 87.95 | 8/8 | 0 |
| `siemens/gpt-oss-120b` | 69.89 | 65.81 | 82.14 | 8/8 | 0 |
| `siemens/Mistral-Small-24B-Instruct-2501-FP8-dynamic` | 0.00 | 0.00 | 0.00 | 0/8 | 1 |

Overall = 25% response checks + 75% observable workspace state.
