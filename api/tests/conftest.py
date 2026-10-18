"""Keep synthetic tests local; no external error reports."""

import os

os.environ["CALL_INTELLIGENCE_SENTRY_DISABLED"] = "1"
