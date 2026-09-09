from unittest.mock import Mock, patch

from actions.webui_host import WebUIHost


def test_agent_waits_for_first_run_setup():
    host = object.__new__(WebUIHost)
    host._has_api_key = Mock(side_effect=[False, False, True])
    with patch("actions.webui_host.time.sleep") as sleep:
        host.wait_for_api_key()
    assert sleep.call_count == 2
    assert host._has_api_key.call_count == 3


def test_configured_installation_starts_without_waiting():
    host = object.__new__(WebUIHost)
    host._has_api_key = Mock(return_value=True)
    with patch("actions.webui_host.time.sleep") as sleep:
        host.wait_for_api_key()
    sleep.assert_not_called()
