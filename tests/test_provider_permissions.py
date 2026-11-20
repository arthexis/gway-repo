"""Regression for safe vs writable mode guards in the live Ansible manifest installer."""

from pathlib import Path

PLAYBOOK = (Path(__file__).resolve().parents[1] / "ansible/provider.yml").read_text()


def test_permission_guards_distinguish_write_bits():
    # The Ansible expressions use modulo 4 on the group/other octal digit.
    for mode in (0o700, 0o744, 0o755, 0o644):
        assert mode % 4 < 2
        assert (mode // 8) % 4 < 2
    for mode in (0o775, 0o757, 0o777, 0o766, 0o664, 0o642):
        assert not (mode % 4 < 2 and (mode // 8) % 4 < 2)


def test_mode_guards_are_applied_to_binary_and_existing_manifest():
    assert "((repo_binary.stat.mode | int(base=8)) // 8) % 4 < 2" in PLAYBOOK
    assert "(repo_binary.stat.mode | int(base=8)) % 4 < 2" in PLAYBOOK
    assert "((repo_existing.results[2].stat.mode | int(base=8)) // 8) % 4 < 2" in PLAYBOOK
    assert "(repo_existing.results[2].stat.mode | int(base=8)) % 4 < 2" in PLAYBOOK
