#!/bin/sh
# Java "Run" phase: compile the candidate's files and, if they define a Main class,
# run it. There are no hidden tests here by design (see the Python runner): Run lets a
# candidate explore; only Submit grades. Runs in the same JVM image as grading so it
# benefits from the same class-data archive.
set -eu
cd /tmp/workspace
exec java $OA_JVM_FLAGS -cp /runner/junit.jar:/runner/oagrade.jar OaGrade run /tmp/workspace
