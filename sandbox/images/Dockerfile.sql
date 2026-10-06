# Sandbox image for SQL tasks.
#
# Grading runs against `sqlite3.connect(":memory:")` inside a unittest.TestCase.
# An in-memory database gives us the property that matters most for SQL grading:
# zero state leakage between tests. setUp builds a fresh schema and seed data for
# every test, so a candidate's CREATE TABLE in test 2 cannot mask a missing
# ORDER BY in test 4.
#
# Deliberately no PostgreSQL client and no server. Questions target the SQL
# standard and sqlite3's dialect, which is what keeps grading deterministic
# across developer machines and CI.
FROM python:3.11-slim-bookworm

ARG RUNSC_VERSION=20241021.0.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# sqlite3 CLI is useful for a candidate's exploratory "Run" phase; it is not used
# for grading. python3 ships its own sqlite3 module, so no server is needed.
RUN apt-get update \
 && apt-get install --no-install-recommends -y sqlite3 \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      "pytest==8.3.4" \
      "pytest-json-report==1.5.0"

RUN groupadd --gid 65532 sandbox \
 && useradd --uid 65532 --gid 65532 --create-home --shell /bin/bash sandbox \
 && mkdir -p /workspace /grading /runner \
 && chown sandbox:sandbox /workspace \
 && chmod 0755 /workspace /grading /runner

COPY --chown=root:root sandbox/runners/sql/ /runner/
RUN chmod 0755 /runner/*.sh \
 && chown -R root:root /runner \
 && chmod -R go-w /runner

WORKDIR /workspace
USER 65532:65532

CMD ["sleep", "infinity"]

LABEL oa.sandbox.language="sql" \
      oa.sandbox.runtime="runsc" \
      oa.sandbox.runsc-version="${RUNSC_VERSION}" \
      oa.sandbox.grading="deterministic" \
      oa.sandbox.hidden-tests="grading-only"
