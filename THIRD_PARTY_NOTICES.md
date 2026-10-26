# Third-party notices

Runtime dependencies are pinned in `requirements.lock`:

- [PyMySQL 1.1.2](https://github.com/PyMySQL/PyMySQL/tree/v1.1.2): MIT License.
- [SQLGlot 28.0.0](https://github.com/tobymao/sqlglot/tree/v28.0.0): MIT License.
- [CPython 3.11](https://docs.python.org/3.11/license.html): Python Software Foundation License and included third-party notices.

Frozen distributions use [PyInstaller](https://pyinstaller.org/en/stable/license.html), licensed under GPL with the distribution exception, and its contributed hooks. The build script copies the installed distributions' license texts into the customer package. PyInstaller is a build dependency, not a customer-side install requirement.

No runtime code is copied from the legacy Go plan-change-capturer or the customer shell scripts. Legacy file formats are independently adapted.

PCC Core source code and documentation are licensed under Apache-2.0; see
[LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party components retain their
respective licenses. Binary distributions must retain the dependency and Python
runtime notices; the application license does not replace them.
