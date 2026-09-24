import sys

import pytest

from app.services import windows_agent_subprocess as system


def test_frozen_system_child_uses_windows_libraries_and_restores_bundle_path(monkeypatch, tmp_path):
    bundle = tmp_path / 'bundle'
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(system, '_get_dll_directory', lambda: str(bundle))
    monkeypatch.setattr(system, '_set_dll_directory', lambda value: calls.append(value))
    environment = {'PATH': ';'.join([str(bundle), str(bundle / 'pywin32_system32'),
                                    str(tmp_path / 'bundle-neighbor'), r'C:\Windows\System32']),
                   'FTW_UPDATE_PACKAGE': 'fixture.exe'}
    with system.windows_system_environment(environment) as child:
        assert calls == [None]
        assert child['PATH'] == ';'.join([str(tmp_path / 'bundle-neighbor'), r'C:\Windows\System32'])
        assert child['FTW_UPDATE_PACKAGE'] == 'fixture.exe'
    assert calls == [None, str(bundle)]
    assert environment['PATH'].startswith(str(bundle))


def test_frozen_library_path_restored_after_system_launch_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', r'C:\fixture', raising=False)
    monkeypatch.setattr(system, '_get_dll_directory', lambda: r'C:\fixture')
    monkeypatch.setattr(system, '_set_dll_directory', lambda value: calls.append(value))
    with pytest.raises(OSError):
        with system.windows_system_environment({}) as _child:
            raise OSError('synthetic child failure')
    assert calls == [None, r'C:\fixture']


def test_unfrozen_source_does_not_change_windows_loader(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.setattr(system, '_set_dll_directory', lambda _value: pytest.fail('unexpected loader mutation'))
    with system.windows_system_environment({'PATH': 'unchanged'}) as child:
        assert child == {'PATH': 'unchanged'}
