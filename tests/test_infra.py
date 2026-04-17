from __future__ import annotations

"""Infrastructure tests: structlog JSON output, backup script."""

import json
import os
import stat
from io import StringIO
from pathlib import Path

import structlog


def test_structlog_json_output():
    """Verify structlog produces valid JSON (not plain text)."""
    output = StringIO()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=False,
    )
    logger = structlog.get_logger()
    logger.info("test_event", user_id="abc123")
    line = output.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["event"] == "test_event"
    assert parsed["user_id"] == "abc123"
    assert parsed.get("log_level") == "info" or parsed.get("level") == "info"
    assert "timestamp" in parsed

    # Restore default config so other tests aren't affected
    from app.core.logging import setup_logging

    setup_logging()


def test_backup_script_exists_and_executable():
    """Verify scripts/backup.sh exists and has +x flag."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
    assert script.exists(), f"backup.sh not found at {script}"
    if os.name != "nt":
        mode = script.stat().st_mode
        assert mode & stat.S_IXUSR, "backup.sh is not executable"


def test_logging_module_setup():
    """Verify app.core.logging.setup_logging is importable and callable."""
    from app.core.logging import setup_logging

    setup_logging()
    logger = structlog.get_logger()
    assert logger is not None
