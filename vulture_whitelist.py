"""Names vulture cannot see being used. Each entry says who uses it.

Run: ``vulture src vulture_whitelist.py``.
"""

from vulture.whitelist_utils import Whitelist

from shakeout.analysis import scopes
from shakeout.emit import BuildResult
from shakeout.runtime import _shakeout_bootstrap, _ShakeoutRuntime

# ast.NodeVisitor dispatches to visit_<NodeType> by name.
scopes._LocalsFinder.visit_MatchAs
scopes._LocalsFinder.visit_MatchStar
scopes._LocalsFinder.visit_MatchMapping
scopes._BindFinder.visit_MatchAs
scopes._BindFinder.visit_MatchStar
scopes._BindFinder.visit_MatchMapping
scopes.RefCollector.visit_NamedExpr

# importlib's finder/loader protocol, called by the import system.
_ShakeoutRuntime.create_module
_ShakeoutRuntime.exec_module
_ShakeoutRuntime.get_source
_ShakeoutRuntime.find_spec

# Called by the code each single-file bundle appends after the runtime.
_shakeout_bootstrap

# Public result fields read by API users and the test harness.
BuildResult.runtime_files

# Attributes written for the standard library to read (ModuleSpec, zipfile.ZipInfo) and
# TypedDict keys of the runtime metadata.
_ = Whitelist()
_.has_location
_.external_attr
_.compress_type
_.dynamic_imports
