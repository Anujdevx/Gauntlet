import java.io.IOException;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;
import org.junit.platform.engine.TestExecutionResult;
import org.junit.platform.engine.support.descriptor.MethodSource;
import org.junit.platform.launcher.Launcher;
import org.junit.platform.launcher.LauncherDiscoveryRequest;
import org.junit.platform.launcher.TestExecutionListener;
import org.junit.platform.launcher.TestIdentifier;
import org.junit.platform.launcher.TestPlan;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;

import static org.junit.platform.engine.discovery.DiscoverySelectors.selectClass;

/**
 * Grades a Java submission with JUnit 5 and writes a pytest-json-report-shaped result.
 *
 * <p>Usage: {@code OaGrade <workspace-dir> <hidden-tests-dir> <result.json>}
 *
 * <p>Emitting the pytest report shape lets the worker reuse one result parser for
 * Python, SQL and Java. A compile failure is reported as a failed "collector" (the
 * same representation pytest uses for an import error), so it reads as a candidate
 * problem with the compiler output attached, never as a platform fault.
 *
 * <p>This class is part of the image and root-owned; a candidate cannot modify it.
 */
public final class OaGrade {
    private static final int MAX_MESSAGE = 4000;
    private static final String DEFAULT_TEST_TIMEOUT = "3 s";

    private record Case(String nodeId, String outcome, double seconds, String message) {}

    public static void main(String[] args) throws Exception {
        int code;
        switch (args[0]) {
            case "grade" -> code = grade(Paths.get(args[1]), Paths.get(args[2]), Paths.get(args[3]));
            case "run" -> code = run(Paths.get(args[1]));
            case "train" -> {
                // Image-build only: exercise every code path once so the class-data archive
                // covers the compiler, JUnit and reflection. Exits normally so the JVM can
                // write the archive (halt() would skip it).
                grade(Paths.get(args[1]), Paths.get(args[2]), Paths.get(args[3]));
                run(Paths.get(args[1]));
                System.exit(0);
                return;
            }
            default -> {
                System.err.println("usage: OaGrade grade|run ...");
                code = 2;
            }
        }
        System.out.flush();
        System.err.flush();
        // Candidate code may have left a non-daemon thread spinning (that is exactly what
        // a timed-out infinite loop is). The result is on disk; do not wait for it.
        Runtime.getRuntime().halt(code);
    }

    /** "Run" phase: compile, then run Main if there is one. No hidden tests are present. */
    private static int run(Path workspace) throws Exception {
        List<Path> sources = javaFiles(workspace);
        if (sources.isEmpty()) {
            System.err.println("no .java files in /workspace; nothing to run");
            return 1;
        }
        Path classes = Files.createTempDirectory("oa-run-classes");
        String errors = compile(sources, classes, workspace.resolveSibling("__none__"));
        if (errors != null) {
            System.err.print(errors);
            return 1;
        }
        if (!Files.exists(workspace.resolve("Main.java"))) {
            System.out.println("Compiled successfully. Add a Main class to run code here; "
                    + "Submit grades it against the hidden tests.");
            return 0;
        }
        URLClassLoader loader = new URLClassLoader(new URL[] {classes.toUri().toURL()}, OaGrade.class.getClassLoader());
        try {
            loader.loadClass("Main").getMethod("main", String[].class).invoke(null, (Object) new String[0]);
            return 0;
        } catch (java.lang.reflect.InvocationTargetException e) {
            e.getCause().printStackTrace();
            return 1;
        }
    }

    private static int grade(Path workspace, Path grading, Path result) throws Exception {
        List<Path> sources = new ArrayList<>();
        sources.addAll(javaFiles(workspace));
        List<Path> testSources = javaFiles(grading);
        sources.addAll(testSources);

        Path classes = Files.createTempDirectory("oa-classes");
        String compileErrors = compile(sources, classes, grading);
        if (compileErrors != null) {
            write(result, "{\"tests\": [], \"collectors\": [{\"nodeid\": \"compilation\", "
                    + "\"outcome\": \"failed\", \"longrepr\": " + quote(compileErrors) + "}], "
                    + "\"summary\": {\"total\": 0}}");
            return 1;
        }

        List<Case> cases = runTests(classes, grading, testSources);
        StringBuilder json = new StringBuilder("{\"tests\": [");
        for (int i = 0; i < cases.size(); i++) {
            Case c = cases.get(i);
            if (i > 0) json.append(", ");
            json.append("{\"nodeid\": ").append(quote(c.nodeId()))
                .append(", \"outcome\": ").append(quote(c.outcome()))
                .append(", \"duration\": ").append(String.format(Locale.ROOT, "%.4f", c.seconds()));
            if (c.message() != null) json.append(", \"longrepr\": ").append(quote(c.message()));
            json.append('}');
        }
        json.append("], \"collectors\": [], \"summary\": {\"total\": ").append(cases.size()).append("}}");
        write(result, json.toString());
        return 0;
    }

    private static List<Path> javaFiles(Path root) throws IOException {
        if (!Files.isDirectory(root)) return List.of();
        try (Stream<Path> walk = Files.walk(root)) {
            return walk.filter(p -> p.toString().endsWith(".java") && Files.isRegularFile(p))
                    .sorted().collect(Collectors.toList());
        }
    }

