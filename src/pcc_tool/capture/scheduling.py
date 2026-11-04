"""Find ready work that does not read tables used by a slow EXPLAIN."""
from collections import OrderedDict, deque


def _table_key(pair):
    if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(s, str) and s for s in pair):
        return tuple(s.casefold() for s in pair)
    return None


def query_tables(queries, objects):
    # CaptureBundle objects are emitted in dependency order. A legacy view or
    # incomplete definition with no known base tables stays in the normal queue.
    base = {}
    for obj in objects:
        key = _table_key((obj.get("schema"), obj.get("table")))
        if key and obj.get("kind") == "table":
            base[key] = frozenset((key,))

    def expand(deps):
        parts = [base.get(_table_key(dep)) for dep in deps]
        return frozenset().union(*parts) if parts and all(parts) else frozenset()

    for obj in objects:
        if obj.get("kind") == "view":
            key = _table_key((obj.get("schema"), obj.get("table")))
            if key:
                base[key] = expand(obj.get("dependencies") or ())
    return {q.query_id: expand(q.dependencies or ()) for q in queries}


class WaitingQueries:
    """FIFO within each dependency set, round-robin between sets."""

    def __init__(self, tables, queries=()):
        self.tables = tables
        self.groups = OrderedDict()
        self.count = 0
        self.missed = frozenset()
        self.common = None
        for query in queries:
            self.add(query)

    def __len__(self):
        return self.count

    def add(self, query):
        key = self.tables.get(query.query_id, frozenset())
        if key not in self.groups:
            self.groups[key] = deque()
            if key:
                self.common = key if self.common is None else self.common & key
        self.groups[key].append(query)
        self.count += 1
        if key and key.isdisjoint(self.missed):
            self.missed = frozenset()

    def take(self, avoid=frozenset()):
        if not self.groups:
            return None
        if avoid:
            # A large set of different joins can all share one hot table.
            if (self.common and not self.common.isdisjoint(avoid)) or (self.missed and self.missed.issubset(avoid)):
                return None
            key = next((group for group in self.groups if group and group.isdisjoint(avoid)), None)
            if key is None:
                self.missed = frozenset(avoid)
                return None
        else:
            key = next(iter(self.groups))
        group = self.groups[key]
        query = group.popleft()
        self.count -= 1
        if group:
            self.groups.move_to_end(key)
        else:
            del self.groups[key]
        return query
