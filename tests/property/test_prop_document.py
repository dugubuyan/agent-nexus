# Feature: doc-exchange-center, Property 4-10: DocumentService property tests
"""
Property-based tests for DocumentService.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.6, 3.1, 3.3, 3.4, 3.5, 3.8, 6.1, 6.3, 6.5**
"""

import os
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.models import Base, ProjectSpace
from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService, VALID_DOC_TYPES, VALID_CONFIG_VARIANTS
from doc_exchange.services.errors import DocExchangeError
from doc_exchange.services.schemas import PushRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _make_session_and_space(engine):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="prop-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()
    return session, space.id


def _make_service(session, docs_root):
    audit = AuditLogService(db=session)
    return DocumentService(db=session, docs_root=docs_root, audit_log_service=audit)


def _push(svc, doc_id, content, pushed_by="agent-1", project_space_id=None, metadata=None):
    req = PushRequest(
        doc_id=doc_id,
        content=content,
        pushed_by=pushed_by,
        project_space_id=project_space_id,
        metadata=metadata or {},
    )
    return svc.push(req)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid subproject IDs: non-empty alphanumeric + hyphens/underscores
valid_subproject_id = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"),
    min_size=1,
    max_size=32,
).filter(lambda s: s.strip("-_") != "")

# Valid doc types (excluding config to keep things simple for non-config tests)
non_config_doc_types = st.sampled_from(sorted(VALID_DOC_TYPES - {"config"}))

# Valid config variants
valid_config_stage = st.sampled_from(sorted(VALID_CONFIG_VARIANTS))

# Non-empty content strings (exclude bare \r and surrogate characters)
non_empty_content = st.text(
    alphabet=st.characters(blacklist_characters="\r", blacklist_categories=("Cs",)),
    min_size=1,
    max_size=500,
)

# Distinct content pairs (for version increment tests)
distinct_content_pair = st.lists(
    st.text(alphabet=st.characters(blacklist_characters="\r", blacklist_categories=("Cs",)), min_size=1, max_size=200),
    min_size=2,
    max_size=10,
    unique=True,
)

# Invalid doc_id strategies
invalid_doc_id = st.one_of(
    # empty string
    st.just(""),
    # whitespace only
    st.just("   "),
    # single segment (no slash)
    st.text(min_size=1, max_size=20).filter(lambda s: "/" not in s and s.strip()),
    # too many segments (4+)
    st.builds(
        lambda a, b, c, d: f"{a}/{b}/{c}/{d}",
        st.text(min_size=1, max_size=10).filter(lambda s: "/" not in s),
        st.text(min_size=1, max_size=10).filter(lambda s: "/" not in s),
        st.text(min_size=1, max_size=10).filter(lambda s: "/" not in s),
        st.text(min_size=1, max_size=10).filter(lambda s: "/" not in s),
    ),
    # valid subproject but invalid doc_type
    st.builds(
        lambda sub, t: f"{sub}/{t}",
        valid_subproject_id,
        st.text(min_size=1, max_size=20).filter(
            lambda s: s not in VALID_DOC_TYPES and "/" not in s and s.strip()
        ),
    ),
    # empty subproject_id part
    st.builds(lambda t: f"/{t}", non_config_doc_types),
)

# Invalid config variants (not in {dev, test, prod})
invalid_config_stage = st.text(min_size=1, max_size=20).filter(
    lambda s: s not in VALID_CONFIG_VARIANTS and s.strip() != ""
)


