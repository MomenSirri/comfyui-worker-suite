"""The backend contract of `tests/handler_contract`, run against this handler."""

import os
import sys
import unittest

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.abspath(os.path.join(SERVICE_DIR, "..", "..", "tests")))

from handler_contract import load_handler
from handler_contract.cases import ContractCases

handler = load_handler(SERVICE_DIR)


class TestBackendContract(ContractCases, unittest.TestCase):
    """The worker behind the Studio's provider models."""

    handler = handler
    serves_video = True
    returns_text_files = True

    def run_handler(self, job):
        return handler.handler(job)


if __name__ == "__main__":
    unittest.main()
