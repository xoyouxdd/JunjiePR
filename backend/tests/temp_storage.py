"""Own the temporary files and diagnostic log for a test invocation."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import traceback


def _temp_base() -> Path:
    current = Path(tempfile.gettempdir())
    if os.name != "nt" or not Path("D:/").is_dir():
        return current
    if current.drive.casefold() == "d:":
        return current
    return Path("D:/JunjiePR-testing/scratch/native")


class _Tee:
    def __init__(self, stream, log):
        self.stream = stream
        self.log = log

    def write(self, text):
        self.log.write(text)
        try:
            return self.stream.write(text)
        except UnicodeEncodeError:
            encoding = self.stream.encoding
            return self.stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding))

    def flush(self):
        self.log.flush()
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def test_workspace(prefix):
    base = _temp_base().resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    variables = ("TEMP", "TMP", "TMPDIR")
    previous = {name: os.environ.get(name) for name in variables}
    previous_temp = tempfile.tempdir
    for name in variables:
        os.environ[name] = str(root)
    tempfile.tempdir = str(root)
    try:
        yield root
    finally:
        tempfile.tempdir = previous_temp
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def test_log(root):
    with (root / "test.log").open("a", encoding="utf-8", errors="backslashreplace", buffering=1) as log:
        with redirect_stdout(_Tee(sys.stdout, log)), redirect_stderr(_Tee(sys.stderr, log)):
            try:
                yield
            except BaseException:
                traceback.print_exc(file=log)
                raise


def finish_test_workspace(root, success):
    if success:
        try:
            if root.resolve(strict=True) != root or root.is_symlink() or (
                os.name == "nt" and
                root.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise OSError("test directory path changed")
            shutil.rmtree(root)
            return
        except OSError as error:
            print(f"Could not remove test temporary files: {error}", file=sys.stderr)
    print(f"Test artifacts retained: {root}", file=sys.stderr)
