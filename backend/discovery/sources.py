"""Combine current collection sources without converting monitoring history into CLI evidence."""


class CombinedReader:
    def __init__(self, direct, monitoring=None, source_gap=None):
        self.direct, self.monitoring, self.source_gap = direct, monitoring, source_gap

    def __call__(self, address):
        direct = self.direct(address)
        monitoring = self.monitoring(address) if self.monitoring else None
        # Preserve separate source/timestamp on every observation and neighbor. NMS data
        # fills coverage gaps; its old poll time must never inherit a fresh SSH timestamp.
        observations = [direct] + ([monitoring] if monitoring else [])
        primary = direct if direct.get("observed_at") else monitoring or direct
        result = {**primary, "observations": observations, "neighbors": []}
        gaps = [part["gap"] for part in observations if part.get("gap")]
        if self.source_gap:
            gaps.append(self.source_gap)
        if gaps:
            result["gap"] = "; ".join(dict.fromkeys(gaps))
        for part in observations:
            for neighbor in part.get("neighbors", []):
                result["neighbors"].append(
                    {**neighbor, "source": part["source"], "observed_at": part.get("observed_at")}
                )
        return result
