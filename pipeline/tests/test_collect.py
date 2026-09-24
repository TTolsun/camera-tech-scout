"""Git history parsing.

The regression these tests exist for: the record separator used to sit at the
*end* of the log format. With ``--numstat`` git prints the format string and
then the file statistics of that same commit, so a trailing separator attached
every commit's statistics to the next record. Only the newest commit parsed
correctly, and the rest silently lost their file lists.

``test_commits_from_real_repository`` is the one that actually catches it,
because it reads output produced by git rather than output we constructed.
"""

from __future__ import annotations

from scout.collect import _FIELD_SEP, _LOG_FORMAT, _RECORD_SEP, _parse_log

from conftest import requires_git


def test_log_format_puts_the_separator_first():
    """A trailing separator is the bug. Pin the ordering explicitly."""
    assert _LOG_FORMAT.startswith("%x1e")
    assert not _LOG_FORMAT.endswith(_RECORD_SEP)


def _record(sha: str, subject: str, body: str, numstat: list[str]) -> str:
    head = _FIELD_SEP.join([sha, "Author Name", "2026-09-01T10:00:00+00:00", subject, body])
    return _RECORD_SEP + head + "\n" + "\n".join(numstat)


def test_parse_log_reads_every_record():
    raw = (
        _record("a" * 40, "first subject", "first body", ["3\t1\tsrc/a.cpp"])
        + _record("b" * 40, "second subject", "", ["10\t0\tdocs/b.md", "2\t2\tsrc/c.cpp"])
        + _record("c" * 40, "third subject", "line one\nline two", ["1\t1\tsrc/d.cpp"])
    )
    commits = _parse_log(raw)

    assert [c.sha for c in commits] == ["a" * 40, "b" * 40, "c" * 40]
    assert [c.subject for c in commits] == ["first subject", "second subject", "third subject"]


def test_parse_log_keeps_file_lists_with_their_own_commit():
    """The exact failure mode of the original bug."""
    raw = (
        _record("a" * 40, "adds one file", "", ["3\t1\tsrc/a.cpp"])
        + _record("b" * 40, "adds two files", "", ["10\t0\tdocs/b.md", "2\t2\tsrc/c.cpp"])
    )
    first, second = _parse_log(raw)

    assert first.files == ["src/a.cpp"]
    assert second.files == ["docs/b.md", "src/c.cpp"]


def test_parse_log_accumulates_line_counts():
    raw = _record("a" * 40, "subject", "", ["10\t0\tdocs/b.md", "2\t3\tsrc/c.cpp"])
    commit = _parse_log(raw)[0]

    assert commit.insertions == 12
    assert commit.deletions == 3


def test_parse_log_keeps_multi_line_bodies_out_of_the_file_list():
    raw = _record("a" * 40, "subject", "line one\nline two", ["1\t1\tsrc/d.cpp"])
    commit = _parse_log(raw)[0]

    assert commit.files == ["src/d.cpp"]
    assert "line one" in commit.body
    assert "line two" in commit.body
    assert "src/d.cpp" not in commit.body
    assert commit.message.startswith("subject")


def test_parse_log_tolerates_binary_numstat():
    """git writes `-` instead of a count for binary files."""
    raw = _record("a" * 40, "adds a binary", "", ["-\t-\tassets/image.png"])
    commit = _parse_log(raw)[0]

    assert commit.files == ["assets/image.png"]
    assert commit.insertions == 0
    assert commit.deletions == 0


def test_parse_log_ignores_empty_input():
    assert _parse_log("") == []
    assert _parse_log("\n\n") == []


# ---------------------------------------------------------------------------
# Against a real repository, which is what makes the regression test real.
# ---------------------------------------------------------------------------
@requires_git
def test_commits_from_real_repository(cam_utils_repo):
    commits = cam_utils_repo.commits(max_count=20)

    assert len(commits) == 3, "the cam-utils fixture has exactly three commits"
    assert all(c.sha for c in commits)
    # Newest first.
    assert "bump version" in commits[0].subject
    assert "clang-format" in commits[-1].subject

    # Every commit has to carry its own file list. This is the assertion the
    # original bug failed.
    assert all(c.files for c in commits), "each commit must report the files it touched"
    assert "CHANGELOG.md" in commits[0].files
    assert "src/cam_utils.cpp" in commits[-1].files


@requires_git
def test_commit_bodies_survive_parsing(cam_utils_repo):
    commits = {c.subject: c for c in cam_utils_repo.commits(max_count=20)}
    tuning = next(c for s, c in commits.items() if "tune" in s)

    assert "96 to 128" in tuning.body


@requires_git
def test_head_and_membership(sync_repo):
    head = sync_repo.head_sha()

    assert head and len(head) == 40
    assert sync_repo.has_commit(head)
    assert not sync_repo.has_commit("0" * 40)


@requires_git
def test_changed_files_between_revisions(sync_repo):
    commits = sync_repo.commits(max_count=10)
    oldest = commits[-1].sha

    changed = sync_repo.changed_files(oldest)

    assert changed is not None
    assert "src/sensor_sync_controller.cpp" in changed


@requires_git
def test_changed_files_returns_none_for_unknown_base(sync_repo):
    """An unreachable base means the caller must fall back to a full scan."""
    assert sync_repo.changed_files("0" * 40) is None


@requires_git
def test_first_commit_for_path(sync_repo):
    found = sync_repo.first_commit_for_path("src/sensor_sync_controller.h")

    assert found is not None
    sha, date = found
    assert len(sha) == 40
    assert date.startswith("20")
