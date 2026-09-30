from __future__ import annotations

from pathlib import Path

import pytest

from scripts.public_port.secure_tree import SecureTree, SecureTreeError


def test_secure_tree_write_refuses_symlink_parent_without_touching_external_tree(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (root / "linked").symlink_to(external, target_is_directory=True)

    with SecureTree(root) as tree:
        with pytest.raises(SecureTreeError, match="no-follow directory"):
            tree.write_file("linked/leak.txt", b"must not escape\n", "100644")

    assert not (external / "leak.txt").exists()


def test_secure_tree_inventory_rejects_untracked_empty_directories(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "empty").mkdir()

    with SecureTree(root) as tree:
        with pytest.raises(SecureTreeError, match="empty directory"):
            tree.inventory()
