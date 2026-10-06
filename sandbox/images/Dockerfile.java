# Sandbox image for Java tasks, graded with JUnit 5.
#
# JDK (not JRE): grading compiles the candidate's code in-process with javax.tools,
# which saves a second JVM start. The JUnit Platform console-standalone jar is baked
# in at build time and checksum-pinned, because the sandbox has no network at run time
# and because a mutable dependency would let the same submission be graded by
# different code a week apart.
FROM eclipse-temurin:21-jdk-alpine

ARG RUNSC_VERSION=20241021.0.0
ARG JUNIT_VERSION=1.11.4
ARG JUNIT_SHA256=b016ef6b1c3454d6d7c2c88ce081dabf289699686af6622d6e4e2e1b54b4a2fc

ADD --checksum=sha256:${JUNIT_SHA256} \
    https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/${JUNIT_VERSION}/junit-platform-console-standalone-${JUNIT_VERSION}.jar \
    /runner/junit.jar

COPY --chown=root:root sandbox/runners/java/ /runner/

# JVM flags shared by the Run and grading phases. Heap, metaspace and code cache are
# capped because the container is held to 256 MiB; sizing is otherwise derived from the
# host, not the cgroup. SharedArchiveFile is the AppCDS archive built just below.
ENV OA_JVM_FLAGS="-Xmx128m -XX:MaxMetaspaceSize=64m -XX:ReservedCodeCacheSize=32m -XX:+UseSerialGC -XX:TieredStopAtLevel=1 -Xss512k -XX:-UsePerfData -Xshare:auto -XX:SharedArchiveFile=/runner/app.jsa -Xlog:cds=off -Xlog:cds+dynamic=off"

# Compile the grader once at build time, against the pinned JUnit jar, then run it on a
# tiny training project to record a class-data archive. Every sandbox then starts with
# JUnit, javac and the grader already parsed and verified, which is most of a cold JVM's
# startup. If the archive ever fails to match, the JVM silently falls back to normal
# loading, so this can only make things faster, never break grading.
COPY sandbox/runners/java-train /opt/oa-train
RUN javac -cp /runner/junit.jar -d /runner /runner/OaGrade.java \
 && (cd /runner && jar cf oagrade.jar OaGrade*.class && rm -f OaGrade.java OaGrade*.class) \
 && OA_TRAIN=1 java -Xmx128m -XX:+UseSerialGC -XX:TieredStopAtLevel=1 \
        -XX:ArchiveClassesAtExit=/runner/app.jsa -Xlog:cds=off -Xlog:cds+dynamic=off \
        -cp /runner/junit.jar:/runner/oagrade.jar \
        OaGrade train /opt/oa-train/ws /opt/oa-train/grading /tmp/train-result.json \
        > /dev/null 2>&1 \
 && test -s /runner/app.jsa \
 && rm -rf /opt/oa-train /tmp/train-result.json \
 && chmod 0644 /runner/junit.jar /runner/oagrade.jar /runner/app.jsa \
 && chmod 0755 /runner/*.sh \
 && chown -R root:root /runner \
 && chmod -R go-w /runner

RUN addgroup -g 65532 sandbox \
 && adduser -D -u 65532 -G sandbox -s /bin/sh sandbox \
 && mkdir -p /workspace /grading \
 && chown sandbox:sandbox /workspace \
 && chmod 0755 /workspace /grading

# No JIT-state or telemetry writes into the read-only root; HOME/TMPDIR are the tmpfs.
ENV JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=/tmp"

WORKDIR /workspace
USER 65532:65532

CMD ["sleep", "infinity"]

LABEL oa.sandbox.language="java" \
      oa.sandbox.runtime="runsc" \
      oa.sandbox.runsc-version="${RUNSC_VERSION}" \
      oa.sandbox.grading="deterministic" \
      oa.sandbox.hidden-tests="grading-only"
