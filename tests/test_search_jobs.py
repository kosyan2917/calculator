from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import patch

from web.backend.search_jobs import SearchJobs


class SearchJobTests(unittest.TestCase):
    def test_identical_work_is_shared(self):
        jobs = SearchJobs()
        started, release, waiting = Event(), Event(), Event()
        calls = []

        def compute():
            calls.append(1)
            started.set()
            release.wait(2)
            return 42

        class ObservedFuture(Future):
            def result(self, timeout=None):
                waiting.set()
                return super().result(timeout)

        with patch("web.backend.search_jobs.Future", ObservedFuture), ThreadPoolExecutor(2) as pool:
            a = pool.submit(jobs.run, "same", compute)
            self.assertTrue(started.wait(2))
            b = pool.submit(jobs.run, "same", compute)
            self.assertTrue(waiting.wait(2))
            release.set()
            self.assertEqual(a.result(), 42)
            self.assertEqual(b.result(), 42)
        self.assertEqual(len(calls), 1)
        self.assertEqual(jobs.pending, {})
