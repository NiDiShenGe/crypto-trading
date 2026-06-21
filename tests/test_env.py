import os
from pathlib import Path
import tempfile

from crypto_trader.env import load_env


def test_load_env() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ".env"
        path.write_text("TEST_CRYPTO_ENV='hello'\n# ignored\n", encoding="utf-8")
        os.environ.pop("TEST_CRYPTO_ENV", None)
        load_env(path)
        assert os.environ["TEST_CRYPTO_ENV"] == "hello"
        os.environ.pop("TEST_CRYPTO_ENV", None)

