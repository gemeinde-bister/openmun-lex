"""Tests for lex_web.aliases module."""

from lex_web.aliases import normalize_doctype, normalize_entity, normalize_organ


class TestNormalizeEntity:
    def test_cm_to_eg(self) -> None:
        assert normalize_entity("cm") == "eg"

    def test_cb_to_bg(self) -> None:
        assert normalize_entity("cb") == "bg"

    def test_co_prefix_to_gt(self) -> None:
        assert normalize_entity("co:bisteralpe") == "gt:bisteralpe"

    def test_canonical_returns_none(self) -> None:
        assert normalize_entity("eg") is None
        assert normalize_entity("bg") is None
        assert normalize_entity("gt:bisteralpe") is None

    def test_unknown_returns_none(self) -> None:
        assert normalize_entity("xx") is None


class TestNormalizeOrgan:
    def test_versammlung(self) -> None:
        assert normalize_organ("versammlung") == "assembly"

    def test_assemblee(self) -> None:
        assert normalize_organ("assemblee") == "assembly"

    def test_rat(self) -> None:
        assert normalize_organ("rat") == "council"

    def test_conseil(self) -> None:
        assert normalize_organ("conseil") == "council"

    def test_parlament(self) -> None:
        assert normalize_organ("parlament") == "parliament"

    def test_canonical_returns_none(self) -> None:
        assert normalize_organ("assembly") is None
        assert normalize_organ("council") is None
        assert normalize_organ("parliament") is None


class TestNormalizeDoctype:
    def test_protokoll(self) -> None:
        assert normalize_doctype("protokoll") == "protocol"

    def test_protocole(self) -> None:
        assert normalize_doctype("protocole") == "protocol"

    def test_beschluss(self) -> None:
        assert normalize_doctype("beschluss") == "decision"

    def test_mitteilung(self) -> None:
        assert normalize_doctype("mitteilung") == "notice"

    def test_communication(self) -> None:
        assert normalize_doctype("communication") == "notice"

    def test_canonical_returns_none(self) -> None:
        assert normalize_doctype("protocol") is None
        assert normalize_doctype("decision") is None
        assert normalize_doctype("notice") is None
