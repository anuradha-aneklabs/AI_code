"""
Audit script to inspect all skipped tests and extract exact skip conditions and reasons.
"""
import pytest
import _pytest.mark

class CollectSkipsPlugin:
    def __init__(self):
        self.collected = []
        self.skipped_items = []

    def pytest_collection_modifyitems(self, session, config, items):
        self.collected = items
        for item in items:
            for marker in item.iter_markers():
                if marker.name in ("skip", "skipif"):
                    # Check condition if skipif
                    is_skipped = False
                    reason = marker.kwargs.get("reason", "")
                    if marker.name == "skip":
                        is_skipped = True
                    elif marker.name == "skipif":
                        conditions = marker.args
                        if any(conditions):
                            is_skipped = True
                    if is_skipped:
                        self.skipped_items.append({
                            "nodeid": item.nodeid,
                            "file": item.nodeid.split("::")[0],
                            "test": item.nodeid.split("::")[1] if "::" in item.nodeid else item.nodeid,
                            "marker": marker.name,
                            "reason": reason,
                            "args": [str(a) for a in marker.args],
                        })

plugin = CollectSkipsPlugin()
pytest.main(["--collect-only", "-q"], plugins=[plugin])

print(f"\nTOTAL COLLECTED TESTS: {len(plugin.collected)}")
print(f"TOTAL SKIPPED TESTS IDENTIFIED: {len(plugin.skipped_items)}")

# Group by file and reason
by_file = {}
for s in plugin.skipped_items:
    f = s["file"]
    if f not in by_file:
        by_file[f] = []
    by_file[f].append(s)

print("\n--- SKIPPED TESTS BY FILE & REASON ---")
for f, items in sorted(by_file.items()):
    print(f"\nFile: {f} (Count: {len(items)})")
    # Group by reason within file
    reasons = {}
    for it in items:
        r = it["reason"] or (it["args"][0] if it["args"] else "unspecified")
        reasons[r] = reasons.get(r, 0) + 1
    for r, count in reasons.items():
        print(f"  [{count} tests] Reason: {r}")

# Also output individual tests sample
print("\n--- SAMPLE SKIPPED TEST NODEIDS ---")
for it in plugin.skipped_items[:15]:
    print(f"  - {it['nodeid']} -> {it['reason']}")
if len(plugin.skipped_items) > 15:
    print(f"  ... and {len(plugin.skipped_items) - 15} more")
