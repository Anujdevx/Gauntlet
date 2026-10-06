import java.util.*;

public class Solution {
    public static int sum(List<Integer> xs) {
        return xs.stream().mapToInt(Integer::intValue).sum();
    }
    public static String join(List<String> xs) {
        return String.join(",", new TreeMap<String, Integer>(Map.of("a", 1)).keySet()) + xs.size();
    }
}