    /** Returns null on success, otherwise the compiler diagnostics. */
    private static String compile(List<Path> sources, Path out, Path hiddenDir) throws IOException {
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) return "no system Java compiler available in the sandbox image";
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        try (StandardJavaFileManager fm = compiler.getStandardFileManager(diagnostics, null, StandardCharsets.UTF_8)) {
            List<String> options = List.of("-d", out.toString(), "-cp", System.getProperty("java.class.path"),
                    "-proc:none", "-Xlint:none", "-encoding", "UTF-8");
            boolean ok = compiler.getTask(null, fm, diagnostics, options, null,
                    fm.getJavaFileObjectsFromPaths(sources)).call();
            if (ok) return null;
        }
        StringBuilder sb = new StringBuilder();
        for (Diagnostic<? extends JavaFileObject> d : diagnostics.getDiagnostics()) {
            if (d.getKind() != Diagnostic.Kind.ERROR) continue;
            // Errors located in the hidden tests (typically "cannot find symbol" for a method
            // the candidate did not write) keep their message, which names the missing API,
            // but not the test file's name or line: that would point into the hidden tests.
            Path src = d.getSource() == null ? null : Paths.get(d.getSource().toUri());
            boolean inTests = src != null && src.startsWith(hiddenDir);
            if (inTests) {
                sb.append("(hidden tests): ");
            } else {
                String file = src == null ? "?" : src.getFileName().toString();
                sb.append(file).append(':').append(d.getLineNumber()).append(": ");
            }
            sb.append(d.getMessage(Locale.ROOT)).append('\n');
        }
        return sb.length() == 0 ? "compilation failed" : sb.toString();
    }

    private static List<Case> runTests(Path classes, Path grading, List<Path> testSources) throws Exception {
        URLClassLoader loader = new URLClassLoader(new URL[] {classes.toUri().toURL()}, OaGrade.class.getClassLoader());
        Thread.currentThread().setContextClassLoader(loader);

        LauncherDiscoveryRequestBuilder request = LauncherDiscoveryRequestBuilder.request();
        for (Path source : testSources) {
            String rel = grading.relativize(source).toString();
            String name = rel.substring(0, rel.length() - ".java".length()).replace('/', '.');
            request.selectors(selectClass(loader.loadClass(name)));
        }
        // A candidate's infinite loop fails one test instead of consuming the whole run.
        request.configurationParameter("junit.jupiter.execution.timeout.default", DEFAULT_TEST_TIMEOUT);
        request.configurationParameter("junit.jupiter.execution.timeout.thread.mode.default", "SEPARATE_THREAD");
        LauncherDiscoveryRequest built = request.build();

        Map<String, Long> started = new ConcurrentHashMap<>();
        List<Case> cases = new ArrayList<>();
        Launcher launcher = LauncherFactory.create();
        launcher.execute(built, new TestExecutionListener() {
            @Override
            public void executionStarted(TestIdentifier id) {
                if (id.isTest()) started.put(id.getUniqueId(), System.nanoTime());
            }

            @Override
            public void executionSkipped(TestIdentifier id, String reason) {
                if (id.isTest()) cases.add(new Case(nodeId(id), "skipped", 0, reason));
            }

            @Override
            public void executionFinished(TestIdentifier id, TestExecutionResult res) {
                if (!id.isTest()) {
                    // A container failure (e.g. a throwing constructor) must not vanish.
                    if (res.getStatus() == TestExecutionResult.Status.FAILED) {
                        cases.add(new Case(nodeId(id), "failed", 0, describe(res)));
                    }
                    return;
                }
                long begin = started.getOrDefault(id.getUniqueId(), System.nanoTime());
                double seconds = (System.nanoTime() - begin) / 1e9;
                String outcome = switch (res.getStatus()) {
                    case SUCCESSFUL -> "passed";
                    case ABORTED -> "skipped";
                    default -> "failed";
                };
                cases.add(new Case(nodeId(id), outcome, seconds,
                        res.getStatus() == TestExecutionResult.Status.SUCCESSFUL ? null : describe(res)));
            }
        });
        return cases;
    }

    /** {@code Class::method} plus the invocation label for parameterised cases. */
    private static String nodeId(TestIdentifier id) {
        String display = id.getDisplayName();
        return id.getSource()
                .filter(MethodSource.class::isInstance)
                .map(MethodSource.class::cast)
                .map(src -> {
                    String cls = src.getClassName().substring(src.getClassName().lastIndexOf('.') + 1);
                    String base = cls + "::" + src.getMethodName();
                    return display.startsWith("[") ? base + " " + display : base;
                })
                .orElse(display);
    }

    /**
     * Candidate-facing failure text. An assertion failure's message contains the hidden
     * test's expected value ("expected: <99> but was: <2>"), which would let a candidate
     * reverse-engineer the tests and hard-code answers, so it is withheld. Any other
     * exception (NullPointerException, a timeout, an index error) comes from the
     * candidate's own code and is shown, because that is what they need to debug.
     */
    private static String describe(TestExecutionResult res) {
        return res.getThrowable().map(t -> {
            if (t instanceof AssertionError) {
                return "Assertion failed. Hidden tests do not reveal expected values.";
            }
            String text = t.getClass().getName() + ": " + t.getMessage();
            return text.length() > MAX_MESSAGE ? text.substring(0, MAX_MESSAGE) : text;
        }).orElse(res.getStatus().name());
    }

    private static void write(Path path, String body) throws IOException {
        Files.createDirectories(path.getParent());
        Files.writeString(path, body, StandardCharsets.UTF_8);
    }

    private static String quote(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
                }
            }
        }
        return sb.append('"').toString();
    }
}
