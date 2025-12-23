"""
Performance Metrics Collector

Collects and tracks performance metrics for the native wrapper manager.
"""

import time
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict, deque
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()


class MetricType(Enum):
    """Metric types."""
    COUNTER = "counter"  # Incremental count
    GAUGE = "gauge"  # Current value
    HISTOGRAM = "histogram"  # Distribution of values
    TIMER = "timer"  # Duration measurements


@dataclass
class Metric:
    """A single metric data point."""
    name: str
    type: MetricType
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class PerformanceStats:
    """Performance statistics for a specific operation."""
    operation: str
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: float = float('inf')
    max_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    p50_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0


class MetricsCollector:
    """
    Collects and aggregates performance metrics.

    Features:
    - Counter, gauge, histogram, and timer metrics
    - Automatic aggregation and statistics
    - Sliding window for recent metrics
    - Exportable metrics format
    """

    def __init__(self, window_size: int = 1000):
        """
        Initialize metrics collector.

        Args:
            window_size: Size of sliding window for recent metrics
        """
        self.window_size = window_size

        # Metrics storage
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._timers: Dict[str, List[float]] = defaultdict(list)

        # Operation tracking
        self._operation_stats: Dict[str, PerformanceStats] = {}

        # Metadata
        self._start_time = datetime.now()

        logger.info(f"Metrics collector initialized (window_size={window_size})")

    def increment_counter(self, name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None):
        """
        Increment a counter metric.

        Args:
            name: Metric name
            value: Value to add
            tags: Optional tags
        """
        key = self._make_key(name, tags)
        self._counters[key] += value

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """
        Set a gauge metric value.

        Args:
            name: Metric name
            value: Current value
            tags: Optional tags
        """
        key = self._make_key(name, tags)
        self._gauges[key] = value

    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """
        Record a histogram value.

        Args:
            name: Metric name
            value: Value to record
            tags: Optional tags
        """
        key = self._make_key(name, tags)
        self._histograms[key].append(value)

    def start_timer(self, operation: str) -> 'TimerContext':
        """
        Start a timer for an operation.

        Args:
            operation: Operation name

        Returns:
            TimerContext for use with context manager

        Example:
            with metrics.start_timer("decrypt"):
                await decrypt_sample()
        """
        return TimerContext(self, operation)

    def record_operation(
        self,
        operation: str,
        duration_ms: float,
        success: bool
    ):
        """
        Record an operation's performance.

        Args:
            operation: Operation name
            duration_ms: Duration in milliseconds
            success: Whether operation succeeded
        """
        if operation not in self._operation_stats:
            self._operation_stats[operation] = PerformanceStats(operation=operation)

        stats = self._operation_stats[operation]
        stats.total_count += 1

        if success:
            stats.success_count += 1
        else:
            stats.failure_count += 1

        stats.total_duration_ms += duration_ms
        stats.min_duration_ms = min(stats.min_duration_ms, duration_ms)
        stats.max_duration_ms = max(stats.max_duration_ms, duration_ms)

        # Update average
        stats.avg_duration_ms = stats.total_duration_ms / stats.total_count

        # Store for percentile calculation
        self._timers[operation].append(duration_ms)

        # Keep only recent values
        if len(self._timers[operation]) > self.window_size:
            self._timers[operation] = self._timers[operation][-self.window_size:]

        # Update percentiles
        self._update_percentiles(operation)

    def _update_percentiles(self, operation: str):
        """Update percentile statistics for an operation."""
        if operation not in self._timers or not self._timers[operation]:
            return

        sorted_times = sorted(self._timers[operation])
        count = len(sorted_times)

        stats = self._operation_stats[operation]

        # Calculate percentiles
        stats.p50_duration_ms = sorted_times[int(count * 0.50)]
        stats.p95_duration_ms = sorted_times[int(count * 0.95)]
        stats.p99_duration_ms = sorted_times[int(count * 0.99)]

    def get_counter(self, name: str, tags: Optional[Dict[str, str]] = None) -> float:
        """Get current counter value."""
        key = self._make_key(name, tags)
        return self._counters.get(key, 0.0)

    def get_gauge(self, name: str, tags: Optional[Dict[str, str]] = None) -> Optional[float]:
        """Get current gauge value."""
        key = self._make_key(name, tags)
        return self._gauges.get(key)

    def get_histogram_stats(self, name: str, tags: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """Get histogram statistics."""
        key = self._make_key(name, tags)
        values = self._histograms.get(key, [])

        if not values:
            return {}

        sorted_values = sorted(values)
        count = len(sorted_values)

        return {
            "count": count,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "avg": sum(sorted_values) / count,
            "p50": sorted_values[int(count * 0.50)],
            "p95": sorted_values[int(count * 0.95)] if count > 20 else sorted_values[-1],
            "p99": sorted_values[int(count * 0.99)] if count > 100 else sorted_values[-1],
        }

    def get_operation_stats(self, operation: str) -> Optional[PerformanceStats]:
        """Get performance statistics for an operation."""
        return self._operation_stats.get(operation)

    def get_all_operation_stats(self) -> Dict[str, PerformanceStats]:
        """Get all operation statistics."""
        return dict(self._operation_stats)

    def get_summary(self) -> Dict[str, Any]:
        """
        Get metrics summary.

        Returns:
            Dict with all metrics
        """
        uptime = (datetime.now() - self._start_time).total_seconds()

        return {
            "uptime_seconds": uptime,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: self.get_histogram_stats(name)
                for name in self._histograms.keys()
            },
            "operations": {
                op: {
                    "total": stats.total_count,
                    "success": stats.success_count,
                    "failure": stats.failure_count,
                    "success_rate": stats.success_count / stats.total_count if stats.total_count > 0 else 0,
                    "avg_duration_ms": stats.avg_duration_ms,
                    "min_duration_ms": stats.min_duration_ms if stats.min_duration_ms != float('inf') else 0,
                    "max_duration_ms": stats.max_duration_ms,
                    "p50_duration_ms": stats.p50_duration_ms,
                    "p95_duration_ms": stats.p95_duration_ms,
                    "p99_duration_ms": stats.p99_duration_ms,
                }
                for op, stats in self._operation_stats.items()
            }
        }

    def reset(self):
        """Reset all metrics."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._timers.clear()
        self._operation_stats.clear()
        self._start_time = datetime.now()
        logger.info("Metrics collector reset")

    def _make_key(self, name: str, tags: Optional[Dict[str, str]]) -> str:
        """Create metric key with tags."""
        if not tags:
            return name

        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


class TimerContext:
    """Context manager for timing operations."""

    def __init__(self, collector: MetricsCollector, operation: str):
        """
        Initialize timer context.

        Args:
            collector: MetricsCollector instance
            operation: Operation name
        """
        self.collector = collector
        self.operation = operation
        self.start_time: Optional[float] = None
        self.success = True

    def __enter__(self):
        """Start timer."""
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timer and record metric."""
        if self.start_time is not None:
            duration_ms = (time.perf_counter() - self.start_time) * 1000

            # Record failure if exception occurred
            if exc_type is not None:
                self.success = False

            self.collector.record_operation(
                operation=self.operation,
                duration_ms=duration_ms,
                success=self.success
            )

    def mark_failure(self):
        """Mark operation as failed."""
        self.success = False


# Global metrics collector instance
_global_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get global metrics collector instance."""
    global _global_collector

    if _global_collector is None:
        _global_collector = MetricsCollector()

    return _global_collector


def reset_metrics_collector():
    """Reset global metrics collector."""
    global _global_collector

    if _global_collector:
        _global_collector.reset()
