"""Tests for extended tools (git worktree and document reader)."""

import base64
import importlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from deepagents.tools.document_reader import _parse_page_range, read_image, read_pdf
from deepagents.tools.git_worktree import (
    _run_git,
    _validate_branch,
    _validate_path,
    git_worktree_create,
    git_worktree_list,
    git_worktree_remove,
)

# ---------------------------------------------------------------------------
# git_worktree validation
# ---------------------------------------------------------------------------


class TestValidateBranch:
    def test_valid_branches(self) -> None:
        for name in ["main", "feat/foo", "fix-bar", "v1.0.0", "user_branch"]:
            _validate_branch(name)  # should not raise

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid branch name"):
            _validate_branch("")

    def test_rejects_shell_metacharacters(self) -> None:
        for bad in ["main;rm -rf /", "branch$(cmd)", "a b", "foo|bar", "a&b"]:
            with pytest.raises(ValueError, match="Invalid branch name"):
                _validate_branch(bad)


class TestValidatePath:
    def test_valid_paths(self) -> None:
        for p in [
            "/tmp/worktree",  # noqa: S108
            "../worktrees/feat-foo",
            "relative/path",
            "~/work",
        ]:
            _validate_path(p)

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid path"):
            _validate_path("")

    def test_rejects_shell_metacharacters(self) -> None:
        for bad in ["/tmp/$(cmd)", "path;evil", "a b", "foo|bar"]:  # noqa: S108
            with pytest.raises(ValueError, match="Invalid path"):
                _validate_path(bad)


# ---------------------------------------------------------------------------
# git_worktree tools (subprocess mocked)
# ---------------------------------------------------------------------------


class TestGitWorktreeCreate:
    @patch("deepagents.tools.git_worktree._run_git")
    def test_creates_with_default_path(self, mock_git) -> None:
        mock_git.return_value = "Preparing worktree"
        result = git_worktree_create.invoke({"branch": "feat/new"})
        mock_git.assert_called_once_with([
            "worktree", "add", "-B", "feat/new", "../worktrees/feat-new",
        ])
        assert "feat/new" in result
        assert "../worktrees/feat-new" in result

    @patch("deepagents.tools.git_worktree._run_git")
    def test_creates_with_custom_path(self, mock_git) -> None:
        mock_git.return_value = "Preparing worktree"
        wt = "/tmp/wt"  # noqa: S108
        result = git_worktree_create.invoke({"branch": "main", "path": wt})
        mock_git.assert_called_once_with([
            "worktree", "add", "-B", "main", wt,
        ])
        assert wt in result

    def test_rejects_invalid_branch(self) -> None:
        with pytest.raises(ValueError, match="Invalid branch name"):
            git_worktree_create.invoke({"branch": "bad;branch"})


class TestGitWorktreeList:
    @patch("deepagents.tools.git_worktree._run_git")
    def test_lists_worktrees(self, mock_git) -> None:
        mock_git.return_value = "/repo  abc1234 [main]\n/tmp/wt  def5678 [feat]"
        result = git_worktree_list.invoke({})
        mock_git.assert_called_once_with(["worktree", "list"])
        assert "[main]" in result
        assert "[feat]" in result


class TestGitWorktreeRemove:
    @patch("deepagents.tools.git_worktree._run_git")
    def test_removes_worktree(self, mock_git) -> None:
        mock_git.return_value = ""
        wt = "/tmp/wt"  # noqa: S108
        result = git_worktree_remove.invoke({"path": wt})
        mock_git.assert_called_once_with(["worktree", "remove", wt])
        assert "removed" in result.lower()

    def test_rejects_invalid_path(self) -> None:
        with pytest.raises(ValueError, match="Invalid path"):
            git_worktree_remove.invoke({"path": "/tmp/$(evil)"})  # noqa: S108


