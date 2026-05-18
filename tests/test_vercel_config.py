from __future__ import annotations

import json
from pathlib import Path


def test_vercel_crons_are_hobby_plan_safe() -> None:
    config_path = Path(__file__).resolve().parents[1] / "webapp" / "vercel.json"
    config = json.loads(config_path.read_text())

    crons = config.get("crons", [])
    assert len(crons) <= 2

    for cron in crons:
        minute, hour, day_of_month, month, day_of_week = cron["schedule"].split()
        assert minute.isdigit()
        assert hour.isdigit()
        assert day_of_month == "*"
        assert month == "*"
        assert day_of_week == "*"
