"""Host executors must also run on GPU servers with Python 3.6."""
import subprocess
from types import SimpleNamespace

import pytest

from services import training_executor as executor


@pytest.mark.parametrize('busy', [False, True])
def test_export_precheck_uses_python36_subprocess_arguments(monkeypatch, tmp_path, busy):
    calls = []

    def run(args, *, stdout=None, stderr=None, universal_newlines=False, timeout=None):
        assert stdout == subprocess.PIPE and stderr == subprocess.PIPE
        assert universal_newlines is True and timeout in (10, 25, 120)
        calls.append(args)
        if args[:3] == ['docker', 'image', 'inspect']:
            value = '[{"Id":"sha256:fixture"}]'
        elif args[0] == 'nvidia-smi':
            value = ('0, GPU-test, T4, 15000, Disabled' if '--query-gpu=' in args[1]
                     else 'GPU-test, 42' if busy else '')
        else:
            assert args[:3] == ['docker', 'run', '--rm']
            assert '--gpus' not in args
            value = ''
        return SimpleNamespace(returncode=0, stdout=value, stderr='')

    monkeypatch.setattr(executor.subprocess, 'run', run)
    config = dict(image='fixture:latest', gpu_uuids=['GPU-test'], output_root=str(tmp_path),
                  kind='model_export', output={'format': 'tensorrt'}, model_path='', parameters={}, mounts=[])
    result = executor.precheck(dict(config=config, pending_onnx=True))
    assert result['ok'] is True
    assert result['image_digest'] == 'sha256:fixture'
    assert len(calls) == 3
    assert next(row for row in result['checks'] if row['name'] == 'GPU GPU-test')['ok'] is True


def test_docker_keeps_stderr_and_optional_failure(monkeypatch):
    monkeypatch.setattr(executor.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout='', stderr='daemon unavailable'))
    with pytest.raises(RuntimeError, match='daemon unavailable'):
        executor.docker(['ps'])
    assert executor.docker(['ps'], check=False).returncode == 1


def test_safe_child_works_without_new_pathlib_api(tmp_path):
    class LegacyPath(type(tmp_path)):
        def is_relative_to(self, *args):
            raise AttributeError('is_relative_to is unavailable on Python 3.6')

        def relative_to(self, *args):
            return type(tmp_path)(self).relative_to(*args)

    root = LegacyPath(tmp_path / 'models')
    root.mkdir()
    assert executor.safe_child(root, 'model.onnx') == root / 'model.onnx'
    with pytest.raises(ValueError, match='路径非法'):
        executor.safe_child(root, '../outside.onnx')
    with pytest.raises(ValueError, match='路径非法'):
        executor.safe_child(root, '/outside.onnx')