class TestRunGit:
    @patch("subprocess.run")
    def test_raises_on_failure(self, mock_run) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "git", stderr="fatal: error"
        )
        with pytest.raises(RuntimeError, match="failed"):
            _run_git(["status"])

    @patch("subprocess.run")
    def test_raises_on_timeout(self, mock_run) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)
        with pytest.raises(RuntimeError, match="timed out"):
            _run_git(["status"])


# ---------------------------------------------------------------------------
# document_reader tools
# ---------------------------------------------------------------------------


class TestReadImage:
    def test_reads_png(self, tmp_path: Path) -> None:
        # Create a minimal valid PNG (1x1 pixel)
        img = tmp_path / "test.png"
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"  # signature
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
            b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img.write_bytes(png_bytes)
        result = read_image.invoke({"path": str(img)})
        assert result.startswith("data:image/png;base64,")
        # Verify round-trip
        encoded = result.split(",", 1)[1]
        assert base64.b64decode(encoded) == png_bytes

    def test_rejects_unsupported_format(self, tmp_path: Path) -> None:
        bmp = tmp_path / "test.bmp"
        bmp.write_bytes(b"BM")
        result = read_image.invoke({"path": str(bmp)})
        assert "Unsupported" in result

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            read_image.invoke({"path": "/nonexistent/image.png"})


class TestReadPdf:
    def test_not_a_pdf(self, tmp_path: Path) -> None:
        txt = tmp_path / "file.txt"
        txt.write_text("hello")
        with pytest.raises(ValueError, match="Not a PDF"):
            read_pdf.invoke({"path": str(txt)})

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            read_pdf.invoke({"path": "/nonexistent/file.pdf"})

    def test_missing_pymupdf_fallback(self, tmp_path: Path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        with patch.dict("sys.modules", {"fitz": None}):
            import deepagents.tools.document_reader as dr  # noqa: PLC0415

            importlib.reload(dr)
            result = dr.read_pdf.invoke({"path": str(pdf)})
            assert "pymupdf is not installed" in result


class TestReadPdfWithPymupdf:
    """Tests that only run when pymupdf is available."""

    @pytest.fixture(autouse=True)
    def _require_pymupdf(self) -> None:
        pytest.importorskip("fitz", reason="pymupdf not installed")

    def test_reads_all_pages(self, tmp_path: Path) -> None:
        import fitz  # noqa: PLC0415

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        tw = fitz.TextWriter(page.rect)
        tw.append((72, 72), "Hello World", fontsize=12)
        tw.write_page(page)
        doc.save(str(pdf_path))
        doc.close()

        result = read_pdf.invoke({"path": str(pdf_path)})
        assert "Page 1" in result
        assert "Hello" in result

    def test_reads_page_range(self, tmp_path: Path) -> None:
        import fitz  # noqa: PLC0415

        pdf_path = tmp_path / "multi.pdf"
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            tw = fitz.TextWriter(page.rect)
            tw.append((72, 72), f"Content {i + 1}", fontsize=12)
            tw.write_page(page)
        doc.save(str(pdf_path))
        doc.close()

        result = read_pdf.invoke({"path": str(pdf_path), "pages": "2-3"})
        assert "Page 2" in result
        assert "Page 3" in result
        assert "Page 1" not in result

    def test_invalid_page_range(self, tmp_path: Path) -> None:
        import fitz  # noqa: PLC0415

        pdf_path = tmp_path / "small.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        with pytest.raises(ValueError, match="out of bounds"):
            read_pdf.invoke({"path": str(pdf_path), "pages": "2-5"})


class TestParsePageRange:
    def test_single_page(self) -> None:
        assert _parse_page_range("3", 10) == (2, 3)

    def test_range(self) -> None:
        assert _parse_page_range("1-5", 10) == (0, 5)

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid page"):
            _parse_page_range("abc", 10)

    def test_out_of_bounds(self) -> None:
        with pytest.raises(ValueError, match="out of bounds"):
            _parse_page_range("5-15", 10)
