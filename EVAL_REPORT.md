# PayPilot Evaluation Report

20 seeded synthetic worlds (September 2026, 300 subscribers each). Both arms faced identical worlds; only the recovery policy differed.

## Headline results

- **Win-rate: 85%** — the agent recovered more money than the naive baseline in that share of worlds
- **Multiplier vs baseline:** mean **2.42×**, median 1.77×, range 0.82× – 11.00×
- **Share of at-risk money recovered:** agent **56.8%** vs baseline 31.4%
- **Compliance:** zero compliance violations across every world and every action (0 total)
- **₹1,118,243 recovered across 20 simulated months; intelligence cost not yet metered (LLM narration was off).**

## Per-world detail

| world | episodes | at-risk ₹ | baseline ₹ | agent ₹ | multiplier |
|---|---|---|---|---|---|
| 1 | 44 | 125,708 | 53,642 | 44,157 | 0.82× |
| 2 | 45 | 118,175 | 35,510 | 87,510 | 2.46× |
| 3 | 29 | 60,015 | 36,469 | 30,116 | 0.83× |
| 4 | 38 | 78,732 | 32,170 | 58,839 | 1.83× |
| 5 | 36 | 66,780 | 17,894 | 31,678 | 1.77× |
| 6 | 36 | 89,989 | 14,948 | 60,985 | 4.08× |
| 7 | 42 | 90,897 | 28,774 | 50,173 | 1.74× |
| 8 | 37 | 98,129 | 6,052 | 66,543 | 11.00× |
| 9 | 49 | 149,507 | 35,518 | 90,211 | 2.54× |
| 10 | 39 | 102,321 | 36,960 | 56,366 | 1.53× |
| 11 | 43 | 69,076 | 17,267 | 25,154 | 1.46× |
| 12 | 38 | 126,212 | 33,984 | 72,316 | 2.13× |
| 13 | 48 | 138,848 | 48,520 | 60,912 | 1.26× |
| 14 | 44 | 96,074 | 16,995 | 58,788 | 3.46× |
| 15 | 41 | 115,682 | 41,096 | 72,226 | 1.76× |
| 16 | 43 | 78,809 | 34,761 | 51,340 | 1.48× |
| 17 | 40 | 106,433 | 46,134 | 41,919 | 0.91× |
| 18 | 34 | 92,316 | 37,155 | 65,567 | 1.76× |
| 19 | 40 | 63,450 | 18,676 | 36,705 | 1.97× |
| 20 | 40 | 104,395 | 15,354 | 56,737 | 3.70× |

## Caveats (read before quoting any number)

- Outcomes come from a **calibrated simulator** (`SIMULATOR_ASSUMPTIONS.md`), not live payments.
- Probabilities are anchored to published industry rates but remain assumptions; the honest claim is the *relative* agent-vs-baseline delta on identical worlds.
- Loss-worlds exist and are shown unedited — we did not tune the doctrine against specific seeds.
- The LLM path is exercised via scripted brains here; the OpenRouter brain uses the same interface with its decisions journaled for replay.