# ---------------------------------------------------------------------------
# Property 4: 文档推送内容保真 Round-Trip
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 4: 文档推送内容保真 Round-Trip


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    doc_type=non_config_doc_types,
    content=non_empty_content,
)
def test_prop_push_content_round_trip(subproject_id, doc_type, content):
    """
    Property 4: 文档推送内容保真 Round-Trip

    For any valid push request (legal doc_id, non-empty content), after a
    successful push, get() returns content identical to what was pushed.
    The filesystem file also contains exactly the pushed content.

    **Validates: Requirements 2.1, 3.1, 3.3, 6.1**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)
            doc_id = f"{subproject_id}/{doc_type}"

            result = _push(svc, doc_id, content, project_space_id=space_id)
            assert result.version == 1

            # DB round-trip: content must be identical
            fetched = svc.get(doc_id, space_id)
            assert fetched.content == content, (
                f"Content mismatch: pushed {content!r}, got {fetched.content!r}"
            )

            # Filesystem round-trip: file must contain exactly the pushed content
            file_path = os.path.join(docs_root, space_id, subproject_id, f"{doc_type}.md")
            assert os.path.exists(file_path), f"Expected file at {file_path}"
            with open(file_path, "r", encoding="utf-8") as f:
                file_content = f.read()
            assert file_content == content, (
                f"File content mismatch: pushed {content!r}, file has {file_content!r}"
            )
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 5: 版本号单调递增
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 5: 版本号单调递增


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    doc_type=non_config_doc_types,
    contents=distinct_content_pair,
)
def test_prop_version_monotonically_increasing(subproject_id, doc_type, contents):
    """
    Property 5: 版本号单调递增

    For any document, multiple pushes (each with different content) must
    return strictly increasing version numbers.

    **Validates: Requirements 2.2**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)
            doc_id = f"{subproject_id}/{doc_type}"

            versions = []
            for content in contents:
                result = _push(svc, doc_id, content, project_space_id=space_id)
                versions.append(result.version)

            # Each version must be strictly greater than the previous
            for i in range(1, len(versions)):
                assert versions[i] > versions[i - 1], (
                    f"Version not strictly increasing at index {i}: {versions}"
                )

            # Versions must be exactly 1, 2, ..., N
            assert versions == list(range(1, len(contents) + 1)), (
                f"Expected versions {list(range(1, len(contents) + 1))}, got {versions}"
            )
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 6: 相同内容推送被拒绝
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 6: 相同内容推送被拒绝


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    doc_type=non_config_doc_types,
    content=non_empty_content,
)
def test_prop_duplicate_content_rejected(subproject_id, doc_type, content):
    """
    Property 6: 相同内容推送被拒绝

    For any existing document, pushing the same content as the latest version
    must be rejected with CONTENT_UNCHANGED, and the version number must not
    increase.

    **Validates: Requirements 2.3**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)
            doc_id = f"{subproject_id}/{doc_type}"

            # First push succeeds
            result = _push(svc, doc_id, content, project_space_id=space_id)
            version_before = result.version

            # Second push with identical content must be rejected
            with pytest.raises(DocExchangeError) as exc_info:
                _push(svc, doc_id, content, project_space_id=space_id)

            assert exc_info.value.error_code == "CONTENT_UNCHANGED", (
                f"Expected CONTENT_UNCHANGED, got {exc_info.value.error_code}"
            )

            # Version must not have changed
            fetched = svc.get(doc_id, space_id)
            assert fetched.version == version_before, (
                f"Version changed after rejected push: {version_before} -> {fetched.version}"
            )
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 7: 非法 doc_id 返回格式错误
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 7: 非法 doc_id 返回格式错误


@settings(max_examples=100)
@given(doc_id=invalid_doc_id)
def test_prop_invalid_doc_id_rejected(doc_id):
    """
    Property 7: 非法 doc_id 返回格式错误

    For any doc_id with invalid format (empty, missing separator, invalid
    doc_type, too many segments), push must be rejected with INVALID_DOC_ID,
    and the error response must include format requirement details.

    **Validates: Requirements 2.4**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)

            with pytest.raises(DocExchangeError) as exc_info:
                _push(svc, doc_id, "some content", project_space_id=space_id)

            err = exc_info.value
            assert err.error_code == "INVALID_DOC_ID", (
                f"Expected INVALID_DOC_ID for doc_id={doc_id!r}, got {err.error_code!r}"
            )
            # Error details must include format information (either 'format' key or valid type hints)
            assert err.details is not None, "Error details must not be None"
            has_format_info = (
                "format" in err.details
                or "valid_doc_types" in err.details
                or "valid_stages" in err.details
            )
            assert has_format_info, (
                f"Error details must include format/type info, got {err.details}"
            )
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 8: config 类型必须含 stage 字段
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 8: config 类型必须含 stage 字段


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    content=non_empty_content,
    bad_stage=st.one_of(
        st.none(),                  # missing stage key entirely
        st.just(""),                # empty string
        invalid_config_stage,       # non-empty but invalid value
    ),
)
def test_prop_config_missing_or_invalid_stage_rejected(subproject_id, content, bad_stage):
    """
    Property 8 (invalid variant): config type push without valid variant must be rejected.

    **Validates: Requirements 2.6, 6.3**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)

            # config without variant in doc_id → always INVALID_DOC_ID
            doc_id = f"{subproject_id}/config"
            if bad_stage is not None and bad_stage != "":
                # invalid variant in doc_id
                doc_id = f"{subproject_id}/config/{bad_stage}"

            with pytest.raises(DocExchangeError) as exc_info:
                _push(svc, doc_id, content, project_space_id=space_id)

            err = exc_info.value
            assert err.error_code == "INVALID_DOC_ID", (
                f"Expected INVALID_DOC_ID, got {err.error_code!r}"
            )
        finally:
            session.close()
            engine.dispose()


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    content=non_empty_content,
    stage=valid_config_stage,
)
def test_prop_config_valid_stage_succeeds(subproject_id, content, stage):
    """
    Property 8 (valid stage): config type push with valid stage must succeed.

    **Validates: Requirements 2.6, 6.3**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)

            # Stage in doc_id
            doc_id_with_stage = f"{subproject_id}/config/{stage}"
            result = _push(svc, doc_id_with_stage, content, project_space_id=space_id)
            assert result.version >= 1
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 9: 版本元数据完整性
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 9: 版本元数据完整性


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    doc_type=non_config_doc_types,
    content=non_empty_content,
    pushed_by=st.one_of(
        st.just("system_llm"),
        valid_subproject_id,  # reuse as project_id
    ),
)
def test_prop_version_metadata_complete(subproject_id, doc_type, content, pushed_by):
    """
    Property 9: 版本元数据完整性

    For any successfully pushed document version, both get() and list_versions()
    must return records containing: version number, pushed_at timestamp, and
    pushed_by equal to the pusher's project_id or "system_llm".

    **Validates: Requirements 3.4, 6.5**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)
            doc_id = f"{subproject_id}/{doc_type}"

            result = _push(svc, doc_id, content, pushed_by=pushed_by, project_space_id=space_id)

            # get() must return complete metadata
            fetched = svc.get(doc_id, space_id)
            assert fetched.version is not None, "version must be present"
            assert fetched.pushed_at is not None, "pushed_at must be present"
            assert fetched.pushed_by == pushed_by, (
                f"pushed_by mismatch: expected {pushed_by!r}, got {fetched.pushed_by!r}"
            )

            # list_versions() must also return complete metadata
            versions = svc.list_versions(doc_id, space_id)
            assert len(versions) == 1
            v = versions[0]
            assert v.version == result.version, "version in list_versions must match push result"
            assert v.pushed_at is not None, "pushed_at must be present in list_versions"
            assert v.pushed_by == pushed_by, (
                f"pushed_by in list_versions mismatch: expected {pushed_by!r}, got {v.pushed_by!r}"
            )
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 10: 历史版本查询 Round-Trip
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 10: 历史版本查询 Round-Trip


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    doc_type=non_config_doc_types,
    contents=distinct_content_pair,
)
def test_prop_historical_version_round_trip(subproject_id, doc_type, contents):
    """
    Property 10: 历史版本查询 Round-Trip

    For any document with multiple versions, querying any historical version
    by doc_id + version number must return content identical to what was pushed
    for that version. list_versions() must return exactly as many entries as
    the number of pushes performed.

    **Validates: Requirements 3.5, 3.8**
    """
    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)
            svc = _make_service(session, docs_root)
            doc_id = f"{subproject_id}/{doc_type}"

            pushed_versions = []
            for content in contents:
                result = _push(svc, doc_id, content, project_space_id=space_id)
                pushed_versions.append((result.version, content))

            # list_versions length must equal number of pushes
            version_list = svc.list_versions(doc_id, space_id)
            assert len(version_list) == len(contents), (
                f"Expected {len(contents)} versions, got {len(version_list)}"
            )

            # Each historical version must return the exact content pushed at that version
            for version_num, expected_content in pushed_versions:
                fetched = svc.get(doc_id, space_id, version=version_num)
                assert fetched.content == expected_content, (
                    f"Content mismatch at version {version_num}: "
                    f"expected {expected_content!r}, got {fetched.content!r}"
                )
                assert fetched.version == version_num, (
                    f"Version number mismatch: expected {version_num}, got {fetched.version}"
                )
        finally:
            session.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# Property 11: 版本保留策略不变量
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 11: 版本保留策略不变量


@settings(max_examples=50)
@given(
    subproject_id=valid_subproject_id,
    doc_type=non_config_doc_types,
    keep_recent_n=st.integers(min_value=1, max_value=5),
    extra_old_count=st.integers(min_value=1, max_value=5),
    milestone_indices=st.lists(st.integers(min_value=0, max_value=4), max_size=3, unique=True),
)
def test_prop_version_retention_invariants(
    subproject_id, doc_type, keep_recent_n, extra_old_count, milestone_indices
):
    """
    Property 11: 版本保留策略不变量

    For any document with N+K versions (K > 0 old versions beyond the retention
    window), after running VersionRetentionService.run_cleanup():
      (a) The latest version still exists with content.
      (b) The most recent N versions still exist with content.
      (c) All milestone versions still exist with content.
      (d) Old non-milestone, non-recent versions have their content archived/deleted.

    **Validates: Requirements 3.9, 3.10, 3.11, 3.12**
    """
    import uuid
    from datetime import timedelta

    from doc_exchange.models.entities import Document, DocumentVersion, DocumentVersionContent
    from doc_exchange.services.version_retention_service import VersionRetentionService

    engine = _make_engine()
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id = _make_session_and_space(engine)

            # Total versions: extra_old_count (old) + keep_recent_n (recent)
            total_versions = extra_old_count + keep_recent_n
            retention_days = 90

            # Clamp milestone_indices to valid range [0, extra_old_count - 1]
            # so milestones are only among the "old" versions (the ones eligible for archival)
            valid_milestone_indices = [i for i in milestone_indices if i < extra_old_count]

            # Create Document record directly (bypass push to control pushed_at timestamps)
            doc_id = f"{subproject_id}/{doc_type}"
            now = datetime.now(timezone.utc)
            old_cutoff = now - timedelta(days=retention_days + 10)  # 10 days past the cutoff

            document = Document(
                id=doc_id,
                project_space_id=space_id,
                subproject_id=subproject_id,
                doc_type=doc_type,
                doc_variant=None,
                latest_version=total_versions,
                created_at=old_cutoff,
            )
            session.add(document)
            session.flush()

            version_ids = []
            # Create old versions (indices 0..extra_old_count-1)
            for i in range(extra_old_count):
                ver_num = i + 1
                is_milestone = i in valid_milestone_indices
                ver_id = str(uuid.uuid4())
                ver = DocumentVersion(
                    id=ver_id,
                    document_id=doc_id,
                    project_space_id=space_id,
                    version=ver_num,
                    content_hash=str(uuid.uuid4()),
                    pushed_by="test-agent",
                    status="published",
                    is_milestone=is_milestone,
                    milestone_stage="v1.0" if is_milestone else None,
                    pushed_at=old_cutoff,  # old enough to be eligible for archival
                    published_at=old_cutoff,
                )
                session.add(ver)
                session.flush()
                content = DocumentVersionContent(
                    version_id=ver_id,
                    project_space_id=space_id,
                    content=f"Old content version {ver_num}",
                )
                session.add(content)
                version_ids.append(ver_id)

            # Create recent versions (indices extra_old_count..total_versions-1)
            recent_start = extra_old_count + 1
            for i in range(keep_recent_n):
                ver_num = extra_old_count + i + 1
                ver_id = str(uuid.uuid4())
                ver = DocumentVersion(
                    id=ver_id,
                    document_id=doc_id,
                    project_space_id=space_id,
                    version=ver_num,
                    content_hash=str(uuid.uuid4()),
                    pushed_by="test-agent",
                    status="published",
                    is_milestone=False,
                    milestone_stage=None,
                    pushed_at=now - timedelta(days=1),  # recent, within retention window
                    published_at=now - timedelta(days=1),
                )
                session.add(ver)
                session.flush()
                content = DocumentVersionContent(
                    version_id=ver_id,
                    project_space_id=space_id,
                    content=f"Recent content version {ver_num}",
                )
                session.add(content)
                version_ids.append(ver_id)

            session.flush()

            # Run cleanup
            svc = VersionRetentionService(
                db=session,
                keep_recent_n=keep_recent_n,
                retention_days=retention_days,
            )
            svc.run_cleanup(project_space_id=space_id)
            session.flush()

            # Helper: check if a version has content
            def has_content(ver_id: str) -> bool:
                c = (
                    session.query(DocumentVersionContent)
                    .filter(DocumentVersionContent.version_id == ver_id)
                    .first()
                )
                return c is not None

            def get_version(ver_num: int) -> DocumentVersion:
                return (
                    session.query(DocumentVersion)
                    .filter(
                        DocumentVersion.document_id == doc_id,
                        DocumentVersion.project_space_id == space_id,
                        DocumentVersion.version == ver_num,
                    )
                    .first()
                )

            # (a) Latest version must still exist with content
            latest_ver = get_version(total_versions)
            assert latest_ver is not None, "Latest version must still exist"
            assert latest_ver.status != "archived", "Latest version must not be archived"
            assert has_content(latest_ver.id), "Latest version must still have content"

            # (b) Most recent N versions must still exist with content
            for i in range(keep_recent_n):
                ver_num = extra_old_count + i + 1
                recent_ver = get_version(ver_num)
                assert recent_ver is not None, f"Recent version {ver_num} must still exist"
                assert recent_ver.status != "archived", f"Recent version {ver_num} must not be archived"
                assert has_content(recent_ver.id), f"Recent version {ver_num} must still have content"

            # (c) All milestone versions must still exist with content
            for i in valid_milestone_indices:
                ver_num = i + 1
                milestone_ver = get_version(ver_num)
                assert milestone_ver is not None, f"Milestone version {ver_num} must still exist"
                assert milestone_ver.status != "archived", f"Milestone version {ver_num} must not be archived"
                assert has_content(milestone_ver.id), f"Milestone version {ver_num} must still have content"

            # (d) Old non-milestone, non-recent versions must be archived (content deleted)
            for i in range(extra_old_count):
                ver_num = i + 1
                is_milestone = i in valid_milestone_indices
                is_recent = ver_num > (total_versions - keep_recent_n)
                is_latest = ver_num == total_versions

                if not is_milestone and not is_recent and not is_latest:
                    old_ver = get_version(ver_num)
                    assert old_ver is not None, f"Old version {ver_num} record must still exist"
                    assert old_ver.status == "archived", (
                        f"Old non-milestone version {ver_num} must be archived, got {old_ver.status}"
                    )
                    assert not has_content(old_ver.id), (
                        f"Old non-milestone version {ver_num} must have content deleted"
                    )

        finally:
            session.close()
            engine.dispose()
