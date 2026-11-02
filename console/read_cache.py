"""Short-lived, coalesced UI reads. Mutating operations must always use fresh checks."""
from copy import deepcopy
import threading
import time

class ReadCache:
    def __init__(self):
        self._meta = threading.Lock()
        self._locks = {}
        self._values = {}
        self._generation = 0

    def clear(self):
        with self._meta:
            self._generation += 1
            self._values.clear()

    def get(self, key, loader, ttl=1):
        with self._meta:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._meta:
                cached = self._values.get(key)
                if cached and time.monotonic() - cached[0] < ttl:
                    return deepcopy(cached[1])
                generation = self._generation
            value = loader()  # errors never become cached success
            with self._meta:
                if generation == self._generation:
                    self._values[key] = (time.monotonic(), deepcopy(value))
            return value
