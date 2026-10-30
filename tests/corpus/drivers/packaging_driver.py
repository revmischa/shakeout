from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

r = Requirement('requests[socks]>=2.0,<3; python_version >= "3.8"')
print(r.name, sorted(r.extras), r.specifier, r.marker)
print(Version("1.2.3rc1") < Version("1.2.3"), Version("2.0.post1").is_postrelease)
print(sorted(SpecifierSet(">=1.0,!=1.5").filter(["0.9", "1.0", "1.5", "2.0"])))
