"""Generate the current genuine premarket snapshot or export an archived report."""
import asyncio
import sys
from pathlib import Path
from backend.main import app
from backend.services.clock import now

async def run():
    async with app.router.lifespan_context(app):
        engine = app.state.premarket
        report = await engine.generate(supplement=True) if "--supplement" in sys.argv else await engine.tick()
        report = report or engine.get(now().date().isoformat())
        if report is None:
            print("No report: outside premarket window, non-trading day or no archive.")
            return 1
        folder = Path("reports")
        folder.mkdir(exist_ok=True)
        stem = folder / ("Pulse-Premarket-" + report["day"])
        stem.with_suffix(".md").write_text(report["markdown"], encoding="utf-8")
        import json
        stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"day": report["day"], "status": report["status"], "coverage": report["coverage"],
                          "candidates": len(report["candidates"]), "file": str(stem.with_suffix(".md"))}, ensure_ascii=False))
        return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
