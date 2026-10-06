# LLM question generation

Gauntlet's question bank can be grown with an LLM. The LLM **authors** a question and
its hidden tests; it never grades. Nothing it writes enters the bank until sandbox
gates, using real execution, prove it is trustworthy. Grading stays 100% deterministic.

## Run it

```bash
# one-off: put a key in .env (gitignored). Free key: https://aistudio.google.com/apikey
GEMINI_API_KEY=...

docker compose up -d --build worker          # worker is the only service given the key
docker compose exec worker python -m oa_worker.scripts.generate generate \
    --node sql.joins.outer --difficulty easy --count 3        # add --dry-run to not save
docker compose exec worker python -m oa_worker.scripts.generate validate-existing
```

Accepted questions land in `question_templates` and appear in the app immediately.
Supported domains: `python`, `sql`, `java` (the domain of the concept node selects the runner).

## Pipeline (per question, up to `--max-attempts`)

1. **Draft.** Gemini returns a schema-validated object (`PythonDraft` / `SqlDraft`).
2. **Static checks** (no sandbox): syntax, forbidden imports (network, time, random, os...),
   entry point defined by starter/reference/every wrong solution, no skipped tests, SQL
   statement allow-list. SQL expected rows are **computed by running the reference query**,
   never written by the model.
3. **Sandbox gates** (production orchestrator, full security envelope):
   | Gate | Passes when |
   |---|---|
   | reference | the author's solution passes every test, identically on two runs; enough tests ran |
   | starter | every test fails on the code the candidate is given |
   | mutants | every deliberately wrong solution fails at least one test (and imports cleanly) |
   | blind | an independent LLM call, shown only the prompt and starter, writes a solution that passes |
4. Any failure is written back to the model as feedback for the next attempt. Infrastructure
   failures (LLM down, Docker down) abort the run and are never reported as "bad question".

## Security notes

- `GEMINI_API_KEY` is passed to the **worker only** (compose). The API and every sandbox
  container never receive it. It is sent in a header, never a URL, and held as `SecretStr`.
- Generated code runs only inside the sandbox. SQL reference queries are additionally executed
  host-side under a read-only SQLite authorizer to compute expected rows.

## Known limits

- Tests that depend on timing or concurrency (asyncio scheduling) are rarely accepted; the
  mutants gate correctly refuses weak or flaky ones. Expect lower yield there.
- `blind` accepts when at least one of two blind solves passes (`--blind-required` to tighten).
- Java failure details are withheld from candidates (and from the gates): an assertion's message
  contains the hidden test's expected value, so feedback to the generator for Java is names-only.
- React/RAG generation is not implemented; the gates are language-agnostic and need only a
  draft schema, a prompt contract and a `MIN_TESTS` entry.
- Free-tier models are intermittently overloaded; the client falls back through
  `GEMINI_MODEL` (comma-separated).
