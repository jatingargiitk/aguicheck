def run(*events, thread="t", run_id="r"):
    return ([{"type": "RUN_STARTED", "threadId": thread, "runId": run_id}] + list(events)
            + [{"type": "RUN_FINISHED", "threadId": thread, "runId": run_id}])


def stream(events):
    return [(0, e) for e in events]
