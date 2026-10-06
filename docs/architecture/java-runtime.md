# Java runtime

Java is a first-class sandbox language alongside Python, SQL and React.

| Piece | Where |
|---|---|
| Image (Temurin 21 JDK, JUnit 5 console jar pinned by SHA-256) | `sandbox/images/Dockerfile.java` |
| Grader and "Run" phase (one JVM: compile in-process, then JUnit) | `sandbox/runners/java/OaGrade.java`, `grade-java.sh`, `run-java.sh` |
| Result adapter (reuses the pytest report shape) | `apps/worker/src/oa_worker/parsers/java_parser.py` |
| Training project for the class-data archive | `sandbox/runners/java-train/` |

## Contract

- The candidate edits `Solution.java` (`public class Solution`, default package). Hidden tests are
  `SolutionTest.java` (JUnit 5), injected on the read-only grading volume only at grading time.
- **Run** compiles and runs `Main` if one exists. It never has the hidden tests. **Submit** grades.
- A compile error is reported as a failed `compilation` case carrying javac's output, counted
  against the candidate. A missing result document is `ERROR`, never a pass.
- Assertion failures say only "Assertion failed": the message of an `AssertionError` contains
  the hidden test's expected value. Other exceptions (NPE, index errors, timeouts) are shown,
  because they come from the candidate's own code.

## Containment (verified in the hardened container)

Infinite loop (each test cut off at 3 s, `SEPARATE_THREAD`), `System.exit`, heap exhaustion
(`-Xmx128m`), 3M-line output flood (capped by the worker's 256 KiB stream cap), and a 100k-thread
bomb (`pids-limit`) are all contained. Candidate code runs in the grader's JVM, the same trust
model as pytest importing candidate code; the result is written to a tmpfs and emitted between
markers by the shell wrapper.

## Startup cost

A cold JVM spends most of its time loading and verifying JUnit and javac. The image records an
AppCDS archive (`app.jsa`) at build time by running the grader on a tiny project, which cut
grading from ~2.8 s to ~1.9 s. The archive needs the grader on the classpath as a **jar**
(a directory of classes is rejected), and the JVM silently falls back to normal loading if it
ever fails to match, so it can only help.
