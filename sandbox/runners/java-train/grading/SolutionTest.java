import static org.junit.jupiter.api.Assertions.*;

import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

class SolutionTest {
    @Test void sumsList() { assertEquals(6, Solution.sum(List.of(1, 2, 3))); }
    @Test void failsOnPurpose() { assertEquals(7, Solution.sum(List.of(1, 2, 3))); }
    @Test void throwsOnPurpose() { assertThrows(IllegalStateException.class, () -> { throw new IllegalStateException("x"); }); }
    @ParameterizedTest @CsvSource({"1,1", "2,2"}) void table(int a, int b) { assertEquals(a, b); }
    @ParameterizedTest @ValueSource(ints = {1, 2}) void values(int a) { assertTrue(a > 0); }
}
