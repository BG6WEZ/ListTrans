"""Run the serial suite from materialized Train/Dev without reading sealed fixtures."""

import os
from pathlib import Path
import sys
from unittest.mock import patch

from m3_r01_projection import projection, seal_holdout, V020


def main():
    path = Path(sys.argv.pop(1))
    seal_holdout()
    os.environ.update(LISTTRANS_V021_M3_RERANKER="off", LLM_ENABLED="false",
                      LISTTRANS_V020_M2_LLM_DECISION="off", PYTHON_DOTENV_DISABLED="1")
    items, labels = projection(path)
    sys.path.insert(0, str(V020))
    import m0_r01_pytest as previous

    with patch.object(previous.m0_r01_data, "read_projection", return_value=(items, labels)):
        return previous.main()


if __name__ == "__main__":
    raise SystemExit(main())
