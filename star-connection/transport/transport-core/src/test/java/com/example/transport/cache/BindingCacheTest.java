package com.example.transport.cache;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Optional;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

class BindingCacheTest {

    private BindingCache cache;

    @BeforeEach
    void setUp() {
        cache = new BindingCache(5, TimeUnit.SECONDS, 1000);
    }

    @Test
    void testPutAndGet() {
        cache.put("agent-001", "backend-001");

        Optional<String> result = cache.get("agent-001");

        assertTrue(result.isPresent());
        assertEquals("backend-001", result.get());
    }

    @Test
    void testGetNonExistent() {
        Optional<String> result = cache.get("non-existent");

        assertFalse(result.isPresent());
    }

    @Test
    void testInvalidate() {
        cache.put("agent-001", "backend-001");

        cache.invalidate("agent-001");

        Optional<String> result = cache.get("agent-001");
        assertFalse(result.isPresent());
    }

    @Test
    void testInvalidateAll() {
        cache.put("agent-001", "backend-001");
        cache.put("agent-002", "backend-002");

        cache.invalidateAll();

        assertFalse(cache.get("agent-001").isPresent());
        assertFalse(cache.get("agent-002").isPresent());
    }

    @Test
    void testGetAll() {
        cache.put("agent-001", "backend-001");
        cache.put("agent-002", "backend-002");

        var all = cache.getAll();

        assertEquals(2, all.size());
        assertEquals("backend-001", all.get("agent-001"));
        assertEquals("backend-002", all.get("agent-002"));
    }

    @Test
    void testSize() {
        assertEquals(0, cache.size());

        cache.put("agent-001", "backend-001");
        assertEquals(1, cache.size());

        cache.put("agent-002", "backend-002");
        assertEquals(2, cache.size());
    }

    @Test
    void testNullKey() {
        cache.put(null, "value");
        assertFalse(cache.get(null).isPresent());
    }

    @Test
    void testNullValue() {
        cache.put("key", null);
        assertFalse(cache.get("key").isPresent());
    }

    @Test
    void testGetStats() {
        cache.put("agent-001", "backend-001");

        // Hit
        cache.get("agent-001");
        // Miss
        cache.get("non-existent");

        BindingCache.CacheStats stats = cache.getStats();

        assertEquals(1, stats.getHitCount());
        assertEquals(1, stats.getMissCount());
    }

    @Test
    void testUpdateValue() {
        cache.put("agent-001", "backend-001");
        cache.put("agent-001", "backend-002");

        Optional<String> result = cache.get("agent-001");

        assertTrue(result.isPresent());
        assertEquals("backend-002", result.get());
    }
}
