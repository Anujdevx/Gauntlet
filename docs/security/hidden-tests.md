# Keeping hidden tests hidden

A hidden test is only hidden if nothing the candidate receives quotes it. Failure text from
pytest, Vitest and the SQL harness quotes the test's source line, its input and its expected
value, so each of those is a channel.

## What a candidate receives

| Field | Content |
|---|---|
| `stdout` / `stderr` | their own program's run-phase output only |
| test `name`, `passed` | the test's name and verdict |
| test `message` | see the policy below |
| `stack_trace` | never set (frames quote the test source) |

The grader's own console output travels separately as `ExecutionResult.grader_output`, declared
`exclude=True`: it is never dumped, persisted, streamed or returned by the API. Only trusted
in-process callers (the question-generation gates) read it.

## Message policy (`parsers/base.py: candidate_message`)

- Assertion failures (`AssertionError`, `AssertionFailedError`, ...) become a fixed notice.
- Exceptions that describe the candidate's own code are shown: `NameError`, `TypeError`,
  `ImportError`, `SyntaxError`, `OperationalError`, `IndexError`, `RecursionError`, ...
- Any other exception shows only its type: `KeyError('x')` or `ValueError('bad 42')` could echo a
  hidden input.
- Timeouts are reported plainly; any path into the grading directory is scrubbed.
- Java compile errors stay visible; locations inside the hidden test file are masked as
  `(hidden tests)`.

## Also closed

- The React grader deletes candidate-supplied `*.test.*` / `*.spec.*` files before staging the
  hidden tests, so a candidate cannot add always-passing tests to inflate the pass ratio.

`apps/worker/tests/test_parsers_withhold_hidden_tests.py`, `test_candidate_message.py` and
`test_output_isolation.py` pin all of this.
