"""
Shared test bootstrap - wires up the offline genlayer SDK stub and
loads contract.py once. Same pattern used across this portfolio's
Intelligent Contract test suites.
"""
import importlib.util
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

_CONTRACT_PATH = os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("weathervault_contract", _CONTRACT_PATH)
_contract_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_module)

WeatherVault = _contract_module.WeatherVault
gl = _contract_module.gl

from genlayer import tx_context, Address, _TransferRecorder  # noqa: E402


def make_contract() -> "WeatherVault":
    return WeatherVault()


def transfers():
    return _TransferRecorder.calls


def reset_transfers():
    _TransferRecorder.reset()
