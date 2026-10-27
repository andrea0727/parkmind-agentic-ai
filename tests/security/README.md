# Security tests

Offline checks that hostile input cannot move the planner past a human decision, that private data and secrets stay where they belong, and that no secret is committed. Everything runs against the real graph with in-memory ports and scripted fakes: no network, database or API key.

## Threat model

| Attacker | Goal | Where it is tested |
|---|---|---|
| A guest typing hostile text (instruction override, role-play, smuggled JSON, unicode tricks, huge input, SQL/shell text, Spanish variants) | Plan or activate without the confirmation and approval interrupts | `test_prompt_injection_resistance.py` |
| A prompt-injected LLM returning the worst extraction it can | Invalid values, extra keys or oversized parties reaching the checker | `test_prompt_injection_resistance.py` |
| A caller of the resume API | Skip, forge or reshape a confirmation or approval; activate an adult-less party | `test_resume_tampering.py` |
| A model with tools | Reach an approval or activation capability | `test_resume_tampering.py` (tool schemas) |
| Any session | Read accessibility data or another thread's data | `test_privacy_and_isolation.py` |
| An upstream failure | Leak a key or connection string to guests, logs or traces | `test_secret_hygiene.py` |
| A contributor | Commit a key or a database password | `test_secret_hygiene.py` (`git ls-files` scan) |

The payloads and worst-case extractions live in `sec_corpus.py`. Add a payload there and every test that is parametrized over the corpus picks it up.

## Run

```
poetry run pytest tests/security -q
```

The repository scan skips itself when `git` is not available. It reports file and kind of finding, never the matched text, and the test files build their fake secrets from pieces so they do not trip it.

## Not covered

A live red-team needs a real model and is out of scope here. A scanner such as Garak or PyRIT would plug in at the extractor seam: implement `GuestInfoExtractor` (or wrap `AnthropicExtractor`), run the graph through `sec_harness.make_rig(extractor=...)`, and reuse `assert_not_planned` and `assert_not_activated` as the pass criteria.
