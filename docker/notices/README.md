# License files for packages that don't ship one

`docker/third_party_notices.py` reads each package's own license files. A package version whose published files contain none needs its license here, as `<name>-<version>.txt`, copied unchanged from the project's official source for that exact version. Update it when the version changes; the check fails until you do.

| File | Source |
|---|---|
| `dnspython-2.9.0.txt` | `LICENSE` in https://github.com/rthalley/dnspython at tag `v2.9.0` (neither the wheel nor the source package on PyPI contains it) |
