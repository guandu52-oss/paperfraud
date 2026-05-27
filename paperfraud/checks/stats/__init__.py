"""Statistical methodology auditing checks.

Detects common statistical errors such as:
  - Claiming raw data follows normal distribution (should be residuals)
  - "No significant difference" → "therefore equivalent/non-toxic" fallacies
"""

from paperfraud.checks.stats.normality_claim import run_normality_claim
from paperfraud.checks.stats.fallacies import run_fallacies
from paperfraud.checks.stats.method_misuse import run_method_misuse

__all__ = ["run_normality_claim", "run_fallacies", "run_method_misuse"]
