# PayPilot Evaluation Report

20 seeded synthetic worlds (September 2026, 300 subscribers each). Both arms faced identical worlds; only the recovery policy differed.

## Headline results

- **Win-rate: 90%** of worlds — the agent recovered more money than the naive baseline
- **Multiplier vs baseline:** mean **4.12×**, median 2.02×, range 0.48× – 26.62×
- **Share of at-risk money recovered:** agent **55.1%** vs baseline 27.5%
- **Compliance:** zero compliance violations across every world and every action (0 total)
- **₹1,088,236 recovered across 20 simulated months; intelligence cost not yet metered (LLM narration was off).**

## Per-world detail

| world | episodes | at-risk ₹ | baseline ₹ | agent ₹ | multiplier |
|---|---|---|---|---|---|
| 1 | 47 | 125,708 | 51,960 | 45,929 | 0.88× |
| 2 | 50 | 118,175 | 35,510 | 87,510 | 2.46× |
| 3 | 32 | 60,015 | 36,469 | 39,202 | 1.07× |
| 4 | 40 | 78,732 | 32,170 | 56,059 | 1.74× |
| 5 | 42 | 66,780 | 17,894 | 19,613 | 1.10× |
| 6 | 39 | 89,989 | 5,122 | 68,365 | 13.35× |
| 7 | 44 | 90,897 | 28,774 | 52,614 | 1.83× |
| 8 | 37 | 98,129 | 6,052 | 58,508 | 9.67× |
| 9 | 53 | 149,507 | 34,467 | 101,552 | 2.95× |
| 10 | 42 | 102,321 | 38,355 | 74,109 | 1.93× |
| 11 | 45 | 69,076 | 9,918 | 34,796 | 3.51× |
| 12 | 41 | 126,212 | 33,984 | 71,356 | 2.10× |
| 13 | 52 | 138,848 | 43,135 | 67,879 | 1.57× |
| 14 | 47 | 96,074 | 14,738 | 50,009 | 3.39× |
| 15 | 43 | 115,682 | 39,443 | 47,148 | 1.20× |
| 16 | 45 | 78,809 | 20,234 | 44,595 | 2.20× |
| 17 | 42 | 106,433 | 37,740 | 58,565 | 1.55× |
| 18 | 37 | 92,316 | 37,155 | 17,729 | 0.48× |
| 19 | 44 | 63,450 | 13,455 | 36,688 | 2.73× |
| 20 | 42 | 104,395 | 2,104 | 56,010 | 26.62× |

## Caveats (read before quoting any number)

- Outcomes come from a **calibrated simulator** (`SIMULATOR_ASSUMPTIONS.md`), not live payments.
- Probabilities are anchored to published industry rates but remain assumptions; the honest claim is the *relative* agent-vs-baseline delta on identical worlds.
- Loss-worlds exist and are shown unedited — we did not tune the doctrine against specific seeds.
- The LLM path is exercised via scripted brains here; the OpenRouter brain uses the same interface with its decisions journaled for replay.
