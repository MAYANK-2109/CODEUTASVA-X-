"""A short-lived cache for expensive request handlers.

Identical requests inside the time-to-live get the stored answer, and
identical requests that arrive together wait for the first one instead of
each doing the work. It is switched off while tests run, so a test always
sees the data it set up."""

import hashlib
import json
import os
import threading
import time
from functools import wraps

MAX_ENTRIES = 256

_guard = threading.Lock()


def enabled() -> bool:
    return "PYTEST_CURRENT_TEST" not in os.environ


def cached(seconds: float):
    def decorate(fn):
        store: dict[str, tuple[float, object]] = {}
        locks: dict[str, threading.Lock] = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled():
                return fn(*args, **kwargs)
            key = hashlib.sha1(json.dumps([args, kwargs], sort_keys=True, default=str).encode()).hexdigest()
            hit = store.get(key)
            if hit and time.time() - hit[0] < seconds:
                return hit[1]
            with _guard:
                lock = locks.setdefault(key, threading.Lock())
            with lock:  # a second identical request waits here, then finds the answer below
                hit = store.get(key)
                if hit and time.time() - hit[0] < seconds:
                    return hit[1]
                result = fn(*args, **kwargs)
                if len(store) >= MAX_ENTRIES:
                    for old in sorted(store, key=lambda k: store[k][0])[: MAX_ENTRIES // 4]:
                        store.pop(old, None)
                        locks.pop(old, None)
                store[key] = (time.time(), result)
                return result

        wrapper.clear = store.clear
        return wrapper

    return decorate
