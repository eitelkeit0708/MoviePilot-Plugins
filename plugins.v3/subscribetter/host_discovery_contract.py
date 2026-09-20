"""Explicit W09 host probe: fetch-only by default; submission requires a separate flag."""


async def run_host_contract(plugin, *, phase="fetch", source_id, confirm_submission=False):
    if phase == "fetch":
        result = await plugin.discovery.test_source(source_id)
        return dict(result, phase="fetch", recognition=False, submission=False, live_contract=True)
    if phase == "run":
        if confirm_submission is not True:
            raise ValueError("SUBMISSION_CONFIRMATION_REQUIRED")
        result = await plugin.discovery_tick(plugin.generation, [source_id])
        return dict(result, phase="run", recognition=True, submission=True, live_contract=True)
    if phase == "records":
        return {"phase": "records", "records": plugin.discovery.records(source_id=source_id),
                "statistics": plugin.discovery.statistics(), "submission": False, "live_contract": True}
    raise ValueError("UNKNOWN_DISCOVERY_PHASE")
