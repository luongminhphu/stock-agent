## Summary

<!-- 1-2 sentences: what does this PR do and why? -->

## Segment(s) affected

<!-- Which micro-segment(s) does this PR touch? -->
- [ ] platform
- [ ] ai
- [ ] market
- [ ] thesis
- [ ] watchlist
- [ ] briefing
- [ ] readmodel
- [ ] bot
- [ ] api

## Checklist

- [ ] PR touches ≤ 2 segments (or justified below)
- [ ] Business logic lives in domain segment, NOT in bot/scheduler/api
- [ ] New service/function has at least 1 unit test
- [ ] No hardcoded secrets or real API keys
- [ ] `ruff check` passes locally
- [ ] `pytest` passes locally

## Boundary justification (if touching > 2 segments)

<!-- Skip if ≤ 2 segments -->

## Breaking changes

<!-- Any contract changes? Backward compat impact? -->
None

## Related issues

<!-- Closes #xxx -->
