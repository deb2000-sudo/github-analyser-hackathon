from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.metrics.base import Metric, MetricContext, MetricResult


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class RepoHealthMetric(Metric):
    name = "repo_health"
    tier = "static"
    description = "Commit count, history spread, contributors, and last-minute dump detection."
    always_on = True
    skippable_when = "never (always-on)"
    default_options = {"dump_window_hours": 6, "dump_commit_ratio": 0.7}
    output_schema = {
        "type": "object",
        "properties": {
            "commit_count": {"type": "integer"},
            "first_commit": {"type": ["string", "null"]},
            "last_commit": {"type": ["string", "null"]},
            "contributors": {"type": "integer"},
            "flag_single_dump": {"type": "boolean"},
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        opts = {**self.default_options, **ctx.options}
        commits = ctx.snapshot.commits
        dates: list[datetime] = []
        for c in commits:
            commit = c.get("commit") or {}
            author = commit.get("author") or {}
            dt = _parse_iso(author.get("date"))
            if dt:
                dates.append(dt)

        dates.sort()
        first = dates[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dates else None
        last = dates[-1].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dates else None

        flag_single_dump = False
        if len(dates) >= 3:
            window_h = float(opts.get("dump_window_hours", 6))
            ratio = float(opts.get("dump_commit_ratio", 0.7))
            end = dates[-1]
            start_window = end - timedelta(hours=window_h)
            in_window = sum(1 for d in dates if d >= start_window)
            if in_window / len(dates) >= ratio:
                flag_single_dump = True

        # Optional hackathon-window awareness (does not change schema; soft signal via dump flag)
        settings = get_settings()
        hs = _parse_iso(settings.hackathon_start)
        he = _parse_iso(settings.hackathon_end)
        if hs and he and dates:
            outside = sum(1 for d in dates if d < hs or d > he)
            if outside == 0 and len(dates) <= 2:
                flag_single_dump = True

        contributors = len(ctx.snapshot.contributors) or len(
            {
                (c.get("author") or {}).get("login")
                or ((c.get("commit") or {}).get("author") or {}).get("email")
                for c in commits
                if c
            }
            - {None}
        )

        data: dict[str, Any] = {
            "commit_count": len(commits),
            "first_commit": first,
            "last_commit": last,
            "contributors": contributors,
            "flag_single_dump": flag_single_dump,
        }
        return MetricResult(name=self.name, status="ok", data=data)
