---
kind: features
label: Features and creation
description: What to build next and how: features users expect, how others built them, and a spec the session can work from.
voice: feature, features, creation, build, roadmap, spec
budget_usd: 2.00
max_turns: 40
---
## Goal

Decide what **{product}** should build next and give the session building it a spec it can start from. {focus}

## How to work

1. From the product context (README, what the session has been working on), list what {product} already does.
2. Find what users of products like it expect as standard ("table stakes") and what delights them. Use
   competitors' feature and changelog pages, their docs, and user requests (forums, GitHub issues, Reddit).
3. Pick the **3 features** with the best value-to-effort for this product right now. For each, find how two or
   three others implemented it: UX patterns, APIs or libraries they use, pitfalls they hit (bug reports and
   post-mortems are gold).
4. Write a short spec for the top one that a developer, or the Claude session working on {product}, could
   start from. Make the acceptance criteria testable.

## Report

## Findings

### What {product} has today
- <capability>

### Table stakes it's missing
- <feature> - <evidence users expect it>

### Top 3 to build next
| Feature | Why (evidence) | Effort (S / M / L) | How others did it |
|---|---|---|---|

### Spec: <the #1 feature>
**Problem:** <one sentence>
**User story:** As a <who>, I want <what>, so that <why>.
**Approach:** <the design in 3-6 bullets, naming libraries or APIs worth using>
**Acceptance criteria:**
- [ ] <testable>
**Pitfalls others hit:** <with links>
**Out of scope:** <what not to build yet>

## Sources
- <title> - <url>

## Recommendation
<what to build first and why, in 2-4 sentences>

## Confidence
<high | medium | low> - <why>
