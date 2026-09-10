from concurrent.futures import Future
from threading import BoundedSemaphore, Lock


class SearchBusyError(RuntimeError):
    pass


class SearchJobs:
    """Bound CPU concurrency and share in-flight identical work per process."""

    def __init__(self, workers: int = 1, capacity: int = 8):
        self.slots = BoundedSemaphore(max(1, workers))
        self.capacity = capacity
        self.pending: dict[str, Future] = {}
        self.lock = Lock()

    def run(self, key: str, function):
        with self.lock:
            future = self.pending.get(key)
            owner = future is None
            if owner:
                if len(self.pending) >= self.capacity:
                    raise SearchBusyError("Search queue is full")
                future = Future()
                self.pending[key] = future
        if not owner:
            return future.result()
        try:
            with self.slots:
                result = function()
            future.set_result(result)
            return result
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
