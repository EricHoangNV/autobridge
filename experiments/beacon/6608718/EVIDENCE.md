# Beacon evaluation evidence — round 6608718

Commit-then-reveal with public randomness (REQ-025): the instances
were fixed by a drand round that did not exist when the protocol froze.

- round: 6608718
- randomness: c7714c546da354e7f86f99135acc961923246003c94223a742841bb6f239e218
- verification URL: https://api.drand.sh/public/6608718
- freeze commit: 9f9681788344354f3f43b8fae7a560fd439fca87 (tag eval-freeze-6608718)
- protocol: /Volumes/OWC-Express-1M2/Development/autobridge/experiments/beacon/6608718/protocol.md
- frontier model: muse-spark-1.3-contributor
- agent model: meta/muse-spark-1.3-contributor
- freeze committed 2026-11-03T07:35:59Z, pushed with its tag at 2026-11-03T07:36:05Z (local clock); the round
  was published at 2026-11-03T07:56:00Z, twenty minutes after the push
- run notes: a first freeze (round 6608587, tag `eval-freeze-6608587`) was refused by the harness self-test
  before any scored instance (reference-patch pointer bug, REQ-093) and re-frozen as this round; two starts
  of this round were cut off by sandbox stops during preflight and instance 0 (no grades written, one
  one-shot model call repeated); the scored run below is a single uninterrupted pass, no retries
- warm: 4 dev-split fix(es) (dev split only (never beacon instances))

## Per-arm summary (scores averaged per instance first; n names instances)

```
autobridge_cold      verified 0.52 [0.32,0.72] n=25  wrong_released=0 wrong_proposed=4
autobridge_warm      verified 0.56 [0.37,0.75] n=25  wrong_released=0 wrong_proposed=3
engine_no_knowledge  verified 0.60 [0.41,0.79] n=25  wrong_released=0 wrong_proposed=2
frontier_agent       verified 0.60 [0.41,0.79] n=25  wrong_released=0 wrong_proposed=2
frontier_oneshot     verified 0.60 [0.41,0.79] n=25  wrong_released=0 wrong_proposed=2

per-family verified (mean over instances):
code_list            verified 1.00 [1.00,1.00] n=1
dates                verified 0.47 [0.00,0.93] n=3
fias_layout          verified 0.70 [0.56,0.84] n=2
fias_link            verified 1.00 [1.00,1.00] n=3
nesting              verified 1.00 [1.00,1.00] n=1
pagination           verified 0.00 [0.00,0.00] n=5
rename               verified 0.00 [0.00,0.00] n=3
semantic             verified 0.92 [0.78,1.00] n=5
transport_auth       verified 1.00 [1.00,1.00] n=2

paired per-instance differences vs autobridge_cold:
autobridge_warm_minus_autobridge_cold    +0.04 [-0.04,+0.12] n=25
engine_no_knowledge_minus_autobridge_cold +0.08 [-0.03,+0.19] n=25
frontier_agent_minus_autobridge_cold     +0.08 [-0.07,+0.23] n=25
frontier_oneshot_minus_autobridge_cold   +0.08 [-0.07,+0.23] n=25
```
