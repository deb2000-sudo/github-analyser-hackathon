"""Static analysis helpers.

Keep this package init import-light. github.client imports MANIFEST_NAMES
from app.analysis.manifests; importing facts here would create a circular import.
"""

from app.analysis.manifests import MANIFEST_NAMES

__all__ = ["MANIFEST_NAMES"]
